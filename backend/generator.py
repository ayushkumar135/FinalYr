#!/usr/bin/env python3
import json
import sys
import re
import shlex
from typing import Any, Dict, List, Optional, Tuple

# -----------------------------
# Quoting helpers
# -----------------------------
def sh_quote(path: str) -> str:
    """Shell-safe quoting for file paths."""
    return shlex.quote(path) if path is not None else "''"

def awk_q(s: str) -> str:
    """Quote a literal for awk double-quoted strings."""
    if s is None:
        return '""'
    s = str(s).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'

# -----------------------------
# Predicates: match_pattern + conditions (+ logic)
# -----------------------------
def build_match_predicate(match_pattern: Optional[str], match_fields: Any) -> Optional[str]:
    if not match_pattern:
        return None
    pat = f"/{match_pattern}/"
    # match_fields empty or None => $0
    if match_fields in (None, [], "all"):
        return f"($0 ~ {pat})"
    if isinstance(match_fields, list) and len(match_fields) > 0:
        parts = [f"(${f} ~ {pat})" for f in match_fields]
        return "(" + " || ".join(parts) + ")"
    return f"($0 ~ {pat})"

def build_condition(c: Dict[str, Any]) -> str:
    field = c["field"]
    op = str(c["operator"]).strip().lower()
    val = c.get("value")
    ctype = str(c.get("type", "string")).lower()

    lhs = f"${field}"
    if op in ("==", "!=", ">", "<", ">=", "<="):
        if ctype == "number":
            return f"(({lhs}+0) {op} {val})"
        else:
            return f"({lhs} {op} {awk_q(val)})"
    if op == "contains":
        return f"(index({lhs}, {awk_q(val)})>0)"
    if op == "matches":
        return f"({lhs} ~ /{val}/)"
    # default to string equality
    return f"({lhs} == {awk_q(val)})"

def build_conditions_logic(conditions: List[Dict[str, Any]], logic: Optional[str]) -> Optional[str]:
    if not conditions:
        return None
    cmap = {c["id"].lower(): build_condition(c) for c in conditions}
    if not logic:
        return "(" + " && ".join(cmap[c["id"].lower()] for c in conditions) + ")"
    expr = re.sub(r"\s+", " ", logic.strip())
    expr = re.sub(r"\bAND\b", "&&", expr, flags=re.IGNORECASE)
    expr = re.sub(r"\bOR\b", "||", expr, flags=re.IGNORECASE)
    for cid, awkexp in sorted(cmap.items(), key=lambda kv: -len(kv[0])):
        expr = re.sub(rf"\b{re.escape(cid)}\b", awkexp, expr, flags=re.IGNORECASE)
    return f"({expr})"

def combine_predicates(match_pred: Optional[str], cond_pred: Optional[str]) -> Optional[str]:
    if match_pred and cond_pred:
        return f"({match_pred} && {cond_pred})"
    return match_pred or cond_pred

# -----------------------------
# AWK program builders
# -----------------------------
def awk_begin(fs_regex=r"[[:space:]]+") -> str:
    return f'BEGIN{{FS="{fs_regex}"; OFS=" "}}'

def key_expr_for_group_by(group_by: List[int]) -> Tuple[str, bool]:
    """Returns (key_expr, multi) where multi indicates multi-field key using SUBSEP."""
    if len(group_by) == 0:
        # No grouping key: single global key
        return 'key="__ALL__"', False
    if len(group_by) == 1:
        return f'key=${group_by[0]}', False
    # multi key with SUBSEP
    parts = [f"${f}" for f in group_by]
    return f'key={parts[0]}', True  # we'll append SUBSEP at use time

def make_key_assignment(group_by: List[int]) -> str:
    if len(group_by) <= 1:
        expr, _ = key_expr_for_group_by(group_by)
        return expr
    # multi-field
    head = f"key=${group_by[0]}"
    tail = "".join([f" SUBSEP ${f}" for f in group_by[1:]])
    return head + tail

def build_filter_body(ir: Dict[str, Any], pred: Optional[str]) -> str:
    # Print fields or entire line
    pfields = ir.get("print_fields")
    if pfields and isinstance(pfields, list) and len(pfields) > 0:
        printer = "print " + ", ".join(f"${i}" for i in pfields)
    else:
        printer = "print $0"
    if pred:
        return f"{pred}{{ {printer} }}"
    return f"{{ {printer} }}"

