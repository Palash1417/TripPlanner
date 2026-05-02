"""Phase 4 unit tests — Destination, Accommodation, Transport, Budget agents,
plus parallel-fan-out orchestrator graph. All mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock

import pytest

from src.agents import accommodation, budget, destination, intent, itinerary, transport
from src.llm.client import LLMResponse
from src.observability import Tracer
from src.orchestrator import TripContext, run_graph
from src.schemas import (
    AccommodationPlan,
    BudgetBreakdown,
    DestinationCatalog,
    Itinerary,
    TransportPlan,
    TripBrief,
)


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


def _japan_destinations() -> DestinationCatalog:
    return DestinationCatalog.model_validate(
        {
            "pois": [
                {
                    "name": "Tsukiji Outer Market",
                    "city": "Tokyo",
                    "category": "food",
                    "match_reasons": ["likes:food"],
                    "est_visit_minutes": 120,
                    "est_cost_usd": 30.0,
                    "tags": ["early-morning"],
                },
                {
                    "name": "Senso-ji Temple",
                    "city": "Tokyo",
                    "category": "temple",
                    "match_reasons": ["likes:temples", "dislikes:crowds → 7am visit"],
                    "est_visit_minutes": 90,
                    "est_cost_usd": 0.0,
                    "tags": ["off-peak"],
                },
                {
                    "name": "Fushimi Inari",
                    "city": "Kyoto",
                    "category": "temple",
                    "match_reasons": ["likes:temples", "dislikes:crowds → off-peak"],
                    "est_visit_minutes": 150,
                    "est_cost_usd": 0.0,
                    "tags": ["sunrise"],
                },
            ]
        }
    )


def _japan_accommodation() -> AccommodationPlan:
    return AccommodationPlan.model_validate(
        {
            "stays": [
                {
                    "name": "Asakusa Boutique Hotel",
                    "city": "Tokyo",
                    "neighborhood": "Asakusa",
                    "property_type": "hotel",
                    "price_per_night_usd": 150.0,
                    "nights": 3,
                    "match_reasons": ["dislikes:crowds → quieter neighborhood"],
                },
                {
                    "name": "Higashiyama Ryokan",
                    "city": "Kyoto",
                    "neighborhood": "Higashiyama",
                    "property_type": "ryokan",
                    "price_per_night_usd": 180.0,
                    "nights": 2,
                    "match_reasons": ["likes:temples → walking distance"],
                },
            ]
        }
    )


def _japan_transport() -> TransportPlan:
    return TransportPlan.model_validate(
        {
            "legs": [
                {
                    "origin": "Tokyo",
                    "destination": "Kyoto",
                    "mode": "train",
                    "duration_minutes": 135,
                    "cost_usd": 130.0,
                    "notes": "Shinkansen Nozomi",
                    "confidence": "high",
                },
            ]
        }
    )


def _japan_budget() -> BudgetBreakdown:
    return BudgetBreakdown.model_validate(
        {
            "currency": "USD",
            "lines": [
                {"category": "lodging", "estimate_usd": 810.0, "confidence": "high"},
                {"category": "transport", "estimate_usd": 130.0, "confidence": "high"},
                {"category": "food", "estimate_usd": 500.0, "confidence": "medium"},
                {"category": "activities", "estimate_usd": 200.0, "confidence": "medium"},
                {"category": "buffer", "estimate_usd": 200.0, "confidence": "high"},
            ],
            "total_estimate_usd": 1840.0,
            "budget_amount_usd": 3000.0,
            "warnings": [],
        }
    )


def _japan_itinerary() -> Itinerary:
    days = []
    for i in range(1, 6):
        days.append(
            {
                "day_number": i,
                "city": "Tokyo" if i <= 3 else "Kyoto",
                "activities": [
                    {
                        "title": "Morning food market",
                        "duration_minutes": 120,
                        "match_reasons": ["likes:food"],
                    },
                    {
                        "title": "Temple visit",
                        "duration_minutes": 90,
                        "match_reasons": ["likes:temples"],
                    },
                ],
            }
        )
    return Itinerary.model_validate({"days": days, "confidence": "medium"})


def _mock_response(parsed) -> LLMResponse:
    return LLMResponse(
        text=parsed.model_dump_json(),
        model="gemini-2.5-pro",
        input_tokens=400,
        output_tokens=600,
        latency_ms=1500,
        cost_usd=0.003,
        parsed=parsed,
    )


# ---------- specialist agents ----------


@pytest.mark.parametrize(
    "agent_module, expected_field, expected_payload_fn",
    [
        (destination, "destination_catalog", _japan_destinations),
        (accommodation, "accommodation_plan", _japan_accommodation),
        (transport, "transport_plan", _japan_transport),
    ],
)
def test_specialist_requires_brief_first(
    agent_module, expected_field, expected_payload_fn, tmp_trace_dir: Path
) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    fake_client = MagicMock()
    with pytest.raises(RuntimeError, match="run intent first"):
        agent_module.run(ctx, client=fake_client)
    fake_client.call.assert_not_called()


def test_destination_stores_and_traces(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _japan_brief()
    fake = MagicMock()
    fake.call.return_value = _mock_response(_japan_destinations())

    catalog = destination.run(ctx, client=fake)

    assert ctx.destination_catalog is catalog
    assert len(catalog.pois) == 3
    grouped = catalog.by_city()
    assert "Tokyo" in grouped and "Kyoto" in grouped
    for poi in catalog.pois:
        assert poi.match_reasons, "every POI must have ≥1 match_reason"
    assert any("destination" in d for d in ctx.decisions)


def test_accommodation_covers_every_city(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _japan_brief()
    fake = MagicMock()
    fake.call.return_value = _mock_response(_japan_accommodation())

    plan = accommodation.run(ctx, client=fake)

    cities_in_brief = set(ctx.brief.destinations)
    cities_in_plan = {s.city for s in plan.stays}
    assert cities_in_brief == cities_in_plan
    assert plan.total_usd == pytest.approx(810.0)


def test_transport_path_connects_destinations(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _japan_brief()
    fake = MagicMock()
    fake.call.return_value = _mock_response(_japan_transport())

    plan = transport.run(ctx, client=fake)

    cities_visited = {leg.origin for leg in plan.legs} | {leg.destination for leg in plan.legs}
    for city in ctx.brief.destinations:
        assert city in cities_visited, f"transport must touch {city}"


def test_budget_consumes_specialists(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _japan_brief()
    ctx.accommodation_plan = _japan_accommodation()
    ctx.transport_plan = _japan_transport()
    ctx.destination_catalog = _japan_destinations()
    fake = MagicMock()
    fake.call.return_value = _mock_response(_japan_budget())

    bd = budget.run(ctx, client=fake)

    # All specialist outputs were forwarded as part of the user payload.
    user_payload = fake.call.call_args.kwargs["user"]
    assert "accommodation_plan" in user_payload
    assert "transport_plan" in user_payload
    assert "destination_pois" in user_payload

    assert ctx.budget is bd
    assert bd.total_estimate_usd == 1840.0
    assert bd.warnings == []


def test_budget_runs_without_specialists(tmp_trace_dir: Path) -> None:
    """Budget agent gracefully degrades to brief-only inputs."""
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _japan_brief()
    fake = MagicMock()
    fake.call.return_value = _mock_response(_japan_budget())

    budget.run(ctx, client=fake)

    user_payload = fake.call.call_args.kwargs["user"]
    assert "accommodation_plan" not in user_payload
    assert "transport_plan" not in user_payload


# ---------- Itinerary now consumes specialists ----------


def test_itinerary_includes_specialist_outputs_in_prompt(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _japan_brief()
    ctx.destination_catalog = _japan_destinations()
    ctx.accommodation_plan = _japan_accommodation()
    ctx.transport_plan = _japan_transport()
    ctx.budget = _japan_budget()

    fake = MagicMock()
    fake.call.return_value = _mock_response(_japan_itinerary())

    itinerary.run(ctx, client=fake)

    user_payload = fake.call.call_args.kwargs["user"]
    for key in ("destination_pois", "accommodation_plan", "transport_plan", "budget"):
        assert key in user_payload, f"itinerary must forward {key}"


# ---------- Orchestrator parallel fan-out ----------


def test_run_graph_runs_specialists_in_parallel(
    tmp_trace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When TRIP_PLANNER_PARALLEL=1, all three specialists must enter their LLM
    call before any returns — proves they ran concurrently rather than serially.
    """
    monkeypatch.setenv("TRIP_PLANNER_PARALLEL", "1")
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    fake = MagicMock()

    arrived = Event()
    in_flight = {"count": 0}
    seen_max = {"max": 0}
    lock_evt = Event()

    def fake_call(system, user, *, tier, schema, max_tokens=2048):
        nonlocal in_flight, seen_max
        if schema is TripBrief:
            return _mock_response(_japan_brief())
        if schema is DestinationCatalog:
            in_flight["count"] += 1
            seen_max["max"] = max(seen_max["max"], in_flight["count"])
            arrived.wait(timeout=1.0)
            in_flight["count"] -= 1
            return _mock_response(_japan_destinations())
        if schema is AccommodationPlan:
            in_flight["count"] += 1
            seen_max["max"] = max(seen_max["max"], in_flight["count"])
            arrived.wait(timeout=1.0)
            in_flight["count"] -= 1
            return _mock_response(_japan_accommodation())
        if schema is TransportPlan:
            in_flight["count"] += 1
            seen_max["max"] = max(seen_max["max"], in_flight["count"])
            # Once all 3 specialists are in flight, release the gate.
            if in_flight["count"] >= 3:
                arrived.set()
            else:
                arrived.wait(timeout=1.0)
            in_flight["count"] -= 1
            return _mock_response(_japan_transport())
        if schema is BudgetBreakdown:
            return _mock_response(_japan_budget())
        if schema is Itinerary:
            return _mock_response(_japan_itinerary())
        raise AssertionError(f"unexpected schema {schema}")

    fake.call.side_effect = fake_call

    run_graph(ctx, client=fake)

    assert seen_max["max"] >= 2, (
        f"expected ≥2 specialists in flight at once, saw {seen_max['max']}"
    )
    assert ctx.brief is not None
    assert ctx.destination_catalog is not None
    assert ctx.accommodation_plan is not None
    assert ctx.transport_plan is not None
    assert ctx.budget is not None
    assert ctx.itinerary is not None


