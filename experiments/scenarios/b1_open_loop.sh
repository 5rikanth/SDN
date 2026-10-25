#!/usr/bin/env bash
# Repeat the B1 install-and-forget pilot using the shared S1 topology.
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
    echo "=== B1 open-loop pilot ${run}/${RUNS} ==="
    sed 's/\r$//' experiments/scenarios/s1_single_intent.sh | bash -s -- --b1
done

echo "B1 pilot runs completed: ${RUNS}"
echo "Raw per-run results: experiments/results/raw/b1_open_loop_*.csv"
