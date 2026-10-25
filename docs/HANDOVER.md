# Project handover

## What this project does

An operator describes a network promise as a YAML intent: which traffic matters
and the bandwidth, latency, loss, isolation or link-avoidance requirement it must
meet. The system validates that request, decides whether it is feasible, computes
a suitable path, generates OpenFlow enforcement, measures the delivered service
and is intended to re-plan when a real SLA breach persists. It is a compact,
reproducible reference implementation for 5G-style service assurance, not a claim
of a novel networking algorithm.

## How to get the code

The repository is the single source of truth:

```bash
git clone https://github.com/platynom/intent-sdn-sla.git
cd intent-sdn-sla
```

It is private, so ask Tanmay for collaborator access first. Do all work on a branch
(`feat/<subtask>-<short-name>`) and open a pull request; `main` is protected.

**Do not work from the Drive zip.** `intent-sdn-sla_20260907.zip` in Google Drive is
a frozen snapshot taken on 7 September 2026. It is there for reference only. Any
change made inside it is invisible to everyone else and will diverge from `main`
immediately.

## The six modules

| Module | Question it answers | Responsibility |
|---|---|---|
| M1 Intent Manager | What does the user want? | Validate and store machine-readable intents; expose the REST API. |
| M2 Admission and Conflict | Can we provide it? | Decide whether capacity exists and resolve competing requests. |
| M3 Path Computation | How can we provide it? | Compute and prune candidate paths, then choose deterministically. |
| M4 Enforcement | Can we make the network do it? | Generate OpenFlow 1.3 rules and OVS HTB queue commands. |
| M5 Telemetry and SLA Monitor | Is it actually working? | Collect measurements and distinguish persistent breaches from noise. |
| M6 Dashboard and Audit | What is happening, and why? | Present status and preserve the causal explanation of every action. |

## Current state

**Baseline ST-1 through ST-6:** Scenario S1 previously passed in the project
container, installing INT-001 OpenFlow rules and a 20 Mbps HTB queue, sending
matching UDP traffic along `s1-s2-s3-s7`, and persisting five one-hertz samples.

**Local ST-7 branch (`feat/st-07-close-loop`):** Confirmed telemetry violations
are persisted to SQLite and bridged into the controller process. The live `--st7`
scenario passed in Python 3.9 with Mininet, OVS and Ryu: telemetry rerouted INT-001
to `s1-s4-s5-s7`, one reroute and a causal audit entry were recorded, and a UDP
probe replied after the route change. The container suite passed 245 tests (11
skipped) and Ruff passed.

**Local ST-8 branch (`feat/st-08-admission`):** Admission now accounts for active
bandwidth reservations on current paths, chooses feasible residual-capacity routes,
and persists an explainable ADMIT/REJECT event. The Python 3.9 container suite
passed 241 tests (11 skipped); Ruff passed.

**Local ST-9 branch (`feat/st-09-preemption`):** Higher-priority requests may
displace one lower-priority reservation if that makes a path feasible; the
controller removes the victim's installed rules before installing the new path.
The live `s9_preemption.sh` scenario passed in Python 3.9 with Mininet, OVS and
Ryu, verifying victim state, the persisted preemption event and flow-rule removal.
The container suite passed 245 tests (11 skipped) and Ruff passed. The M5 detector
already has EWMA, consecutive-confirmation, hysteresis and dwell gates.

**Local ST-10 branch (`feat/st-10-two-phase`):** Strict paths now stage tagged
forwarding rules, flip untagged ingress classifiers after an OpenFlow barrier,
retain the old version through a bounded Mininet drain interval, then remove its
rules. Versioned classifier cookies and VLAN matches let withdrawal and cleanup
target the right rules. The live `s10_two_phase.sh` scenario passed in the Python
3.9 container: it flipped VLAN 100 to VLAN 101 onto the alternate route, removed
the old north-path rules, and delivered a UDP probe afterward. The suite passed
248 tests (11 skipped) and Ruff passed. The drain interval is sized for this
emulated topology; production WAN deployments need measured packet-lifetime
bounds before claiming lossless updates.

These branches are local and have not been pushed or merged. GitHub rejected the
push for account `5rikanth` with HTTP 403; collaborator write access is needed before
opening the required PRs. Protected `main` remains unchanged.

**Local ST-11 branch (`feat/st-11-dashboard`):** The M6 dashboard now serves a
read-only operator view of intent state, paths, latest SLA measurements, selectable
measurement charts and the persisted audit trail. JSON endpoints expose the same
overview and per-intent detail. Compose runs the dashboard in a separate container
and publishes port 8080 on `127.0.0.1` only. The page and endpoints were checked in
the browser and container; the Python 3.9 suite passed 252 tests (11 skipped) and
Ruff passed.

## What is not true yet

1. ST-12–14 evaluation and final reporting remain.
2. The live result is from containerized Mininet and Open vSwitch, not physical
   switching hardware.

## Acceptance commands

```bash
docker compose build
docker compose up -d
docker compose exec -T sdn pytest tests -q
docker compose exec -T sdn ruff check src tests topology experiments
docker compose exec -T sdn bash experiments/scenarios/s1_single_intent.sh
docker compose exec -T sdn bash experiments/scenarios/s1_single_intent.sh --st7
docker compose exec -T sdn bash experiments/scenarios/s9_preemption.sh
docker compose exec -T sdn bash experiments/scenarios/s10_two_phase.sh
curl http://localhost:8080/api/dashboard
```

The `--st7` mode runs the S1 live checks, writes one deterministic confirmed
violation to SQLite, and waits for the controller to install the expected
alternate route, checks its switch rules, sends a UDP probe across it, and checks
the causal audit record. It is an integration check; the normal S1 command
remains the ST-1 through ST-6 baseline.

## Read these first

1. `SETUP.md` - install, verify and run the software-only workflow.
2. `docs/architecture.md` - topology, routes and why alternatives are mandatory.
3. `docs/decisions.md` - frozen design decisions and the ONOS fallback.
4. `prompts/` - bounded implementation briefs, beginning with ST-7.

## Resolved defect: persisted violation events

The original S1 run persisted measurements but not confirmed violation events.
ST-7 now saves confirmed detector events to SQLite and has Ryu consume them through
the controller's event bus and `ControlLoop`. The live Python 3.9 scenario observed
a red telemetry breach, rerouted INT-001 from `s1-s2-s3-s7` to `s1-s4-s5-s7`,
recorded one reroute and a causal audit entry, and received a UDP reply afterward.
Recheck after a new run with:
`sqlite3 experiments/results/state.db 'select count(*) from events;'`
