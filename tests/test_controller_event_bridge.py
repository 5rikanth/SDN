"""Tests for delivery of persisted M5 events into the Ryu process."""

from types import SimpleNamespace

from src.common import db
from src.common.audit import AuditLog
from src.common.events import Event, EventBus, EventType, Severity, sla_restored, sla_violated
from src.common.models import Guarantee, IntentRecord, IntentState, Match, PathPlan
from src.enforcement import controller as controller_module
from src.enforcement.controller import Team16Controller
from src.telemetry.loop import ControlLoop
from topology.topology_spec import build_graph


def test_persisted_violation_is_dispatched_once():
    """A separate telemetry process must not strand violations outside Ryu."""
    conn = db.connect(":memory:")
    db.init_db(conn)
    source = sla_violated("INT-bridge", "delay_ms", 31, 20)
    db.save_event(conn, source)

    instance = object.__new__(Team16Controller)
    instance.event_bus = EventBus()
    instance._seen_violation_ids = set()
    delivered = []
    instance.event_bus.subscribe(delivered.append, source.type)

    instance._dispatch_persisted_violations(conn)
    instance._dispatch_persisted_violations(conn)

    assert delivered == [source]
    conn.close()


def test_resolved_historical_violation_is_not_replayed():
    """Restarting Ryu must not reroute for an SLA breach already restored."""
    conn = db.connect(":memory:")
    db.init_db(conn)
    violation = sla_violated("INT-resolved", "delay_ms", 31, 20)
    restored = sla_restored("INT-resolved", "delay_ms", 12)
    db.save_event(conn, violation)
    db.save_event(conn, restored)

    instance = object.__new__(Team16Controller)
    instance.event_bus = EventBus()
    instance._seen_violation_ids = set()
    delivered = []
    instance.event_bus.subscribe(delivered.append, EventType.SLA_VIOLATED)

    instance._dispatch_persisted_violations(conn)

    assert delivered == []
    conn.close()


def test_committed_later_path_prevents_replay_after_controller_restart():
    """A crash after path commit must not make restart move traffic a second time."""
    conn = db.connect(":memory:")
    db.init_db(conn)
    intent = IntentRecord(
        id="INT-after-crash", tenant="bridge", priority=1,
        match=Match("10.0.0.1/32", "10.0.0.2/32", "udp", 5001),
        guarantee=Guarantee(max_latency_ms=20), state=IntentState.ACTIVE,
    )
    db.save_intent(conn, intent)
    db.save_event(conn, Event(
        EventType.SLA_VIOLATED, "old breach", intent.id, Severity.WARNING,
        {"metric": "delay_ms", "observed": 30, "threshold": 20}, at=100,
    ))
    switches = ("s1", "s4", "s5", "s7")
    db.save_path(conn, PathPlan(
        intent.id, switches, PathPlan.links_for(list(switches)), 15, 50,
        computed_at=101,
    ))

    instance = object.__new__(Team16Controller)
    instance.event_bus = EventBus()
    instance._seen_violation_ids = set()
    delivered = []
    instance.event_bus.subscribe(delivered.append, EventType.SLA_VIOLATED)
    instance._dispatch_persisted_violations(conn)

    assert delivered == []
    conn.close()


def test_no_event_bus_makes_bridge_a_safe_noop():
    """Software-only controller tests may construct the app without Ryu state."""
    conn = db.connect(":memory:")
    db.init_db(conn)
    instance = object.__new__(Team16Controller)

    instance._dispatch_persisted_violations(conn)

    conn.close()


def test_persisted_violation_closes_loop_through_controller_bus():
    """A saved M5 event must reach ST-7 and persist the alternate route."""
    conn = db.connect(":memory:")
    db.init_db(conn)
    intent = IntentRecord(
        id="INT-bridge-loop", tenant="bridge", priority=1,
        match=Match("10.0.0.1/32", "10.0.0.2/32", "udp", 5001),
        guarantee=Guarantee(min_bandwidth_mbps=20, max_latency_ms=20),
        state=IntentState.ACTIVE, dwell_seconds=0,
    )
    north = ("s1", "s2", "s3", "s7")
    db.save_intent(conn, intent)
    db.save_path(conn, PathPlan(
        intent.id, north, PathPlan.links_for(list(north)), 6, 100
    ))

    class FakeController:
        def install_plan(self, plan, record, bus):
            return 2 * len(plan.switches)

    bus = EventBus()
    audit = AuditLog(conn)
    bus.subscribe(audit.record)
    ControlLoop(conn, build_graph(), FakeController(), bus, audit)
    controller = object.__new__(Team16Controller)
    controller.event_bus = bus
    controller._seen_violation_ids = set()
    db.save_event(conn, sla_violated(intent.id, "delay_ms", 30, 20))

    controller._dispatch_persisted_violations(conn)

    assert db.reroute_count(conn, intent.id) == 1
    assert db.current_path(conn, intent.id).switches != north
    conn.close()


def test_controller_sync_polls_event_table_and_closes_loop(tmp_path, monkeypatch):
    """The Ryu sync cycle must bridge M5's SQLite event into ST-7."""
    db_path = tmp_path / "state.db"
    event_conn = db.connect(db_path)
    db.init_db(event_conn)
    intent = IntentRecord(
        id="INT-sync-loop", tenant="bridge", priority=1,
        match=Match("10.0.0.1/32", "10.0.0.2/32", "udp", 5001),
        guarantee=Guarantee(min_bandwidth_mbps=20, max_latency_ms=20),
        state=IntentState.ACTIVE, dwell_seconds=0,
    )
    north = ("s1", "s2", "s3", "s7")
    plan = PathPlan(intent.id, north, PathPlan.links_for(list(north)), 6, 100)
    db.save_intent(event_conn, intent)
    db.save_path(event_conn, plan)
    db.save_event(event_conn, sla_violated(intent.id, "delay_ms", 30, 20))

    class FakeController:
        def install_plan(self, new_plan, record, bus):
            return 2 * len(new_plan.switches)

    bus = EventBus()
    audit = AuditLog(event_conn)
    bus.subscribe(audit.record)
    instance = object.__new__(Team16Controller)
    instance.event_bus = bus
    instance._seen_violation_ids = set()
    instance._installed_plans = {intent.id: (plan.version, plan.computed_at)}
    instance.datapaths = {}
    instance.logger = SimpleNamespace(info=lambda *args: None)
    ControlLoop(event_conn, build_graph(), instance, bus, audit)
    monkeypatch.setattr(instance, "install_plan", FakeController().install_plan)
    real_connect = db.connect
    monkeypatch.setattr(controller_module.dbm, "connect", lambda: real_connect(db_path))

    instance._sync_intents_from_db()

    assert db.reroute_count(event_conn, intent.id) == 1
    assert db.current_path(event_conn, intent.id).switches != north
    assert len(audit.history(intent.id)) >= 2
    event_conn.close()