def test_run_graph_surfaces_specialist_failure(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    fake = MagicMock()

    def fake_call(system, user, *, tier, schema, max_tokens=2048):
        if schema is TripBrief:
            return _mock_response(_japan_brief())
        if schema is DestinationCatalog:
            raise ValueError("destination blew up")
        if schema is AccommodationPlan:
            return _mock_response(_japan_accommodation())
        if schema is TransportPlan:
            return _mock_response(_japan_transport())
        raise AssertionError(f"unexpected schema {schema}")

    fake.call.side_effect = fake_call

    with pytest.raises(RuntimeError, match="specialist fan-out failed"):
        run_graph(ctx, client=fake)

    assert ctx.brief is not None
    assert ctx.destination_catalog is None
    assert ctx.accommodation_plan is not None or ctx.transport_plan is not None


def test_trace_records_all_phase4_agents(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    fake = MagicMock()

    def fake_call(system, user, *, tier, schema, max_tokens=2048):
        return {
            TripBrief: _mock_response(_japan_brief()),
            DestinationCatalog: _mock_response(_japan_destinations()),
            AccommodationPlan: _mock_response(_japan_accommodation()),
            TransportPlan: _mock_response(_japan_transport()),
            BudgetBreakdown: _mock_response(_japan_budget()),
            Itinerary: _mock_response(_japan_itinerary()),
        }[schema]

    fake.call.side_effect = fake_call
    run_graph(ctx, client=fake)

    events = [
        json.loads(line)
        for line in (tmp_trace_dir / f"{ctx.trip_id}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    agents_seen = {e["agent"] for e in events if e["event"] == "span_end"}
    # Phase 5 added critic; the full set is now 7. Phase 4 specialists must all be present.
    assert agents_seen >= {
        "intent",
        "destination",
        "accommodation",
        "transport",
        "budget",
        "itinerary",
    }
    assert "critic" in agents_seen, "Phase 5 critic must also emit a span"
