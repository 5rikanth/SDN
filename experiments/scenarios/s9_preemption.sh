#!/usr/bin/env bash
# ST-9 live check: a critical reservation displaces a lower-priority intent.
set -euo pipefail

RYU_PID=""
API_PID=""
TOPO_PID=""
LOG_DIR="/var/log/team16"

cleanup() {
    for pid in "${TOPO_PID}" "${API_PID}" "${RYU_PID}"; do
        if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
            kill "${pid}" 2>/dev/null || true
            wait "${pid}" 2>/dev/null || true
        fi
    done
    timeout 15s python3.9 /usr/local/bin/mn -c >/dev/null 2>&1 || true
    # Do not let this demonstration's active reservation bias later scenarios.
    python3.9 - <<'PY'
from src.common import db
conn = db.connect()
db.init_db(conn)
conn.execute("DELETE FROM intents WHERE id IN ('INT-901', 'INT-902')")
conn.commit()
conn.close()
PY
}
trap cleanup EXIT INT TERM

mkdir -p "${LOG_DIR}" /var/run/team16
timeout 15s python3.9 /usr/local/bin/mn -c >/dev/null 2>&1 || true
python3.9 - <<'PY'
from src.common import db
conn = db.connect()
db.init_db(conn)
conn.execute("DELETE FROM intents WHERE id IN ('INT-901', 'INT-902')")
conn.commit()
conn.close()
PY

echo "=== Starting Ryu and the intent API ==="
ryu-manager --ofp-tcp-listen-port 6653 src/enforcement/controller.py >"${LOG_DIR}/ryu-st9.log" 2>&1 &
RYU_PID=$!
sleep 2
python3.9 -m uvicorn src.intent_manager.api:app --host 127.0.0.1 --port 8000 >"${LOG_DIR}/api-st9.log" 2>&1 &
API_PID=$!
sleep 2

echo "=== Starting Mininet topology ==="
python3.9 - <<'PY' >"${LOG_DIR}/topo-st9.log" 2>&1 &
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
    local target="$2"
    local http_code
    http_code=$(curl -s -o "${target}" -w "%{http_code}" -X POST \
        http://127.0.0.1:8000/intent -H "Content-Type: text/plain" \
        --data-binary "@${file}")
    if [ "${http_code}" != "201" ]; then
        echo "FAIL: ${file} returned HTTP ${http_code}"
        cat "${target}" || true
        exit 1
    fi
}

echo "=== Reserving 80 Mbps for lower-priority INT-901 ==="
submit_intent experiments/scenarios/fixtures/s9_preemptible.yaml /tmp/s9_victim.json
for attempt in $(seq 1 20); do
    VICTIM_STATE=$(python3.9 -c "from src.common import db; c=db.connect(); print(db.load_intent(c,'INT-901').state.value); c.close()")
    [ "${VICTIM_STATE}" = "active" ] && break
    sleep 0.5
done
if [ "${VICTIM_STATE}" != "active" ]; then
    echo "FAIL: lower-priority reservation never became active (state=${VICTIM_STATE})"
    exit 1
fi
VICTIM_COOKIE=$(python3.9 -c "from src.enforcement.flowmod import cookie_for_intent; print(hex(cookie_for_intent('INT-901')))")
if ! sudo ovs-ofctl -O OpenFlow13 dump-flows s1 | grep -q "cookie=${VICTIM_COOKIE}.*priority=100"; then
    echo "FAIL: lower-priority flow rule was not installed"
    exit 1
fi

echo "=== Requesting 60 Mbps for higher-priority INT-902 ==="
submit_intent experiments/scenarios/fixtures/s9_critical.yaml /tmp/s9_critical.json
for attempt in $(seq 1 30); do
    CRITICAL_STATE=$(python3.9 -c "from src.common import db; c=db.connect(); print(db.load_intent(c,'INT-902').state.value); c.close()")
    [ "${CRITICAL_STATE}" = "active" ] && break
    sleep 0.5
done
if [ "${CRITICAL_STATE}" != "active" ]; then
    echo "FAIL: critical intent never became active (state=${CRITICAL_STATE})"
    exit 1
fi

python3.9 - <<'PY'
from src.common import db
conn = db.connect()
victim = db.load_intent(conn, 'INT-901')
critical = db.load_intent(conn, 'INT-902')
path = db.current_path(conn, 'INT-902')
events = db.events_for(conn, 'INT-901')
assert victim.state.value == 'preempted', victim.state
assert critical.state.value == 'active', critical.state
assert path is not None and path.switches == ('s1', 's2', 's3', 's7'), path
assert any(event.type.value == 'intent.preempted' for event in events), events
print('PASS: critical intent active on', ' -> '.join(path.switches))
print('PASS: victim state and persisted intent.preempted event verified')
conn.close()
PY

CRITICAL_COOKIE=$(python3.9 -c "from src.enforcement.flowmod import cookie_for_intent; print(hex(cookie_for_intent('INT-902')))")
for attempt in $(seq 1 20); do
    FLOWS=$(sudo ovs-ofctl -O OpenFlow13 dump-flows s1)
    if ! echo "${FLOWS}" | grep -q "cookie=${VICTIM_COOKIE}.*priority=100" \
        && echo "${FLOWS}" | grep -q "cookie=${CRITICAL_COOKIE}.*priority=100"; then
        break
    fi
    sleep 0.5
done
if echo "${FLOWS}" | grep -q "cookie=${VICTIM_COOKIE}.*priority=100"; then
    echo "FAIL: preempted INT-901 flow rules remain installed"
    exit 1
fi
if ! echo "${FLOWS}" | grep -q "cookie=${CRITICAL_COOKIE}.*priority=100"; then
    echo "FAIL: INT-902 flow rules were not installed after preemption"
    exit 1
fi
echo "PASS: victim rules removed before critical intent rules are active"
echo "=== ST-9 LIVE PREEMPTION CHECK PASSED ==="
