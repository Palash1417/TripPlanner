"""Phase 2 unit tests — Intent agent + TripContext + CLI, all mocked."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents import intent
from src.llm.client import LLMResponse
from src.observability import Tracer
from src.orchestrator import TripContext
from src.schemas import TripBrief


def _mock_response(brief: TripBrief, *, model: str = "gemini-2.5-flash") -> LLMResponse:
    return LLMResponse(
        text=brief.model_dump_json(),
        model=model,
        input_tokens=120,
        output_tokens=80,
        latency_ms=420,
        cost_usd=0.0005,
        parsed=brief,
    )


def _japan_brief() -> TripBrief:
    return TripBrief.model_validate(
        {
            "origin": None,
            "destinations": ["Tokyo", "Kyoto"],
            "duration_days": 5,
            "budget": {"amount": 3000, "currency": "USD"},
            "preferences": {
                "likes": ["food", "temples"],
                "dislikes": ["crowds"],
                "pace": "moderate",
            },
            "open_questions": [],
        }
    )


# ---------- TripContext ----------


def test_trip_context_default_tracer(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="hi")
    assert ctx.trip_id.startswith("trip_")
    assert ctx.brief is None
    assert ctx.decisions == []


def test_trip_context_log_decision_and_summary(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="hi")
    ctx.log_decision("first")
    ctx.log_decision("second")
    summary = ctx.summary()
    assert summary["decisions"] == ["first", "second"]
    assert summary["brief_extracted"] is False


# ---------- Intent agent ----------


def test_intent_run_stores_brief_and_traces(tmp_trace_dir: Path) -> None:
    ctx = TripContext(
        user_request="Plan a 5-day trip to Japan. Tokyo + Kyoto. $3000.",
        tracer=Tracer(trace_dir=str(tmp_trace_dir)),
    )

    fake_client = MagicMock()
    fake_client.call.return_value = _mock_response(_japan_brief())

    brief = intent.run(ctx, client=fake_client)

    assert ctx.brief is brief
    assert brief.destinations == ["Tokyo", "Kyoto"]
    assert any("intent" in d for d in ctx.decisions)

    fake_client.call.assert_called_once()
    call_kwargs = fake_client.call.call_args.kwargs
    assert call_kwargs["tier"] == "fast"
    assert call_kwargs["schema"] is TripBrief

    trace_lines = (tmp_trace_dir / f"{ctx.trip_id}.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in trace_lines]
    assert [e["event"] for e in events] == ["span_start", "span_end"]
    assert events[1]["agent"] == "intent"
    assert events[1]["payload"]["output"]["destinations"] == ["Tokyo", "Kyoto"]
    assert events[1]["cost_usd"] == 0.0005


def test_intent_run_propagates_llm_failure(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    fake_client = MagicMock()
    fake_client.call.side_effect = ValueError("LLM did not return valid JSON")

    with pytest.raises(ValueError):
        intent.run(ctx, client=fake_client)

    assert ctx.brief is None
    events = [
        json.loads(line)
        for line in (tmp_trace_dir / f"{ctx.trip_id}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["event"] == "span_error"


def test_intent_system_prompt_covers_critical_rules() -> None:
    """Sanity check the prompt addresses the edge cases we declared P0/P1."""
    p = intent.SYSTEM_PROMPT.lower()
    assert "ignore previous instructions" in p, "edge case 1.9 (prompt injection)"
    assert "open_questions" in intent.SYSTEM_PROMPT, "must populate open_questions"
    assert "passport" in p or "personally identifying" in p, "edge case 11.3 (PII)"
    assert "$" in intent.SYSTEM_PROMPT and "usd" in p, "currency mapping"
    assert "vague" in p or "fun" in p or "relaxing" in p, "edge case 1.6 (vague prefs)"


# ---------- CLI ----------


def test_cli_no_args_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    from src.ui.cli import main

    rc = main([])
    assert rc == 2
    assert "usage" in capsys.readouterr().out.lower()


# CLI render + error-path tests live in tests/phase3/test_itinerary.py — the CLI
# was refactored in Phase 3 to call run_graph (intent + itinerary) rather than
# intent.run directly, so the Phase 3 tests now cover both behaviors.
