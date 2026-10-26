# Continue the Intent SDN SLA project

This repository contains the complete project source and the latest checkpoint
from the local feature branch. Start from the branch
`feat/st-12-evaluation`; do not start from `main` if you need the latest
ST-7 through ST-12 work.

## Get the checkpoint

```bash
git clone https://github.com/5rikanth/SDN.git
cd SDN
git fetch origin
git checkout feat/st-12-evaluation
```

If GitHub shows the branch under a pull request, use the branch listed there.
The repository's `main` branch is protected; make changes on a branch named
`feat/<subtask>-<name>`, push it, and open a pull request for review.

## Read first

1. `docs/Team16_Steps1to6_Explained.pdf`
2. `SETUP.md`
3. `docs/HANDOVER.md`
4. `docs/architecture.md`
5. This README and `prompts/ST-07_close_the_loop.md`

The project handoff bundle and research report are in `handoff/`; the report
source and rendered copy are also in `reports/`.

## Run it

Use Python 3.9. Ryu is incompatible with Python 3.10 and later (see ADR-001).
The software-only walkthrough needs no Docker, root, or Mininet:

```bash
python demo_st4.py
```

To exercise Mininet, OVS, and Ryu, run them inside Docker:

```bash
docker compose up --build -d
docker compose exec sdn make demo
```

See `SETUP.md` for environment details and `docs/HANDOVER.md` for
container-backed scenarios and evaluation commands.

## Checkpoint and next work

The current checkpoint is commit `024910099e91bb8a3fbf1021249ce1eea0ea4dcf`
on `feat/st-12-evaluation`. Its recorded container validation was 261 passed,
11 skipped, with Ruff clean. The branch includes the ST-7 through ST-12 work,
ten-run B0/B1 data, ten controlled B2 runs, and a single B3 pilot.

Continue by:

1. Re-run B3 nine more times using the command in `handoff/HANDOFF.md`.
2. Add an alternating violation/recovery trace to evaluate dwell suppression.
3. Ask the team to confirm the original E1–E8 definitions; the definitions in
   `docs/EVALUATION_PROTOCOL_PROPOSAL.md` are proposals, not authoritative.
4. Complete ST-13 and ST-14 after obtaining their briefs.
5. Update the report with validated results and submit changes through a PR.

The baseline known defect described in `docs/HANDOVER.md` is fixed on this
checkpoint. Do not widen the frozen intent grammar; CI checks it. Do not edit
failing tests to make them pass.

## Research report

`reports/Research_Papers_and_Project_Implementation_Report.docx` is the
two-page report prepared for the current literature and implementation
checkpoint. It predates the latest B2/B3 measurements. Treat
`docs/HANDOVER.md` and the experiment CSV files as the source of truth for
current project progress, then refresh the report after further runs.
