"""Aggregate trace files into a quality summary. Phase 8 deliverable.

Reads JSONL trace files written by `src.observability.tracer` (one file per
trip) and prints critic-pass rate, mean cost, mean wall time, and a histogram
of the most common critic-failure rules across all trips.

Usage:
    python scripts/quality_report.py                  # defaults to ./traces
    python scripts/quality_report.py path/to/traces   # custom dir
    python scripts/quality_report.py --json           # machine-readable
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional


@dataclass
class TripRecord:
    """One trip distilled from a single .jsonl trace file."""

    trip_id: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    wall_ms: Optional[int] = None
    critic_passed: Optional[bool] = None
    critic_revisions: int = 0
    critic_violations: list[dict[str, str]] = field(default_factory=list)
    itinerary_built: bool = False
    has_error_span: bool = False
    has_summary: bool = False


def load_trip(path: Path) -> Optional[TripRecord]:
    """Parse one trace file into a TripRecord. Returns None if the file is empty."""
    record: Optional[TripRecord] = None
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record is None:
                    record = TripRecord(trip_id=event.get("trip_id") or path.stem)

                if event.get("event") == "span_error":
                    record.has_error_span = True

                if event.get("event") == "trip_summary":
                    record.has_summary = True
                    payload = event.get("payload") or {}
                    record.wall_ms = payload.get("wall_ms")
                    totals = payload.get("totals") or {}
                    record.cost_usd = float(totals.get("cost_usd") or 0.0)
                    record.input_tokens = int(totals.get("input_tokens") or 0)
                    record.output_tokens = int(totals.get("output_tokens") or 0)
                    record.critic_passed = payload.get("critic_passed")
                    record.critic_revisions = int(payload.get("critic_revisions") or 0)
                    record.critic_violations = list(
                        payload.get("critic_violations") or []
                    )
                    record.itinerary_built = bool(payload.get("itinerary_built"))
    except OSError:
        return None
    return record


def load_trips(trace_dir: Path) -> Iterator[TripRecord]:
    if not trace_dir.exists():
        return
    for path in sorted(trace_dir.glob("*.jsonl")):
        record = load_trip(path)
        if record is not None:
            yield record


def build_report(trips: Iterable[TripRecord]) -> dict[str, Any]:
    trips = list(trips)
    summarized = [t for t in trips if t.has_summary]
    judged = [t for t in summarized if t.critic_passed is not None]
    passed = [t for t in judged if t.critic_passed]

    costs = [t.cost_usd for t in summarized if t.cost_usd]
    walls = [t.wall_ms for t in summarized if t.wall_ms is not None]

    rule_counter: Counter[str] = Counter()
    agent_counter: Counter[str] = Counter()
    for trip in summarized:
        if trip.critic_passed is False:
            for v in trip.critic_violations:
                if v.get("severity") == "fail":
                    rule_counter[v.get("rule", "unknown")] += 1
                    agent_counter[v.get("agent", "unknown")] += 1

    revisions = [t.critic_revisions for t in summarized]

    return {
        "trips_total": len(trips),
        "trips_with_summary": len(summarized),
        "trips_judged": len(judged),
        "critic_pass_rate": (len(passed) / len(judged)) if judged else None,
        "trips_with_error_span": sum(1 for t in trips if t.has_error_span),
        "cost_usd": {
            "total": sum(costs),
            "mean": statistics.fmean(costs) if costs else None,
            "p50": statistics.median(costs) if costs else None,
            "max": max(costs) if costs else None,
        },
        "wall_ms": {
            "mean": statistics.fmean(walls) if walls else None,
            "p50": statistics.median(walls) if walls else None,
            "max": max(walls) if walls else None,
        },
        "revisions": {
            "mean": statistics.fmean(revisions) if revisions else None,
            "max": max(revisions) if revisions else None,
            "rev0_pass": sum(
                1 for t in summarized
                if t.critic_passed and t.critic_revisions == 0
            ),
        },
        "top_failure_rules": rule_counter.most_common(5),
        "top_failure_agents": agent_counter.most_common(5),
    }


def render_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("Trip Planner - Quality Report")
    lines.append("=" * 60)
    lines.append(f"Trips found:           {report['trips_total']}")
    lines.append(f"Trips with summary:    {report['trips_with_summary']}")
    lines.append(f"Trips with error span: {report['trips_with_error_span']}")
    lines.append("")

    rate = report["critic_pass_rate"]
    rate_str = f"{rate * 100:.1f}%" if rate is not None else "n/a"
    lines.append(
        f"Critic pass rate:      {rate_str}   "
        f"({report['trips_judged']} judged)"
    )
    rev = report["revisions"]
    if rev["mean"] is not None:
        lines.append(
            f"  pass on rev 0:       {rev['rev0_pass']}   "
            f"(mean revisions: {rev['mean']:.2f}, max: {rev['max']})"
        )
    lines.append("")

    cost = report["cost_usd"]
    if cost["mean"] is not None:
        lines.append(
            f"Cost / run (USD):      mean ${cost['mean']:.6f}   "
            f"p50 ${cost['p50']:.6f}   max ${cost['max']:.6f}"
        )
        lines.append(f"Total spend:           ${cost['total']:.6f}")
    else:
        lines.append("Cost / run (USD):      no cost data")

    wall = report["wall_ms"]
    if wall["mean"] is not None:
        lines.append(
            f"Wall time (ms):        mean {wall['mean']:.0f}   "
            f"p50 {wall['p50']:.0f}   max {wall['max']}"
        )
    lines.append("")

    if report["top_failure_rules"]:
        lines.append("Top failing rules:")
        for rule, count in report["top_failure_rules"]:
            lines.append(f"  - {rule}: {count}")
    else:
        lines.append("Top failing rules:     (none)")

    if report["top_failure_agents"]:
        lines.append("Top guilty agents:")
        for agent, count in report["top_failure_agents"]:
            lines.append(f"  - {agent}: {count}")

    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "trace_dir",
        nargs="?",
        default="traces",
        help="directory containing *.jsonl trace files (default: ./traces)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print machine-readable JSON instead of the text summary",
    )
    args = parser.parse_args(argv)

    trace_dir = Path(args.trace_dir)
    trips = list(load_trips(trace_dir))
    report = build_report(trips)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
