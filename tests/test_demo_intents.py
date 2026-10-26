"""Check that the shipped real-world demo intents are valid and routeable."""

from __future__ import annotations

from pathlib import Path

from src.common import db
from src.common.models import IntentRecord, IntentState
from src.intent_manager.validator import validate_yaml
from src.pathing.compute import compute_path
from scripts import seed_demo_intents
from topology.topology_spec import HOSTS, build_graph

ROOT = Path(__file__).resolve().parents[1]
SEED_FILES = (
    ROOT / "intents" / "demo_010_v2x_safety.yaml",
    ROOT / "intents" / "demo_011_remote_surgery.yaml",
    ROOT / "intents" / "demo_012_public_safety_video.yaml",
    ROOT / "intents" / "demo_013_smart_grid.yaml",
)


def test_demo_intents_are_valid_and_have_feasible_paths():
    graph = build_graph()
    seen_ids: set[str] = set()
    host_cidrs = {f"{host.ip}/32" for host in HOSTS}

    for path in SEED_FILES:
        result = validate_yaml(path.read_text(encoding="utf-8"))
        assert result.ok, f"{path.name}: {result.errors}"
        assert result.intent["id"] not in seen_ids
        seen_ids.add(result.intent["id"])
        assert result.intent["match"]["src"] in host_cidrs
        assert result.intent["match"]["dst"] in host_cidrs

        record = IntentRecord.from_document({"intent": result.intent})
        plan, reasons = compute_path(record, graph)
        assert plan is not None, f"{path.name} has no feasible path: {reasons}"


def test_demo_telemetry_is_labelled_and_seeded_only_once(tmp_path, monkeypatch):
    """Create labelled illustrative data once, without overwriting later samples."""
    db_path = tmp_path / "demo-telemetry.db"
    real_connect = db.connect
    conn = real_connect(db_path)
    db.init_db(conn)

    path = SEED_FILES[0]
    result = validate_yaml(path.read_text(encoding="utf-8"))
    assert result.ok, result.errors
    record = IntentRecord.from_document({"intent": result.intent})
    record.state = IntentState.ADMITTED
    plan, reasons = compute_path(record, build_graph())
    assert plan is not None, reasons
    db.save_intent(conn, record)
    db.save_path(conn, plan)
    conn.close()

    monkeypatch.setattr(db, "connect", lambda path_arg=None: real_connect(path_arg or db_path))

    assert seed_demo_intents.seed_demo_measurements("INT-010", sample_count=6) is True

    conn = real_connect(db_path)
    rows = conn.execute(
        "SELECT at, delay_ms, jitter_ms, loss_pct, throughput_mbps "
        "FROM measurements WHERE intent_id = ? ORDER BY at",
        ("INT-010",),
    ).fetchall()
    assert len(rows) == 6
    assert all(row["delay_ms"] is not None for row in rows)
    assert all(row["loss_pct"] is not None for row in rows)
    event_rows = conn.execute(
        "SELECT payload_json FROM events WHERE intent_id = ? AND type = ?",
        ("INT-010", "measurement.taken"),
    ).fetchall()
    assert len(event_rows) == 1
    import json
    marker = json.loads(event_rows[0]["payload_json"])
    assert marker["source"] == "demo-simulation"
    assert marker["simulated"] is True
    conn.close()

    assert seed_demo_intents.seed_demo_measurements("INT-010", sample_count=6) is False
    conn = real_connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM measurements WHERE intent_id = ?", ("INT-010",)
    ).fetchone()[0]
    conn.close()
    assert count == 6
