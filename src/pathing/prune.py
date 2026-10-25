"""
M3 constraint pruning and path selection.

Implements prune() to filter candidate paths according to intent guarantees,
and select_best() to choose the optimal surviving path deterministically.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import networkx as nx

from src.common.models import Guarantee, PathPlan


def prune(
    paths: List[List[str]],
    guarantee: Guarantee,
    graph: nx.Graph,
    tenant_paths: Optional[Dict[str, List[List[str]]]] = None,
) -> Tuple[List[List[str]], Dict[str, str]]:
    """
    Filter candidate paths by guarantee constraints in strict order:
      1. avoid_links: reject if path uses a named link.
      2. max_latency_ms: reject if summed edge delay_ms exceeds it.
      3. min_bandwidth_mbps: reject if the minimum edge bw_mbps is lower.
      4. isolate_from: reject if the path shares ANY link with a path used by a listed tenant.

    max_loss_pct is ignored (loss is measured, not predicted).

    Returns:
      surviving_paths, rejection_reasons
      where rejection_reasons maps "-".join(path) to a plain-English explanation.
    """
    surviving: List[List[str]] = []
    reasons: Dict[str, str] = {}

    for path in paths:
        path_key = "-".join(path)
        canonical_links = set(PathPlan.links_for(path))

        # 1. avoid_links
        if guarantee.avoid_links:
            violated_avoid = False
            for link in guarantee.avoid_links:
                if link in canonical_links:
                    reasons[path_key] = f"uses avoided link: {link}"
                    violated_avoid = True
                    break
            if violated_avoid:
                continue

        # 2. max_latency_ms
        if guarantee.max_latency_ms is not None:
            total_delay = 0.0
            for u, v in zip(path, path[1:]):
                edge_data = graph.get_edge_data(u, v, default={})
                total_delay += float(edge_data.get("delay_ms", 0.0))
            if total_delay > guarantee.max_latency_ms:
                reasons[path_key] = (
                    f"exceeds max_latency_ms: {total_delay:g} > {guarantee.max_latency_ms:g}"
                )
                continue

        # 3. min_bandwidth_mbps
        if guarantee.min_bandwidth_mbps is not None:
            bottleneck_bw = float("inf")
            for u, v in zip(path, path[1:]):
                edge_data = graph.get_edge_data(u, v, default={})
                bw = float(edge_data.get("residual_mbps", edge_data.get("bw_mbps", 0.0)))
                if bw < bottleneck_bw:
                    bottleneck_bw = bw
            if bottleneck_bw < guarantee.min_bandwidth_mbps:
                reasons[path_key] = (
                    f"insufficient bandwidth: {bottleneck_bw:g} < {guarantee.min_bandwidth_mbps:g}"
                )
                continue

        # 4. isolate_from
        if guarantee.isolate_from and tenant_paths:
            conflict_tenant = None
            conflict_link = None
            for tenant in guarantee.isolate_from:
                other_paths = tenant_paths.get(tenant, [])
                for op in other_paths:
                    op_links = set(PathPlan.links_for(op))
                    shared = canonical_links & op_links
                    if shared:
                        conflict_tenant = tenant
                        conflict_link = sorted(shared)[0]
                        break
                if conflict_tenant:
                    break

            if conflict_tenant:
                reasons[path_key] = (
                    f"shares link {conflict_link} with isolated tenant {conflict_tenant}"
                )
                continue

        surviving.append(path)

    return surviving, reasons


def select_best(paths: List[List[str]], graph: nx.Graph) -> Optional[List[str]]:
    """
    Select the optimal path deterministically.

    Ranking criteria:
      1. Highest bottleneck bandwidth (spare capacity preference) -> (-bottleneck)
      2. Lower total delay -> (+total_delay)
      3. Fewer hops -> (+len(path))
      4. Lexicographic order of canonical links -> tuple(PathPlan.links_for(path))
    """
    if not paths:
        return None

    def score_path(path: List[str]):
        bottleneck_bw = float("inf")
        total_delay = 0.0
        for u, v in zip(path, path[1:]):
            edge_data = graph.get_edge_data(u, v, default={})
            bw = float(edge_data.get("residual_mbps", edge_data.get("bw_mbps", 0.0)))
            if bw < bottleneck_bw:
                bottleneck_bw = bw
            total_delay += float(edge_data.get("delay_ms", 0.0))

        canonical_links = PathPlan.links_for(path)
        # Note: bottleneck_bw is negated so max bottleneck comes first
        return (-bottleneck_bw, total_delay, len(path), canonical_links)

    return min(paths, key=score_path)
