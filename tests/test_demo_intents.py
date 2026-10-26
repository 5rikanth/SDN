"""Check that the shipped real-world demo intents are valid and routeable."""

from __future__ import annotations

from pathlib import Path

from src.common.models import IntentRecord
from src.intent_manager.validator import validate_yaml
from src.pathing.compute import compute_path
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
