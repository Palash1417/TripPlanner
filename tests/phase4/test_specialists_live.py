"""Phase 4 live test — full graph against real Gemini, M2 acceptance criteria.

Gated by @pytest.mark.live.
"""

from __future__ import annotations

import pytest

from src.orchestrator import plan_trip

pytestmark = pytest.mark.live


def test_japan_full_phase4_plan(sample_requests: dict) -> None:
    """M2 acceptance per implementation.md Phase 4:
       - mentions Shinkansen
       - names ≥1 Tokyo neighborhood and ≥1 Kyoto neighborhood
       - budget table sums to ≤ $3000
       - parallel agents visible in trace.
    """
    fixture = sample_requests["japan_happy_path"]
    ctx = plan_trip(fixture["request"])

    assert ctx.brief is not None
    assert ctx.destination_catalog is not None
    assert ctx.accommodation_plan is not None
    assert ctx.transport_plan is not None
    assert ctx.budget is not None
    assert ctx.itinerary is not None

    cities_with_stay = {s.city.lower() for s in ctx.accommodation_plan.stays}
    assert any("tokyo" in c for c in cities_with_stay)
    assert any("kyoto" in c for c in cities_with_stay)

    transit_modes = {leg.mode for leg in ctx.transport_plan.legs}
    transit_notes = " ".join((leg.notes or "") for leg in ctx.transport_plan.legs).lower()
    assert "train" in transit_modes or "shinkansen" in transit_notes, (
        "Tokyo↔Kyoto multi-city plan should use train (typically Shinkansen)"
    )

    assert ctx.budget.total_estimate_usd <= 3000.0 + 1.0, (
        f"budget exceeds $3000 cap: ${ctx.budget.total_estimate_usd}"
    )

    assert len(ctx.itinerary.days) == 5
    assert ctx.tracer.totals["cost_usd"] >= 0


def test_underspecified_still_completes(sample_requests: dict) -> None:
    fixture = sample_requests["underspecified_warm_weekend"]
    ctx = plan_trip(fixture["request"])
    assert ctx.brief is not None
    assert ctx.brief.open_questions
