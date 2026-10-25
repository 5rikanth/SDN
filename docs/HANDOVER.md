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

**In progress: ST-9 preemption (`feat/st-09-preemption`):** Higher-priority
requests may displace one lower-priority reservation if that makes a path feasible;
the controller removes the victim's installed rules. The M5 detector already has
EWMA, consecutive-confirmation, hysteresis and dwell gates. Run the acceptance
commands below on the current feature branch before calling ST-9 complete.

These branches are local and have not been pushed or merged. GitHub rejected the
push for account `5rikanth` with HTTP 403; collaborator write access is needed before
opening the required PRs. Protected `main` remains unchanged.

## What is not true yet

1. The dashboard is not implemented; it remains ST-11 work.
2. ST-9 preemption is in progress; ST-10 two-phase updates and ST-12–14 evaluation
   and final reporting remain.
3. The live result is from containerized Mininet and Open vSwitch, not physical
   switching hardware.

## Acceptance commands

```bash
docker compose build
docker compose up -d
docker compose exec -T sdn pytest tests -q
docker compose exec -T sdn ruff check src tests topology experiments
docker compose exec -T sdn bash experiments/scenarios/s1_single_intent.sh
docker compose exec -T sdn bash experiments/scenarios/s1_single_intent.sh --st7
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
