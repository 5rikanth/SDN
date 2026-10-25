"""ST-9 admission preemption and installed-rule cleanup regressions."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from src.admission.feasibility import assess
from src.common import db as dbm
from src.common.events import EventType
from src.common.models import (
    Guarantee, IntentRecord, IntentState, Match, PathPlan, Verdict,
)
from src.enforcement import controller as controller_mod
from src.intent_manager import api as api_mod
from topology.topology_spec import build_graph


@pytest.fixture
def conn():
    connection = dbm.connect(":memory:")
    dbm.init_db(connection)
    yield connection
    connection.close()


def make_intent(identifier, priority, bandwidth):
    return IntentRecord(
        id=identifier, tenant="telemedicine-video", priority=priority,
        match=Match(src="10.0.0.1/32", dst="10.0.0.2/32", proto="udp"),
        guarantee=Guarantee(min_bandwidth_mbps=bandwidth),
    )


def reserve_north(conn, identifier="INT-901", priority=5, bandwidth=80):
    victim = make_intent(identifier, priority, bandwidth)
    victim.state = IntentState.ACTIVE
    dbm.save_intent(conn, victim)
    dbm.save_path(conn, PathPlan(
        identifier, ("s1", "s2", "s3", "s7"),
        ("s1-s2", "s2-s3", "s3-s7"), 6, 100,
    ))
    return victim


def test_higher_priority_request_can_preempt_one_lower_priority_reservation(conn):
    """Without priority-aware eviction, a critical intent is rejected despite reclaimable capacity."""
    victim = reserve_north(conn)
    request = make_intent("INT-902", priority=1, bandwidth=60)

    decision, plan = assess(conn, request, build_graph())

    assert decision.verdict is Verdict.PREEMPT
    assert decision.preempted_intent_id == victim.id
    assert plan is not None and plan.switches == ("s1", "s2", "s3", "s7")


def test_equal_or_higher_priority_intents_are_never_preempted(conn):
    """A requester must not evict an equal or more important admitted service."""
    reserve_north(conn, priority=1)
    request = make_intent("INT-903", priority=1, bandwidth=60)

    decision, plan = assess(conn, request, build_graph())

    assert decision.verdict is Verdict.REJECT
    assert decision.preempted_intent_id is None
    assert plan is None


def test_api_persists_preempted_state_and_causal_event(conn):
    """Persisting PREEMPTED without an event hides why the victim lost service."""
    victim = reserve_north(conn)
    body = """intent:
  id: INT-904
  tenant: telemedicine-video
  priority: 1
  match:
    src: 10.0.0.1/32
    dst: 10.0.0.2/32
    proto: udp
  guarantee:
    min_bandwidth_mbps: 60
"""

    async def receive():
        return {"type": "http.request", "body": body.encode(), "more_body": False}

    request = Request(
        {"type": "http", "method": "POST", "path": "/intent",
         "headers": [(b"content-type", b"text/plain")]},
        receive,
    )
    response = asyncio.run(api_mod.create_intent(request, conn))

    assert response.status_code == 201
    assert json.loads(response.body)["state"] == "admitted"
    assert dbm.load_intent(conn, victim.id).state is IntentState.PREEMPTED
    decision_event = next(e for e in dbm.events_for(conn, "INT-904")
                          if e.type is EventType.ADMISSION_DECIDED)
    assert decision_event.payload["preempted_intent_id"] == victim.id
    event = next(e for e in dbm.events_for(conn, victim.id)
                 if e.type is EventType.INTENT_PREEMPTED)
    assert event.payload["by_intent_id"] == "INT-904"
    assert "higher-priority" in event.message


def test_controller_removes_rules_for_preempted_intent(tmp_path, monkeypatch):
    """Leaving a preempted cookie installed would keep forwarding traffic after capacity was reassigned."""
    db_path = tmp_path / "state.db"
    real_connect = dbm.connect
    conn = real_connect(db_path)
    dbm.init_db(conn)
    victim = reserve_north(conn)
    victim.state = IntentState.PREEMPTED
    dbm.save_intent(conn, victim)
    conn.close()

    instance = object.__new__(controller_mod.Team16Controller)
    instance.datapaths = {}
    instance._installed_plans = {victim.id: (None, 123.0)}
    instance.logger = SimpleNamespace(
        info=lambda *args: None,
        error=lambda *args: None,
        exception=lambda *args: None,
    )
    removed = []
    monkeypatch.setattr(controller_mod.dbm, "connect", lambda: real_connect(db_path))
    monkeypatch.setattr(
        instance, "remove_intent", lambda intent_id: removed.append(intent_id) or 1
    )

    instance._sync_intents_from_db()

    assert removed == [victim.id]
    assert victim.id not in instance._installed_plans
