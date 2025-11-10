#!/usr/bin/env python3
import json
import sys
import re
import shlex
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# Quoting Helpers
# ============================================================

def sh_quote(path: str) -> str:
    """Shell-safe quoting for file paths."""
    return shlex.quote(path) if path is not None else "''"

def awk_q(s: str) -> str:
    """Quote literal for awk strings."""
    if s is None:
        return '""'
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

def sed_q(s: Optional[str]) -> str:
    """Quote literal for sed single-quoted context."""
    if s is None:
        s = ""
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


# ============================================================
# Match Predicate + Conditions + Logic
# ============================================================

def build_match_predicate(pattern: Optional[str], fields: Any) -> Optional[str]:
    if not pattern:
        return None
    pat = f"/{pattern}/"
    if fields in (None, [], "all"):
        return f"($0 ~ {pat})"
    if isinstance(fields, list) and len(fields) > 0:
        parts = [f"(${f} ~ {pat})" for f in fields]
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

    if op == "starts_with":
        return f"({lhs} ~ /^" + str(val) + "/)"

    if op == "ends_with":
        return f"({lhs} ~ /" + str(val) + "$/)"

    return f"({lhs} == {awk_q(val)})"


def build_conditions_logic(conditions, logic):
    if not conditions:
        return None

    cmap = {c["id"].lower(): build_condition(c) for c in conditions}

    if not logic:
        return "(" + " && ".join(cmap[c["id"].lower()] for c in conditions) + ")"

    expr = re.sub(r"\s+", " ", logic.strip())
    expr = re.sub(r"\bAND\b", "&&", expr, flags=re.IGNORECASE)
    expr = re.sub(r"\bOR\b", "||", expr, flags=re.IGNORECASE)

    for cid, awkexp in sorted(cmap.items(), key=lambda kv: -len(kv[0])):
        expr = re.sub(rf"\b{cid}\b", awkexp, expr, flags=re.IGNORECASE)

    return f"({expr})"


def combine_predicates(match_pred, cond_pred):
    if match_pred and cond_pred:
        return f"({match_pred} && {cond_pred})"
    return match_pred or cond_pred


# ============================================================
# Line-Level Mutations (sed before awk)
# ============================================================

def build_sed_mutations(mutations):
    if not mutations:
        return ""

    cmds = []
    for m in mutations:
        mtype = (m.get("type") or "").lower()
        pat = m.get("pattern") or ""
        rep = m.get("replacement") or ""

        if mtype == "sed_substitute":
            cmds.append(f"sed -E 's|{pat}|{rep}|g'")

        elif mtype == "delete_lines":
            cmds.append(f"sed -E '/{pat}/d'")

        elif mtype == "prefix":
            cmds.append(f"sed -E 's|^|{rep}|'")

        elif mtype == "suffix":
            cmds.append(f"sed -E 's|$|{rep}|'")

        elif mtype == "collapse_spaces":
            cmds.append("sed -E 's|[[:space:]]+| |g'")

    return " | ".join(cmds)


# ============================================================
# Field-Level Transformations (inside AWK)
# ============================================================

def build_transformations(ir):
    transforms = ir.get("transformations") or []
    code = []

    for t in transforms:
        ttype = (t.get("type") or "").lower()
        f = t.get("field")
        if not f:
            continue

        if ttype == "lowercase":
            code.append(f"${f} = tolower(${f})")

        elif ttype == "uppercase":
            code.append(f"${f} = toupper(${f})")

        elif ttype == "trim":
            code.append(f"${f} = gensub(/^\\s+|\\s+$/, \"\", \"g\", ${f})")

        elif ttype in ("replace", "substitute"):
            pat = t.get("pattern", "")
            rep = t.get("replacement", "")
            code.append(f"${f} = gensub(/{pat}/, {awk_q(rep)}, \"g\", ${f})")

        elif ttype == "extract_substring":
            pat = t.get("pattern", "")
            code.append(f"${f} = gensub(/{pat}/, \"\\\\1\", 1, ${f})")

    return "; ".join(code)


# ============================================================
# AWK Program Builders
# ============================================================

def awk_begin(fs_regex=r"[[:space:]]+") -> str:
    return f'BEGIN{{FS="{fs_regex}"; OFS=" "}}'


def make_key_assignment(group_by: List[int]) -> str:
    if len(group_by) == 0:
        return 'key="__ALL__"'
    if len(group_by) == 1:
        return f"key=${group_by[0]}"
    return "key=" + " ".join([f"${group_by[0]}"] +
                             [f"SUBSEP ${g}" for g in group_by[1:]])


def build_filter_body(ir, pred):
    pfields = ir.get("print_fields")
    if pfields:
        printer = "print " + ", ".join(f"${i}" for i in pfields)
    else:
        printer = "print $0"

    trans = build_transformations(ir)

    if pred:
        if trans:
            return f"{pred}{{ {trans}; {printer} }}"
        return f"{pred}{{ {printer} }}"

    if trans:
        return f"{{ {trans}; {printer} }}"
    return f"{{ {printer} }}"


