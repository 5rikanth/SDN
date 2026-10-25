"""Regression coverage for persistence at the live telemetry boundary."""

from src.common import db
from src.common.events import sla_violated
from src.telemetry.runtime import persist_confirmed_event


def test_confirmed_violation_is_persisted_for_st7():
    """An in-process detector event must survive for ST-7 subscribers and audit."""
    conn = db.connect(":memory:")
    db.init_db(conn)
    event = sla_violated("INT-001", "delay_ms", 31.2, 20.0)

    persist_confirmed_event(conn, event)

    stored = db.events_for(conn, "INT-001")
    assert len(stored) == 1
    assert stored[0].id == event.id
    assert stored[0].type is event.type
    assert stored[0].payload == event.payload
    conn.close()


def test_no_event_does_not_write_an_event_row():
    """Healthy samples must not create fabricated event history."""
    conn = db.connect(":memory:")
    db.init_db(conn)

    persist_confirmed_event(conn, None)

    assert db.events_for(conn) == []
    conn.close()
