# Intent-Based SDN for 5G SLA Enforcement

[Continue from the latest project checkpoint](README_CONTINUE.md) — read order,
Python/Docker setup, current status, and next steps for a new contributor.

Write down a service promise. The system installs it on the network, measures whether
it is being kept, and re-plans automatically when it is not — on one laptop.

```bash
git clone https://github.com/platynom/intent-sdn-sla.git && cd intent-sdn-sla
docker compose up --build -d          # pinned Mininet + OVS + Ryu
docker compose exec sdn make demo     # topology, controller, one URLLC intent
```

The read-only operator dashboard is available locally at `http://localhost:8080`
after `docker compose up --build -d`. It reads the shared SQLite database and
displays intents, current paths, recent measurements and the audit trail.

---

## What this is

A reference implementation of the intent lifecycle — expression, translation,
activation and **assurance** — for transport-layer network slices, built to run on a
single commodity laptop with 8 GB of RAM.

**We make no novelty claim.** Intent-based SLA assurance is established work, as is
consistent network update. What this repository contributes is an artifact: an
implementation small enough that anyone can run it, measured against explicit
baselines with confidence intervals, with each component's contribution isolated.

## Architecture

| Module | Question it answers | What it does |
|---|---|---|
| **M1** Intent Manager | *What does the user want?* | YAML parse, JSON-Schema validation, REST API, SQLite store |
| **M2** Admission & Conflict | *Can we provide it?* | Residual-capacity graph; admit / reject / preempt / degrade |
| **M3** Path Computation | *How can we provide it?* | Yen k-shortest paths with constraint pruning |
| **M4** Enforcement | *Can we make the network do it?* | OpenFlow 1.3 rules, HTB queues, two-phase versioned update |
| **M5** Telemetry & SLA Monitor | *Is it actually working?* | Per-intent delay, jitter and loss; hysteresis and dwell timer |
| **M6** Dashboard & Audit Log | *What is happening, and why?* | Live topology, SLA badges, causal event chain |

```
intents/*.yaml → M1 → M2 → M3 → M4 → [ Mininet / Open vSwitch ] → M5
                            ↑                                       │
                            └───────── violation event ─────────────┘
```

The edge from M5 back to M3 is the closed loop. Everything above it is compilation;
everything below it is assurance.

## The intent grammar is frozen

Five constraint types, three violation actions, one 1–5 priority scale. See
[`src/intent_manager/schema.json`](src/intent_manager/schema.json) and ADR-002 in
[`docs/decisions.md`](docs/decisions.md). CI fails if the grammar widens.

```yaml
intent:
  id: INT-001
  tenant: telemedicine-video
  priority: 1                    # 1 = highest … 5 = best effort
  match:
    src: 10.0.0.1/32
    dst: 10.0.0.2/32
    proto: udp
    dport: 5001
  guarantee:                     # the five frozen constraint types
    min_bandwidth_mbps: 20
    max_latency_ms: 20
    max_loss_pct: 0.5
    # isolate_from: [public-internet]
    # avoid_links: [s3-s4]
  consistency: strict            # strict → two-phase update | relaxed → direct install
  on_violation: reroute          # reroute | degrade | alert_only
  dwell_seconds: 10              # anti-oscillation hold-off
```

## Repository layout

```
intents/              example intent per scenario, plus deliberately invalid ones
src/
  intent_manager/     M1  schema.json, validator.py, REST API
  admission/          M2  residual graph, feasibility, preemption
  pathing/            M3  yen_ksp.py, prune.py
  enforcement/        M4  flowmod.py, queues.py, controller.py
  telemetry/          M5  collector.py, probes.py, detector.py, runtime.py
  dashboard/          M6  read-only operator dashboard and audit view
  common/             models.py, db.py, events.py, audit.py
topology/             Mininet topology and tc link budgets
experiments/          run_all.sh, scenarios/, results/raw/ (CSV committed), plots/
docs/                 architecture.md, decisions.md (ADRs), viva.md
tests/                unit tests plus one end-to-end smoke test
```

