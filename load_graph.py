"""Load a SENTINEL transaction sample into Neo4j.

Graph model
    (:Customer)-[:USED_DEVICE {first_seen, last_seen, tx_count}]->(:Device)
    (:Customer)-[:PAID {transaction_id, amount, time, fraud_label, channel, device_id}]->(:Merchant)

Every write is a MERGE keyed on an id that has a uniqueness constraint, and all
edge properties are SET from values computed from the sample (never incremented),
so running the load twice leaves the graph exactly the same.

Usage
    python load_graph.py                 # source and size from .env
    python load_graph.py --source csv --sample 5000
    python load_graph.py --reset         # wipe the graph first
"""
import argparse
import glob
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from common import env, get_driver

BATCH = 1000

CONSTRAINTS = [
    "CREATE CONSTRAINT customer_id IF NOT EXISTS FOR (c:Customer) REQUIRE c.customer_id IS UNIQUE",
    "CREATE CONSTRAINT device_id IF NOT EXISTS FOR (d:Device) REQUIRE d.device_id IS UNIQUE",
    "CREATE CONSTRAINT merchant_id IF NOT EXISTS FOR (m:Merchant) REQUIRE m.merchant_id IS UNIQUE",
    # One PAID edge per transaction (relationship uniqueness, Neo4j 5.7+).
    "CREATE CONSTRAINT paid_transaction_id IF NOT EXISTS FOR ()-[p:PAID]-() REQUIRE p.transaction_id IS UNIQUE",
]
INDEXES = [
    "CREATE INDEX paid_fraud_label IF NOT EXISTS FOR ()-[p:PAID]-() ON (p.fraud_label)",
    "CREATE INDEX paid_device_id IF NOT EXISTS FOR ()-[p:PAID]-() ON (p.device_id)",
    "CREATE INDEX paid_time IF NOT EXISTS FOR ()-[p:PAID]-() ON (p.time)",
]


# --------------------------------------------------------------------------- #
# Sources. Both return the same shape:
#   tx:        list of dicts (transaction_id, customer_id, device_id, merchant_id,
#              amount, time, fraud_label, channel, category)
#   customers / devices / merchants: dict id -> properties
# --------------------------------------------------------------------------- #
def _city(loc) -> str | None:
    if isinstance(loc, str):
        try:
            loc = json.loads(loc)
        except ValueError:
            return None
    return loc.get("city") if isinstance(loc, dict) else None


def _tx_row(r: dict, fraud) -> dict:
    return {
        "transaction_id": r["transaction_id"],
        "customer_id": r["customer_id"],
        "device_id": r.get("device_id") or None,
        "merchant_id": r["merchant_id"],
        "amount": float(r["transaction_amount"]),
        "time": datetime.fromisoformat(str(r["transaction_time"]).replace("Z", "+00:00")),
        "fraud_label": int(fraud) if fraud is not None and fraud == fraud else 0,
        "channel": r["payment_channel"],
        "category": r["merchant_category"],
    }


class Supabase:
    def __init__(self):
        self.url = env("SUPABASE_URL").rstrip("/") + "/rest/v1"
        key = env("SUPABASE_KEY")
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    def get(self, table: str, params: dict) -> list[dict]:
        r = requests.get(f"{self.url}/{table}", headers=self.headers, params=params, timeout=60)
        r.raise_for_status()
        return r.json()

    def transactions(self, n: int) -> list[dict]:
        cols = ("transaction_id,customer_id,merchant_id,device_id,transaction_amount,"
                "payment_channel,merchant_category,transaction_time,labels(fraud_label)")
        rows, page = [], 1000  # PostgREST returns at most 1000 rows per request
        while len(rows) < n:
            chunk = self.get("transactions", {
                "select": cols,
                "order": "transaction_time.asc,transaction_id.asc",
                "limit": min(page, n - len(rows)),
                "offset": len(rows),
            })
            if not chunk:
                break
            for r in chunk:
                lab = r.get("labels")
                lab = lab[0] if isinstance(lab, list) and lab else lab
                rows.append(_tx_row(r, (lab or {}).get("fraud_label")))
            print(f"  fetched {len(rows):,}/{n:,} transactions", end="\r")
        print()
        return rows

    def lookup(self, table: str, key: str, cols: str, ids: set[str]) -> dict[str, dict]:
        out, ids = {}, sorted(ids)
        for i in range(0, len(ids), 250):
            part = ids[i:i + 250]
            for r in self.get(table, {"select": cols, key: f"in.({','.join(part)})"}):
                out[r[key]] = r
        return out


