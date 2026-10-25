"""Build a pathing graph annotated with capacity left by active guarantees."""

from __future__ import annotations

import sqlite3
from typing import Dict, Iterable, List, Optional

import networkx as nx

from src.common import db as dbm
from src.common.models import PathPlan
from topology.topology_spec import build_graph


def residual_graph(
    conn: sqlite3.Connection,
    graph: Optional[nx.Graph] = None,
    exclude_intent_id: Optional[str] = None,
    exclude_intent_ids: Optional[Iterable[str]] = None,
) -> nx.Graph:
    """Return a copy of topology with per-edge reserved and residual Mbps.

    Reservations are the active intents' minimum bandwidth guarantees on their
    current paths. Unspecified guarantees reserve no capacity. The physical
    ``bw_mbps`` field is retained for PathPlan reporting.
    """
    result = (graph if graph is not None else build_graph()).copy()
    excluded = set(exclude_intent_ids or ())
    if exclude_intent_id is not None:
        excluded.add(exclude_intent_id)
    reserved: Dict[str, float] = {PathPlan.links_for([u, v])[0]: 0.0 for u, v in result.edges}
    for rec in dbm.active_intents(conn):
        if rec.id in excluded:
            continue
        amount = float(rec.guarantee.min_bandwidth_mbps or 0.0)
        if amount <= 0:
            continue
        plan = dbm.current_path(conn, rec.id)
        if plan is None:
            continue
        for link in plan.links:
            if link in reserved:
                reserved[link] += amount

    for u, v, data in result.edges(data=True):
        link = PathPlan.links_for([u, v])[0]
        capacity = float(data.get("bw_mbps", 0.0))
        data["capacity_mbps"] = capacity
        data["reserved_mbps"] = reserved.get(link, 0.0)
        data["residual_mbps"] = max(0.0, capacity - data["reserved_mbps"])
    return result


def tenant_paths(
    conn: sqlite3.Connection, exclude_intent_ids: Optional[Iterable[str]] = None
) -> Dict[str, List[List[str]]]:
    """Current paths keyed by tenant for isolation checks."""
    result: Dict[str, List[List[str]]] = {}
    excluded = set(exclude_intent_ids or ())
    for rec in dbm.active_intents(conn):
        if rec.id in excluded:
            continue
        plan = dbm.current_path(conn, rec.id)
        if plan:
            result.setdefault(rec.tenant, []).append(list(plan.switches))
    return result
