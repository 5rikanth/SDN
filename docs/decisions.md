# Architecture Decision Records

Every architectural choice and its reasoning. This file is where viva answers come
from. Append, never rewrite history.

---

## ADR-001 - SDN controller: Ryu 4.34 on Python 3.9, inside Docker

**Date:** 07 Aug 2026 (ST-1) **Status:** Accepted **Decision owner:** whole team

**Context.** The project needs an OpenFlow 1.3 controller. Two realistic options:
Ryu (Python, small API, large tutorial base) and ONOS 2.7 (Java, production-grade,
actively maintained).

Ryu is effectively unmaintained. Its `eventlet` dependency breaks on Python 3.10+
because `ssl.wrap_socket` was removed. Ubuntu 22.04 ships Python 3.10, so a host
install fails outright.

**Decision.** Ryu 4.34, pinned to Python 3.9, `eventlet==0.30.2` and
`dnspython==1.16.0`, entirely inside the Docker image. Never pip-install Ryu on a
host machine.

**Reasoning.** Three members, fourteen weeks, and the difficulty of this project is
in the control loop, not the controller API. Ryu lets us write the whole controller
in the same language as the rest of the system. ONOS would add a Java toolchain and
a substantial learning curve to a schedule that has no slack for it.

**Consequences.**
- We inherit an unmaintained dependency. Mitigated by pinning inside a container,
  so the environment is reproducible regardless of host Python.
- We cannot claim to have extended a production controller.
- If Ryu proves unworkable by the end of ST-1, ONOS 2.7 is the documented fallback.
  After ST-1 this decision is closed.

**Considered and rejected.** *os-ken*, the maintained OpenStack fork of Ryu with a
near-identical API, would remove the Python 3.9 pin. Rejected for now only because
its documentation base is far smaller and we could not verify its current state
inside ST-1. Worth revisiting if Ryu blocks us.

---

## ADR-002 - The intent grammar is frozen at five constraint types

**Date:** 07 Aug 2026 (ST-1) **Status:** Accepted **Decision owner:** whole team

**Context.** Scope creep in the intent grammar is listed in the project plan's risk
register at likelihood High, impact High. Every new constraint type multiplies work
across M1 (validation), M2 (feasibility), M3 (pruning), M4 (enforcement) and M5
(measurement).

**Decision.** The grammar is frozen at:
- **Five constraint types:** `min_bandwidth_mbps`, `max_latency_ms`, `max_loss_pct`,
  `isolate_from`, `avoid_links`
- **Three violation actions:** `reroute`, `degrade`, `alert_only`
- **One priority scale:** integer 1 (highest) to 5 (best effort)
- **Two consistency modes:** `strict` (two-phase versioned update),
  `relaxed` (direct install)

`additionalProperties: false` at every level of the schema, so an unknown field is a
hard error rather than a silent ignore.

**Enforcement.** `tests/test_intent_validation.py` asserts the exact constraint set,
the exact action set and the priority bounds. CI runs those assertions on every push.
Widening the grammar requires editing a test that says, in terms, not to.

**Consequences.** Jitter is deliberately absent as a *constraint* even though M5
measures it, because a jitter bound would need its own admission maths. Any new
constraint type proposed after ST-3 is rejected by default.

---

## ADR-003 - Evaluation metric E9 and the flow-table pilot are additions to the plan

**Date:** 07 Aug 2026 (ST-1) **Status:** Accepted

**Context.** The project plan defines metrics E1-E8. Two additions were made during
planning and should not be presented as though they came from the plan.

**Decision.**
1. **E9 - time and resources from a clean VM to first result.** The plan claims
   laptop reproducibility but never measures it. E9 turns the claim into a number.
2. **Flow-table cap pilot pulled forward into ST-2.** Two-phase update roughly
   doubles flow-table occupancy during a switch-over, but that cost only bites where
   switch memory is scarce. Open vSwitch in software keeps flow tables in RAM, so
   metric E5 may return a flat result. Testing this in August rather than in the
   evaluation weeks is the difference between a scheduling adjustment and a crisis.

**Consequences.** If the pilot shows no measurable difference, E5 is demoted and the
evaluation leans on E2, E3, E4 and E6, which Mininet represents faithfully.

### Pilot result (25 Oct 2026)

The corrected pilot created 2,000 distinct OpenFlow matches per trial and tested
the documented OVS bridge `other-config:flow-limit` values 100, 500, 1,000, 2,000
and 5,000, plus an unlimited control. OVS 2.17.12 retained all 2,000 rules in every
trial (about 0.29–0.31 seconds per batch). The tested software-switch configuration
therefore showed no measurable cap effect; this does not establish hardware TCAM
capacity or behavior. The raw CSV is
`experiments/results/raw/flow_table_pilot_20261025161825.csv`.
