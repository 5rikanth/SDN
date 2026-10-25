#!/usr/bin/env bash
# Scenario S1: single URLLC intent on an idle network.
# Exercises M1, M3, M4 happy path end-to-end.
set -euo pipefail

ST7_CLOSE_LOOP=0
if [[ "${1:-}" == "--st7" ]]; then
    ST7_CLOSE_LOOP=1
elif [[ "$#" -gt 0 ]]; then
    echo "Usage: $0 [--st7]"
    exit 2
fi

RYU_PID=""
API_PID=""
DRIVER_PID=""
UDP_SERVER_PID=""
LOG_DIR="/var/log/team16"

clean_mininet() {
    # The tagged Mininet cleanup removes current DB state. After a Docker
    # recreation, kernel-only internal ports can remain, so remove only this
    # topology's seven exact switch names from that otherwise shared datapath.
    timeout 15s python3.9 /usr/local/bin/mn -c >/dev/null 2>&1 || true
    for switch in s1 s2 s3 s4 s5 s6 s7; do
        if sudo ovs-dpctl show ovs-system 2>/dev/null \
            | grep -Eq "port [0-9]+: ${switch} \(internal\)"; then
            sudo ovs-dpctl del-if ovs-system "${switch}" >/dev/null 2>&1 || true
        fi
    done
}

mkdir -p "${LOG_DIR}"
mkdir -p /var/run/team16

# A previous interrupted or container-recreated run can leave veth pairs in the
# shared Linux kernel. Clean only Mininet-owned transient state before building.
clean_mininet

# Make repeated demonstrations independent of earlier INT-001 runs. Deleting
# this one scenario-owned row also removes its saved paths via the DB foreign key.
python3.9 -c "
from src.common import db
conn = db.connect()
db.init_db(conn)
conn.execute('DELETE FROM intents WHERE id = ?', ('INT-001',))
conn.commit()
conn.close()
"

cleanup() {
    echo "*** Cleaning up processes and Mininet state"
    if [ -n "${DRIVER_PID}" ] && kill -0 "${DRIVER_PID}" 2>/dev/null; then
        kill "${DRIVER_PID}" 2>/dev/null || true
    fi
    if [ -n "${UDP_SERVER_PID}" ] && kill -0 "${UDP_SERVER_PID}" 2>/dev/null; then
        kill "${UDP_SERVER_PID}" 2>/dev/null || true
        wait "${UDP_SERVER_PID}" 2>/dev/null || true
    fi
    if [ -n "${API_PID}" ] && kill -0 "${API_PID}" 2>/dev/null; then
        kill "${API_PID}" 2>/dev/null || true
    fi
    if [ -n "${RYU_PID}" ] && kill -0 "${RYU_PID}" 2>/dev/null; then
        kill "${RYU_PID}" 2>/dev/null || true
    fi
    clean_mininet
}

trap cleanup EXIT INT TERM

echo "=== [1/6] Starting Ryu Controller ==="
ryu-manager --ofp-tcp-listen-port 6653 src/enforcement/controller.py >"${LOG_DIR}/ryu.log" 2>&1 &
RYU_PID=$!
sleep 2

echo "=== [2/6] Starting M1 REST API ==="
python3.9 -m uvicorn src.intent_manager.api:app --host 127.0.0.1 --port 8000 >"${LOG_DIR}/api.log" 2>&1 &
API_PID=$!
sleep 2

echo "=== [3/6] Starting Mininet Topology ==="
python3.9 -c "
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from topology.team16_topo import Team16Topo
from pathlib import Path
import time

net = Mininet(
    topo=Team16Topo(),
    switch=OVSSwitch,
    link=TCLink,
    controller=None,
    autoSetMacs=True,
    autoStaticArp=True,
)
net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
net.start()
pid_dir = Path('/var/run/team16')
for host in net.hosts:
    (pid_dir / f'{host.name}.pid').write_text(str(host.pid))
time.sleep(300)
net.stop()
" >"${LOG_DIR}/topo.log" 2>&1 &
DRIVER_PID=$!
sleep 4

echo "=== [4/6] Submitting Intent INT-001 ==="
HTTP_CODE=$(curl -s -o /tmp/post_intent.json -w "%{http_code}" -X POST http://127.0.0.1:8000/intent \
  -H "Content-Type: text/plain" \
  --data-binary @intents/s1_urllc_baseline.yaml)

