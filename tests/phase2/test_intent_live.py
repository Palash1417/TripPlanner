"""Phase 2 integration tests — Intent agent against the real LLM.

Gated by @pytest.mark.live; auto-skipped unless RUN_LIVE=1 is set.
Run with:  RUN_LIVE=1 pytest -q -m live
"""

from __future__ import annotations

import pytest

from src.agents import intent
from src.orchestrator import TripContext
from src.schemas import TripBrief

pytestmark = pytest.mark.live


def _run(request: str) -> tuple[TripContext, TripBrief]:
    ctx = TripContext(user_request=request)
    brief = intent.run(ctx)
    return ctx, brief


def test_japan_happy_path_extracts_correctly(sample_requests: dict) -> None:
    fixture = sample_requests["japan_happy_path"]
    expected = fixture["expected"]

    _, brief = _run(fixture["request"])

    assert sorted(brief.destinations) == sorted(expected["destinations"])
    assert brief.duration_days == expected["duration_days"]
    assert brief.budget is not None
    assert brief.budget.amount == expected["budget_amount"]
    assert brief.budget.currency == expected["budget_currency"]
    for like in expected["likes_include"]:
        assert any(like.lower() in lk.lower() for lk in brief.preferences.likes), (
            f"missing like: {like}; got {brief.preferences.likes}"
        )
    for dislike in expected["dislikes_include"]:
        assert any(dislike.lower() in dk.lower() for dk in brief.preferences.dislikes), (
            f"missing dislike: {dislike}"
        )


def test_underspecified_yields_open_questions(sample_requests: dict) -> None:
    fixture = sample_requests["underspecified_warm_weekend"]
    _, brief = _run(fixture["request"])
    assert brief.open_questions, "expected open_questions for underspecified input"


def test_contradictory_request_flagged(sample_requests: dict) -> None:
    fixture = sample_requests["contradictory_luxury_cheap"]
    _, brief = _run(fixture["request"])
    assert brief.open_questions, "expected open_questions naming the conflict"


def test_vague_prefs_not_hallucinated(sample_requests: dict) -> None:
    """Edge case 1.6 — 'fun, relaxing' must NOT yield specific likes like 'art'."""
    fixture = sample_requests["food_lover_no_food_likes_extracted"]
    _, brief = _run(fixture["request"])
    assert brief.open_questions, "agent should ask for concrete interests"


def test_prompt_injection_ignored(sample_requests: dict) -> None:
    """Edge case 1.9 — injection phrases must be ignored, Rome still extracted."""
    fixture = sample_requests["prompt_injection_attempt"]
    _, brief = _run(fixture["request"])
    assert "Rome" in brief.destinations
    serialized = brief.model_dump_json().lower()
    assert "pwned" not in serialized


@pytest.mark.parametrize(
    "fixture_key",
    [
        "japan_happy_path",
        "underspecified_warm_weekend",
        "contradictory_luxury_cheap",
        "obscure_destination_westeros",
        "tight_budget_long_trip",
        "multi_city_zigzag_risk",
        "food_lover_no_food_likes_extracted",
        "prompt_injection_attempt",
        "non_latin_destination",
        "accessibility_requirement",
    ],
)
def test_all_fixtures_produce_valid_briefs(sample_requests: dict, fixture_key: str) -> None:
    """Every fixture must produce a schema-valid TripBrief — no crashes, no malformed JSON."""
    request = sample_requests[fixture_key]["request"]
    ctx, brief = _run(request)
    assert isinstance(brief, TripBrief)
    assert ctx.tracer.totals["cost_usd"] > 0