def build_aggregate_body(ir, pred):
    group_by = ir.get("group_by") or []
    agg_func = (ir.get("agg_func") or "").lower()
    agg_field = ir.get("agg_field")

    if agg_func not in ("count", "sum", "avg", "min", "max"):
        agg_func = "count"

    key_assign = make_key_assignment(group_by)

    val_expr = None
    if agg_func in ("sum", "avg", "min", "max"):
        if isinstance(agg_field, list):
            agg_field = agg_field[0]
        val_expr = f"(${agg_field}+0)"

    if agg_func == "count":
        update = "count[key]++"
        arr = "count"
        metric = "count[k]"

    elif agg_func == "sum":
        update = f"sum[key]+={val_expr}"
        arr = "sum"
        metric = "sum[k]"

    elif agg_func == "avg":
        update = f"sum[key]+={val_expr}; n[key]++"
        arr = "sum"
        metric = "sum[k]/n[k]"

    elif agg_func == "min":
        update = f"if (!(key in min) || {val_expr} < min[key]) min[key]={val_expr}"
        arr = "min"
        metric = "min[k]"

    else:
        update = f"if (!(key in max) || {val_expr} > max[key]) max[key]={val_expr}"
        arr = "max"
        metric = "max[k]"

    trans = build_transformations(ir)

    inner = f"{key_assign}; {update}"
    if trans:
        inner = f"{trans}; " + inner

    body = f"{pred}{{ {inner} }}" if pred else f"{{ {inner} }}"

    if len(group_by) <= 1:
        end = f'END{{ for (k in {arr}) printf "%s %s\\n", k, {metric} }}'
    else:
        key_fmt = " ".join(["%s"] * len(group_by))
        keys = ", ".join([f"a[{i}]" for i in range(1, len(group_by)+1)])
        end = (
            f'END{{ for (k in {arr}) {{ split(k,a,SUBSEP); '
            f'printf "{key_fmt} %s\\n", {keys}, {metric} }} }}'
        )

    return body, end


# ============================================================
# Sorting + Limit
# ============================================================

def build_sort(ir, is_aggregate):
    sort_by = ir.get("sort_by") or []
    order = (ir.get("sort_order") or "").lower()

    if not sort_by:
        return None

    keys = []

    if is_aggregate:
        group_by = ir.get("group_by") or []
        agg_field = ir.get("agg_field")
        metric_col = len(group_by) + 1

        pos = {f: i+1 for i, f in enumerate(group_by)}

        for f in sort_by:
            col = pos.get(f, metric_col)
            keys.append(f"-k{col},{col}")

    else:
        p = ir.get("print_fields") or []
        if not p:
            keys.append("-k1,1")
        else:
            pos = {f: i+1 for i, f in enumerate(p)}
            for f in sort_by:
                col = pos.get(f, 1)
                keys.append(f"-k{col},{col}")

    order_flag = "" if order in ("", "asc") else "-r"
    return f" | sort {order_flag} " + " ".join(keys)


def build_limit(ir):
    lim = ir.get("limit")
    if lim is None:
        return None
    try:
        n = int(lim)
        return f" | head -n {n}"
    except:
        return None


# ============================================================
# ✅ ✅ FIXED PIPELINE GENERATOR (sed receives the filename)
# ============================================================

def generate_pipeline(ir):

    match_pred = build_match_predicate(ir.get("match_pattern"), ir.get("match_fields"))
    cond_pred = None
    if ir.get("conditions"):
        cond_pred = build_conditions_logic(ir.get("conditions"), ir.get("logic"))

    pred = combine_predicates(match_pred, cond_pred)

    is_aggregate = bool(ir.get("agg_func") or ir.get("group_by"))

    
    if is_aggregate:
        body, endblk = build_aggregate_body(ir, pred)
        awk_cmd = f"awk '{awk_begin()}; {body} {endblk}'"
    else:
        body = build_filter_body(ir, pred)
        awk_cmd = f"awk '{awk_begin()}; {body}'"

    filename = ir.get("file")
    sed_cmds = build_sed_mutations(ir.get("line_mutations"))

    
    if sed_cmds:
        if filename:
            pipeline = f"{sed_cmds} {sh_quote(filename)} | {awk_cmd}"
        else:
            pipeline = f"{sed_cmds} | {awk_cmd}"
    else:
        
        pipeline = awk_cmd
        if filename:
            pipeline += " " + sh_quote(filename)

    
    sort_cmd = build_sort(ir, is_aggregate)
    if sort_cmd:
        pipeline += sort_cmd

    limit_cmd = build_limit(ir)
    if limit_cmd:
        pipeline += limit_cmd

    return pipeline


# ============================================================
# CLI Entry for Node.js
# ============================================================

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
