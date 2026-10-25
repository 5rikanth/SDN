#!/usr/bin/env bash
# ST-10: exercise a strict route change while Mininet, OVS and Ryu are live.
set -euo pipefail

LOG_DIR="/var/log/team16"
mkdir -p "${LOG_DIR}" /var/run/team16

clean_mininet() {
    timeout 15s python3.9 /usr/local/bin/mn -c >/dev/null 2>&1 || true
    for switch in s1 s2 s3 s4 s5 s6 s7; do
        if sudo ovs-dpctl show ovs-system 2>/dev/null \
            | grep -Eq "port [0-9]+: ${switch} \\(internal\\)"; then
            sudo ovs-dpctl del-if ovs-system "${switch}" >/dev/null 2>&1 || true
        fi
    done
}

RYU_PID=""
API_PID=""
TOPO_PID=""
ECHO_PID=""
cleanup() {
    for pid in "${ECHO_PID}" "${TOPO_PID}" "${API_PID}" "${RYU_PID}"; do
        if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
            kill "${pid}" 2>/dev/null || true
        fi
    done
    clean_mininet
}
trap cleanup EXIT INT TERM

clean_mininet
python3.9 -c "from src.common import db; c=db.connect(); db.init_db(c); c.execute('DELETE FROM intents WHERE id = ?', ('INT-001',)); c.commit(); c.close()"

ryu-manager --ofp-tcp-listen-port 6653 src/enforcement/controller.py >"${LOG_DIR}/ryu.log" 2>&1 &
RYU_PID=$!
sleep 2
python3.9 -m uvicorn src.intent_manager.api:app --host 127.0.0.1 --port 8000 >"${LOG_DIR}/api.log" 2>&1 &
API_PID=$!
sleep 2
python3.9 -c "
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from pathlib import Path
import time
from topology.team16_topo import Team16Topo
net = Mininet(topo=Team16Topo(), switch=OVSSwitch, link=TCLink,
              controller=None, autoSetMacs=True, autoStaticArp=True)
net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
net.start()
pid_dir = Path('/var/run/team16')
for host in net.hosts:
    (pid_dir / f'{host.name}.pid').write_text(str(host.pid))
time.sleep(300)
net.stop()
" >"${LOG_DIR}/s10_topo.log" 2>&1 &
TOPO_PID=$!
sleep 4

HTTP_CODE=$(curl -s -o /tmp/s10_intent.json -w "%{http_code}" -X POST \
  http://127.0.0.1:8000/intent -H "Content-Type: text/plain" \
  --data-binary @intents/s1_urllc_baseline.yaml)
if [ "${HTTP_CODE}" != "201" ]; then
    cat /tmp/s10_intent.json
    echo "FAIL: intent submission returned HTTP ${HTTP_CODE}"
    exit 1
fi

# Wait for the initial strict version (VLAN 100) to appear on the north route.
for _ in $(seq 1 30); do
    if sudo ovs-ofctl -O OpenFlow13 dump-flows s2 | grep -q "dl_vlan=100\|vlan_vid=0x1064"; then break; fi
    sleep 0.2
done
if ! sudo ovs-ofctl -O OpenFlow13 dump-flows s2 | grep -q "dl_vlan=100\|vlan_vid=0x1064"; then
    echo "FAIL: initial version 0 was not installed on s2"
    exit 1
fi

# Persist a south route as a new strict revision; Ryu's DB sync performs the
# staged install, classifier flip, drain, then old-version cleanup.
python3.9 - <<'PY'
import time
from src.common import db
from src.common.models import PathPlan
conn = db.connect()
db.init_db(conn)
plan = PathPlan(
    intent_id="INT-001",
    switches=("s1", "s4", "s5", "s7"),
    links=PathPlan.links_for(["s1", "s4", "s5", "s7"]),
    est_latency_ms=15.0,
    bottleneck_mbps=50.0,
    version=1,
    computed_at=time.time() + 1.0,
)
db.save_path(conn, plan)
conn.close()
PY

UPDATED=0
for _ in $(seq 1 50); do
    S4_FLOWS=$(sudo ovs-ofctl -O OpenFlow13 dump-flows s4)
    S2_FLOWS=$(sudo ovs-ofctl -O OpenFlow13 dump-flows s2)
    if echo "${S4_FLOWS}" | grep -q "dl_vlan=101\|vlan_vid=0x1065" \
       && ! echo "${S2_FLOWS}" | grep -q "dl_vlan=100\|vlan_vid=0x1064"; then
        UPDATED=1
        break
    fi
    sleep 0.2
done
if [ "${UPDATED}" -ne 1 ]; then
    echo "FAIL: version 1 did not replace the old north route"
    sudo ovs-ofctl -O OpenFlow13 dump-flows s2
    sudo ovs-ofctl -O OpenFlow13 dump-flows s4
    exit 1
fi

H1_PID=$(cat /var/run/team16/h1.pid)
H2_PID=$(cat /var/run/team16/h2.pid)
mnexec -da "${H2_PID}" python3.9 -u -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(('10.0.0.2',5001)); exec('while True:\n data,addr=s.recvfrom(64)\n s.sendto(data,addr)')" >"${LOG_DIR}/s10_echo.log" 2>&1 &
ECHO_PID=$!
sleep 0.5
mnexec -da "${H1_PID}" python3.9 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(2); s.sendto(b'st10',('10.0.0.2',5001)); assert s.recvfrom(64)[0] == b'st10'"

echo "PASS: strict update flipped VLAN 100 to VLAN 101, removed old north-path rules, and preserved UDP delivery"
