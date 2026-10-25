"""One-hertz live telemetry process for an installed intent."""

from __future__ import annotations

import argparse
import re
import subprocess
import time

from src.common import db
from src.common.models import Measurement
from src.enforcement.flowmod import cookie_for_intent
from src.telemetry.detector import ViolationDetector
from src.telemetry.probes import ActiveProbeSource


def persist_confirmed_event(conn, event) -> None:
    """Persist one detector transition when a confirmed event was emitted."""
    if event is not None:
        db.save_event(conn, event)


def flow_bytes(switch: str, cookie: int) -> int:
    """Read byte counters for one intent cookie from one OpenFlow switch."""
    result = subprocess.run(
        ["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", switch],
        capture_output=True,
        text=True,
        check=True,
    )
    marker = f"cookie={hex(cookie)}"
    return sum(
        int(value)
        for line in result.stdout.splitlines()
        if marker in line
        for value in re.findall(r"n_bytes=(\d+)", line)
    )


def main() -> int:
    """Collect and persist a bounded sequence of live measurements."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--intent", required=True)
    parser.add_argument("--source-pid", required=True, type=int)
    parser.add_argument("--target", required=True)
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--switch", default="s1")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    conn = db.connect()
    db.init_db(conn)
    intent = db.load_intent(conn, args.intent)
    if intent is None:
        raise SystemExit(f"unknown intent: {args.intent}")
    detector = ViolationDetector(intent)
    probes = ActiveProbeSource()
    cookie = cookie_for_intent(args.intent)
    previous_bytes = None
    previous_at = None

    for _ in range(args.samples):
        started = time.time()
        probe_command = (
            "import socket,time;"
            "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(1);"
            "t=time.perf_counter();s.sendto(b'team16',(%r,%d));s.recvfrom(64);"
            "print((time.perf_counter()-t)*1000)"
        ) % (args.target, args.port)
        reply = subprocess.run(
            [
                "mnexec", "-da", str(args.source_pid),
                "python3.9", "-c", probe_command,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            delay = float(reply.stdout.strip()) if reply.returncode == 0 else None
        except ValueError:
            delay = None
        probe = probes.observation(
            args.intent, started, delay, 0.0 if delay is not None else 100.0
        )
        current_bytes = flow_bytes(args.switch, cookie)
        throughput = None
        if previous_bytes is not None and previous_at is not None and started > previous_at:
            throughput = (
                max(0, current_bytes - previous_bytes)
                * 8
                / (started - previous_at)
                / 1_000_000
            )
        sample = Measurement(
            args.intent,
            started,
            probe.delay_ms,
            probe.jitter_ms,
            probe.loss_pct,
            throughput,
        )
        db.save_measurement(
            conn,
            sample.intent_id,
            sample.at,
            sample.delay_ms,
            sample.jitter_ms,
            sample.loss_pct,
            sample.throughput_mbps,
        )
        event = detector.observe(sample, now=started)
        # The detector emits confirmed transitions in-process. Persist them
        # alongside measurements so ST-7 and the audit view can consume them.
        persist_confirmed_event(conn, event)
        event_name = event.type.value if event else "none"
        probe_error = ""
        if probe.delay_ms is None:
            detail = (reply.stdout + reply.stderr).strip().replace("\n", " | ")
            probe_error = f" probe_error={detail!r}"
        print(
            f"sample delay_ms={sample.delay_ms} jitter_ms={sample.jitter_ms} "
            f"loss_pct={sample.loss_pct} throughput_mbps={sample.throughput_mbps} "
            f"status={detector.status.value} event={event_name}{probe_error}"
        )
        previous_bytes, previous_at = current_bytes, started
        elapsed = time.time() - started
        if elapsed < args.interval:
            time.sleep(args.interval - elapsed)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
