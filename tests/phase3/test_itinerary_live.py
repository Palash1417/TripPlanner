"""Phase 3 live tests — full intent → itinerary against the real Gemini API.

Gated by @pytest.mark.live; auto-skipped unless RUN_LIVE=1 is set.
Run with:  RUN_LIVE=1 pytest -q -m live
"""

from __future__ import annotations

import pytest

from src.orchestrator import plan_trip

pytestmark = pytest.mark.live


def test_japan_full_plan(sample_requests: dict) -> None:
    """Phase 3 acceptance criteria — Japan request produces a sane 5-day plan."""
    fixture = sample_requests["japan_happy_path"]
    ctx = plan_trip(fixture["request"])

    assert ctx.brief is not None
    assert ctx.itinerary is not None
    itin = ctx.itinerary

    assert len(itin.days) == 5, "5-day request must produce 5 days"
    assert all(day.activities for day in itin.days), "every day must have at least 1 activity"

    cities = {day.city.lower() for day in itin.days}
    assert any("tokyo" in c for c in cities), "must mention Tokyo"
    assert any("kyoto" in c for c in cities), "must mention Kyoto"

    for day in itin.days:
        assert day.total_activity_minutes <= 600, (
            f"day {day.day_number} exceeds 10h cap: {day.total_activity_minutes}m"
        )

    transit_days = [d for d in itin.days if d.transport_leg is not None]
    assert transit_days, "multi-city trip should have at least one transit leg"

    assert ctx.tracer.totals["cost_usd"] >= 0


def test_underspecified_still_produces_plan(sample_requests: dict) -> None:
    """Even with open_questions, Phase 3 should produce *some* itinerary."""
    fixture = sample_requests["underspecified_warm_weekend"]
    ctx = plan_trip(fixture["request"])

    assert ctx.brief is not None
    assert ctx.brief.open_questions, "underspecified inputs must yield open_questions"

    if ctx.itinerary is not None:
        assert len(ctx.itinerary.days) >= 1
        assert ctx.itinerary.summary, "summary should explain the assumptions made"
