#!/usr/bin/env bash
# Repeat the B2 relaxed, no-hysteresis closed-loop pilot.
set -euo pipefail

RUNS=10
if [[ $# -gt 0 ]]; then
    if [[ $# -ne 2 || "$1" != "--runs" || ! "$2" =~ ^[1-9][0-9]*$ ]]; then
        echo "Usage: $0 [--runs N]" >&2
        exit 2
    fi
    RUNS="$2"
fi

for ((run = 1; run <= RUNS; run++)); do
    echo "=== B2 relaxed closed-loop run ${run}/${RUNS} ==="
    sed 's/\r$//' experiments/scenarios/s1_single_intent.sh | bash -s -- --b2
done

echo "B2 closed-loop runs completed: ${RUNS}"
echo "Raw per-run results: experiments/results/raw/b2_closed_loop_*.csv"
