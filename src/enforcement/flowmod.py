"""
M4 OpenFlow 1.3 rule generation.

Translates high-level PathPlans and Intents into OpenFlow 1.3 flow modification
structures for Ryu controllers, ensuring deterministic cookie generation and
correct match specification (eth_type=0x0800 for IPv4).
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Tuple

try:
    from ryu.ofproto import ether, inet, ofproto_v1_3
    RYU_AVAILABLE = True
except ImportError:  # pragma: no cover
    RYU_AVAILABLE = False
    ether = None
    inet = None
    ofproto_v1_3 = None

from src.common.models import IntentRecord, PathPlan
from topology.topology_spec import HOSTS


def _resolve_ip_to_host_port(ip_str: str, switch: str, port_map: Dict[Tuple[str, str], int]) -> int:
    """Find the port on the access switch connected to the host matching ip_str."""
    raw_ip = ip_str.split("/")[0].strip()
    for host in HOSTS:
        if host.ip == raw_ip:
            # Never guess an access port: a wrong default silently redirects traffic.
            return port_map[(switch, host.name)]
    raise KeyError(f"no host found with IP {ip_str}")


def cookie_for_intent(intent_id: str) -> int:
    """
    Derive a 64-bit integer cookie deterministically from intent.id.

    We use the first 8 bytes of the SHA-256 hash of the intent ID string,
    masked to a 64-bit unsigned integer (0 to 2^64 - 1). This ensures that
    every intent has a unique, deterministic cookie that can be used to identify,
    track, and bulk-delete all flow rules installed for that intent across the network.
    """
    digest = hashlib.sha256(intent_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False) & ((1 << 48) - 1)


def cookie_for_version(intent_id: str, version: int) -> int:
    """Return a version-specific cookie for ingress classifiers."""
    if version not in (0, 1):
        raise ValueError("strict path versions must be 0 or 1")
    # Transit cookies use only the low 48 bits; reserve a separate namespace
    # so classifier deletes cannot match transit rules.
    return (1 << (62 + version)) | cookie_for_intent(intent_id)


def build_match(datapath: Any, intent: IntentRecord) -> Any:
    """
    Construct an OpenFlow 1.3 OFPMatch object from intent.match.

    Matches:
      - eth_type: 0x0800 (IPv4) - ALWAYS required for IP/L4 matching in OVS.
      - ipv4_src, ipv4_dst: from intent.match.src and intent.match.dst (IP or CIDR).
      - ip_proto: 6 (TCP), 17 (UDP), 1 (ICMP), or omitted if "any".
      - tcp_dst / udp_dst: optional port match if proto is tcp or udp and dport is set.
    """
    match_kwargs: Dict[str, Any] = {
        "eth_type": 0x0800,
    }

    # Parse src and dst IPs (support CIDR if provided)
    src_ip = intent.match.src.split("/")[0] if intent.match.src.endswith("/32") else intent.match.src
    dst_ip = intent.match.dst.split("/")[0] if intent.match.dst.endswith("/32") else intent.match.dst

    match_kwargs["ipv4_src"] = src_ip
    match_kwargs["ipv4_dst"] = dst_ip

    proto = intent.match.proto.lower()
    if proto == "tcp":
        match_kwargs["ip_proto"] = 6
        if intent.match.dport is not None:
            match_kwargs["tcp_dst"] = intent.match.dport
    elif proto == "udp":
        match_kwargs["ip_proto"] = 17
        if intent.match.dport is not None:
            match_kwargs["udp_dst"] = intent.match.dport
    elif proto == "icmp":
        match_kwargs["ip_proto"] = 1

    if not RYU_AVAILABLE or datapath is None:
        return match_kwargs

    return datapath.ofproto_parser.OFPMatch(**match_kwargs)


def rules_for_path(
    plan: PathPlan,
    intent: IntentRecord,
    port_map: Dict[Tuple[str, str], int],
    priority: int = 100,
) -> List[Dict[str, Any]]:
    """
    Generate the list of flow rule specifications for a given PathPlan.

    Computes bidirectional rules (forward along plan.switches, reverse back).
    For each switch on the path:
      - Forward rule: matches forward traffic, outputs to next switch (or destination host on egress).
      - Reverse rule: matches reverse traffic, outputs to previous switch (or source host on ingress).

    Returns plain dictionaries:
      {"switch": sw_name, "match": match_spec, "actions": actions_spec, "priority": priority, "cookie": cookie, "direction": "forward"|"reverse"}
    """
    cookie = cookie_for_intent(intent.id)
    rules: List[Dict[str, Any]] = []
    switches = list(plan.switches)
    num_switches = len(switches)

    if num_switches == 0:
        return rules

    # 1. Forward direction: src -> dst
    for idx, sw in enumerate(switches):
        if idx < num_switches - 1:
            next_hop = switches[idx + 1]
            out_port = port_map.get((sw, next_hop))
        else:
            # Egress switch: forward out to destination host port
            out_port = _resolve_ip_to_host_port(intent.match.dst, sw, port_map)

        forward_match = {
            "eth_type": 0x0800,
            "src": intent.match.src,
            "dst": intent.match.dst,
            "proto": intent.match.proto,
            "dport": intent.match.dport,
        }

        rules.append({
            "switch": sw,
            "match": forward_match,
            "actions": [{"type": "OUTPUT", "port": out_port}],
            "priority": priority,
            "cookie": cookie,
            "direction": "forward",
        })

    # 2. Reverse direction: dst -> src
    for idx, sw in enumerate(switches):
        if idx > 0:
            prev_hop = switches[idx - 1]
            out_port = port_map.get((sw, prev_hop))
        else:
            # Ingress switch for forward is egress for reverse: output to source host port
            out_port = _resolve_ip_to_host_port(intent.match.src, sw, port_map)

        # Reverse match swaps src and dst; if dport was specified for UDP/TCP, reverse matches sport or any
        reverse_match = {
            "eth_type": 0x0800,
            "src": intent.match.dst,
            "dst": intent.match.src,
            "proto": intent.match.proto,
            "dport": None,  # Reverse direction reply traffic
        }

        rules.append({
            "switch": sw,
            "match": reverse_match,
            "actions": [{"type": "OUTPUT", "port": out_port}],
            "priority": priority,
            "cookie": cookie,
            "direction": "reverse",
        })

    return rules


def install(controller: Any, rules: List[Dict[str, Any]]) -> int:
    """
    Install a list of flow rules via the given controller.

    Sends OFPFlowMod (ADD) messages to the respective datapaths.
    Returns the count of successfully installed rules.
    """
    if not rules:
        return 0

    installed_count = 0
    for rule in rules:
        sw_name = rule["switch"]
        datapath = None
        if hasattr(controller, "get_datapath"):
            datapath = controller.get_datapath(sw_name)
        elif hasattr(controller, "datapaths"):
            datapath = controller.datapaths.get(sw_name)

        if datapath is None and RYU_AVAILABLE:
            continue

        if RYU_AVAILABLE and datapath is not None:
            parser = datapath.ofproto_parser
            ofproto = datapath.ofproto

            match_dict = rule["match"]
            match_kwargs: Dict[str, Any] = {"eth_type": ether.ETH_TYPE_IP}
            src_ip = match_dict["src"].split("/")[0] if match_dict["src"].endswith("/32") else match_dict["src"]
            dst_ip = match_dict["dst"].split("/")[0] if match_dict["dst"].endswith("/32") else match_dict["dst"]
            match_kwargs["ipv4_src"] = src_ip
            match_kwargs["ipv4_dst"] = dst_ip

            proto = match_dict.get("proto", "any").lower()
            if proto == "tcp":
                match_kwargs["ip_proto"] = inet.IPPROTO_TCP
                if match_dict.get("dport") is not None:
                    match_kwargs["tcp_dst"] = match_dict["dport"]
            elif proto == "udp":
                match_kwargs["ip_proto"] = inet.IPPROTO_UDP
                if match_dict.get("dport") is not None:
                    match_kwargs["udp_dst"] = match_dict["dport"]
            elif proto == "icmp":
                match_kwargs["ip_proto"] = inet.IPPROTO_ICMP

            if "vlan_id" in match_dict:
                match_kwargs["vlan_vid"] = ofproto.OFPVID_PRESENT | int(match_dict["vlan_id"])
            elif match_dict.get("vlan_untagged"):
                match_kwargs["vlan_vid"] = ofproto.OFPVID_NONE

            match = parser.OFPMatch(**match_kwargs)

            actions = []
            for act in rule.get("actions", []):
                action_type = act.get("type")
                if action_type == "OUTPUT":
                    actions.append(parser.OFPActionOutput(act["port"]))
                elif action_type == "PUSH_VLAN":
                    actions.append(parser.OFPActionPushVlan(ether.ETH_TYPE_8021Q))
                elif action_type == "SET_VLAN":
                    vlan_vid = ofproto.OFPVID_PRESENT | int(act["vlan_id"])
                    actions.append(parser.OFPActionSetField(vlan_vid=vlan_vid))
                elif action_type == "POP_VLAN":
                    actions.append(parser.OFPActionPopVlan())

            inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
            mod = parser.OFPFlowMod(
                datapath=datapath,
                cookie=rule["cookie"],
                command=ofproto.OFPFC_ADD,
                priority=rule.get("priority", 100),
                match=match,
                instructions=inst,
            )
            datapath.send_msg(mod)

        installed_count += 1

    return installed_count


def remove(
    controller: Any,
    cookie: int,
    cookie_mask: int = 0xFFFFFFFFFFFFFFFF,
) -> int:
    """
    Remove all flow rules matching the specified cookie across all datapaths.

    Sends OFPFlowMod (DELETE) messages with cookie and cookie_mask.
    Returns the number of switches on which deletion was issued.
    """
    removed_count = 0
    datapaths = []
    if hasattr(controller, "get_all_datapaths"):
        datapaths = controller.get_all_datapaths()
    elif hasattr(controller, "datapaths"):
        datapaths = list(controller.datapaths.values())

    for datapath in datapaths:
        if RYU_AVAILABLE and datapath is not None:
            parser = datapath.ofproto_parser
            ofproto = datapath.ofproto
            mod = parser.OFPFlowMod(
                datapath=datapath,
                cookie=cookie,
                cookie_mask=cookie_mask,
                command=ofproto.OFPFC_DELETE,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY,
            )
            datapath.send_msg(mod)
            removed_count += 1
        else:
            removed_count += 1

    return removed_count


def remove_vlan_version(controller: Any, cookie: int, vlan_id: int) -> int:
    """Delete only tagged forwarding rules for one strict-update VLAN version."""
    removed_count = 0
    if hasattr(controller, "get_all_datapaths"):
        datapaths = controller.get_all_datapaths()
    elif hasattr(controller, "datapaths"):
        datapaths = list(controller.datapaths.values())
    else:
        datapaths = []

    for datapath in datapaths:
        if RYU_AVAILABLE and datapath is not None:
            parser = datapath.ofproto_parser
            ofproto = datapath.ofproto
            match = parser.OFPMatch(
                vlan_vid=ofproto.OFPVID_PRESENT | int(vlan_id)
            )
            datapath.send_msg(parser.OFPFlowMod(
                datapath=datapath,
                cookie=cookie,
                cookie_mask=0xFFFFFFFFFFFFFFFF,
                command=ofproto.OFPFC_DELETE,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY,
                match=match,
            ))
            removed_count += 1
        elif not RYU_AVAILABLE:
            removed_count += 1
    return removed_count


def remove_untagged(controller: Any, cookie: int) -> int:
    """Delete only the legacy untagged rules for an intent cookie."""
    removed_count = 0
    datapaths = controller.get_all_datapaths() if hasattr(controller, "get_all_datapaths") else list(getattr(controller, "datapaths", {}).values())
    for datapath in datapaths:
        if RYU_AVAILABLE and datapath is not None:
            ofproto = datapath.ofproto
            match = datapath.ofproto_parser.OFPMatch(vlan_vid=ofproto.OFPVID_NONE)
            mod = datapath.ofproto_parser.OFPFlowMod(
                datapath=datapath, cookie=cookie, cookie_mask=0xFFFFFFFFFFFFFFFF,
                command=ofproto.OFPFC_DELETE, out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY, match=match,
            )
            datapath.send_msg(mod)
            removed_count += 1
        elif not RYU_AVAILABLE:
            removed_count += 1
    return removed_count
