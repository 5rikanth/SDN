"""
Live demo of everything working as of ST-4.

    python demo_st4.py

No Docker, no Mininet, no root. Shows the intent lifecycle from YAML text to a
constraint-satisfying path, including a rejection with its stated reason.
"""

from __future__ import annotations

import time

from src.common import db as dbm
from src.common.models import IntentRecord
from src.intent_manager.validator import validate_yaml
from src.pathing.compute import compute_path
from src.pathing.yen_ksp import k_shortest_paths
from topology.topology_spec import LINKS, build_graph

RULE = "=" * 78


def show(title):
    print("\n" + RULE + "\n" + title + "\n" + RULE)


URLLC = """
intent:
  id: INT-001
  tenant: telemedicine-video
  priority: 1
  match: {src: 10.0.0.1/32, dst: 10.0.0.2/32, proto: udp, dport: 5001}
  guarantee: {min_bandwidth_mbps: 20, max_latency_ms: 20, max_loss_pct: 0.5}
"""

GREEDY = """
intent:
  id: INT-002
  tenant: bulk-backup
  priority: 4
  match: {src: 10.0.0.7/32, dst: 10.0.0.8/32, proto: tcp, dport: 873}
  guarantee: {min_bandwidth_mbps: 500, max_latency_ms: 5}
"""

BROKEN = """
intent:
  id: INT-003
  tenant: rogue
  priority: 9
  match: {src: 10.0.0.1/32, dst: 10.0.0.2/32}
  guarantee: {max_jitter_ms: 5}
"""


def main():
    graph = build_graph()

    show("1. THE NETWORK")
    print(f"{len(graph.nodes)} switches, {len(LINKS)} links\n")
    for link in LINKS:
        print(f"   {link.name:8s} {link.bw_mbps:6.0f} Mbps   {link.delay_ms:5.1f} ms")

    print("\n   Routes from s1 to s7 (where h1 and h2 live):")
    for path in k_shortest_paths(graph, "s1", "s7", k=3):
        delay = sum(graph[a][b]["delay_ms"] for a, b in zip(path, path[1:]))
        bw = min(graph[a][b]["bw_mbps"] for a, b in zip(path, path[1:]))
        print(f"   {' -> '.join(path):22s} {bw:6.0f} Mbps   {delay:5.1f} ms")

    show("2. A VALID INTENT IS ACCEPTED AND COMPILED TO A PATH")
    print(URLLC.strip())
    result = validate_yaml(URLLC)
    print(f"\n   validation : {'accepted' if result.ok else 'rejected'}")

    rec = IntentRecord.from_document({"intent": result.intent})
    start = time.perf_counter()
    plan, reasons = compute_path(rec, graph)
    elapsed_ms = (time.perf_counter() - start) * 1000

    print(f"   promise    : {rec.guarantee.min_bandwidth_mbps} Mbps, "
          f"{rec.guarantee.max_latency_ms} ms, {rec.guarantee.max_loss_pct}% loss")
    print(f"   chosen path: {' -> '.join(plan.switches)}")
    print(f"   delivers   : {plan.bottleneck_mbps} Mbps, {plan.est_latency_ms} ms")
    print(f"   links used : {', '.join(plan.links)}")
    print(f"   computed in: {elapsed_ms:.1f} ms  (budget is 200 ms)")

    if reasons:
        print("\n   Routes rejected, and why:")
        for path, why in reasons.items():
            print(f"     {path:22s} {why}")

    show("3. AN IMPOSSIBLE INTENT IS REFUSED, WITH A REASON")
    print(GREEDY.strip())
    rec2 = IntentRecord.from_document({"intent": validate_yaml(GREEDY).intent})
    plan2, reasons2 = compute_path(rec2, graph)
    print(f"\n   path found : {plan2}")
    print("   why not    :")
    for path, why in reasons2.items():
        print(f"     {path:22s} {why}")
    print("\n   No silent failure. Every refusal explains itself.")

    show("4. THE INTENT GRAMMAR IS FROZEN")
    print(BROKEN.strip())
    bad = validate_yaml(BROKEN)
    print(f"\n   validation : {'accepted' if bad.ok else 'rejected'}")
    for err in bad.errors:
        print(f"     {err.path}\n       {err.message}")
    print("\n   priority 9 is outside the 1-5 scale, and max_jitter_ms is not one")
    print("   of the five frozen constraint types. Both are caught before the")
    print("   intent reaches the network.")

    show("5. STATE IS PERSISTED")
    conn = dbm.connect(":memory:")
    dbm.init_db(conn)
    dbm.save_intent(conn, rec)
    dbm.save_path(conn, plan)
    dbm.save_path(conn, plan)  # simulate a reroute
    print(f"   schema version : {dbm.schema_version(conn)}")
    print(f"   intents stored : {len(dbm.active_intents(conn))}")
    print(f"   current path   : {' -> '.join(dbm.current_path(conn, 'INT-001').switches)}")
    print(f"   reroute count  : {dbm.reroute_count(conn, 'INT-001')}  (metric E6)")
    conn.close()

    show("STATUS")
    print("   This walkthrough demonstrates the software-only ST-1 through ST-4 path.")
    print("   The current project status and live-network checks are documented in")
    print("   README.md and docs/HANDOVER.md.")


if __name__ == "__main__":
    main()
