#!/usr/bin/env bash
# Repeat the B3 strict, damped closed-loop baseline under the controlled fault.
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
    echo "=== B3 strict closed-loop run ${run}/${RUNS} ==="
    sed 's/\r$//' experiments/scenarios/s1_single_intent.sh | bash -s -- --b3
done

echo "B3 closed-loop runs completed: ${RUNS}"
echo "Raw per-run results: experiments/results/raw/b3_closed_loop_*.csv"
