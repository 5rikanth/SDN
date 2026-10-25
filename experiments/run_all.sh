#!/usr/bin/env bash
# Reproduce the currently implemented B0 and E5 measurements and plots.
set -euo pipefail

RUNS=10
REUSE_B0=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --runs)
      RUNS="${2:?--runs needs a value}"
      shift 2
      ;;
    --reuse-b0)
      REUSE_B0=1
      shift
      ;;
    *)
      echo "Usage: $0 [--runs N] [--reuse-b0]" >&2
      exit 2
      ;;
  esac
done

clean_mininet() {
  timeout 15s python3.9 /usr/local/bin/mn -c >/dev/null 2>&1 || true
}
trap clean_mininet EXIT INT TERM

if [[ "$REUSE_B0" -eq 0 ]]; then
  sed 's/\r$//' experiments/scenarios/b0_baseline.sh | bash -s -- --runs "$RUNS"
fi
clean_mininet
sed 's/\r$//' experiments/scenarios/flow_table_pilot.sh | bash -s

B0_FILE=$(find experiments/results/raw -maxdepth 1 -type f -name 'b0_baseline_*.csv' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)
PILOT_FILE=$(find experiments/results/raw -maxdepth 1 -type f -name 'flow_table_pilot_*.csv' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)
if [[ -z "$B0_FILE" || -z "$PILOT_FILE" ]]; then
  echo "Missing B0 or flow-table pilot CSV in experiments/results/raw" >&2
  exit 1
fi

STAMP=$(date +"%Y%m%d%H%M%S")
SUMMARY="experiments/results/summary/evaluation_${STAMP}.csv"
python3.9 -m experiments.summarize_results "$B0_FILE" --output "$SUMMARY"
python3.9 experiments/plot_results.py --summary "$SUMMARY" --pilot "$PILOT_FILE" \
  --out-dir experiments/figures --suffix "$STAMP"

cat <<EOF
Available evidence regenerated.
B0: $B0_FILE
E5 pilot: $PILOT_FILE
Summary: $SUMMARY
Figures: experiments/figures/*_$STAMP.png
B1-B3 and the original E1-E8 measurement contract are not implemented in this checkout.
EOF
