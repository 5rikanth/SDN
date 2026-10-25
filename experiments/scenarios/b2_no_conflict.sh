#!/usr/bin/env bash
# B2 live admission ablation: intentionally admit individually feasible,
# conflicting reservations, then verify the resulting overcommit in Mininet/OVS.
set -euo pipefail

RYU_PID=""
API_PID=""
TOPO_PID=""
LOG_DIR="/var/log/team16"

cleanup_rows() {
    python3.9 - <<'PY'
from src.common import db
conn = db.connect()
db.init_db(conn)
conn.execute("DELETE FROM intents WHERE id IN ('INT-921', 'INT-922')")
conn.commit()
conn.close()
PY
}

cleanup() {
    for pid in "${TOPO_PID}" "${API_PID}" "${RYU_PID}"; do
        if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
            kill "${pid}" 2>/dev/null || true
            wait "${pid}" 2>/dev/null || true
        fi
    done
    timeout 15s python3.9 /usr/local/bin/mn -c >/dev/null 2>&1 || true
    cleanup_rows
}
trap cleanup EXIT INT TERM

mkdir -p "${LOG_DIR}"
timeout 15s python3.9 /usr/local/bin/mn -c >/dev/null 2>&1 || true
cleanup_rows

ryu-manager --ofp-tcp-listen-port 6653 src/enforcement/controller.py >"${LOG_DIR}/ryu-b2.log" 2>&1 &
RYU_PID=$!
sleep 2
TEAM16_EXPERIMENT_MODE=b2 python3.9 -m uvicorn src.intent_manager.api:app \
    --host 127.0.0.1 --port 8000 >"${LOG_DIR}/api-b2.log" 2>&1 &
API_PID=$!
sleep 2

python3.9 - <<'PY' >"${LOG_DIR}/topo-b2.log" 2>&1 &
import time
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from topology.team16_topo import Team16Topo
net = Mininet(topo=Team16Topo(), switch=OVSSwitch, link=TCLink,
              controller=None, autoSetMacs=True, autoStaticArp=True)
net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
net.start()
time.sleep(300)
net.stop()
PY
TOPO_PID=$!
sleep 6

submit_intent() {
    local file="$1"
    local output="$2"
    local status
    status=$(curl -s -o "${output}" -w "%{http_code}" -X POST \
        http://127.0.0.1:8000/intent -H "Content-Type: text/plain" \
        --data-binary "@${file}")
    if [ "${status}" != "201" ]; then
        echo "FAIL: ${file} returned HTTP ${status}"
        cat "${output}" || true
        exit 1
    fi
}

echo "=== B2: requesting first 80 Mbps reservation ==="
submit_intent experiments/scenarios/fixtures/b2_preemptible.yaml /tmp/b2_901.json
echo "=== B2: requesting conflicting 60 Mbps reservation ==="
submit_intent experiments/scenarios/fixtures/b2_conflicting.yaml /tmp/b2_902.json

for attempt in $(seq 1 30); do
    STATES=$(python3.9 - <<'PY'
from src.common import db
conn = db.connect()
print(','.join(db.load_intent(conn, item).state.value
               for item in ('INT-921', 'INT-922')))
conn.close()
PY
)
    [ "${STATES}" = "active,active" ] && break
    sleep 0.5
done
if [ "${STATES}" != "active,active" ]; then
    echo "FAIL: both B2 intents did not become active (states=${STATES})"
    exit 1
fi

python3.9 - <<'PY'
from src.common import db
from topology.topology_spec import LINKS
conn = db.connect()
victim = db.load_intent(conn, 'INT-921')
conflict = db.load_intent(conn, 'INT-922')
victim_path = db.current_path(conn, victim.id)
conflict_path = db.current_path(conn, conflict.id)
assert victim_path is not None and conflict_path is not None
shared = set(victim_path.links).intersection(conflict_path.links)
capacities = {link.name: link.bw_mbps for link in LINKS}
capacity = min(capacities[link] for link in shared)
claimed = victim.guarantee.min_bandwidth_mbps + conflict.guarantee.min_bandwidth_mbps
assert claimed > capacity, (claimed, capacity, shared)
event = next(event for event in db.events_for(conn, conflict.id)
             if event.type.value == 'admission.decided')
assert 'reservation/conflict checks bypassed' in event.payload['reason']
print(f'PASS: both reservations active on shared links {sorted(shared)}')
print(f'PASS: declared demand {claimed} Mbps exceeds shared capacity {capacity} Mbps')
print('PASS: persisted admission reason identifies the B2 conflict-check bypass')
conn.close()
PY

for intent_id in INT-921 INT-922; do
    cookie=$(python3.9 -c "from src.enforcement.flowmod import cookie_for_intent; print(hex(cookie_for_intent('${intent_id}')))" )
    if ! sudo ovs-ofctl -O OpenFlow13 dump-flows s1 | grep -q "cookie=${cookie}.*priority=100"; then
        echo "FAIL: ${intent_id} rules were not installed on s1"
        exit 1
    fi
done
echo "PASS: both B2 intent rules are installed on OVS"
echo "=== B2 LIVE NO-CONFLICT-CHECK BASELINE PASSED ==="