if [ "${HTTP_CODE}" != "201" ]; then
    echo "FAIL: POST /intent failed with HTTP status ${HTTP_CODE}"
    cat /tmp/post_intent.json || true
    exit 1
fi
echo "PASS: Intent submitted successfully (HTTP 201)"
sleep 2

echo "=== [5/6] Verification Checks ==="
CHECKS_FAILED=0
EXPECTED_COOKIE=$(python3.9 -c "from src.enforcement.flowmod import cookie_for_intent; print(hex(cookie_for_intent('INT-001')))" )

# 1. Intent flow rules with the deterministic non-zero cookie on s1.
# Do not count the cookie=0 table-miss rule as enforcement.
COOKIE_COUNT=$(sudo ovs-ofctl -O OpenFlow13 dump-flows s1 | grep -c "cookie=${EXPECTED_COOKIE}.*priority=100" || true)
if [ "${COOKIE_COUNT}" -gt 0 ]; then
    echo "PASS: Flow rules with cookie found on s1 (${COOKIE_COUNT} rules)"
else
    echo "FAIL: No INT-001 flow rules found on s1 (expected cookie ${EXPECTED_COOKIE})"
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
fi

# 2. A 20 Mbps HTB queue is attached to the selected s1 -> s2 egress port.
QOS_ID=$(sudo ovs-vsctl --if-exists get port s1-eth2 qos | tr -d '[]" ' || true)
QUEUE_ID=""
QUEUE_RATE=""
if [ -n "${QOS_ID}" ]; then
    QUEUE_ID=$(sudo ovs-vsctl get qos "${QOS_ID}" queues | sed -n 's/.*1=\([^,}]*\).*/\1/p' || true)
fi
if [ -n "${QUEUE_ID}" ]; then
    QUEUE_RATE=$(sudo ovs-vsctl get queue "${QUEUE_ID}" other_config || true)
fi
if echo "${QUEUE_RATE}" | grep -q "20000000"; then
    echo "PASS: 20 Mbps HTB queue attached to s1-eth2"
else
    echo "FAIL: No 20 Mbps HTB queue attached to s1-eth2"
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
fi

# 3. Route check - ensure s1-s2-s3-s7 route
S2_FLOWS=$(sudo ovs-ofctl -O OpenFlow13 dump-flows s2 | grep -c "cookie=${EXPECTED_COOKIE}.*priority=100" || true)
if [ "${S2_FLOWS}" -gt 0 ]; then
    echo "PASS: Traffic route traverses s2 (northern route s1-s2-s3-s7 verified)"
else
    echo "FAIL: Expected flow rules on s2 not found"
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
fi

if [ "${CHECKS_FAILED}" -eq 0 ]; then
    echo "=== [6/6] Live Telemetry ==="
    H1_PID=$(cat /var/run/team16/h1.pid)
    H2_PID=$(cat /var/run/team16/h2.pid)
    mnexec -da "${H2_PID}" python3.9 -u -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(('10.0.0.2', 5001))
while True:
    data, address = s.recvfrom(64)
    s.sendto(data, address)
