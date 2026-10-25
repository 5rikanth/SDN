"""M2's explainable admission feasibility check (preemption is a later stage)."""

from __future__ import annotations

import sqlite3
from typing import Optional, Tuple

import networkx as nx

from src.admission.residual import residual_graph, tenant_paths
from src.common.models import AdmissionDecision, IntentRecord, PathPlan, Verdict
from src.pathing.compute import compute_path


def assess(
    conn: sqlite3.Connection, intent: IntentRecord, graph: Optional[nx.Graph] = None
) -> Tuple[AdmissionDecision, Optional[PathPlan]]:
    """Return ADMIT plus a feasible plan, or REJECT with specific reasons."""
    available = residual_graph(conn, graph)
    plan, reasons = compute_path(intent, available, tenant_paths(conn))
    if plan is not None:
        return AdmissionDecision(
            intent.id, Verdict.ADMIT,
            f"Admitted: a feasible path is available with residual capacity for tenant {intent.tenant}.",
        ), plan
    detail = "; ".join(f"{path}: {reason}" for path, reason in sorted(reasons.items()))
    return AdmissionDecision(
        intent.id, Verdict.REJECT,
        f"Rejected: no path satisfies the requested guarantees. {detail}".strip(),
    ), None
