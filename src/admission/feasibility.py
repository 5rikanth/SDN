"""M2's explainable admission feasibility check (preemption is a later stage)."""

from __future__ import annotations

import sqlite3
from typing import Optional, Tuple

import networkx as nx

from src.admission.residual import residual_graph, tenant_paths
from src.common import db as dbm
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

    # Priority 1 is highest. A lower-priority active intent may be displaced
    # only when removing its reservation alone makes the request feasible.
    # This keeps the decision explainable and aligns with the existing
    # AdmissionDecision interface, which records one preempted intent.
    victims = sorted(
        (candidate for candidate in dbm.active_intents(conn)
         if candidate.priority > intent.priority),
        key=lambda candidate: (-candidate.priority, -candidate.created_at, candidate.id),
    )
    for victim in victims:
        candidate_graph = residual_graph(
            conn, graph, exclude_intent_ids={victim.id}
        )
        candidate_plan, _ = compute_path(
            intent,
            candidate_graph,
            tenant_paths(conn, exclude_intent_ids={victim.id}),
        )
        if candidate_plan is not None:
            return AdmissionDecision(
                intent.id,
                Verdict.PREEMPT,
                f"Admitted by preempting lower-priority intent {victim.id} "
                f"(priority {victim.priority}) to free residual capacity.",
                preempted_intent_id=victim.id,
            ), candidate_plan
    detail = "; ".join(f"{path}: {reason}" for path, reason in sorted(reasons.items()))
    return AdmissionDecision(
        intent.id, Verdict.REJECT,
        f"Rejected: no path satisfies the requested guarantees. {detail}".strip(),
    ), None
