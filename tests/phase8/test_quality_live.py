"""Phase 8 live test — Japan example must produce a critic-passing plan
and emit a `trip_summary` event with cost/latency/verdict captured.

Gated by @pytest.mark.live; auto-skipped unless RUN_LIVE=1. This is the
quality gate referenced in implementation.md §8 acceptance criteria.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.orchestrator import plan_trip

pytestmark = pytest.mark.live


def _summary_event(trace_path: Path) -> dict:
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("event") == "trip_summary":
            return event
    raise AssertionError(f"no trip_summary event in {trace_path}")


def test_japan_critic_pass_and_summary_emitted(
    sample_requests: dict, tmp_trace_dir: Path
) -> None:
    fixture = sample_requests["japan_happy_path"]
    ctx = plan_trip(fixture["request"])

    assert ctx.brief is not None
    assert ctx.itinerary is not None
    assert ctx.critic_verdicts, "critic must produce at least one verdict"

    final = ctx.critic_verdicts[-1]
    # Acceptance: Japan example passes critic on first or second round in ≥80% of runs.
    # A single live run can flake; we treat critic_passed=True as the win and
    # tolerate a graceful failure as long as the summary is well-formed.
    assert final.passed, (
        f"Japan example failed critic after {len(ctx.critic_verdicts)} rev(s); "
        f"violations: {[v.message for v in final.violations]}"
    )

    summary = _summary_event(Path(ctx.tracer.path))
    payload = summary["payload"]
    assert payload["critic_passed"] is True
    assert payload["itinerary_built"] is True
    assert payload["wall_ms"] > 0
    assert payload["totals"]["cost_usd"] >= 0.0
    assert payload["totals"]["input_tokens"] > 0
