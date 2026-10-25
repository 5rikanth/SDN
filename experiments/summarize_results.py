#!/usr/bin/env python3
"""Summarize repeated baseline CSV measurements with 95% t intervals."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

# Two-sided 95% Student t critical values for degrees of freedom 1–30.
_T95 = (
    0.0, 12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306,
    2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110,
    2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056,
    2.052, 2.048, 2.045, 2.042,
)


def _read_values(path: Path) -> Dict[Tuple[str, str], List[Tuple[int, float]]]:
    grouped: Dict[Tuple[str, str], List[Tuple[int, float]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"run_id", "metric", "value", "unit"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path} is not a run_id/metric/value/unit CSV")
        for row in reader:
            try:
                run_id = int(row["run_id"])
                value = float(row["value"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid numeric measurement in {path}: {row}") from exc
            grouped[(row["metric"], row["unit"])].append((run_id, value))
    return grouped


def summarize(paths: Iterable[Path]) -> List[dict]:
    """Return one summary row per file and metric."""
    output = []
    for path in paths:
        baseline = path.name.split("_", 1)[0].upper()
        for (metric, unit), observations in sorted(_read_values(path).items()):
            by_run: Dict[int, List[float]] = defaultdict(list)
            for run_id, value in observations:
                by_run[run_id].append(value)
            values = [statistics.mean(sample) for sample in by_run.values()]
            n = len(values)
            mean = statistics.mean(values)
            deviation = statistics.stdev(values) if n > 1 else None
            if deviation is None:
                low = high = None
            else:
                critical = _T95[n - 1] if n - 1 < len(_T95) else 1.96
                margin = critical * deviation / math.sqrt(n)
                low, high = mean - margin, mean + margin
            output.append({
                "baseline": baseline,
                "source_file": path.name,
                "metric": metric,
                "unit": unit,
                "runs": n,
                "mean": mean,
                "sample_stddev": deviation,
                "ci95_low": low,
                "ci95_high": high,
            })
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="raw baseline CSV file(s)")
    parser.add_argument("--output", type=Path, required=True, help="summary CSV path")
    args = parser.parse_args()
    summaries = summarize(args.inputs)
    if not summaries:
        raise SystemExit("no measurements found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(summaries[0])
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summaries)
    print(f"Wrote {len(summaries)} metric summaries to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
