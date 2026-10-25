# Intent-Based SDN for 5G SLA Enforcement

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
pip install -r requirements.txt
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

Four baselines × eight metrics, at least ten repetitions each, 95% confidence
intervals, fixed seeds. Raw CSV is committed next to the plotting scripts so every
figure is regenerable.

```bash
./experiments/run_all.sh        # reproduces every figure from scratch
```

| | Baseline |
|---|---|
| **B0** | Static shortest-path forwarding; no intents, no monitoring |
| **B1** | Intents installed, but open-loop (install-and-forget) |
| **B2** | Closed loop, naive update, no conflict check, no hysteresis |
| **B3** | Full system: conflict-aware admission + two-phase + hysteresis |

## Status

ST-1 through ST-11 have local feature-branch implementations and container
validation. The live ST-10 scenario verifies a strict versioned route update across
Mininet, Open vSwitch and Ryu. ST-11 adds the read-only dashboard at
`http://localhost:8080`. ST-12 has a ten-run B0 baseline sample; remaining B1–B3
comparisons need the missing metric and setup definitions. ST-13–14 and final
reporting remain. See
[`docs/HANDOVER.md`](docs/HANDOVER.md) for branch status and validation results.

**Resolved ST-7 defect.** Confirmed violation events are persisted to SQLite for
the controller and audit log to consume. The live ST-7 scenario previously verified
the persisted event, reroute, causal audit entry and post-reroute UDP delivery.

## Licence

MIT. See [`CITATION.cff`](CITATION.cff) for the work whose methods this implements —
in particular the two-phase versioned update of Reitblatt et al. (SIGCOMM 2012), which
we use as a building block and do not claim as a contribution.
