"""Run every query in queries.cypher and print the results as tables.

Usage
    python run_queries.py                       # all queries
    python run_queries.py q1 q3                 # only names starting with q1 / q3
    python run_queries.py --min-customers 4 --window-days 7
Results are also written to output/<query>.csv.
"""
import argparse
import re
import time
from pathlib import Path

import pandas as pd

from common import ROOT, get_driver


def load_queries(path: Path) -> dict[str, str]:
    """Split the file on '// name: <id>' markers."""
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"^// name:\s*(\S+)\s*$", text, flags=re.M)
    return {parts[i]: parts[i + 1].strip().rstrip(";") for i in range(1, len(parts), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("only", nargs="*", help="run queries whose name starts with these prefixes")
    ap.add_argument("--min-customers", type=int, default=3)
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--device-id", help="device for q5_cluster_graph (default: top of q4)")
    args = ap.parse_args()

    queries = load_queries(ROOT / "queries.cypher")
    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    pd.set_option("display.width", 220, "display.max_columns", 20, "display.max_colwidth", 60)

    params = {"min_customers": args.min_customers, "window_days": args.window_days}
    with get_driver() as driver:
        print("Connected to Neo4j (verify_connectivity OK)\n")
        for name, cypher in queries.items():
            if args.only and not any(name.startswith(p) for p in args.only):
                continue
            if name == "q5_cluster_graph":
                if not params.get("device_id"):
                    continue
            t0 = time.time()
            records, _, _ = driver.execute_query(cypher, **params)
            elapsed = time.time() - t0
            if name == "q4_most_suspicious_cluster" and records:
                params["device_id"] = args.device_id or records[0]["device"]

            print(f"=== {name}  ({len(records)} rows, {elapsed:.2f}s)")
            if name == "q5_cluster_graph":
                nodes = {n.element_id for r in records for n in (r["d"], r["c"], r["m"]) if n}
                fraud = sum(1 for r in records if r["p"] and r["p"]["fraud_label"] == 1)
                print(f"device {params['device_id']}: {len(nodes)} nodes, "
                      f"{sum(1 for r in records if r['p'])} payments, {fraud} fraud\n")
                continue
            df = pd.DataFrame([r.data() for r in records])
            print(df.to_string(index=False) if len(df) else "(no rows)", "\n")
            df.to_csv(out_dir / f"{name}.csv", index=False)


if __name__ == "__main__":
    main()
