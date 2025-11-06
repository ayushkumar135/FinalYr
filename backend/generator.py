#!/usr/bin/env python3
import sys, json, shlex, re

# ---------------------------------------------------
# YOUR ORIGINAL FUNCTIONS (UNCHANGED)
# ---------------------------------------------------
# ✅ ALL your helper + predicate + awk builder functions stay EXACTLY as you provided
# ✅ EXCEPT removing “example usage”
# ✅ I place them below:

# -----------------------------
# Helpers
# -----------------------------
def sh_quote(s: str) -> str:
    if s is None:
        return "''"
    return "'" + s.replace("'", "'\"'\"'") + "'"

def awk_str(val: str) -> str:
    if val is None:
        return '""'
    v = val.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{v}"'

def build_match_predicate(match_pattern, match_fields):
    if not match_pattern:
        return None
    pat = f'/{match_pattern}/'
    if match_fields == "all" or match_fields is None:
        return f'($0 ~ {pat})'
    if isinstance(match_fields, list) and match_fields:
        parts = [f'($${f} ~ {pat})' for f in match_fields]
        return "(" + " || ".join(parts) + ")"
    return None

def build_condition(cond):
    field = cond["field"]
    op = cond["operator"].strip().lower()
    val = cond["value"]
    ctype = cond.get("type", "string").lower()
    lhs = f'$${field}'

    if op in ("==", "!=", ">", "<", ">=", "<="):
        if ctype == "number":
            lhs = f'(${lhs}+0)'
            rhs = str(val)
            return f'({lhs} {op} {rhs})'
        else:
            rhs = awk_str(str(val))
            return f'({lhs} {op} {rhs})'

    if op == "contains":
        return f'(index({lhs}, {awk_str(str(val))})>0)'

    if op == "matches":
        return f'({lhs} ~ /{val}/)'

    return f'({lhs} == {awk_str(str(val))})'

def build_logic(conditions, logic_str):
    if not conditions:
        return None
    condition_map = {c["id"].lower(): build_condition(c) for c in conditions}
    if not logic_str:
        return "(" + " && ".join(condition_map[c["id"].lower()] for c in conditions) + ")"

    expr = logic_str.strip()
    expr = re.sub(r"\s+", " ", expr, flags=re.MULTILINE).strip()
    expr = re.sub(r"\bAND\b", "&&", expr, flags=re.IGNORECASE)
    expr = re.sub(r"\bOR\b", "||", expr, flags=re.IGNORECASE)

    # Replace conditions
    for cid, awkexp in sorted(condition_map.items(), key=lambda x: -len(x[0])):
        expr = re.sub(rf"\b{re.escape(cid)}\b", awkexp, expr, flags=re.IGNORECASE)
    return f"({expr})"

def build_predicate(ir):
    preds = []
    mp = build_match_predicate(ir.get("match_pattern"), ir.get("match_fields"))
    if mp: preds.append(mp)
    conds = ir.get("conditions") or []
    if conds: preds.append(build_logic(conds, ir.get("logic")))
    if preds: return "(" + " && ".join(preds) + ")"
    return None

def awk_begin(fs_regex=r"[[:space:]]+"):
    return f'BEGIN{{FS="{fs_regex}"; OFS=" "}}'

def build_filter_awkir(ir):
    pred = build_predicate(ir)
    print_fields = ir.get("print_fields")
    if print_fields:
        printer = "print " + ", ".join(f"$${i}" for i in print_fields)
    else:
        printer = "print $0"
    if pred:
        body = f'{pred}{{ {printer} }}'
    else:
        body = f'{{ {printer} }}'
    return f"awk '{awk_begin()}; {body}'"

# (the rest continues...)

def build_aggregate_awkir(ir):
    group_by = ir.get("group_by") or []
    agg_func = (ir.get("agg_func") or "").lower() or None
    agg_field = ir.get("agg_field")
    pred = build_predicate(ir)

    if not group_by or not agg_func:
        return build_filter_awkir(ir)

    if len(group_by) == 1:
        key_expr = f'$${group_by[0]}'
    else:
        parts = [f'$${i}' for i in group_by]
        key_expr = "key=" + parts[0] + "".join([f" SUBSEP {p}" for p in parts[1:]])

    update = []
    if agg_func == "count":
        update.append(f'count[{key_expr}]++')
        metric_expr = "count[k]"
    else:
        f = f'$${agg_field}+0'
        update.append(f'sum[{key_expr}]+={f}')
        if agg_func == "avg":
            update.append(f'n[{key_expr}]++')
            metric_expr = "sum[k]/n[k]"
        elif agg_func == "sum":
            metric_expr = "sum[k]"
        elif agg_func == "max":
            update.append(f'if (!(k in max) || ({f}>max[k])) max[k]={f}')
            metric_expr = "max[k]"
        elif agg_func == "min":
            update.append(f'if (!(k in min) || ({f}<min[k])) min[k]={f}')
            metric_expr = "min[k]"
        else:
            metric_expr = "sum[k]"

    pred_guard = pred if pred else "1"
    body = f'{pred_guard}{{ key={key_expr}; {"; ".join(update)} }}'

    end_lines = [
        'for (k in sum) { printf "%s %s\\n", k, ' + metric_expr + " }"
    ]

    awk_prog = f"awk '{awk_begin()}; {body} END{{ {' '.join(end_lines)} }}'"
    return awk_prog

def build_sort(ir, action):
    sort_by = ir.get("sort_by") or []
    sort_order = (ir.get("sort_order") or "").lower()
    if not sort_by: return None
    order_flag = "" if sort_order in (None, "asc") else "-r"
    keys = [f"-k1,1"]
    return " | sort " + order_flag + " " + " ".join(keys)

def build_limit(ir):
    if ir.get("limit"):
        return f" | head -n {ir['limit']}"
    return None

def generate_pipeline(ir):
    action = (ir.get("action") or "filter").lower()
    if action == "aggregate":
        base = build_aggregate_awkir(ir)
    else:
        base = build_filter_awkir(ir)

    if ir.get("file"):
        base += " " + shlex.quote(ir["file"])

    s = build_sort(ir, action)
    if s: base += s

    l = build_limit(ir)
    if l: base += l

    return base

# ---------------------------------------------------
# ✅ MAIN ENTRY FOR NODE.JS
# ---------------------------------------------------
def main():
    try:
        ir = json.loads(sys.stdin.read())
        cmd = generate_pipeline(ir)
        print(json.dumps({"cmd": cmd}))  # ONLY JSON
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    main()
