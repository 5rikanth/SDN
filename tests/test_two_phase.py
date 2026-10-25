"""Contracts for strict versioned OpenFlow updates."""

from __future__ import annotations

from src.common.models import Guarantee, IntentRecord, Match, PathPlan
from src.enforcement import flowmod, two_phase


def _intent() -> IntentRecord:
    return IntentRecord(
        id="INT-ST10",
        tenant="tests",
        priority=1,
        match=Match("10.0.0.1/32", "10.0.0.2/32", "udp", 5001),
        guarantee=Guarantee(min_bandwidth_mbps=10),
    )


def _plan(switches: tuple[str, ...], version: int | None) -> PathPlan:
    return PathPlan(
        intent_id="INT-ST10",
        switches=switches,
        links=PathPlan.links_for(list(switches)),
        est_latency_ms=5,
        bottleneck_mbps=100,
        version=version,
    )


def _ports(switches: tuple[str, ...]) -> dict[tuple[str, str], int]:
    ports = {(switches[0], "h1"): 1, (switches[-1], "h2"): 2}
    for index, (left, right) in enumerate(zip(switches, switches[1:]), 3):
        ports[(left, right)] = index
        ports[(right, left)] = index + 10
    return ports


def test_version_cookie_namespace_does_not_overlap_transit_cookie():
    intent_id = "INT-ST10"
    transit = flowmod.cookie_for_intent(intent_id)
    v0 = flowmod.cookie_for_version(intent_id, 0)
    v1 = flowmod.cookie_for_version(intent_id, 1)
    assert transit < 2**48
    assert v0 != v1
    assert v0 >= 2**62 and v1 >= 2**63


def test_versioned_rules_classify_untagged_and_forward_tagged_packets():
    switches = ("s1", "s2", "s7")
    plan = two_phase.with_version(_plan(switches, None), 1)
    rules = two_phase.versioned_rules(plan, _intent(), _ports(switches))
    assert len(rules) == 2 * len(switches)
    classifiers = [rule for rule in rules if rule["priority"] == 200]
    transit = [rule for rule in rules if rule["priority"] == 100]
    assert len(classifiers) == 2
    assert all(rule["match"]["vlan_untagged"] for rule in classifiers)
    assert all(rule["actions"][:2] == [
        {"type": "PUSH_VLAN"}, {"type": "SET_VLAN", "vlan_id": 101}
    ] for rule in classifiers)
    assert all(rule["match"]["vlan_id"] == 101 for rule in transit)
    assert all(rule["cookie"] == flowmod.cookie_for_intent("INT-ST10") for rule in transit)
    assert all(rule["cookie"] == flowmod.cookie_for_version("INT-ST10", 1) for rule in classifiers)
    assert sum(any(action["type"] == "POP_VLAN" for action in rule["actions"]) for rule in transit) == 2


def test_two_phase_installs_transit_then_flips_then_drains_and_removes_old(monkeypatch):
    calls: list[object] = []
    old = _plan(("s1", "s2", "s7"), 0)
    new = _plan(("s1", "s4", "s7"), 1)

    def install(_controller, rules):
        calls.append(("install", [rule["priority"] for rule in rules]))
        return len(rules)

    def remove(_controller, cookie, cookie_mask=0xFFFFFFFFFFFFFFFF):
        calls.append(("remove-cookie", cookie))
        return 1

    def remove_vlan(_controller, cookie, vlan_id):
        calls.append(("remove-vlan", cookie, vlan_id))
        return 1

    monkeypatch.setattr(two_phase.flowmod, "install", install)
    monkeypatch.setattr(two_phase.flowmod, "remove", remove)
    monkeypatch.setattr(two_phase.flowmod, "remove_vlan_version", remove_vlan)
    controller = type("Controller", (), {
        "port_map": _ports(new.switches),
        "barrier_all": lambda self: calls.append(("barrier",)),
        "wait_for_packet_drain": lambda self, plan: calls.append(("drain", plan.version)),
    })()

    installed = two_phase.install(controller, new, _intent(), old)
    assert installed == 2 * len(new.switches)
    assert calls[0][0] == "install" and set(calls[0][1]) == {100}
    assert calls[1] == ("barrier",)
    assert calls[2][0] == "install" and set(calls[2][1]) == {200}
    assert calls[3] == ("barrier",)
    assert calls[4] == ("drain", 0)
    assert ("remove-vlan", flowmod.cookie_for_intent("INT-ST10"), 100) in calls