class CsvExport:
    """Reads the SENTINEL export (tables/<name>/<split>/part-*.csv.gz)."""

    def __init__(self):
        self.dir = Path(env("CSV_DIR"))

    def _parts(self, table: str, split: str) -> list[str]:
        return sorted(glob.glob(str(self.dir / table / split / "part-*.csv.gz")))

    def transactions(self, n: int) -> list[dict]:
        frames, total = [], 0
        for split in ("warmup", "train", "validation", "holdout"):
            for tx_path in self._parts("transactions", split):
                tx = pd.read_csv(tx_path)
                lab = pd.read_csv(tx_path.replace("transactions", "labels"),
                                  usecols=["transaction_id", "fraud_label"])
                frames.append(tx.merge(lab, on="transaction_id", how="left"))
                total += len(tx)
                if total >= n + 5000:  # one extra part so the time sort is exact at the edge
                    break
            if total >= n + 5000:
                break
        df = pd.concat(frames)
        df["_t"] = pd.to_datetime(df["transaction_time"], utc=True)
        df = df.sort_values(["_t", "transaction_id"]).head(n)
        df = df.astype(object).where(df.notna(), None)
        return [_tx_row(r, r["fraud_label"]) for r in df.to_dict("records")]

    def lookup(self, table: str, key: str, cols: str, ids: set[str]) -> dict[str, dict]:
        df = pd.concat(pd.read_csv(p) for p in self._parts(table, "all"))
        df = df[df[key].isin(ids)][cols.split(",")]
        return {r[key]: r for r in df.astype(object).where(df.notna(), None).to_dict("records")}


# --------------------------------------------------------------------------- #
# Neo4j writes
# --------------------------------------------------------------------------- #
MERGE_CUSTOMERS = """
UNWIND $rows AS r
MERGE (c:Customer {customer_id: r.customer_id})
SET c.age = r.customer_age, c.home_city = r.home_city, c.region = r.region
"""
MERGE_DEVICES = """
UNWIND $rows AS r
MERGE (d:Device {device_id: r.device_id})
SET d.kind = r.device_kind
"""
MERGE_MERCHANTS = """
UNWIND $rows AS r
MERGE (m:Merchant {merchant_id: r.merchant_id})
SET m.name = r.synthetic_name, m.category = r.merchant_category, m.city = r.city
"""
MERGE_PAID = """
UNWIND $rows AS r
MATCH (c:Customer {customer_id: r.customer_id})
MATCH (m:Merchant {merchant_id: r.merchant_id})
MERGE (c)-[p:PAID {transaction_id: r.transaction_id}]->(m)
SET p.amount = r.amount, p.time = r.time, p.fraud_label = r.fraud_label,
    p.channel = r.channel, p.device_id = r.device_id
"""
MERGE_USED_DEVICE = """
UNWIND $rows AS r
MATCH (c:Customer {customer_id: r.customer_id})
MATCH (d:Device {device_id: r.device_id})
MERGE (c)-[u:USED_DEVICE]->(d)
SET u.first_seen = r.first_seen, u.last_seen = r.last_seen, u.tx_count = r.tx_count
"""


def write(driver, query: str, rows: list[dict], label: str):
    for i in range(0, len(rows), BATCH):
        driver.execute_query(query, rows=rows[i:i + BATCH])
        print(f"  {label}: {min(i + BATCH, len(rows)):,}/{len(rows):,}", end="\r")
    print()


def setup_schema(driver):
    for stmt in CONSTRAINTS + INDEXES:
        try:
            driver.execute_query(stmt)
        except Exception as exc:  # e.g. relationship constraints on an older server
            print(f"  skipped: {stmt.split(' IF')[0]} ({exc.__class__.__name__})")
    driver.execute_query("CALL db.awaitIndexes(300)")


def counts(driver) -> dict:
    q = """
    CALL () { MATCH (n:Customer) RETURN count(n) AS customers }
    CALL () { MATCH (n:Device) RETURN count(n) AS devices }
    CALL () { MATCH (n:Merchant) RETURN count(n) AS merchants }
    CALL () { MATCH ()-[r:PAID]->() RETURN count(r) AS paid,
              count(DISTINCT r.transaction_id) AS distinct_paid_ids, sum(r.fraud_label) AS fraud_paid }
    CALL () { MATCH ()-[r:USED_DEVICE]->() RETURN count(r) AS used_device }
    RETURN customers, devices, merchants, paid, distinct_paid_ids, fraud_paid, used_device
    """
    return driver.execute_query(q).records[0].data()


