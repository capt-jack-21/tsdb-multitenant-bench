#!/usr/bin/env python3
"""Rebuild plans_summary.csv from the saved EXPLAIN JSON files (the source of truth).

  python bench/summarize_plans.py [--results-dir results]

Files are named <tag>_t<tenants>_<variant>_<query>.json. Use this after changing the analysis
code, or to re-derive the summary without rerunning anything.
"""
import argparse
import csv
import json
import re
from pathlib import Path

import common as C

NAME = re.compile(r"^(?P<tag>.+)_t(?P<tenants>\d+)_(?P<variant>[A-Z])_(?P<query>q\d)\.json$")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results-dir", default=str(C.ROOT / "results"))
    a = p.parse_args()
    results = Path(a.results_dir)
    rows = []
    for f in sorted((results / "plans").glob("*.json")):
        m = NAME.match(f.name)
        if not m:
            continue
        info = C.analyze_plan(json.loads(f.read_text()))
        rows.append({"tag": m["tag"], "tenants": int(m["tenants"]), "variant": m["variant"],
                     "query": m["query"], **{k: (";".join(v) if isinstance(v, list) else v)
                                              for k, v in info.items()}})
    if not rows:
        print("no plan files found")
        return
    out = results / "plans_summary.csv"
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
