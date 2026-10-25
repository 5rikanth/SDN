"""
M1 REST API: POST /intent, DELETE /intent/{id}, GET /intents.

Provides HTTP interface for intent ingestion, path computation trigger,
intent state inspection, and withdrawal.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from src.common import db as dbm
from src.common.events import Event, EventBus, EventType
from src.common.models import IntentRecord, IntentState, to_dict
from src.admission.feasibility import assess
from src.intent_manager.validator import validate
from topology.topology_spec import build_graph

app = FastAPI(title="M1 Intent Manager API")

# Shared event bus for publishing lifecycle events
event_bus = EventBus()


def get_db():
    """Dependency that supplies a database connection per request."""
    conn = dbm.connect()
    try:
        yield conn
    finally:
        conn.close()


@app.post("/intent", status_code=status.HTTP_201_CREATED)
async def create_intent(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Ingest a new intent document as YAML (text/plain) or JSON.

    Validates schema and semantics. On success, persists intent, emits INTENT_RECEIVED,
    computes path, updates/saves path, and returns 201 with intent details and path/reasons.
    """
    content_type = request.headers.get("content-type", "")
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8")

    doc: Any = None
    if "application/json" in content_type:
        try:
            doc = await request.json()
        except Exception:
            try:
                doc = yaml.safe_load(body_str)
            except Exception:
                doc = body_str
    else:
        # Default to yaml parsing (covers text/plain, text/yaml, application/x-yaml, etc.)
        try:
            doc = yaml.safe_load(body_str)
        except Exception as exc:
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=[{"path": "(root)", "message": f"Malformed YAML: {exc}"}],
            )

    result = validate(doc)
    if not result.ok:
        error_payload = [{"path": e.path, "message": e.message} for e in result.errors]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload,
        )

    # Document is valid: construct IntentRecord
    # result.intent is the validated dict
    rec = IntentRecord.from_document({"intent": result.intent})
    rec.state = IntentState.PENDING
    dbm.save_intent(conn, rec)

    # Emit INTENT_RECEIVED event
    event_bus.publish(
        Event(
            type=EventType.INTENT_RECEIVED,
            intent_id=rec.id,
            message=f"Intent {rec.id} received from tenant {rec.tenant}",
            payload={"intent_id": rec.id, "tenant": rec.tenant, "priority": rec.priority},
        )
    )

    # M2 admission uses the persisted active paths to account for reservations.
    graph = build_graph()
    decision, plan = assess(conn, rec, graph)
    rec.state = IntentState.ADMITTED if decision.admitted else IntentState.REJECTED
    dbm.save_intent(conn, rec)
    decision_event = Event(
        type=EventType.ADMISSION_DECIDED,
        intent_id=rec.id,
        message=decision.reason,
        payload={
            "verdict": decision.verdict.value,
            "reason": decision.reason,
            "preempted_intent_id": decision.preempted_intent_id,
        },
    )
    dbm.save_event(conn, decision_event)
    event_bus.publish(decision_event)

    if decision.verdict.value == "preempt" and decision.preempted_intent_id:
        victim = dbm.load_intent(conn, decision.preempted_intent_id)
        if victim is not None:
            victim.state = IntentState.PREEMPTED
            dbm.save_intent(conn, victim)
            preempted_event = Event(
                type=EventType.INTENT_PREEMPTED,
                intent_id=victim.id,
                message=(f"Intent {victim.id} was preempted by higher-priority "
                         f"intent {rec.id}: {decision.reason}"),
                payload={"by_intent_id": rec.id, "reason": decision.reason},
            )
            dbm.save_event(conn, preempted_event)
            event_bus.publish(preempted_event)

    response_data: Dict[str, Any] = {
        "id": rec.id,
        "tenant": rec.tenant,
        "priority": rec.priority,
        "state": rec.state.value,
    }
    if decision.preempted_intent_id:
        response_data["preempted_intent_id"] = decision.preempted_intent_id

    if plan is not None:
        dbm.save_path(conn, plan)
        rec.current_path = plan
        response_data["path"] = list(plan.links)
        response_data["switches"] = list(plan.switches)
        response_data["est_latency_ms"] = plan.est_latency_ms
        response_data["bottleneck_mbps"] = plan.bottleneck_mbps
        event_bus.publish(
            Event(
                type=EventType.PATH_COMPUTED,
                intent_id=rec.id,
                message=f"Path computed for {rec.id}: {' -> '.join(plan.switches)}",
                payload={"links": list(plan.links), "switches": list(plan.switches)},
            )
        )
    else:
        response_data["reasons"] = {"admission": decision.reason}
        event_bus.publish(
            Event(
                type=EventType.PATH_INFEASIBLE,
                intent_id=rec.id,
                message=f"No feasible path for {rec.id}",
                payload={"reasons": response_data["reasons"]},
            )
        )

    return JSONResponse(status_code=status.HTTP_201_CREATED, content=response_data)


@app.get("/intents")
def list_intents(conn: sqlite3.Connection = Depends(get_db)) -> List[Dict[str, Any]]:
    """Return all intents, newest first."""
    rows = conn.execute("SELECT * FROM intents ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        rec = dbm._row_to_intent(r)
        plan = dbm.current_path(conn, rec.id)
        d = to_dict(rec)
        d["current_path"] = to_dict(plan) if plan else None
        out.append(d)
    return out


@app.get("/intent/{intent_id}")
def get_intent(intent_id: str, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """Retrieve one intent and its current path. Returns 404 if unknown."""
    rec = dbm.load_intent(conn, intent_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Intent {intent_id} not found")
    plan = dbm.current_path(conn, intent_id)
    d = to_dict(rec)
    d["current_path"] = to_dict(plan) if plan else None
    return d


@app.delete("/intent/{intent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_intent(intent_id: str, conn: sqlite3.Connection = Depends(get_db)):
    """Withdraw an intent. Sets state to WITHDRAWN and emits INTENT_WITHDRAWN."""
    rec = dbm.load_intent(conn, intent_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Intent {intent_id} not found")

    rec.state = IntentState.WITHDRAWN
    dbm.save_intent(conn, rec)

    event_bus.publish(
        Event(
            type=EventType.INTENT_WITHDRAWN,
            intent_id=intent_id,
            message=f"Intent {intent_id} withdrawn",
            payload={"intent_id": intent_id},
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/health")
def health(conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """Health check returning intent count."""
    row = conn.execute("SELECT COUNT(*) AS count FROM intents").fetchone()
    count = int(row["count"]) if row else 0
    return {"status": "ok", "intents": count}
