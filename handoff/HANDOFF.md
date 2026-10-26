# Intent SDN SLA project handoff

Prepared 25 October 2026. This package contains the Git bundle for the current
project branch, this continuation note, and the two-page research report.

## Restore the project

Extract the handoff ZIP, then run:

```bash
git clone intent-sdn-sla.bundle intent-sdn-sla
cd intent-sdn-sla
git checkout feat/st-12-evaluation
```

The bundle contains `main` and `feat/st-12-evaluation`. The feature branch is at
`024910099e91bb8a3fbf1021249ce1eea0ea4dcf` and includes the ST-7 through ST-12
work and raw experiment results. `main` was not modified.

## Current state

- Python 3.9 container suite: 261 passed, 11 skipped; Ruff passed.
- Live ST-7, ST-9, ST-10, B1, B2 and B3 pilot scenarios passed inside Docker.
- B0 and B1 each have ten-run data. B2 has ten controlled runs using a 40 ms
  delay on the primary path and a latency-only SLA. B2's separate admission
  ablation verified 140 Mbps of conflicting reservations on a 100 Mbps route.
- B3 strict update/default detector passed once; nine more runs are needed for a
  meaningful comparison. The B2/B3 summary is a pilot comparison only.
- The first B2 idle-throughput trials were invalid because low probe traffic
  triggered a bandwidth breach. They were discarded and are not in the branch.
- Detailed status and rerun commands: `docs/HANDOVER.md` and `README.md`.

## Continue here

1. Run nine more B3 closed-loop repetitions:
   `docker compose exec -T sdn bash -lc "sed 's/\\r$//' experiments/scenarios/b3_closed_loop.sh | bash -s -- --runs 9"`
2. Add an alternating violation/recovery trace to measure dwell suppression and
   reroute count.
3. Ask the team to confirm the original E1–E8 definitions. The definitions in
   `docs/EVALUATION_PROTOCOL_PROPOSAL.md` are explicitly a proposal.
4. Complete the approved ST-12 comparison, then obtain the missing ST-13/ST-14
   briefs and finish those milestones.
5. The checkpoint is now published at
   `https://github.com/5rikanth/SDN/tree/feat/st-12-evaluation`. The destination
   had no `main` branch at upload time, so a PR could not be opened. Once the
   team establishes its protected default branch, open a PR for review.

## Project constraints

- Preserve the frozen intent grammar and its existing tests.
- Keep main protected; work and review changes on feature branches.
- Run all Mininet, OVS and Ryu commands inside the Docker container.
- Use Python 3.9 for Ryu.

The attached research report predates the latest B2/B3 measurements. Use
`docs/HANDOVER.md` for the current implementation and evaluation status.
