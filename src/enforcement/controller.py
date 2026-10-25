"""
M4 Ryu Enforcement Controller.

Ryu OpenFlow 1.3 application that installs table-miss flow rules, discovers
topology port mappings, installs flow rules for computed PathPlans, and configures
OVS QoS/HTB queues.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

try:
    from ryu.base import app_manager
    from ryu.controller import ofp_event
    from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from ryu.ofproto import ofproto_v1_3
    from ryu.lib import hub
    from ryu.topology import event as topo_event
    from ryu.topology.api import get_link, get_switch
    RYU_AVAILABLE = True
    BaseApp = app_manager.RyuApp
except ImportError:  # pragma: no cover
    RYU_AVAILABLE = False
    BaseApp = object
    CONFIG_DISPATCHER = "CONFIG_DISPATCHER"
    MAIN_DISPATCHER = "MAIN_DISPATCHER"
    ofproto_v1_3 = None
    ofp_event = None
    topo_event = None
    get_link = None
    get_switch = None
    hub = None

    def set_ev_cls(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator

from src.common import db as dbm
from src.common.audit import AuditLog
from src.common.events import Event, EventBus, EventType, Severity, path_installed
from src.common.models import IntentRecord, IntentState, PathPlan
from src.enforcement.flowmod import cookie_for_intent, install, remove, rules_for_path
from src.enforcement.queues import ensure_queue
from src.telemetry.loop import ControlLoop
from topology.topology_spec import HOSTS, SWITCHES, build_graph


class Team16Controller(BaseApp):
    """
    Ryu controller managing OpenFlow 1.3 switches for the Team16 5G transport topology.
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION if RYU_AVAILABLE else 0x04]

    def __init__(self, *args, **kwargs):
        if RYU_AVAILABLE:
            super().__init__(*args, **kwargs)
        self.logger = logging.getLogger("Team16Controller")
        self.datapaths: Dict[str, Any] = {}
        self.dpid_to_name: Dict[int, str] = {}
        self.name_to_dpid: Dict[str, int] = {}
        # (sw_a, sw_b) -> out_port or (sw_a, host_name) -> out_port
        self.port_map: Dict[Tuple[str, str], int] = {}
        self.event_bus: Optional[EventBus] = EventBus()
        # SQLite is the cross-process handoff between M1/M3 and this Ryu app.
        # The value records the path revision already installed for each intent.
        self._installed_plans: Dict[str, Tuple[Optional[int], float]] = {}
        self._event_conn = None
        self._audit_log = None
        self._control_loop = None
        self._seen_violation_ids = set()
        if RYU_AVAILABLE:
            self._event_conn = dbm.connect()
            dbm.init_db(self._event_conn)
            self._audit_log = AuditLog(self._event_conn)
            self.event_bus.subscribe(self._audit_log.record)
            self._control_loop = ControlLoop(
                self._event_conn, build_graph(), self, self.event_bus, self._audit_log
            )
        self._sync_thread = hub.spawn(self._enforcement_loop) if RYU_AVAILABLE else None

    def _enforcement_loop(self) -> None:
        """Install paths persisted by the API as their switches become ready."""
        while True:
            try:
                self._sync_intents_from_db()
            except Exception:
                self.logger.exception("Failed to synchronize persisted intents")
            hub.sleep(0.5)

    def _sync_intents_from_db(self) -> None:
        """Apply new paths and withdrawals found in the shared SQLite database."""
        conn = dbm.connect()
        try:
            dbm.init_db(conn)
            rows = conn.execute(
                "SELECT id, state FROM intents "
                "WHERE state IN ('pending', 'admitted', 'active', 'withdrawn', 'preempted') "
                "ORDER BY CASE state WHEN 'preempted' THEN 0 "
                "WHEN 'withdrawn' THEN 1 ELSE 2 END, id"
            ).fetchall()

            for row in rows:
                intent_id = str(row["id"])
                if row["state"] in (
                    IntentState.WITHDRAWN.value,
                    IntentState.PREEMPTED.value,
                ):
                    if intent_id in self._installed_plans:
                        self.remove_intent(intent_id)
                        self._installed_plans.pop(intent_id, None)
                    continue

                intent = dbm.load_intent(conn, intent_id)
                plan = dbm.current_path(conn, intent_id)
                if intent is None or plan is None:
                    continue

                # Never partially install a path while its switches are still connecting.
                if any(switch not in self.datapaths for switch in plan.switches):
                    continue

                revision = (plan.version, plan.computed_at)
                if self._installed_plans.get(intent_id) == revision:
                    continue

                if intent_id in self._installed_plans:
                    self.remove_intent(intent_id)

                installed = self.install_plan(plan, intent)
                expected = 2 * len(plan.switches)
                if installed != expected:
                    self.logger.error(
                        "Installed %s of %s expected rules for intent %s",
                        installed,
                        expected,
                        intent_id,
                    )
                    continue

                intent.state = IntentState.ACTIVE
                intent.updated_at = time.time()
                dbm.save_intent(conn, intent)
                self._installed_plans[intent_id] = revision
                self.logger.info(
                    "Activated intent %s with %s rules on path %s",
                    intent_id,
                    installed,
                    " -> ".join(plan.switches),
                )

            # M5 runs in a separate process. Its durable SQLite event rows are
            # the handoff into this controller process's in-memory event bus.
            self._dispatch_persisted_violations(conn)
        finally:
            conn.close()

    def _dispatch_persisted_violations(self, conn) -> None:
        """Publish each unresolved M5 violation once to this process's bus.

        Only the newest violation per intent is actionable. A later recovery or
        a causal ControlLoop audit outcome resolves an older row, including
        across controller restarts.
        """
        if getattr(self, "event_bus", None) is None:
            return
        if not hasattr(self, "_seen_violation_ids"):
            self._seen_violation_ids = set()
        resolution_types = (
            EventType.SLA_RESTORED.value,
            EventType.PATH_INSTALLED.value,
            EventType.PATH_INSTALL_FAILED.value,
            EventType.PATH_INFEASIBLE.value,
            EventType.VIOLATION_SUPPRESSED.value,
        )
        placeholders = ", ".join("?" for _ in resolution_types)
        rows = conn.execute(
            "SELECT rowid AS event_order, id, type, severity, intent_id, at, "
            "message, payload_json "
            "FROM events WHERE type = ? OR type IN (" + placeholders + ") "
            "ORDER BY rowid ASC",
            (EventType.SLA_VIOLATED.value,) + resolution_types,
        ).fetchall()
        events = [
            (
                Event(
                    id=str(row["id"]),
                    type=EventType(row["type"]),
                    severity=Severity(row["severity"]),
                    intent_id=row["intent_id"],
                    at=float(row["at"]),
                    message=row["message"],
                    payload=json.loads(row["payload_json"]),
                ),
                int(row["event_order"]),
            )
            for row in rows
        ]
        latest_violation = {}
        for event, event_order in events:
            if event.type is EventType.SLA_VIOLATED and event.intent_id:
                latest_violation[event.intent_id] = (event, event_order)

        causal_parts = {"measurement", "threshold", "decision", "action"}
        for intent_id, (violation, violation_order) in latest_violation.items():
            if violation.id in self._seen_violation_ids:
                continue
            current_path = dbm.current_path(conn, intent_id)
            if current_path is not None and current_path.computed_at > violation.at:
                # A crash may happen after the alternative path is committed but
                # before its causal audit row. The path revision is durable proof
                # that a later decision already changed this intent's route.
                continue
            resolved = any(
                other.intent_id == intent_id
                and other_order > violation_order
                and (
                    other.type is EventType.SLA_RESTORED
                    or causal_parts.issubset(other.payload)
                )
                for other, other_order in events
            )
            if resolved:
                continue
            self._seen_violation_ids.add(violation.id)
            self.event_bus.publish(violation)

    def _dpid_to_sw_name(self, dpid: int) -> str:
        """Convert integer DPID (e.g. 1, 7) to switch name ('s1', 's7')."""
        return f"s{dpid}"

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER) if RYU_AVAILABLE else lambda fn: fn
    def switch_features_handler(self, ev):
        """
        Install table-miss flow entry upon switch connection.

        Priority 0 with no match (matches all) and output to OFPP_CONTROLLER so unmatched
        packets are forwarded to the controller rather than silently dropped by OVS.
        """
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id
        sw_name = self._dpid_to_sw_name(dpid)

        self.datapaths[sw_name] = datapath
        self.dpid_to_name[dpid] = sw_name
        self.name_to_dpid[sw_name] = dpid

        # Install table-miss flow: match all, send to controller
        match = parser.OFPMatch()
        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER
            )
        ]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=0,
            match=match,
            instructions=inst,
        )
        datapath.send_msg(mod)
        self.logger.info("Installed table-miss flow entry on switch %s (dpid=%s)", sw_name, dpid)

        # Trigger topology / port discovery
        self._discover_ports(datapath)

    def _discover_ports(self, datapath: Any):
        """Discover port mappings using topology API or port descriptions."""
        if not RYU_AVAILABLE:
            return

        try:
            # Query switch links
            links = get_link(self, datapath.id) if get_link else []
            for link in links:
                src_sw = self._dpid_to_sw_name(link.src.dpid)
                dst_sw = self._dpid_to_sw_name(link.dst.dpid)
                self.port_map[(src_sw, dst_sw)] = link.src.port_no
                self.port_map[(dst_sw, src_sw)] = link.dst.port_no
        except Exception:
            pass

        # Build fallback / deterministic port map based on known host-switch attachments if not already present
        # In Team16Topo:
        # Switch interfaces are allocated in order:
        # First all hosts attached to that switch, then switch-to-switch links in order.
        # We also discover dynamically via packet_in or LLDP events when available.
        self._init_default_port_map()
        self.logger.info("Current port map: %s", self.port_map)

    def _init_default_port_map(self):
        """
        Initialize the deterministic port map according to Team16Topo interface allocations.

        In Team16Topo:
          Each switch adds host access links first, then switch-to-switch links in LINKS order.
          s1: h1 (eth1), s2 (eth2), s4 (eth3), s6 (eth4)
          s2: h5 (eth1), s1 (eth2), s3 (eth3)
          s3: h7 (eth1), s2 (eth2), s7 (eth3)
          s4: h3 (eth1), s1 (eth2), s5 (eth3)
          s5: h4 (eth1), s4 (eth2), s7 (eth3)
          s6: h6 (eth1), s1 (eth2), s7 (eth3)
          s7: h2 (eth1), h8 (eth2), s3 (eth3), s5 (eth4), s6 (eth5)
        """
        from topology.topology_spec import LINKS

        port_counts: Dict[str, int] = {sw: 0 for sw in SWITCHES}

        # 1. Host links
        for host in HOSTS:
            port_counts[host.switch] += 1
            self.port_map.setdefault((host.switch, host.name), port_counts[host.switch])

        # 2. Switch links
        for link in LINKS:
            port_counts[link.a] += 1
            self.port_map.setdefault((link.a, link.b), port_counts[link.a])
            port_counts[link.b] += 1
            self.port_map.setdefault((link.b, link.a), port_counts[link.b])

    def get_datapath(self, switch_name: str) -> Any:
        return self.datapaths.get(switch_name)

    def get_all_datapaths(self) -> list[Any]:
        return list(self.datapaths.values())

    def install_plan(
        self,
        plan: PathPlan,
        intent: IntentRecord,
        event_bus: Optional[EventBus] = None,
    ) -> int:
        """
        Install OpenFlow rules and configure QoS queues for a computed PathPlan.

        Generates flow rules using rules_for_path, installs them to datapaths,
        configures HTB queues if min_bandwidth_mbps is requested, and emits
        events.path_installed(...) on success.
        """
        self._init_default_port_map()

        # Configure HTB queues along the path if min_bandwidth_mbps is specified
        if intent.guarantee.min_bandwidth_mbps:
            min_bw = intent.guarantee.min_bandwidth_mbps
            switches = list(plan.switches)
            for idx, sw in enumerate(switches[:-1]):
                next_sw = switches[idx + 1]
                out_port_num = self.port_map.get((sw, next_sw))
                if out_port_num is not None:
                    # In OVS, port name is typically f"{sw}-eth{out_port_num}"
                    port_name = f"{sw}-eth{out_port_num}"
                    ensure_queue(switch=sw, port=port_name, min_mbps=min_bw)

        # Generate OpenFlow 1.3 rules
        rules = rules_for_path(plan=plan, intent=intent, port_map=self.port_map)
        installed_count = install(self, rules)

        # Keep the DB synchronizer's cache aligned when ST-7 invokes this method
        # directly from the event handler, outside _sync_intents_from_db.
        expected = 2 * len(plan.switches)
        if installed_count == expected and hasattr(self, "_installed_plans"):
            self._installed_plans[intent.id] = (plan.version, plan.computed_at)

        # Emit path_installed event
        bus = event_bus or self.event_bus
        if bus is not None:
            ev = path_installed(
                intent_id=intent.id,
                switches=list(plan.switches),
                mode="direct",
                rules=installed_count,
            )
            bus.publish(ev)

        return installed_count

    def remove_intent(
        self,
        intent_id: str,
        event_bus: Optional[EventBus] = None,
    ) -> int:
        """
        Remove all flow rules associated with an intent by its deterministic cookie.
        """
        cookie = cookie_for_intent(intent_id)
        return remove(self, cookie)
