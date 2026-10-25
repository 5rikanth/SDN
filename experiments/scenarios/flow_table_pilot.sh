#!/usr/bin/env bash
set -euo pipefail

# flow_table_pilot.sh – thin wrapper that invokes the Python driver.
# Generates dummy flows on s1, sweeps flow‑limit, records metrics, prints verdict.
# Writes CSV to experiments/results/raw/flow_table_pilot_<timestamp>.csv

RESULT_DIR="experiments/results/raw"
mkdir -p "${RESULT_DIR}"
TIMESTAMP=$(date +"%Y%m%d%H%M%S")
CSV_FILE="${RESULT_DIR}/flow_table_pilot_${TIMESTAMP}.csv"

# A previous interrupted Mininet run can leave kernel interfaces behind.
clean_mininet() {
  timeout 15s python3.9 /usr/local/bin/mn -c >/dev/null 2>&1 || true
}
clean_mininet

# Keep the raw artifact parseable while still showing the human-readable verdict.
VERDICT_FILE=$(mktemp)
cleanup() {
  status=$?
  trap - EXIT
  if [ -s "${VERDICT_FILE}" ]; then
    cat "${VERDICT_FILE}"
  fi
  rm -f "${VERDICT_FILE}"
  clean_mininet
  exit "${status}"
}
trap cleanup EXIT
python -m experiments.scenarios._driver flowpilot > "${CSV_FILE}" 2> "${VERDICT_FILE}"

echo "Flow‑table pilot results written to ${CSV_FILE}"
