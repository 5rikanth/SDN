"""Dashboard data, custom intent builder, and audit API contracts."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from src.common import db
from src.common.events import Event, EventType, Severity
from src.common.models import Guarantee, IntentRecord, IntentState, Match, PathPlan
from src.dashboard import app as dashboard_app


@pytest.fixture()
def dashboard_db(tmp_path, monkeypatch):
    path = tmp_path / "dashboard.db"
    real_connect = db.connect
    conn = real_connect(path)
    db.init_db(conn)
    intent = IntentRecord(
        id="INT-DASH-1",
        tenant="dashboard-test",
        priority=2,
        match=Match("10.0.0.1/32", "10.0.0.2/32", "udp", 5001),
        guarantee=Guarantee(min_bandwidth_mbps=10, max_latency_ms=20),
        state=IntentState.ACTIVE,
    )
    db.save_intent(conn, intent)
    db.save_path(conn, PathPlan(
        intent_id=intent.id,
        switches=("s1", "s2", "s7"),
        links=PathPlan.links_for(["s1", "s2", "s7"]),
        est_latency_ms=8,
        bottleneck_mbps=100,
        version=1,
    ))
    db.save_measurement(conn, intent.id, 100.0, 9.5, 1.0, 0.0, 4.0)
    db.save_event(conn, Event(
        type=EventType.PATH_INSTALLED,
        intent_id=intent.id,
        severity=Severity.INFO,
        message="INT-DASH-1: installed s1 -> s2 -> s7",
        payload={"mode": "two-phase"},
        at=101.0,
    ))
    conn.close()
    monkeypatch.setattr(db, "connect", lambda path_arg=None: real_connect(path_arg or path))
    return intent.id


def test_dashboard_overview_lists_path_measurement_and_events(dashboard_db):
    overview = dashboard_app.dashboard_data()
    assert len(overview["intents"]) == 1
    row = overview["intents"][0]
    assert row["id"] == dashboard_db
    assert row["state"] == "active"
    assert row["path"]["switches"] == ["s1", "s2", "s7"]
    assert row["latest_measurement"]["delay_ms"] == 9.5
    assert overview["events"][0]["type"] == "path.installed"


def test_dashboard_intent_detail_limits_and_orders_measurements(dashboard_db):
    detail = dashboard_app.intent_detail(dashboard_db)
    assert detail["intent"]["id"] == dashboard_db
    assert detail["path"]["version"] == 1
    assert detail["measurements"][-1]["throughput_mbps"] == 4.0
    assert detail["reroutes"] == 0
    assert detail["events"][0]["payload"] == {"mode": "two-phase"}


def test_dashboard_unknown_intent_returns_not_found(dashboard_db):
    with pytest.raises(HTTPException) as error:
        dashboard_app.intent_detail("INT-MISSING")
    assert error.value.status_code == 404


def test_dashboard_html_has_custom_intent_builder():
    page = dashboard_app.dashboard()
    assert "Intent SDN SLA" in page
    assert "/api/dashboard" in page
    assert "Create your own application intent" in page
    assert "/api/submit-intent" in page
    assert "Minimum bandwidth (Mbps)" in page
    assert "Download YAML" in page


def test_submit_intent_forwards_custom_document_to_m1(monkeypatch):
    monkeypatch.setenv("TEAM16_INTENT_API_URL", "http://intent-api:8000/")
    captured = {}
    response_body = b'{"id":"INT-002","state":"admitted","switches":["s1","s2"]}'

    class FakeResponse:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return response_body

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["document"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(dashboard_app.urllib.request, "urlopen", fake_urlopen)
    document = {"intent": {"id": "INT-002", "tenant": "custom-telemetry"}}

    result = dashboard_app.submit_intent(document)

    assert result == {
        "http_status": 201,
        "intent_result": {"id": "INT-002", "state": "admitted", "switches": ["s1", "s2"]},
    }
    assert captured["url"] == "http://intent-api:8000/intent"
    assert captured["method"] == "POST"
    assert captured["document"] == document
    assert captured["timeout"] == 12
