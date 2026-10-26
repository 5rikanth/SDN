"""Seed predefined real-world demo intents into M1 once, without UI input."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from src.common import db

API_BASE = os.environ.get("TEAM16_INTENT_API_URL", "http://intent-api:8000").rstrip("/")
ROOT = Path(__file__).resolve().parents[1]
SEED_FILES = (
    ROOT / "intents" / "demo_010_v2x_safety.yaml",
    ROOT / "intents" / "demo_011_remote_surgery.yaml",
    ROOT / "intents" / "demo_012_public_safety_video.yaml",
    ROOT / "intents" / "demo_013_smart_grid.yaml",
)


def request_json(method: str, path: str, document: dict[str, Any] | None = None) -> Any:
    """Send a JSON request to M1 and decode the response."""
    body = json.dumps(document).encode("utf-8") if document is not None else None
    request = urllib.request.Request(
        API_BASE + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = response.read()
            return json.loads(payload.decode("utf-8")) if payload else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"M1 returned HTTP {exc.code} for {path}: {detail}") from exc


def wait_for_api(timeout_seconds: int = 120) -> None:
    """Wait for M1 to accept requests before seeding."""
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            request_json("GET", "/health")
            return
        except Exception as exc:  # API may still be starting.
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"M1 did not become ready: {last_error}")


def main() -> None:
    # Make first-time startup safe when the local SQLite file has not yet been initialized.
    conn = db.connect()
    try:
        db.init_db(conn)
    finally:
        conn.close()

    wait_for_api()
    existing = {row["id"] for row in request_json("GET", "/intents")}
    for path in SEED_FILES:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        intent_id = document["intent"]["id"]
        if intent_id in existing:
            print(f"[seed] {intent_id} already exists; leaving its current state unchanged.", flush=True)
            continue
        result = request_json("POST", "/intent", document)
        existing.add(intent_id)
        route = " -> ".join(result.get("switches", []))
        print(
            f"[seed] {intent_id} ({document['intent']['tenant']}): "
            f"{result.get('state', 'unknown')}"
            + (f"; path {route}" if route else "")
            + (f"; reason {result.get('reasons')}" if result.get("reasons") else ""),
            flush=True,
        )


if __name__ == "__main__":
    main()
