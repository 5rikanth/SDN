"""Version-tagged, two-phase path updates for strict intents.

The new path's tagged forwarding rules are installed before either ingress
classifier is changed. Old-version rules remain until classifiers have crossed a
barrier and the old path's bounded emulated delay has elapsed.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from src.common.models import IntentRecord, PathPlan
from src.enforcement import flowmod

_VLAN_BASE = 100


def next_version(current: Optional[PathPlan] = None) -> int:
    """Return the first version or toggle between two bounded VLAN tags."""
    if current is None or current.version is None:
        return 0
    return 1 - (current.version % 2)


def with_version(plan: PathPlan, version: int) -> PathPlan:
    """Return a copy with a version suitable for storing in the existing model."""
    if version not in (0, 1):
        raise ValueError("strict path versions must be 0 or 1")
    return replace(plan, version=version)


def _tag(version: int) -> int:
    if version not in (0, 1):
        raise ValueError("strict path versions must be 0 or 1")
    return _VLAN_BASE + version


def versioned_rules(
    plan: PathPlan,
    intent: IntentRecord,
    port_map: Dict[Tuple[str, str], int],
) -> List[Dict[str, Any]]:
    """Build tagged transit/egress rules and untagged ingress classifiers."""
    if plan.version not in (0, 1):
        raise ValueError("strict path plan must carry version 0 or 1")
    tag = _tag(plan.version)
    classifier_cookie = flowmod.cookie_for_version(intent.id, plan.version)
    transit_cookie = flowmod.cookie_for_intent(intent.id)
    generated = flowmod.rules_for_path(plan, intent, port_map)
    out: List[Dict[str, Any]] = []
    sequences = {
        "forward": list(plan.switches),
        "reverse": list(reversed(plan.switches)),
    }

    for rule in generated:
        direction = rule["direction"]
        sequence = sequences[direction]
        switch = rule["switch"]
        index = sequence.index(switch)
        match = dict(rule["match"])
        actions = list(rule["actions"])
        priority = rule["priority"]
        if index == 0:
            # A classifier is the only rule that sees the untagged packet.
            match["vlan_untagged"] = True
            actions = [
                {"type": "PUSH_VLAN"},
                {"type": "SET_VLAN", "vlan_id": tag},
            ] + actions
            priority = 200
        else:
            match["vlan_id"] = tag
            if index == len(sequence) - 1:
                actions = [{"type": "POP_VLAN"}] + actions
        out.append({
            "switch": switch,
            "match": match,
            "actions": actions,
            "priority": priority,
            "cookie": classifier_cookie if index == 0 else transit_cookie,
            "direction": direction,
            "version": plan.version,
        })
    return out


def _send_barriers(controller: Any) -> None:
    """Queue one barrier per datapath to order phases on each OpenFlow channel."""
    if hasattr(controller, "barrier_all"):
        controller.barrier_all()


def _remove_version(controller: Any, intent_id: str, version: int) -> int:
    classifiers = flowmod.remove(
        controller, flowmod.cookie_for_version(intent_id, version)
    )
    transit = flowmod.remove_vlan_version(
        controller, flowmod.cookie_for_intent(intent_id), _tag(version)
    )
    return classifiers + transit


def install(
    controller: Any,
    plan: PathPlan,
    intent: IntentRecord,
    old_plan: Optional[PathPlan] = None,
) -> int:
    """Install one strict version and remove the previous version after flip.

    A staged update fails closed: classifiers stay on the old version unless
    every required new rule was sent successfully. An initial path has no old
    version to clean up.
    """
    rules = versioned_rules(plan, intent, controller.port_map)
    classifiers = [rule for rule in rules if rule["priority"] == 200]
    forwarding = [rule for rule in rules if rule["priority"] != 200]
    staged = 0
    flip_started = False
    try:
        staged = flowmod.install(controller, forwarding)
        if staged != len(forwarding):
            raise RuntimeError(
                f"staged {staged} of {len(forwarding)} versioned path rules"
            )

        # OpenFlow preserves message order on each datapath connection. The
        # barrier separates complete staging from the ingress classifier flip.
        _send_barriers(controller)
        flip_started = True
        flipped = flowmod.install(controller, classifiers)
        if flipped != len(classifiers):
            raise RuntimeError(
                f"updated {flipped} of {len(classifiers)} ingress classifiers"
            )
        _send_barriers(controller)
    except Exception:
        if old_plan is not None and old_plan.version in (0, 1) and flip_started:
            old_rules = versioned_rules(old_plan, intent, controller.port_map)
            old_classifiers = [r for r in old_rules if r["priority"] == 200]
            flowmod.install(controller, old_classifiers)
            _send_barriers(controller)
        elif old_plan is None and flip_started:
            flowmod.remove(controller, flowmod.cookie_for_version(intent.id, plan.version))
        # Staged rules have not been selected by ingress yet, so it is safe to
        # remove only the failed generation. For a first install also remove
        # its tagged transit rules.
        if plan.version not in (old_plan.version if old_plan else -1,):
            _remove_version(controller, intent.id, plan.version)
        raise

    total = staged + flipped
    if old_plan is not None and old_plan.version in (0, 1):
        if hasattr(controller, "wait_for_packet_drain"):
            controller.wait_for_packet_drain(old_plan)
        _remove_version(controller, intent.id, old_plan.version)
    elif old_plan is not None:
        # Compatibility with a pre-ST-10 untagged strict path.
        flowmod.remove_untagged(controller, flowmod.cookie_for_intent(intent.id))
    return total
