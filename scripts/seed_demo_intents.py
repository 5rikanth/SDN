"""Seed demo intents and clearly marked illustrative telemetry for the dashboard."""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from src.common import db
from src.common.events import Event, EventType, Severity

API_BASE = os.environ.get("TEAM16_INTENT_API_URL", "http://intent-api:8000").rstrip("/")
ROOT = Path(__file__).resolve().parents[1]
SEED_FILES = (
    ROOT / "intents" / "demo_010_v2x_safety.yaml",
    ROOT / "intents" / "demo_011_remote_surgery.yaml",
    ROOT / "intents" / "demo_012_public_safety_video.yaml",
    ROOT / "intents" / "demo_013_smart_grid.yaml",
)
DEMO_SAMPLE_COUNT = 20
DEMO_SAMPLE_INTERVAL_SECONDS = 1.0


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


def seed_demo_measurements(intent_id: str, sample_count: int = DEMO_SAMPLE_COUNT) -> bool:
    """Write illustrative telemetry only when no sample exists for this intent.

    The sample interval is recorded in the audit payload so the dashboard can
    distinguish these sample rows from subsequent real M5 collector readings.
    No simulated values are used if the intent already has measurements.
    """
    conn = db.connect()
    try:
        db.init_db(conn)
        intent = db.load_intent(conn, intent_id)
        plan = db.current_path(conn, intent_id)
        if intent is None or plan is None:
            print(f"[telemetry] {intent_id}: no stored intent/path; skipping demo samples.", flush=True)
            return False

        count = int(
            conn.execute(
                "SELECT COUNT(*) FROM measurements WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()[0]
        )
        if count:
            print(
                f"[telemetry] {intent_id}: {count} existing sample(s); leaving them unchanged.",
                flush=True,
            )
            return False

        end_at = time.time()
        start_at = end_at - (sample_count - 1) * DEMO_SAMPLE_INTERVAL_SECONDS
        max_latency = intent.guarantee.max_latency_ms
        base_delay = float(plan.est_latency_ms or 1.0)
        if max_latency is not None:
            base_delay = min(base_delay, float(max_latency) * 0.8)
        base_delay = max(0.1, base_delay)
        delay_variation = max(0.05, min(0.35, base_delay * 0.025))

        max_loss = intent.guarantee.max_loss_pct
        sample_loss = min(0.05, max(0.0, float(max_loss) * 0.15)) if max_loss is not None else 0.02
        min_bandwidth = intent.guarantee.min_bandwidth_mbps
        sample_throughput = None
        if min_bandwidth is not None:
            sample_throughput = min(
                float(min_bandwidth) * 1.15,
                float(plan.bottleneck_mbps) * 0.8,
            )

        previous_delay = None
        for index in range(sample_count):
            at = start_at + index * DEMO_SAMPLE_INTERVAL_SECONDS
            delay = base_delay + math.sin(index * 0.63) * delay_variation
            delay = max(0.1, delay)
            if max_latency is not None:
                delay = min(delay, float(max_latency) * 0.9)
            jitter = None if previous_delay is None else abs(delay - previous_delay)
            db.save_measurement(
                conn,
                intent_id,
                at,
                delay_ms=delay,
                jitter_ms=jitter,
                loss_pct=sample_loss,
                throughput_mbps=sample_throughput,
            )
            previous_delay = delay

        # This audit entry is deliberately explicit: these are visual demo
        # samples, not observations collected from Mininet/OVS.
        db.save_event(
            conn,
            Event(
                type=EventType.MEASUREMENT_TAKEN,
                intent_id=intent_id,
                severity=Severity.INFO,
                message=(
                    f"DEMO TELEMETRY (SIMULATED): added {sample_count} illustrative samples; "
                    "not live network measurements."
                ),
                payload={
                    "source": "demo-simulation",
                    "simulated": True,
                    "sample_start_at": start_at,
                    "sample_end_at": end_at,
                    "sample_count": sample_count,
                    "interval_seconds": DEMO_SAMPLE_INTERVAL_SECONDS,
                },
                at=end_at + 0.001,
            ),
        )
        print(
            f"[telemetry] {intent_id}: added {sample_count} SIMULATED demo samples "
            "(not live network readings).",
            flush=True,
        )
        return True
    finally:
        conn.close()


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
            + (f"; computed path {route}" if route else "")
            + (f"; reason {result.get('reasons')}" if result.get("reasons") else ""),
            flush=True,
        )

    # This also runs for previously seeded IDs so an existing database can get
    # the demo samples once after upgrading the seeder. Existing measurements
    # (including real collector data) are never overwritten or mixed with samples.
    for path in SEED_FILES:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        seed_demo_measurements(document["intent"]["id"])


if __name__ == "__main__":
    main()
