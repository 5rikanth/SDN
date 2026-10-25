#!/usr/bin/env python3
"""Plot only measurements actually present in an evaluation summary."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _summary_rows(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8") as stream:
        return {row["metric"]: row for row in csv.DictReader(stream)}


def plot_b0_metric(rows: dict, metric: str, title: str, ylabel: str, output: Path) -> None:
    row = rows.get(metric)
    if row is None:
        return
    mean = float(row["mean"])
    low = float(row["ci95_low"])
    high = float(row["ci95_high"])
    lower_error = max(0.0, mean - low)
    upper_error = max(0.0, high - mean)

    fig, ax = plt.subplots(figsize=(6.4, 4.0), constrained_layout=True)
    ax.bar(["B0"], [mean], color="#147d79", width=0.5)
    ax.errorbar(
        ["B0"],
        [mean],
        yerr=[[lower_error], [upper_error]],
        fmt="none",
        ecolor="#243746",
        capsize=6,
        linewidth=1.4,
    )
    ax.set_title(f"{title} (n={row['runs']}, 95% Student t CI)")
    ax.set_ylabel(ylabel)
    ax.set_ylim(0, max(high * 1.22, 0.1))
    ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(output, dpi=180)
    plt.close(fig)


def plot_flow_pilot(path: Path, output: Path) -> None:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or not {"limit", "requested_rules", "installed_rules"}.issubset(rows[0]):
        raise ValueError(f"{path} is not a flow-table pilot CSV")

    labels = [row["limit"] for row in rows]
    installed = [int(row["installed_rules"]) for row in rows]
    requested = max(int(row["requested_rules"]) for row in rows)
    positions = list(range(len(rows)))

    fig, ax = plt.subplots(figsize=(7.0, 4.0), constrained_layout=True)
    ax.bar(positions, installed, color="#147d79", label="Rules retained")
    ax.axhline(requested, color="#c56a27", linestyle="--", linewidth=1.4, label="Requested")
    ax.set_xticks(positions, labels)
    ax.set_xlabel("Configured OVS flow limit")
    ax.set_ylabel("Installed dummy rules")
    ax.set_ylim(0, requested * 1.18)
    ax.set_title("OVS 2.17.12 flow-table cap pilot")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.0), ncol=2, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(output, dpi=180)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--suffix", required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows = _summary_rows(args.summary)
    plot_b0_metric(
        rows,
        "rtt_avg",
        "B0 mean round-trip latency",
        "RTT (ms)",
        args.out_dir / f"b0_rtt_avg_{args.suffix}.png",
    )
    plot_b0_metric(
        rows,
        "throughput",
        "B0 mean throughput",
        "Throughput (Mbits/s)",
        args.out_dir / f"b0_throughput_{args.suffix}.png",
    )
    plot_flow_pilot(
        args.pilot,
        args.out_dir / f"e5_flow_limit_{args.suffix}.png",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
