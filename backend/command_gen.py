#!/usr/bin/env python3
import sys, json, re, traceback
from typing import Any, Dict, List

def safe_print_stdout(obj):
    print(json.dumps(obj))
    sys.stdout.flush()

def safe_err(msg: str):
    print(msg, file=sys.stderr)
    sys.stderr.flush()

def to_wsl_path(s: Any) -> str:
    if not s:
        return "''"
    s = str(s)
    if s.startswith("/mnt/") or s.startswith("/"):
        return "'" + s + "'"
    m = re.match(r'^([A-Za-z]):[\\/](.*)$', s)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2).replace("\\", "/")
        return f"'/mnt/{drive}/{rest}'"
    return "'" + s.replace("\\", "/") + "'"

def escape_single(s: Any) -> str:
    if s is None:
        return ""
    return str(s).replace("'", "'\"'\"'")

def safe_int(x: Any, default: int = None):
    try:
        return int(x)
    except Exception:
        return default

def fields_expr(fields: Any) -> str:
    if not fields or fields == "all":
        return "$0"
    if isinstance(fields, list) and len(fields) > 0:
        return ", ".join("$" + str(i) for i in fields)
    return "$0"

def build_command(ir: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    try:
        action = (ir.get("action") or "").lower()
        if not action:
            errors.append("Missing action")
            return {"errors": errors, "command": ""}

        lf_raw = ir.get("log_file", "")
        lf = to_wsl_path(lf_raw)
        pat_raw = ir.get("grep_regex") or ""
        pat = escape_single(pat_raw)
        pf = fields_expr(ir.get("fields"))

        # Build single-step awk or echo fallback
        if action == "list":
            script = "BEGIN{IGNORECASE=1} $0 ~ pat { print " + pf + " }"
            cmd = "awk -v pat='" + pat + "' '" + script + "' " + lf
            return {"errors": errors, "command": cmd}

        if action == "count":
            script = "BEGIN{IGNORECASE=1} $0 ~ pat { c++ } END { print c+0 }"
            cmd = "awk -v pat='" + pat + "' '" + script + "' " + lf
            return {"errors": errors, "command": cmd}

        if action in ("unique", "distinct"):
            key_expr = pf
            script = "BEGIN{IGNORECASE=1} $0 ~ pat { key = " + key_expr + "; if(!seen[key]++) print key }"
            cmd = "awk -v pat='" + pat + "' '" + script + "' " + lf
            return {"errors": errors, "command": cmd}

        if action == "unique_count":
            key_expr = pf
            script = "BEGIN{IGNORECASE=1} $0 ~ pat { seen[" + key_expr + "] = 1 } END { print length(seen)+0 }"
            cmd = "awk -v pat='" + pat + "' '" + script + "' " + lf
            return {"errors": errors, "command": cmd}

        if action in ("top", "group_count"):
            limit = safe_int(ir.get("limit"), 0) or 0
            group_by = ir.get("group_by") or ir.get("fields")
            if isinstance(group_by, list) and group_by:
                key_concat = " || '|' || ".join("$" + str(i) for i in group_by)
            else:
                key_concat = pf
            if limit > 0:
                script = ("BEGIN{IGNORECASE=1} $0 ~ pat { key = " + key_concat +
                          "; cnt[key]++ } END { PROCINFO[\"sorted_in\"]=\"@val_num_desc\"; p=0; for(k in cnt) { print cnt[k], k; if(++p==" + str(limit) + ") break } }")
            else:
                script = ("BEGIN{IGNORECASE=1} $0 ~ pat { key = " + key_concat +
                          "; cnt[key]++ } END { PROCINFO[\"sorted_in\"]=\"@val_num_desc\"; for(k in cnt) print cnt[k], k }")
            cmd = "awk -v pat='" + pat + "' '" + script + "' " + lf
            return {"errors": errors, "command": cmd}

        if action == "head":
            n = safe_int(ir.get("limit"), 10) or 10
            if pat_raw:
                script = "BEGIN{IGNORECASE=1} $0 ~ pat { print; if(++c==" + str(n) + ") exit }"
                cmd = "awk -v pat='" + pat + "' '" + script + "' " + lf
            else:
                script = "NR<=" + str(n) + " { print }"
                cmd = "awk '" + script + "' " + lf
            return {"errors": errors, "command": cmd}

        if action == "tail":
            n = safe_int(ir.get("limit"), 100) or 100
            script = ("BEGIN{IGNORECASE=1} $0 ~ pat { buf[++m] = $0 } END { start = (m > " + str(n) +
                      " ? m - " + str(n) + " + 1 : 1); for(i = start; i <= m; i++) print buf[i] }")
            cmd = "awk -v pat='" + pat + "' '" + script + "' " + lf
            return {"errors": errors, "command": cmd}

        if action == "sample":
            n = safe_int(ir.get("limit"), 1) or 1
            script = (
                f'BEGIN{{srand()}} '
                f'$0 ~ pat {{ '
                f'if(++m <= {n}) arr[m] = $0; '
                f'else {{ k = int(rand()*m)+1; if(k <= {n}) arr[k] = $0 }} '
                f'}} '
                f'END {{ limit = (m < {n} ? m : {n}); for(i=1; i<=limit; i++) print arr[i] }}'
            )
            cmd = f'awk -v pat="{pat}" "{script}" {lf}'
            return {"errors": errors, "command": cmd}


        if action == "exists":
            script = "BEGIN{IGNORECASE=1} $0 ~ pat { print; exit }"
            cmd = "awk -v pat='" + pat + "' '" + script + "' " + lf
            return {"errors": errors, "command": cmd}

        if action == "aggregate":
            nf_raw = ir.get("fields")
            if nf_raw is None:
                errors.append("aggregate requires fields")
                return {"errors": errors, "command": ""}
            if isinstance(nf_raw, list) and nf_raw:
                nf_raw = nf_raw[0]
            nf = safe_int(nf_raw, None)
            if nf is None:
                errors.append("fields must be integer index")
                return {"errors": errors, "command": ""}
            agg = (ir.get("agg") or "sum").lower()
            group_by = ir.get("group_by")
            if group_by and isinstance(group_by, list) and group_by:
                key_concat = " || '|' || ".join("$" + str(i) for i in group_by)
                if agg == "sum":
                    script = "BEGIN{IGNORECASE=1} $0 ~ pat { key = " + key_concat + "; sum[key] += $" + str(nf) + " } END { for(k in sum) print k, sum[k] }"
                elif agg == "avg":
                    script = "BEGIN{IGNORECASE=1} $0 ~ pat { key = " + key_concat + "; sum[key] += $" + str(nf) + "; cnt[key]++ } END { for(k in sum) print k, sum[k]/cnt[k] }"
                else:
                    errors.append(f"aggregate {agg} not implemented for group_by")
                    return {"errors": errors, "command": ""}
            else:
                if agg == "sum":
                    script = "BEGIN{IGNORECASE=1} $0 ~ pat { s += $" + str(nf) + " } END { print s+0 }"
                elif agg == "avg":
                    script = "BEGIN{IGNORECASE=1} $0 ~ pat { s += $" + str(nf) + "; c++ } END { print (c? s/c : 0) }"
                else:
                    errors.append(f"aggregate {agg} not implemented")
                    return {"errors": errors, "command": ""}
            cmd = "awk -v pat='" + pat + "' '" + script + "' " + lf
            return {"errors": errors, "command": cmd}

        if action in ("time_series", "rate", "anomaly"):
            return {"errors": errors, "command": "echo 'TIME_SERIES: requires timestamp parsing; use Python for this.'"}

        if action == "stats":
            nf_raw = ir.get("fields")
            if nf_raw is None:
                errors.append("stats requires fields")
                return {"errors": errors, "command": ""}
            if isinstance(nf_raw, list) and nf_raw:
                nf_raw = nf_raw[0]
            nf = safe_int(nf_raw, None)
            if nf is None:
                errors.append("numeric_field must be integer")
                return {"errors": errors, "command": ""}
            script = ("BEGIN{IGNORECASE=1} $0 ~ pat { x = $" + str(nf) +
                      "; sum += x; cnt++; if(min==\"\" || x<min) min = x; if(max==\"\" || x>max) max = x } "
                      "END { print \"count=\" cnt; print \"min=\" (cnt?min:0); print \"max=\" (cnt?max:0); print \"avg=\" (cnt? sum/cnt:0) }")
            cmd = "awk -v pat='" + pat + "' '" + script + "' " + lf
            return {"errors": errors, "command": cmd}

        errors.append(f"Unsupported action: {action}")
        return {"errors": errors, "command": ""}

    except Exception as e:
        # give traceback on stderr but return JSON error to stdout
        safe_err("command_gen exception:\n" + traceback.format_exc())
        return {"errors": [str(e)], "command": ""}

def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            safe_print_stdout({"errors": ["no input received"], "command": ""})
            sys.exit(0)
        ir = json.loads(raw)
        safe_err("command_gen received IR: " + json.dumps(ir))
    except Exception as e:
        safe_err("failed parsing input: " + str(e))
        safe_print_stdout({"errors": [f"Invalid input JSON: {e}"], "command": ""})
        sys.exit(0)

    out = build_command(ir)
    # Always print JSON to stdout for Node to parse
    safe_print_stdout(out)
    sys.exit(0)

if __name__ == "__main__":
    main()
