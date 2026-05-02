"""Phase 3 unit tests — Itinerary agent + orchestrator graph + CLI render, all mocked."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents import intent, itinerary
from src.llm.client import LLMResponse
from src.observability import Tracer
from src.orchestrator import TripContext, run_graph
from src.schemas import Itinerary, TripBrief


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
                        "start_time": "07:00",
                        "duration_minutes": 120,
                        "match_reasons": ["likes:food", "dislikes:crowds → off-peak"],
                        "est_cost_usd": 30.0,
                    },
                    {
                        "title": "Temple visit",
                        "start_time": "10:00",
                        "duration_minutes": 90,
                        "match_reasons": ["likes:temples"],
                        "est_cost_usd": 5.0,
                    },
                ],
                "transport_leg": (
                    {
                        "origin": "Tokyo",
                        "destination": "Kyoto",
                        "mode": "train",
                        "duration_minutes": 135,
                        "cost_usd": 130.0,
                        "notes": "Shinkansen Nozomi",
                        "match_reasons": [],
                        "confidence": "high",
                    }
                    if i == 4
                    else None
                ),
            }
        )
    return Itinerary.model_validate(
        {"days": days, "summary": "Food + temples, off-peak.", "confidence": "medium"}
    )


def _mock_response(parsed, *, model: str = "gemini-2.5-pro") -> LLMResponse:
    return LLMResponse(
        text=parsed.model_dump_json(),
        model=model,
        input_tokens=500,
        output_tokens=900,
        latency_ms=2500,
        cost_usd=0.005,
        parsed=parsed,
    )


# ---------- TripContext ----------


def test_trip_context_holds_itinerary(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x")
    assert ctx.itinerary is None
    ctx.brief = _japan_brief()
    ctx.itinerary = _japan_itinerary()
    summary = ctx.summary()
    assert summary["itinerary_built"] is True
    assert summary["days"] == 5


# ---------- Itinerary agent ----------


def test_itinerary_run_requires_brief(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    fake_client = MagicMock()
    with pytest.raises(RuntimeError, match="run intent first"):
        itinerary.run(ctx, client=fake_client)
    fake_client.call.assert_not_called()


def test_itinerary_run_stores_and_traces(tmp_trace_dir: Path) -> None:
    ctx = TripContext(
        user_request="Plan a 5-day Japan trip.",
        tracer=Tracer(trace_dir=str(tmp_trace_dir)),
    )
    ctx.brief = _japan_brief()

    fake_client = MagicMock()
    fake_client.call.return_value = _mock_response(_japan_itinerary())

    itin = itinerary.run(ctx, client=fake_client)

    assert ctx.itinerary is itin
    assert len(itin.days) == 5
    assert itin.days[0].city == "Tokyo"
    assert itin.days[4].city == "Kyoto"

    fake_client.call.assert_called_once()
    kwargs = fake_client.call.call_args.kwargs
    assert kwargs["tier"] == "smart"
    assert kwargs["schema"] is Itinerary

    events = [
        json.loads(line)
        for line in (tmp_trace_dir / f"{ctx.trip_id}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [e["event"] for e in events] == ["span_start", "span_end"]
    assert events[1]["agent"] == "itinerary"
    assert len(events[1]["payload"]["output"]["days"]) == 5
    assert events[1]["cost_usd"] == 0.005


def test_itinerary_system_prompt_covers_critical_rules() -> None:
    p = itinerary.SYSTEM_PROMPT.lower()
    assert "duration_days" in p, "must respect requested duration"
    assert "match_reasons" in p, "every activity ties back to brief"
    assert "600 minutes" in p or "10 hours" in p, "day balance rule"
    assert "transit" in p or "transport" in p, "inter-city transit handling"
    # Phase 4 update: the agent now consumes specialist outputs when present.
    assert "specialist" in p, "Phase 4: must mention consuming specialist outputs"


# ---------- Orchestrator graph ----------


# Linear-graph ordering test removed — Phase 4 changed the topology to
# intent → [destination ‖ accommodation ‖ transport] → budget → itinerary,
# now covered by tests/phase4/test_specialists.py::test_run_graph_runs_specialists_in_parallel
# and ::test_trace_records_all_phase4_agents.


# CLI render + error-path tests moved to tests/phase7/test_ui.py — the CLI
# was split in Phase 7 to call run_intent then complete_plan instead of run_graph,
# so the Phase 7 tests cover both render and error-path behavior.