## Development

```bash
python3.9 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest tests -q          # software-only suite; no network or root needed
ruff check src tests topology experiments
```

Everything touching Mininet, Open vSwitch or Ryu **must** run inside the container.
Ryu 4.34 does not work on Python 3.10+; see ADR-001.

### Branch policy

`main` is protected. Work on `feat/<subtask>-<short-name>`, open a pull request, and
have one other member review it. CI must pass before merge. A change to a module
interface needs agreement from both sides, recorded in `docs/decisions.md`.

## Evaluation

The project plan calls for four baselines × eight metrics, at least ten repetitions
and 95% confidence intervals. The checkout currently has a ten-run B0 harness and
an E5 feasibility pilot; it does not contain the original E1–E8 measurement
definitions or the original B1–B3 scenario specifications. The runner regenerates only evidence
that is implemented and labels the remaining work. A candidate contract for review
is in `docs/EVALUATION_PROTOCOL_PROPOSAL.md`; it is not the missing original plan.

```bash
docker compose exec -T sdn bash -lc "sed 's/\\r$//' experiments/run_all.sh | bash -s"
# Add --reuse-b0 to reuse the existing ten-run CSV instead of running B0 again.
# Run a B1 install-and-forget pilot (no telemetry probes):
docker compose exec -T sdn bash -lc "sed 's/\\r$//' experiments/scenarios/b1_open_loop.sh | bash -s -- --runs 10"
# Run the B2 no-conflict-check admission ablation (intentionally overbooks a link):
docker compose exec -T sdn bash -lc "sed 's/\\r$//' experiments/scenarios/b2_no_conflict.sh | bash -s"
# Run ten B2 relaxed closed-loop measurements:
docker compose exec -T sdn bash -lc "sed 's/\\r$//' experiments/scenarios/b2_closed_loop.sh | bash -s -- --runs 10"
# Run the matching B3 strict closed-loop baseline:
docker compose exec -T sdn bash -lc "sed 's/\\r$//' experiments/scenarios/b3_closed_loop.sh | bash -s -- --runs 10"
```

| | Baseline |
|---|---|
| **B0** | Static shortest-path forwarding; no intents, no monitoring |
| **B1** | Intents installed, but open-loop (install-and-forget) |
| **B2** | Closed loop, naive update, no conflict check, no hysteresis |
| **B3** | Full system: conflict-aware admission + two-phase + hysteresis |

## Status

The cumulative local ST-12 branch contains ST-1 through ST-11. Its current
container validation passes 261 tests (11 skipped) with Ruff clean; live ST-7,
ST-9 and ST-10 scenarios pass in sequence. ST-11 adds the read-only dashboard at
`http://localhost:8080`. ST-12 has a ten-run B0 baseline sample and a corrected
flow-table pilot: on OVS 2.17.12, 2,000 unique rules installed at every tested
limit from 100 through 5,000, so the software-switch E5 cap had no observed effect.
An open-loop B1 pilot has ten POST-response latency runs (mean 61.982 ms,
95% t interval 57.251–66.712 ms); it remains a pilot measurement and is not yet
included in the cross-baseline comparison. B2 now has ten controlled closed-loop
runs plus a live admission-ablation pilot. One matching B3 strict-update run
passed, but nine more B3 repetitions and an alternating anti-oscillation trace
remain. ST-13–14 reporting also remains. See
[`docs/HANDOVER.md`](docs/HANDOVER.md) for branch status and validation results.

**Resolved ST-7 defect.** Confirmed violation events are persisted to SQLite for
the controller and audit log to consume. The live ST-7 scenario previously verified
the persisted event, reroute, causal audit entry and post-reroute UDP delivery.

## Licence

MIT. See [`CITATION.cff`](CITATION.cff) for the work whose methods this implements —
in particular the two-phase versioned update of Reitblatt et al. (SIGCOMM 2012), which
we use as a building block and do not claim as a contribution.
