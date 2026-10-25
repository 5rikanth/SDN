#!/usr/bin/env python3
"""Driver for experiment scripts.
Provides two subcommands:
  baseline --runs N   Run the B0 baseline measurement.
  flowpilot           Run the flow‑table pressure pilot.
Both commands print CSV rows to stdout with columns:
run_id,timestamp,metric,value,unit (or appropriate for flowpilot).
"""
import argparse
import csv
import re
import sys
import time

import networkx as nx
from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.link import TCLink
from topology.team16_topo import Team16Topo
from topology.topology_spec import HOSTS, build_graph


def _attached_switch(host_name):
    """Return the access switch declared for a topology host."""
    return next(host.switch for host in HOSTS if host.name == host_name)


def _port_toward(net, switch_name, neighbor_name):
    """Read the live OpenFlow port joining a switch to one neighbor."""
    switch = net.get(switch_name)
    neighbor = net.get(neighbor_name)
    interface = switch.connectionsTo(neighbor)[0][0]
    return switch.ports[interface]


def set_static_forwarding(net, src_host="h1", dst_host="h2"):
    """Install a loop-free bidirectional path without a controller."""
    for sw in net.switches:
        sw.cmd(f"ovs-vsctl set-fail-mode {sw.name} secure")
        sw.cmd(f"ovs-ofctl -O OpenFlow13 del-flows {sw.name}")

    graph = build_graph()
    route = nx.shortest_path(
        graph,
        _attached_switch(src_host),
        _attached_switch(dst_host),
        weight="delay_ms",
    )
    nodes = [src_host, *route, dst_host]
    for index, switch_name in enumerate(route, start=1):
        left_port = _port_toward(net, switch_name, nodes[index - 1])
        right_port = _port_toward(net, switch_name, nodes[index + 1])
        switch = net.get(switch_name)
        switch.cmd(
            f"ovs-ofctl -O OpenFlow13 add-flow {switch_name} "
            f"priority=10,in_port={left_port},actions=output:{right_port}"
        )
        switch.cmd(
            f"ovs-ofctl -O OpenFlow13 add-flow {switch_name} "
            f"priority=10,in_port={right_port},actions=output:{left_port}"
        )

def parse_ping(ping_output):
    """Extract numeric loss and RTT values from iputils ping output."""
    loss_match = re.search(r"([0-9]+(?:\.[0-9]+)?)%\s+packet loss", ping_output)
    loss = loss_match.group(1) if loss_match else "0"
    stats_match = re.search(
        r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = "
        r"([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+)",
        ping_output,
    )
    rtt_vals = stats_match.groups() if stats_match else ("0", "0", "0", "0")
    return loss, rtt_vals


def parse_iperf_mbps(iperf_output):
    """Return the final numeric Mbits/sec result from iperf3 text output."""
    values = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s+Mbits/sec", iperf_output)
    return values[-1] if values else "0"

def baseline(runs):
    net = Mininet(topo=Team16Topo(), switch=OVSSwitch, link=TCLink, controller=None)
    net.start()
    set_static_forwarding(net)
    h1 = net.get('h1')
    h2 = net.get('h2')
    for i in range(1, runs+1):
        ts = int(time.time())
        # ping
        ping_out = h1.cmd('ping -c 100 -i 0.1 10.0.0.2')
        loss, (rtt_min, rtt_avg, rtt_max, rtt_mdev) = parse_ping(ping_out)
        # iperf3 server in background
        h2.cmd('iperf3 -s -1 -D')
        time.sleep(1)
        iperf_out = h1.cmd('iperf3 -c 10.0.0.2 -t 30 -f m')
        thr = parse_iperf_mbps(iperf_out)
        # output csv rows
        writer = csv.writer(sys.stdout)
        writer.writerow([i, ts, 'loss', loss, '%'])
        writer.writerow([i, ts, 'rtt_min', rtt_min, 'ms'])
        writer.writerow([i, ts, 'rtt_avg', rtt_avg, 'ms'])
        writer.writerow([i, ts, 'rtt_max', rtt_max, 'ms'])
        writer.writerow([i, ts, 'rtt_mdev', rtt_mdev, 'ms'])
        writer.writerow([i, ts, 'throughput', thr, 'Mbits/s'])
    net.stop()

def flowpilot():
    net = Mininet(topo=Team16Topo(), switch=OVSSwitch, link=TCLink, controller=None)
    net.start()
    set_static_forwarding(net)
    s1 = net.get('s1')
    limits = ["100", "500", "1000", "2000", "5000", "unlimited"]
    requested = 2000
    enforceable = False
    writer = csv.writer(sys.stdout)
    writer.writerow(['limit','requested_rules','installed_rules','rejected','install_seconds'])
    for lim in limits:
        # Clear the previous batch and configure the documented bridge cap.
        s1.cmd(f"ovs-ofctl -O OpenFlow13 del-flows {s1.name}")
        if lim != "unlimited":
            s1.cmd(f"ovs-vsctl set bridge {s1.name} other-config:flow-limit={lim}")
        else:
            s1.cmd(f"ovs-vsctl clear bridge {s1.name} other-config:flow-limit")

        # Distinct matches are essential: OVS replaces duplicate flows, which
        # would otherwise make the requested rule count meaningless.
        flow_file = '/tmp/dummy_flows.txt'
        with open(flow_file, 'w') as f:
            for index in range(requested):
                address = f"10.{(index // 65536) + 1}.{(index // 256) % 256}.{index % 256}"
                f.write(f'priority=1,ip,nw_src={address},actions=drop\n')

        before = s1.cmd(
            f"ovs-ofctl -O OpenFlow13 dump-flows {s1.name}"
        )
        before_count = sum(line.lstrip().startswith("cookie=") for line in before.splitlines())
        start = time.time()
        result = s1.cmd(f"ovs-ofctl -O OpenFlow13 add-flows {s1.name} {flow_file} 2>&1")
        end = time.time()
        after = s1.cmd(f"ovs-ofctl -O OpenFlow13 dump-flows {s1.name}")
        after_count = sum(line.lstrip().startswith("cookie=") for line in after.splitlines())
        installed = max(0, after_count - before_count)
        rejected = 'yes' if (
            'error' in result.lower() or 'failed' in result.lower()
        ) else 'no'
        install_secs = f"{end - start:.3f}"
        writer.writerow([lim, requested, installed, rejected, install_secs])
        if lim != "unlimited" and installed < requested:
            enforceable = True
    s1.cmd(f"ovs-ofctl -O OpenFlow13 del-flows {s1.name}")
    s1.cmd(f"ovs-vsctl clear bridge {s1.name} other-config:flow-limit")
    # Keep the raw file valid CSV; the shell wrapper replays this verdict.
    verdict = ("VERDICT: flow-table limit is enforceable - metric E5 is viable"
               if enforceable else
               "VERDICT: flow-table limit had no effect - metric E5 will be flat, see ADR-003")
    print(verdict, file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(description='Experiment driver')
    subparsers = parser.add_subparsers(dest='cmd')
    baseline_parser = subparsers.add_parser('baseline', help='Run B0 baseline')
    baseline_parser.add_argument('--runs', type=int, default=10, help='Number of runs')
    subparsers.add_parser('flowpilot', help='Run flow‑table pressure pilot')
    args = parser.parse_args()
    if args.cmd == 'baseline':
        baseline(args.runs)
    elif args.cmd == 'flowpilot':
        flowpilot()
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == '__main__':
    main()