def fmt(c: dict) -> str:
    return ", ".join(f"{k}={v:,}" for k, v in c.items())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["supabase", "csv"], default=env("SOURCE", "supabase"))
    ap.add_argument("--sample", type=int, default=int(env("SAMPLE_SIZE", "20000")))
    ap.add_argument("--reset", action="store_true", help="delete all nodes before loading")
    args = ap.parse_args()
    if args.sample < 5000:
        raise SystemExit("--sample must be at least 5000")

    t0 = time.time()
    src = Supabase() if args.source == "supabase" else CsvExport()
    print(f"Reading {args.sample:,} transactions from {args.source} ...")
    tx = src.transactions(args.sample)
    source_ids = {r["transaction_id"] for r in tx}
    print(f"Source sample: {len(tx):,} rows, {len(source_ids):,} distinct transaction_ids, "
          f"{sum(r['fraud_label'] for r in tx):,} fraud-labelled")
    if len(source_ids) < 5000:
        raise SystemExit("Fewer than 5,000 distinct transactions in the source sample")

    cust_ids = {r["customer_id"] for r in tx}
    dev_ids = {r["device_id"] for r in tx if r["device_id"]}
    merch_ids = {r["merchant_id"] for r in tx}
    print(f"Reading {len(cust_ids):,} customers, {len(dev_ids):,} devices, {len(merch_ids):,} merchants ...")
    customers = src.lookup("customers", "customer_id", "customer_id,customer_age,home_city,region", cust_ids)
    devices = src.lookup("devices", "device_id", "device_id,device_kind", dev_ids)
    merchants = src.lookup("merchants", "merchant_id",
                           "merchant_id,synthetic_name,merchant_category,merchant_location", merch_ids)
    for m in merchants.values():
        m["city"] = _city(m.pop("merchant_location", None))

    # Ids missing from the lookup tables still become nodes (just without attributes).
    cust_rows = [customers.get(i, {"customer_id": i}) for i in sorted(cust_ids)]
    dev_rows = [devices.get(i, {"device_id": i}) for i in sorted(dev_ids)]
    merch_rows = [merchants.get(i, {"merchant_id": i}) for i in sorted(merch_ids)]

    # One USED_DEVICE edge per (customer, device), with totals computed here so
    # a re-run SETs the same values instead of adding to them.
    usage = defaultdict(list)
    for r in tx:
        if r["device_id"]:
            usage[(r["customer_id"], r["device_id"])].append(r["time"])
    used_rows = [{"customer_id": c, "device_id": d, "first_seen": min(ts),
                  "last_seen": max(ts), "tx_count": len(ts)} for (c, d), ts in usage.items()]

    with get_driver() as driver:
        print("Connected to Neo4j (verify_connectivity OK)")
        print("Graph BEFORE load: " + fmt(counts(driver)))
        if args.reset:
            with driver.session() as s:  # IN TRANSACTIONS needs an auto-commit transaction
                s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS").consume()
            print("Graph wiped")
        setup_schema(driver)
        write(driver, MERGE_CUSTOMERS, cust_rows, "Customer")
        write(driver, MERGE_DEVICES, dev_rows, "Device")
        write(driver, MERGE_MERCHANTS, merch_rows, "Merchant")
        write(driver, MERGE_PAID, tx, "PAID")
        write(driver, MERGE_USED_DEVICE, used_rows, "USED_DEVICE")
        c = counts(driver)

    span = f"{min(r['time'] for r in tx):%Y-%m-%d} .. {max(r['time'] for r in tx):%Y-%m-%d}"
    print(f"\nLoaded {len(tx):,} transactions ({span}) in {time.time() - t0:.0f}s")
    print("Graph AFTER load:  " + fmt(c))
    ok = c["paid"] == c["distinct_paid_ids"] >= len(source_ids)
    print(f"Check: {c['paid']:,} PAID edges for {c['distinct_paid_ids']:,} distinct transaction_ids "
          f"(source sample {len(source_ids):,}) -> {'no duplicates' if ok else 'MISMATCH'}")


if __name__ == "__main__":
    main()