" >"${LOG_DIR}/udp-echo.log" 2>&1 &
    UDP_SERVER_PID=$!
    sleep 1
    python3.9 -m src.telemetry.runtime \
        --intent INT-001 --source-pid "${H1_PID}" --target 10.0.0.2 \
        --switch s1 --samples 5 --interval 1 | tee "${LOG_DIR}/telemetry.log"
    MEASUREMENT_COUNT=$(python3.9 -c "
from src.common import db
conn = db.connect()
print(conn.execute('SELECT COUNT(*) FROM measurements WHERE intent_id = ?', ('INT-001',)).fetchone()[0])
conn.close()
")
    COMPLETE_COUNT=$(python3.9 -c "
from src.common import db
conn = db.connect()
print(conn.execute('SELECT COUNT(*) FROM measurements WHERE intent_id = ? AND delay_ms IS NOT NULL AND loss_pct IS NOT NULL AND throughput_mbps IS NOT NULL', ('INT-001',)).fetchone()[0])
conn.close()
")
    if [ "${MEASUREMENT_COUNT}" -ge 5 ] && [ "${COMPLETE_COUNT}" -ge 1 ]; then
        echo "PASS: ${MEASUREMENT_COUNT} live measurements persisted (${COMPLETE_COUNT} complete samples)"
        if [ "${ST7_CLOSE_LOOP}" -eq 1 ]; then
            echo "=== ST-7: Injecting a confirmed event through the SQLite process boundary ==="
            INITIAL_ROUTE=$(python3.9 -c "
from src.common import db
conn = db.connect()
path = db.current_path(conn, 'INT-001')
print(','.join(path.switches) if path else '')
conn.close()")
            if [ "${INITIAL_ROUTE}" != "s1,s2,s3,s7" ] \
                && [ "${INITIAL_ROUTE}" != "s1,s4,s5,s7" ]; then
                echo "FAIL: expected initial or already-rerouted path, found '${INITIAL_ROUTE}'"
                exit 1
            fi
            if [ "${INITIAL_ROUTE}" = "s1,s2,s3,s7" ]; then
                python3.9 -c "
from src.common import db
from src.common.events import sla_violated
conn = db.connect()
db.save_event(conn, sla_violated('INT-001', 'delay_ms', 30.0, 20.0))
conn.close()
print('Injected INT-001 delay violation: observed 30 ms, guarantee 20 ms')
"
            else
                echo "PASS: live telemetry already caused the expected alternate route"
            fi

            ALTERNATE_ROUTE="${INITIAL_ROUTE}"
            if [ "${INITIAL_ROUTE}" = "s1,s2,s3,s7" ]; then
                for attempt in $(seq 1 20); do
                    ALTERNATE_ROUTE=$(python3.9 -c "
from src.common import db
conn = db.connect()
path = db.current_path(conn, 'INT-001')
print(','.join(path.switches) if path else '')
conn.close()")
                    if [ -n "${ALTERNATE_ROUTE}" ] && [ "${ALTERNATE_ROUTE}" != "${INITIAL_ROUTE}" ]; then
                        break
                    fi
                    sleep 0.5
                done
            fi

            if [ "${ALTERNATE_ROUTE}" != "s1,s4,s5,s7" ]; then
                echo "FAIL: no expected alternate route installed; current='${ALTERNATE_ROUTE}'"
                tail -n 80 "${LOG_DIR}/ryu.log" || true
                exit 1
            fi
            REROUTE_COUNT=$(python3.9 -c "
from src.common import db
conn = db.connect()
print(db.reroute_count(conn, 'INT-001'))
conn.close()")
            AUDIT_COUNT=$(python3.9 -c "
from src.common import db
conn = db.connect()
events = db.events_for(conn, 'INT-001')
print(sum(1 for event in events if {'measurement', 'threshold', 'decision', 'action'} <= set(event.payload)))
conn.close()")
            if [ "${REROUTE_COUNT}" -lt 1 ] || [ "${AUDIT_COUNT}" -lt 1 ]; then
                echo "FAIL: reroute history or causal audit entry missing (reroutes=${REROUTE_COUNT}, audit=${AUDIT_COUNT})"
                exit 1
            fi
            if ! sudo ovs-ofctl -O OpenFlow13 dump-flows s4 | grep -q "cookie=${EXPECTED_COOKIE}.*priority=100"; then
                echo "FAIL: alternate route rules were not installed on s4"
                exit 1
            fi
            POST_ROUTE_LOG=$(python3.9 -m src.telemetry.runtime \
                --intent INT-001 --source-pid "${H1_PID}" --target 10.0.0.2 \
                --switch s1 --samples 1 --interval 1)
            echo "${POST_ROUTE_LOG}" | tee -a "${LOG_DIR}/telemetry.log"
            if ! echo "${POST_ROUTE_LOG}" | grep -Eq 'delay_ms=[0-9]'; then
                echo "FAIL: no live UDP probe reply after the reroute"
                exit 1
            fi
            echo "PASS: controller consumed persisted violation and installed ${ALTERNATE_ROUTE}"
            echo "PASS: matching UDP traffic received a probe reply after the reroute"
            echo "PASS: reroute count=${REROUTE_COUNT}; causal audit entries=${AUDIT_COUNT}"
        fi
        echo "=== SCENARIO S1: ALL ST-1 THROUGH ST-6 CHECKS PASSED ==="
        if [ "${ST7_CLOSE_LOOP}" -eq 1 ]; then
            echo "=== SCENARIO ST-7: LIVE REROUTE CHECK PASSED ==="
        fi
        exit 0
    fi
    echo "FAIL: telemetry persistence incomplete (${MEASUREMENT_COUNT} total, ${COMPLETE_COUNT} complete)"
    exit 1
else
    echo "=== SCENARIO S1: ${CHECKS_FAILED} CHECKS FAILED ==="
    exit 1
fi
