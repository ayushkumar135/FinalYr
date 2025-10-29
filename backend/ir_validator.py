#!/usr/bin/env python3
import sys
import json
from typing import Dict, Any, List

VALID_ACTIONS = {
    "list", "count", "top", "distinct", "unique", "unique_count",
    "group_count", "aggregate", "time_series", "rate",
    "filter", "head", "tail", "sample", "stats", "exists", "anomaly", "sort"
}

def validate_ir(ir: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    # Debug marker
    print("hello", file=sys.stderr)  # ✅ goes to stderr so JSON stdout stays clean
    if not ir.get("original_query"):
        errors.append("Missing original_query.")
    if not ir.get("log_file"):
        errors.append("Missing log_file.")
    action = ir.get("action")
    if not action:
        errors.append("Missing action.")
    elif action not in VALID_ACTIONS:
        errors.append(f"Invalid action '{action}'. Allowed: {sorted(VALID_ACTIONS)}")
    return errors

if __name__ == "__main__":
    try:
        # Debug marker
        print("okkkkk", file=sys.stderr)  # ✅ stderr, won't break JSON output
        raw = sys.stdin.read()
        if not raw.strip():
            print(json.dumps({"errors": ["No input received."]}))
            sys.exit(0)
        ir = json.loads(raw)
        errs = validate_ir(ir)
        result = {"errors": errs}
        print(json.dumps(result))
        sys.exit(0)
    except Exception as e:
        print(json.dumps({"errors": [str(e)]}))
        sys.exit(0)  # ✅ always exit 0 so Node doesn't reject
