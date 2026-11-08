"""Draw one suspicious cluster (a shared device, its customers, the merchants they paid).

    python visualize_cluster.py                      # top device from q4
    python visualize_cluster.py --device D_9778c4c36d78e39f
Writes screenshots/cluster_<device>.png
"""
import argparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
import numpy as np  # noqa: E402

from common import ROOT, get_driver  # noqa: E402
from run_queries import load_queries  # noqa: E402

COLORS = {"Device": "#e8a33d", "Customer": "#4c8bd6", "Merchant": "#7a9a5a"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device")
    ap.add_argument("--min-customers", type=int, default=3)
    args = ap.parse_args()
    queries = load_queries(ROOT / "queries.cypher")

    with get_driver() as driver:
        device = args.device or driver.execute_query(
            queries["q4_most_suspicious_cluster"], min_customers=args.min_customers
        ).records[0]["device"]
        records = driver.execute_query(queries["q5_cluster_graph"], device_id=device).records

    g = nx.MultiDiGraph()
    for r in records:
        d, c, m, p = r["d"], r["c"], r["m"], r["p"]
        g.add_node(d["device_id"], kind="Device", label=f"{d['device_id']}\n({d['kind']})")
        g.add_node(c["customer_id"], kind="Customer", label=f"{c['customer_id']}\n{c['home_city']}, {c['age']}")
        g.add_edge(c["customer_id"], d["device_id"], key="used", fraud=0)
        if m is not None:
            g.add_node(m["merchant_id"], kind="Merchant", label=f"{m['merchant_id']}\n{m['category']}")
            g.add_edge(c["customer_id"], m["merchant_id"], key=p["transaction_id"], fraud=p["fraud_label"])

    # Collapse parallel PAID edges into one line per customer->merchant with a count.
    simple = nx.DiGraph()
    simple.add_nodes_from(g.nodes(data=True))
    for u, v, data in g.edges(data=True):
        e = simple.get_edge_data(u, v, {"n": 0, "fraud": 0})
        simple.add_edge(u, v, n=e["n"] + 1, fraud=e["fraud"] + data["fraud"])

    # Radial layout: device in the middle, customers on a ring, each merchant
    # pushed straight out from the customer(s) that paid it.
    customers = sorted(n for n, a in simple.nodes(data=True) if a["kind"] == "Customer")
    pos = {device: np.zeros(2)}
    for i, c in enumerate(customers):
        angle = 2 * np.pi * i / len(customers)
        pos[c] = np.array([np.cos(angle), np.sin(angle)])
    for n, a in simple.nodes(data=True):
        if a["kind"] == "Merchant":
            v = np.mean([pos[c] for c in simple.predecessors(n)], axis=0)
            pos[n] = v / (np.linalg.norm(v) or 1) * 2
    fig, ax = plt.subplots(figsize=(13, 11))
    for (u, v), a in simple.edges.items():
        used = v == device
        nx.draw_networkx_edges(
            simple, pos, edgelist=[(u, v)], ax=ax, arrows=True, arrowsize=12,
            edge_color="#999999" if used else ("#d62728" if a["fraud"] else "#5a8f3a"),
            width=1.5 if used else 1 + a["n"] * 0.4, style="dashed" if used else "solid")
        if not used:
            mid = (pos[u] + pos[v]) / 2
            txt = f"{a['n']} PAID" + (f"\n{a['fraud']} fraud" if a["fraud"] else "")
            ax.text(*mid, txt, fontsize=7, ha="center", color="#d62728" if a["fraud"] else "#333333",
                    bbox=dict(facecolor="white", edgecolor="none", alpha=0.8, pad=1))
    for kind, color in COLORS.items():
        nodes = [n for n, a in simple.nodes(data=True) if a["kind"] == kind]
        nx.draw_networkx_nodes(simple, pos, nodelist=nodes, node_color=color, ax=ax,
                               node_size=2600 if kind == "Device" else 1300, label=kind)
    nx.draw_networkx_labels(simple, pos, {n: a["label"] for n, a in simple.nodes(data=True)},
                            font_size=6.5, ax=ax)
    total = sum(a["n"] for (u, v), a in simple.edges.items() if v != device)
    fraud = sum(a["fraud"] for a in simple.edges.values())
    ax.set_title(f"Shared device {device}: {len(customers)} customers, {total} payments, {fraud} fraud-labelled"
                 "\n(dashed = USED_DEVICE, green = PAID, red = PAID with fraud_label = 1)", fontsize=11)
    ax.legend(loc="lower left", markerscale=0.4)
    ax.axis("off")
    out = ROOT / "screenshots" / f"cluster_{device}.png"
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
