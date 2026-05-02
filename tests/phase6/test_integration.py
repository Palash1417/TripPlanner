"""Phase 6 integration tests — agents/critic with tools wired in.

All mocked at the LLM and HTTP layer; no live network calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents import accommodation, critic, destination, transport
from src.llm.client import LLMResponse
from src.observability import Tracer
from src.orchestrator import TripContext
from src.schemas import (
    AccommodationPlan,
    BudgetBreakdown,
    DestinationCatalog,
    Itinerary,
    TransportPlan,
    TripBrief,
)
from src.tools import web_search


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


def _eur_brief() -> TripBrief:
    return TripBrief.model_validate(
        {
            "destinations": ["Paris"],
            "duration_days": 3,
            "budget": {"amount": 1000, "currency": "EUR"},  # ~$1080 USD
        }
    )


def _mock_response(parsed) -> LLMResponse:
    return LLMResponse(
        text=parsed.model_dump_json(),
        model="gemini-2.5-flash",
        input_tokens=300,
        output_tokens=300,
        latency_ms=500,
        cost_usd=0.001,
        parsed=parsed,
    )


# ---------- Critic uses currency for non-USD budgets ----------


def test_critic_budget_converts_eur_to_usd(tmp_trace_dir: Path) -> None:
    """Brief in EUR; if total_estimate_usd > converted EUR, fail. Otherwise pass."""
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _eur_brief()  # 1000 EUR ~= $1080 USD
    ctx.itinerary = Itinerary.model_validate(
        {
            "days": [
                {
                    "day_number": i,
                    "city": "Paris",
                    "activities": [
                        {"title": "Museum", "duration_minutes": 90, "match_reasons": ["in Paris"]},
                    ],
                }
                for i in range(1, 4)
            ],
            "confidence": "medium",
        }
    )
    ctx.budget = BudgetBreakdown.model_validate(
        {
            "currency": "USD",
            "lines": [
                {"category": "lodging", "estimate_usd": 600, "confidence": "high"},
                {"category": "food", "estimate_usd": 400, "confidence": "medium"},
            ],
            "total_estimate_usd": 1000.0,
            "budget_amount_usd": 1080.0,
            "warnings": [],
        }
    )
    verdict = critic.run(ctx)
    # $1000 USD < ~$1080 USD (1000 EUR converted), so budget rule passes.
    assert all(v.rule != "budget" for v in verdict.violations)


def test_critic_budget_flags_eur_overrun(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _eur_brief()
    ctx.itinerary = Itinerary.model_validate(
        {
            "days": [
                {"day_number": 1, "city": "Paris",
                 "activities": [{"title": "Museum", "duration_minutes": 90,
                                 "match_reasons": ["in Paris"]}]},
                {"day_number": 2, "city": "Paris",
                 "activities": [{"title": "Walk", "duration_minutes": 90,
                                 "match_reasons": ["in Paris"]}]},
                {"day_number": 3, "city": "Paris",
                 "activities": [{"title": "Cafe", "duration_minutes": 90,
                                 "match_reasons": ["in Paris"]}]},
            ],
            "confidence": "medium",
        }
    )
    ctx.budget = BudgetBreakdown.model_validate(
        {
            "currency": "USD",
            "lines": [
                {"category": "lodging", "estimate_usd": 1500, "confidence": "high"},
                {"category": "food", "estimate_usd": 500, "confidence": "medium"},
            ],
            "total_estimate_usd": 2000.0,
            "budget_amount_usd": 1080.0,
            "warnings": [],
        }
    )
    verdict = critic.run(ctx)
    budget_violations = [v for v in verdict.violations if v.rule == "budget"]
    assert budget_violations
    assert "EUR" in budget_violations[0].message  # mentions original currency


# ---------- Critic uses geo for intra-day distance ----------


def test_critic_intraday_distance_violation(tmp_trace_dir: Path) -> None:
    """Two POIs ~400km apart on the same day with no transport_leg → violation."""
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _japan_brief()
    ctx.destination_catalog = DestinationCatalog.model_validate(
        {
            "pois": [
                {
                    "name": "Senso-ji", "city": "Tokyo", "category": "temple",
                    "match_reasons": ["likes:temples"], "est_visit_minutes": 90,
                    "lat": 35.7148, "lon": 139.7967,
                },
                {
                    "name": "Fushimi Inari", "city": "Kyoto", "category": "temple",
                    "match_reasons": ["likes:temples"], "est_visit_minutes": 90,
                    "lat": 34.9671, "lon": 135.7727,
                },
            ]
        }
    )
    ctx.itinerary = Itinerary.model_validate(
        {
            "days": [
                {
                    "day_number": 1,
                    "city": "Tokyo",
                    "activities": [
                        {"title": "Senso-ji morning", "duration_minutes": 90,
                         "poi_name": "Senso-ji",
                         "match_reasons": ["likes:temples"]},
                        {"title": "Fushimi Inari afternoon", "duration_minutes": 90,
                         "poi_name": "Fushimi Inari",
                         "match_reasons": ["likes:temples"]},
                    ],
                }
            ],
            "confidence": "medium",
        }
    )
    verdict = critic.run(ctx)
    geo_violations = [v for v in verdict.violations if v.rule == "geo_feasibility"]
    assert geo_violations, "expected intra-day distance violation"
    assert "km" in geo_violations[0].message


# ---------- Transport agent injects curated routes ----------


def test_transport_agent_includes_curated_routes_in_prompt(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _japan_brief()  # Tokyo + Kyoto → curated route exists

    fake = MagicMock()
    fake.call.return_value = _mock_response(
        TransportPlan.model_validate({
            "legs": [{"origin": "Tokyo", "destination": "Kyoto", "mode": "train",
                      "duration_minutes": 135, "cost_usd": 130, "match_reasons": [],
                      "notes": "Shinkansen", "confidence": "high"}],
        })
    )

    transport.run(ctx, client=fake)

    user_payload = fake.call.call_args.kwargs["user"]
    assert "curated_routes" in user_payload
    assert "Tokyo" in user_payload and "Kyoto" in user_payload
    assert "Shinkansen" in user_payload  # curated note injected


def test_transport_agent_no_curated_routes_for_unknown_pair(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = TripBrief.model_validate(
        {"destinations": ["Atlantis", "Springfield"], "duration_days": 3}
    )
    fake = MagicMock()
    fake.call.return_value = _mock_response(
        TransportPlan.model_validate({
            "legs": [{"origin": "Atlantis", "destination": "Springfield",
                      "mode": "flight", "duration_minutes": 90, "cost_usd": 100,
                      "match_reasons": [], "confidence": "medium"}],
        })
    )
    transport.run(ctx, client=fake)
    user_payload = fake.call.call_args.kwargs["user"]
    # curated_routes key still present but empty
    assert '"curated_routes": {}' in user_payload


# ---------- Destination/Accommodation use web_search when available ----------


def test_destination_skips_search_when_no_provider(
    tmp_trace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k in ("TAVILY_API_KEY", "SERPER_API_KEY", "BRAVE_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _japan_brief()

    fake = MagicMock()
    fake.call.return_value = _mock_response(
        DestinationCatalog.model_validate({
            "pois": [{"name": "Senso-ji", "city": "Tokyo", "category": "temple",
                      "match_reasons": ["likes:temples"], "est_visit_minutes": 90}],
        })
    )

    destination.run(ctx, client=fake)
    user_payload = fake.call.call_args.kwargs["user"]
    assert "search_hints" not in user_payload


def test_destination_injects_search_hints_when_available(
    tmp_trace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "fake")
    monkeypatch.setattr(
        web_search,
        "search",
        lambda q, max_results=4: [
            {"title": f"Top {q}", "url": "https://example.com/x", "snippet": "Tasty"}
        ],
    )

    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _japan_brief()

    fake = MagicMock()
    fake.call.return_value = _mock_response(
        DestinationCatalog.model_validate({
            "pois": [{"name": "Senso-ji", "city": "Tokyo", "category": "temple",
                      "match_reasons": ["likes:temples"], "est_visit_minutes": 90}],
        })
    )

    destination.run(ctx, client=fake)
    user_payload = fake.call.call_args.kwargs["user"]
    assert "search_hints" in user_payload
    assert "Tasty" in user_payload  # snippet from mocked search


def test_accommodation_search_hints_per_city(
    tmp_trace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "fake")
    seen_queries: list[str] = []

    def fake_search(q, max_results=4):
        seen_queries.append(q)
        return [{"title": "Best neighborhoods", "url": "u", "snippet": "Asakusa"}]

    monkeypatch.setattr(web_search, "search", fake_search)

    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _japan_brief()

    fake = MagicMock()
    fake.call.return_value = _mock_response(
        AccommodationPlan.model_validate({
            "stays": [
                {"name": "Asakusa Hotel", "city": "Tokyo", "neighborhood": "Asakusa",
                 "property_type": "hotel", "price_per_night_usd": 150, "nights": 3,
                 "match_reasons": ["dislikes:crowds → quieter"]},
                {"name": "Higashiyama Ryokan", "city": "Kyoto", "neighborhood": "Higashiyama",
                 "property_type": "ryokan", "price_per_night_usd": 200, "nights": 2,
                 "match_reasons": ["likes:temples → walkable"]},
            ],
        })
    )

    accommodation.run(ctx, client=fake)

    # One search per destination city
    assert any("Tokyo" in q for q in seen_queries)
    assert any("Kyoto" in q for q in seen_queries)