def build_aggregate_body(ir: Dict[str, Any], pred: Optional[str]) -> Tuple[str, str]:
    """
    Returns (body, end_block)
    Supports: count, sum, avg, min, max
    """
    group_by = ir.get("group_by") or []
    agg_func = (ir.get("agg_func") or "").lower()
    agg_field = ir.get("agg_field")
    if agg_func not in ("count", "sum", "avg", "min", "max"):
        agg_func = "count"

    key_assign = make_key_assignment(group_by)

    # value expression (numeric) for functions requiring field
    val_expr = None
    if agg_func in ("sum", "avg", "min", "max"):
        # agg_field may be number or [number]
        if isinstance(agg_field, list):
            agg_field = agg_field[0] if agg_field else None
        if not isinstance(agg_field, int):
            # Fallback to count if not provided
            agg_func = "count"
        else:
            val_expr = f"(${agg_field}+0)"

    # Update statements
    if agg_func == "count":
        update = "count[key]++"
        end_array = "count"
        metric = "count[k]"
    elif agg_func == "sum":
        update = f"sum[key]+={val_expr}"
        end_array = "sum"
        metric = "sum[k]"
    elif agg_func == "avg":
        update = f"sum[key]+={val_expr}; n[key]++"
        end_array = "sum"  # we iterate over sum
        metric = "sum[k]/n[k]"
    elif agg_func == "min":
        update = f'if (!(key in min) || {val_expr} < min[key]) min[key]={val_expr}'
        end_array = "min"
        metric = "min[k]"
    else:  # max
        update = f'if (!(key in max) || {val_expr} > max[key]) max[key]={val_expr}'
        end_array = "max"
        metric = "max[k]"

    guarded = f"{pred}{{ {key_assign}; {update} }}" if pred else f"{{ {key_assign}; {update} }}"
    # Print group keys followed by metric
    # For multi-field keys: split(k,a,SUBSEP); print a[1], a[2], ..., metric
    if len(group_by) <= 1:
        end = (
            f'END{{ for (k in {end_array}) {{ printf "%s %s\\n", k, {metric} }} }}'
        )
    else:
        # build printing of all key parts
        key_printf_parts = ['a[1]'] + [f'a[{i}]' for i in range(2, len(group_by)+1)]
        key_fmt = " ".join(["%s"] * len(key_printf_parts))
        end = (
            'END{ for (k in ' + end_array + ') { '
            'split(k,a,SUBSEP); '
            f'printf "' + key_fmt + ' %s\\n", ' + ", ".join(key_printf_parts) + f', {metric} }}'
        )
    return guarded, end

# -----------------------------
# Sorting & limit
# -----------------------------
def build_sort(ir: Dict[str, Any], is_aggregate: bool) -> Optional[str]:
    sort_by = ir.get("sort_by") or []
    sort_order = (ir.get("sort_order") or "").lower() or None
    if not sort_by:
        return None

    # Map input fields to output columns:
    # Aggregate output: [group_by..., metric]
    # Filter output: [print_fields...] (or raw line => can't map; use -k1,1)
    keys = []

    if is_aggregate:
        group_by = ir.get("group_by") or []
        agg_field = ir.get("agg_field")
        if isinstance(agg_field, list):
            agg_field = agg_field[0] if agg_field else None
        metric_col = len(group_by) + 1
        outpos = {f: i+1 for i, f in enumerate(group_by)}
        for f in sort_by:
            col = outpos.get(f)
            if col is None and (agg_field is not None and f == agg_field):
                col = metric_col
            if col is None:
                col = metric_col  # default to metric when in doubt
            keys.append(f"-k{col},{col}")
    else:
        pfields = ir.get("print_fields") or []
        if not pfields:
            # Unknown columns when printing raw lines: fallback to first field
            keys.append("-k1,1")
        else:
            outpos = {f: i+1 for i, f in enumerate(pfields)}
            for f in sort_by:
                col = outpos.get(f, 1)
                keys.append(f"-k{col},{col}")

    order_flag = "" if sort_order in (None, "asc") else "-r"
    return " | sort " + order_flag + " " + " ".join(keys)

def build_limit(ir: Dict[str, Any]) -> Optional[str]:
    lim = ir.get("limit")
    if lim is None:
        return None
    try:
        n = int(lim)
    except Exception:
        return None
    if n > 0:
        return f" | head -n {n}"
    return None

# -----------------------------
# Main generator
# -----------------------------
def generate_pipeline(ir: Dict[str, Any]) -> str:
    """
    Returns a shell pipeline string (awk + optional sort + head).
    """
    # Build predicate from match + conditions/logic
    match_pred = build_match_predicate(ir.get("match_pattern"), ir.get("match_fields"))
    conds = ir.get("conditions") or []
    cond_pred = build_conditions_logic(conds, ir.get("logic")) if conds else None
    pred = combine_predicates(match_pred, cond_pred)

    # Determine if this is an aggregate or filter
    agg_func = (ir.get("agg_func") or "").lower()
    group_by = ir.get("group_by") or []
    is_aggregate = bool(agg_func or group_by)

    if is_aggregate:
        body, endblk = build_aggregate_body(ir, pred)
        awk_prog = f"awk '{awk_begin()}; {body} {endblk}'"
    else:
        body = build_filter_body(ir, pred)
        awk_prog = f"awk '{awk_begin()}; {body}'"

    # Attach file after program
    file_arg = ir.get("file")
    if file_arg:
        awk_prog += " " + sh_quote(file_arg)

    # Sorting + Limit
    srt = build_sort(ir, is_aggregate)
    if srt:
        awk_prog += srt
    lim = build_limit(ir)
    if lim:
        awk_prog += lim

    return awk_prog

# -----------------------------
# CLI entry (for Node runPython)
# -----------------------------
def main():
    try:
        raw = sys.stdin.read()
        ir = json.loads(raw)
        cmd = generate_pipeline(ir)
        print(json.dumps({"cmd": cmd}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    main()
