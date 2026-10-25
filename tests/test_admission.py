"""ST-8 residual capacity and explainable admission decisions."""

from __future__ import annotations

import pytest

from src.admission.feasibility import assess
from src.admission.residual import residual_graph
from src.common import db as dbm
from src.common.events import EventType
from src.common.models import (
    Guarantee, IntentRecord, IntentState, Match, PathPlan, Verdict,
)
from topology.topology_spec import build_graph


@pytest.fixture
def conn():
    connection = dbm.connect(":memory:")
    dbm.init_db(connection)
    yield connection
    connection.close()


def make_intent(identifier, tenant, source, destination, bandwidth):
    return IntentRecord(
        id=identifier, tenant=tenant, priority=2,
        match=Match(src=source, dst=destination, proto="udp"),
        guarantee=Guarantee(min_bandwidth_mbps=bandwidth),
    )


def test_residual_graph_accounts_for_active_current_paths(conn):
    active = make_intent("A", "tenant-a", "10.0.0.1/32", "10.0.0.2/32", 80)
    active.state = IntentState.ACTIVE
    dbm.save_intent(conn, active)
    dbm.save_path(conn, PathPlan("A", ("s1", "s2", "s3", "s7"),
                                 ("s1-s2", "s2-s3", "s3-s7"), 6, 100))

    graph = residual_graph(conn, build_graph())
    assert graph["s1"]["s2"]["reserved_mbps"] == 80
    assert graph["s1"]["s2"]["residual_mbps"] == 20
    assert graph["s1"]["s2"]["bw_mbps"] == 100


def test_feasibility_selects_alternative_with_enough_residual_capacity(conn):
    active = make_intent("A", "tenant-a", "10.0.0.1/32", "10.0.0.2/32", 80)
    active.state = IntentState.ACTIVE
    dbm.save_intent(conn, active)
    dbm.save_path(conn, PathPlan("A", ("s1", "s2", "s3", "s7"),
                                 ("s1-s2", "s2-s3", "s3-s7"), 6, 100))
    request = make_intent("B", "tenant-b", "10.0.0.1/32", "10.0.0.2/32", 30)

    decision, plan = assess(conn, request, build_graph())
    assert decision.verdict is Verdict.ADMIT
    assert plan is not None
    assert plan.switches == ("s1", "s4", "s5", "s7")


def test_infeasible_decision_has_a_reason(conn):
    request = make_intent("B", "tenant-b", "10.0.0.1/32", "10.0.0.2/32", 500)
    decision, plan = assess(conn, request, build_graph())
    assert decision.verdict is Verdict.REJECT
    assert plan is None
    assert "no path satisfies" in decision.reason
    assert "500" in decision.reason


def test_api_persists_admission_decision_event(conn):
    from fastapi.testclient import TestClient
    from src.intent_manager import api as api_mod

    api_mod.app.dependency_overrides[api_mod.get_db] = lambda: conn
    body = """intent:
  id: INT-801
  tenant: telemedicine-video
  priority: 1
  match:
    src: 10.0.0.1/32
    dst: 10.0.0.2/32
  guarantee:
    min_bandwidth_mbps: 20
"""
    try:
        response = TestClient(api_mod.app).post(
            "/intent", content=body, headers={"content-type": "text/plain"}
        )
    finally:
        api_mod.app.dependency_overrides.clear()

    assert response.status_code == 201, response.text
    assert response.json()["state"] == "admitted"
    events = dbm.events_for(conn, "INT-801")
    assert len(events) == 1
    assert events[0].type is EventType.ADMISSION_DECIDED
    assert events[0].payload["verdict"] == "admit"
