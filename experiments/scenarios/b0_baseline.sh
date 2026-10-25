#!/usr/bin/env bash
set -euo pipefail

# b0_baseline.sh – thin wrapper that invokes the Python driver.
# Measures latency, loss and throughput between h1 and h2 using the driver.
# Writes CSV to experiments/results/raw/b0_baseline_<timestamp>.csv

usage() {
  echo "Usage: $0 [--runs N]"
  exit 1
}

RUNS=10
while [[ $# -gt 0 ]]; do
  case $1 in
    --runs)
      RUNS=${2:-10}
      shift 2
      ;;
    *) usage ;;
  esac
done

clean_mininet() {
  timeout 15s python3.9 /usr/local/bin/mn -c >/dev/null 2>&1 || true
}
clean_mininet
trap clean_mininet EXIT

RESULT_DIR="experiments/results/raw"
mkdir -p "${RESULT_DIR}"
TIMESTAMP=$(date +"%Y%m%d%H%M%S")
CSV_FILE="${RESULT_DIR}/b0_baseline_${TIMESTAMP}.csv"

echo "run_id,timestamp,metric,value,unit" > "${CSV_FILE}"

# Run the driver and append its CSV rows.
python -m experiments.scenarios._driver baseline --runs "$RUNS" >> "${CSV_FILE}"

echo "Baseline results written to ${CSV_FILE}"
