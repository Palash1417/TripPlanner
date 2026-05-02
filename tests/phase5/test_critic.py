"""Phase 5 unit tests — Critic agent (5 rules) + revision loop + 2-round cap."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents import critic
from src.llm.client import LLMResponse
from src.observability import Tracer
from src.orchestrator import TripContext, run_graph
from src.schemas import (
    AccommodationPlan,
    BudgetBreakdown,
    CriticVerdict,
    DestinationCatalog,
    Itinerary,
    TransportPlan,
    TripBrief,
)


# ---------- fixture builders ----------


def _japan_brief() -> TripBrief:
    return TripBrief.model_validate(
        {
            "destinations": ["Tokyo", "Kyoto"],
            "duration_days": 5,
            "budget": {"amount": 3000, "currency": "USD"},
            "preferences": {
                "likes": ["food", "temples"],
                "dislikes": ["crowds"],
                "pace": "moderate",
            },
        }
    )


def _budget(total: float, lines: list | None = None) -> BudgetBreakdown:
    if lines is None:
        # By default split as 3 even-ish lines summing to `total`
        a = round(total * 0.6, 2)
        b = round(total * 0.3, 2)
        c = round(total - a - b, 2)
        lines = [
            {"category": "lodging", "estimate_usd": a, "confidence": "high"},
            {"category": "transport", "estimate_usd": b, "confidence": "high"},
            {"category": "food", "estimate_usd": c, "confidence": "medium"},
        ]
    return BudgetBreakdown.model_validate(
        {
            "currency": "USD",
            "lines": lines,
            "total_estimate_usd": total,
            "budget_amount_usd": 3000.0,
            "warnings": [],
        }
    )


def _itinerary(days_data: list[dict]) -> Itinerary:
    return Itinerary.model_validate(
        {"days": days_data, "summary": "test", "confidence": "medium"}
    )


def _good_day(num: int, city: str = "Tokyo") -> dict:
    return {
        "day_number": num,
        "city": city,
        "activities": [
            {
                "title": "Food market",
                "duration_minutes": 90,
                "match_reasons": ["likes:food"],
            },
            {
                "title": "Temple",
                "duration_minutes": 60,
                "match_reasons": ["likes:temples", "dislikes:crowds → 7am visit"],
            },
        ],
    }


def _good_itinerary() -> Itinerary:
    return _itinerary(
        [
            _good_day(1, "Tokyo"),
            _good_day(2, "Tokyo"),
            _good_day(3, "Tokyo"),
            {
                "day_number": 4,
                "city": "Kyoto",
                "activities": [
                    {"title": "Food crawl", "duration_minutes": 90, "match_reasons": ["likes:food"]},
                    {"title": "Shrine", "duration_minutes": 90, "match_reasons": ["likes:temples"]},
                ],
                "transport_leg": {
                    "origin": "Tokyo",
                    "destination": "Kyoto",
                    "mode": "train",
                    "duration_minutes": 135,
                    "cost_usd": 130.0,
                    "match_reasons": [],
                    "confidence": "high",
                },
            },
            _good_day(5, "Kyoto"),
        ]
    )


def _ctx_with_good_plan(tmp_trace_dir: Path) -> TripContext:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _japan_brief()
    ctx.budget = _budget(2000.0)
    ctx.itinerary = _good_itinerary()
    return ctx


# ---------- happy path ----------


def test_critic_passes_clean_plan(tmp_trace_dir: Path) -> None:
    ctx = _ctx_with_good_plan(tmp_trace_dir)
    verdict = critic.run(ctx)
    assert verdict.passed is True
    assert verdict.violations == []
    assert verdict.revision == 0
    assert ctx.latest_verdict is verdict


def test_critic_requires_brief_and_itinerary(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    with pytest.raises(RuntimeError, match="TripBrief"):
        critic.run(ctx)
    ctx.brief = _japan_brief()
    with pytest.raises(RuntimeError, match="Itinerary"):
        critic.run(ctx)


# ---------- 5 rules — one test per architecture §7.1 rule ----------


def test_rule_budget_violation(tmp_trace_dir: Path) -> None:
    ctx = _ctx_with_good_plan(tmp_trace_dir)
    ctx.budget = _budget(3500.0)  # over the 3000 brief budget
    verdict = critic.run(ctx)
    assert verdict.passed is False
    rules = {v.rule for v in verdict.violations}
    assert "budget" in rules
    msg = next(v.message for v in verdict.violations if v.rule == "budget")
    assert "3500" in msg and "3000" in msg


def test_rule_coverage_violation(tmp_trace_dir: Path) -> None:
    """No activity match_reason mentions 'temples' anywhere — coverage fails."""
    ctx = _ctx_with_good_plan(tmp_trace_dir)
    ctx.itinerary = _itinerary(
        [
            {
                "day_number": i,
                "city": "Tokyo" if i <= 3 else "Kyoto",
                "activities": [
                    {"title": "Food", "duration_minutes": 60, "match_reasons": ["likes:food"]},
                    {"title": "Walk", "duration_minutes": 60, "match_reasons": ["in city"]},
                ],
                "transport_leg": (
                    {
                        "origin": "Tokyo",
                        "destination": "Kyoto",
                        "mode": "train",
                        "duration_minutes": 135,
                        "cost_usd": 130.0,
                        "match_reasons": [],
                        "confidence": "high",
                    }
                    if i == 4
                    else None
                ),
            }
            for i in range(1, 6)
        ]
    )
    verdict = critic.run(ctx)
    assert verdict.passed is False
    coverage_msgs = [v.message for v in verdict.violations if v.rule == "coverage"]
    assert coverage_msgs, "expected a coverage violation"
    assert "temples" in coverage_msgs[0]


def test_rule_avoidance_violation(tmp_trace_dir: Path) -> None:
    """A match_reason mentions 'crowds' (a dislike) WITHOUT a mitigation marker."""
    ctx = _ctx_with_good_plan(tmp_trace_dir)
    ctx.itinerary = _itinerary(
        [
            {
                "day_number": i,
                "city": "Tokyo" if i <= 3 else "Kyoto",
                "activities": [
                    {"title": "Food", "duration_minutes": 60, "match_reasons": ["likes:food"]},
                    {
                        "title": "Crowded plaza",
                        "duration_minutes": 60,
                        # references dislike but offers no mitigation marker
                        "match_reasons": ["likes:temples", "popular with crowds"],
                    },
                ],
                "transport_leg": (
                    {
                        "origin": "Tokyo",
                        "destination": "Kyoto",
                        "mode": "train",
                        "duration_minutes": 135,
                        "cost_usd": 130.0,
                        "match_reasons": [],
                        "confidence": "high",
                    }
                    if i == 4
                    else None
                ),
            }
            for i in range(1, 6)
        ]
    )
    verdict = critic.run(ctx)
    assert verdict.passed is False
    avoidance = [v for v in verdict.violations if v.rule == "avoidance"]
    assert avoidance, "expected an avoidance violation"
    assert "crowds" in avoidance[0].message


def test_rule_geo_feasibility_violation(tmp_trace_dir: Path) -> None:
    """Day 4 changes city (Tokyo→Kyoto) but has no transport_leg."""
    ctx = _ctx_with_good_plan(tmp_trace_dir)
    bad = _good_itinerary()
    bad_dict = bad.model_dump(mode="json")
    bad_dict["days"][3]["transport_leg"] = None  # remove the train leg
    ctx.itinerary = Itinerary.model_validate(bad_dict)
    verdict = critic.run(ctx)
    assert verdict.passed is False
    geo = [v for v in verdict.violations if v.rule == "geo_feasibility"]
    assert geo, "expected a geo_feasibility violation"
    assert "transport_leg" in geo[0].message


def test_rule_day_balance_violation(tmp_trace_dir: Path) -> None:
    """A single day exceeds the 600-minute (10h) ceiling."""
    ctx = _ctx_with_good_plan(tmp_trace_dir)
    bad_day = {
        "day_number": 1,
        "city": "Tokyo",
        "activities": [
            {"title": "All day food tour", "duration_minutes": 700,
             "match_reasons": ["likes:food"]},
            {"title": "Late temple walk", "duration_minutes": 60,
             "match_reasons": ["likes:temples"]},
        ],
    }
    days = _good_itinerary().model_dump(mode="json")["days"]
    days[0] = bad_day
    ctx.itinerary = Itinerary.model_validate({"days": days, "confidence": "medium"})
    verdict = critic.run(ctx)
    assert verdict.passed is False
    balance = [v for v in verdict.violations if v.rule == "day_balance"]
    assert balance, "expected a day_balance violation"
    assert "day 1" in balance[0].message.lower()


# ---------- revision loop / 2-round cap ----------


def _mock_response(parsed) -> LLMResponse:
    return LLMResponse(
        text=parsed.model_dump_json(),
        model="gemini-2.5-flash",
        input_tokens=300,
        output_tokens=400,
        latency_ms=900,
        cost_usd=0.001,
        parsed=parsed,
    )


def _mock_specialists(fake) -> dict:
    """Wire up the mock client to return canned responses for every agent."""
    from src.schemas import (
        AccommodationPlan,
        BudgetBreakdown,
        DestinationCatalog,
        Itinerary,
        TransportPlan,
        TripBrief,
    )

    catalog = DestinationCatalog.model_validate(
        {
            "pois": [
                {
                    "name": "Tsukiji",
                    "city": "Tokyo",
                    "category": "food",
                    "match_reasons": ["likes:food"],
                    "est_visit_minutes": 90,
                },
                {
                    "name": "Senso-ji",
                    "city": "Tokyo",
                    "category": "temple",
                    "match_reasons": ["likes:temples", "dislikes:crowds → 7am"],
                    "est_visit_minutes": 90,
                },
            ]
        }
    )
    accommodation = AccommodationPlan.model_validate(
        {
            "stays": [
                {"name": "Asakusa Hotel", "city": "Tokyo", "neighborhood": "Asakusa",
                 "property_type": "hotel", "price_per_night_usd": 150.0, "nights": 3,
                 "match_reasons": ["dislikes:crowds → quieter"]},
                {"name": "Higashiyama Ryokan", "city": "Kyoto",
                 "neighborhood": "Higashiyama", "property_type": "ryokan",
                 "price_per_night_usd": 200.0, "nights": 2,
                 "match_reasons": ["likes:temples → walkable"]},
            ]
        }
    )
    transport = TransportPlan.model_validate(
        {
            "legs": [
                {"origin": "Tokyo", "destination": "Kyoto", "mode": "train",
                 "duration_minutes": 135, "cost_usd": 130.0, "match_reasons": [],
                 "notes": "Shinkansen", "confidence": "high"},
            ]
        }
    )
    over_budget = BudgetBreakdown.model_validate(
        {
            "currency": "USD",
            "lines": [
                {"category": "lodging", "estimate_usd": 2500.0, "confidence": "high"},
                {"category": "transport", "estimate_usd": 700.0, "confidence": "high"},
                {"category": "food", "estimate_usd": 800.0, "confidence": "medium"},
            ],
            "total_estimate_usd": 4000.0,
            "budget_amount_usd": 3000.0,
            "warnings": [],
        }
    )
    in_budget = BudgetBreakdown.model_validate(
        {
            "currency": "USD",
            "lines": [
                {"category": "lodging", "estimate_usd": 1300.0, "confidence": "high"},
                {"category": "transport", "estimate_usd": 300.0, "confidence": "high"},
                {"category": "food", "estimate_usd": 600.0, "confidence": "medium"},
            ],
            "total_estimate_usd": 2200.0,
            "budget_amount_usd": 3000.0,
            "warnings": [],
        }
    )
    itinerary = _good_itinerary()
    brief = _japan_brief()

    return {
        "brief": brief,
        "catalog": catalog,
        "accommodation": accommodation,
        "transport": transport,
        "over_budget": over_budget,
        "in_budget": in_budget,
        "itinerary": itinerary,
    }


def test_revision_loop_recovers_from_budget_overrun(tmp_trace_dir: Path) -> None:
    """First budget call goes over 3000, second comes in under — graph passes on rev 1."""
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    fixtures = _mock_specialists(None)

    fake = MagicMock()
    budget_calls = {"count": 0}

    def fake_call(system, user, *, tier, schema, max_tokens=2048):
        if schema is TripBrief:
            return _mock_response(fixtures["brief"])
        if schema is DestinationCatalog:
            return _mock_response(fixtures["catalog"])
        if schema is AccommodationPlan:
            return _mock_response(fixtures["accommodation"])
        if schema is TransportPlan:
            return _mock_response(fixtures["transport"])
        if schema is BudgetBreakdown:
            budget_calls["count"] += 1
            return _mock_response(
                fixtures["over_budget"] if budget_calls["count"] == 1 else fixtures["in_budget"]
            )
        if schema is Itinerary:
            return _mock_response(fixtures["itinerary"])
        raise AssertionError(f"unexpected schema {schema}")

    fake.call.side_effect = fake_call
    run_graph(ctx, client=fake)

    assert ctx.budget is not None
    assert ctx.budget.total_estimate_usd == 2200.0
    assert len(ctx.critic_verdicts) >= 2
    assert ctx.critic_verdicts[-1].passed is True
    assert ctx.current_revision >= 1
    assert budget_calls["count"] == 2  # over → re-ran budget once


def test_revision_loop_caps_at_two_rounds(tmp_trace_dir: Path) -> None:
    """If the plan is permanently broken, give up after _MAX_REVISIONS."""
    from src.orchestrator import graph as graph_mod

    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    fixtures = _mock_specialists(None)

    fake = MagicMock()

    def fake_call(system, user, *, tier, schema, max_tokens=2048):
        if schema is TripBrief:
            return _mock_response(fixtures["brief"])
        if schema is DestinationCatalog:
            return _mock_response(fixtures["catalog"])
        if schema is AccommodationPlan:
            return _mock_response(fixtures["accommodation"])
        if schema is TransportPlan:
            return _mock_response(fixtures["transport"])
        if schema is BudgetBreakdown:
            return _mock_response(fixtures["over_budget"])  # always over
        if schema is Itinerary:
            return _mock_response(fixtures["itinerary"])
        raise AssertionError(f"unexpected schema {schema}")

    fake.call.side_effect = fake_call
    run_graph(ctx, client=fake)

    # 1 initial + _MAX_REVISIONS revisions = 1 + 2 = 3 verdicts
    assert len(ctx.critic_verdicts) == graph_mod._MAX_REVISIONS + 1
    assert ctx.critic_verdicts[-1].passed is False
    assert any("gave up" in d for d in ctx.decisions)


def test_critic_verdict_recorded_in_trace(tmp_trace_dir: Path) -> None:
    import json

    ctx = _ctx_with_good_plan(tmp_trace_dir)
    critic.run(ctx)
    events = [
        json.loads(line)
        for line in (tmp_trace_dir / f"{ctx.trip_id}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    critic_events = [e for e in events if e["agent"] == "critic"]
    assert critic_events, "critic must emit a trace event"
    assert critic_events[0]["payload"]["output"]["passed"] is True
