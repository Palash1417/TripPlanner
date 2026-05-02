"""Phase 7 live test — full graph via the orchestrator split.

Gated by @pytest.mark.live; auto-skipped unless RUN_LIVE=1.
"""

from __future__ import annotations

import pytest

from src.orchestrator import TripContext, complete_plan, run_intent

pytestmark = pytest.mark.live


def test_underspecified_request_yields_open_questions(sample_requests: dict) -> None:
    """Acceptance — underspecified fixture must surface clarifying questions."""
    fixture = sample_requests["underspecified_warm_weekend"]
    ctx = TripContext(user_request=fixture["request"])
    run_intent(ctx)
    assert ctx.brief is not None
    assert ctx.brief.open_questions, "expected non-empty open_questions"


def test_orchestrator_split_runs_end_to_end(sample_requests: dict) -> None:
    """run_intent → (no clarification) → complete_plan should produce a full plan."""
    fixture = sample_requests["japan_happy_path"]
    ctx = TripContext(user_request=fixture["request"])
    run_intent(ctx)
    assert ctx.brief is not None
    complete_plan(ctx)
    assert ctx.itinerary is not None
    assert ctx.critic_verdicts
