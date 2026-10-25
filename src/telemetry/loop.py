"""Closed-loop response from confirmed SLA violations to path enforcement."""

from __future__ import annotations

from typing import Optional, Set

from src.common import db
from src.common.events import Event, EventBus, EventType, Severity
from src.common.models import Consistency, IntentState, OnViolation
from src.enforcement import two_phase
from src.pathing.compute import compute_path


class ControlLoop:
    """Recompute and install a safe alternative after a confirmed violation."""

    def __init__(self, conn, graph, controller, bus: EventBus, audit) -> None:
        self.conn = conn
        self.graph = graph
        self.controller = controller
        self.bus = bus
        self.audit = audit
        self._processed_event_ids: Set[str] = set()
        bus.subscribe(self.on_violation, EventType.SLA_VIOLATED)

    def _audit(self, event, decision, action, event_type, severity=Severity.INFO):
        metric = event.payload.get("metric")
        observed = event.payload.get("observed")
        threshold = event.payload.get("threshold")
        return self.audit.chain(
            event.intent_id,
            f"{metric}={observed}",
            f"{metric}={threshold}",
            decision,
            action,
            severity=severity,
            event_type=event_type,
        )

    def on_violation(self, event: Event, now: Optional[float] = None):
        """Handle one violation synchronously without letting failures escape."""
        if event.type is not EventType.SLA_VIOLATED or not event.intent_id:
            return None
        if event.id in self._processed_event_ids:
            return None

        intent = db.load_intent(self.conn, event.intent_id)
        if intent is None:
            return None
        current = db.current_path(self.conn, intent.id)
        timestamp = event.at if now is None else now

        required = {"metric", "observed", "threshold"}
        if not required.issubset(event.payload):
            self._processed_event_ids.add(event.id)
            return self._audit(
                event,
                "invalid violation event",
                "network unchanged",
                EventType.PATH_INSTALL_FAILED,
                Severity.ERROR,
            )

        if intent.on_violation is OnViolation.ALERT_ONLY:
            self._processed_event_ids.add(event.id)
            return self._audit(
                event, "alert-only policy", "operator alerted; network unchanged",
                EventType.VIOLATION_SUPPRESSED,
            )

        if intent.on_violation is OnViolation.DEGRADE:
            intent.state = IntentState.DEGRADED
            db.save_intent(self.conn, intent)
            self._processed_event_ids.add(event.id)
            return self._audit(
                event, "degrade policy", "intent marked degraded; path retained",
                EventType.VIOLATION_SUPPRESSED,
            )

        if not intent.may_replan_at(timestamp):
            self._processed_event_ids.add(event.id)
            return self._audit(
                event, "dwell timer active", "current path retained",
                EventType.VIOLATION_SUPPRESSED,
            )

        candidate_graph = self.graph.copy()
        if current is not None:
            candidate_graph.remove_edges_from(zip(current.switches, current.switches[1:]))
        new_plan, reasons = compute_path(intent, candidate_graph)
        if new_plan is None:
            self._processed_event_ids.add(event.id)
            detail = "; ".join(str(value) for value in reasons.values())
            return self._audit(
                event,
                "no feasible alternative",
                f"current path retained: {detail or 'no path-computation reason'}",
                EventType.PATH_INFEASIBLE,
                Severity.WARNING,
            )

        if intent.consistency is Consistency.STRICT:
            new_plan = two_phase.with_version(
                new_plan, two_phase.next_version(current)
            )

        try:
            installed = self.controller.install_plan(new_plan, intent, self.bus)
            expected = 2 * len(new_plan.switches)
            if installed != expected:
                raise RuntimeError(
                    f"installed {installed} of {expected} expected OpenFlow rules"
                )
        except Exception as exc:  # noqa: BLE001 - event subscribers must be isolated
            self._processed_event_ids.add(event.id)
            return self._audit(
                event,
                "alternative installation failed",
                f"current path retained: {exc}",
                EventType.PATH_INSTALL_FAILED,
                Severity.ERROR,
            )

        db.save_path(self.conn, new_plan)
        intent.current_path = new_plan
        intent.last_replan_at = timestamp
        intent.state = IntentState.ACTIVE
        db.save_intent(self.conn, intent)
        self._processed_event_ids.add(event.id)
        return self._audit(
            event,
            "feasible alternative selected",
            f"installed {' -> '.join(new_plan.switches)}",
            EventType.PATH_INSTALLED,
        )
