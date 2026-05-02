"""Phase 7 unit tests — orchestrator split, CLI clarify loop, Streamlit smoke."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.llm.client import LLMResponse
from src.observability import Tracer
from src.orchestrator import (
    TripContext,
    complete_plan,
    run_graph,
    run_intent,
)
from src.schemas import (
    AccommodationPlan,
    BudgetBreakdown,
    CriticVerdict,
    DestinationCatalog,
    Itinerary,
    TransportPlan,
    TripBrief,
)
from src.ui import cli


# ---------- helpers ----------


def _japan_brief(*, open_questions: list[str] | None = None) -> TripBrief:
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
            "open_questions": open_questions or [],
        }
    )


def _all_mocks(fake) -> None:
    """Wire up canned responses for every schema."""
    catalog = DestinationCatalog.model_validate(
        {"pois": [
            {"name": "Senso-ji", "city": "Tokyo", "category": "temple",
             "match_reasons": ["likes:temples", "dislikes:crowds → 7am"],
             "est_visit_minutes": 90},
            {"name": "Tsukiji", "city": "Tokyo", "category": "food",
             "match_reasons": ["likes:food"], "est_visit_minutes": 90},
        ]}
    )
    accom = AccommodationPlan.model_validate(
        {"stays": [
            {"name": "Asakusa Hotel", "city": "Tokyo", "neighborhood": "Asakusa",
             "property_type": "hotel", "price_per_night_usd": 150, "nights": 3,
             "match_reasons": ["dislikes:crowds → quieter"]},
            {"name": "Higashiyama Ryokan", "city": "Kyoto", "neighborhood": "Higashiyama",
             "property_type": "ryokan", "price_per_night_usd": 200, "nights": 2,
             "match_reasons": ["likes:temples → walkable"]},
        ]}
    )
    transport = TransportPlan.model_validate(
        {"legs": [
            {"origin": "Tokyo", "destination": "Kyoto", "mode": "train",
             "duration_minutes": 135, "cost_usd": 130, "match_reasons": [],
             "notes": "Shinkansen", "confidence": "high"}
        ]}
    )
    bd = BudgetBreakdown.model_validate(
        {"currency": "USD",
         "lines": [
             {"category": "lodging", "estimate_usd": 850, "confidence": "high"},
             {"category": "transport", "estimate_usd": 130, "confidence": "high"},
             {"category": "food", "estimate_usd": 500, "confidence": "medium"},
             {"category": "activities", "estimate_usd": 200, "confidence": "medium"},
             {"category": "buffer", "estimate_usd": 200, "confidence": "high"},
         ],
         "total_estimate_usd": 1880.0,
         "budget_amount_usd": 3000.0,
         "warnings": []}
    )
    days = [
        {"day_number": i, "city": "Tokyo" if i <= 3 else "Kyoto",
         "activities": [
             {"title": "Food", "duration_minutes": 90, "match_reasons": ["likes:food"]},
             {"title": "Temple", "duration_minutes": 90,
              "match_reasons": ["likes:temples", "dislikes:crowds → 7am"]},
         ],
         "transport_leg": (
             {"origin": "Tokyo", "destination": "Kyoto", "mode": "train",
              "duration_minutes": 135, "cost_usd": 130, "match_reasons": [],
              "confidence": "high"}
             if i == 4 else None
         )}
        for i in range(1, 6)
    ]
    itin = Itinerary.model_validate({"days": days, "confidence": "medium"})

    def _resp(parsed):
        return LLMResponse(
            text=parsed.model_dump_json(),
            model="gemini-2.5-flash",
            input_tokens=300, output_tokens=300,
            latency_ms=500, cost_usd=0.001,
            parsed=parsed,
        )

    brief = _japan_brief()

    def fake_call(system, user, *, tier, schema, max_tokens=2048):
        return {
            TripBrief: _resp(brief),
            DestinationCatalog: _resp(catalog),
            AccommodationPlan: _resp(accom),
            TransportPlan: _resp(transport),
            BudgetBreakdown: _resp(bd),
            Itinerary: _resp(itin),
        }[schema]

    fake.call.side_effect = fake_call


# ---------- orchestrator split ----------


def test_run_intent_only_executes_intent(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    fake = MagicMock()
    _all_mocks(fake)

    run_intent(ctx, client=fake)

    assert ctx.brief is not None
    assert ctx.destination_catalog is None
    assert ctx.itinerary is None
    assert ctx.critic_verdicts == []
    assert fake.call.call_count == 1


def test_complete_plan_requires_brief(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    fake = MagicMock()
    with pytest.raises(RuntimeError, match="run intent first"):
        complete_plan(ctx, client=fake)
    fake.call.assert_not_called()


def test_complete_plan_runs_full_pipeline(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    fake = MagicMock()
    _all_mocks(fake)

    run_intent(ctx, client=fake)
    complete_plan(ctx, client=fake)

    assert ctx.destination_catalog is not None
    assert ctx.accommodation_plan is not None
    assert ctx.transport_plan is not None
    assert ctx.budget is not None
    assert ctx.itinerary is not None
    assert ctx.critic_verdicts


def test_run_graph_still_works(tmp_trace_dir: Path) -> None:
    """Backward compat — run_graph delegates to run_intent + complete_plan."""
    ctx = TripContext(user_request="x", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    fake = MagicMock()
    _all_mocks(fake)
    run_graph(ctx, client=fake)
    assert ctx.brief is not None and ctx.itinerary is not None


# ---------- CLI clarification loop ----------


def test_clarify_returns_empty_when_no_open_questions() -> None:
    brief = _japan_brief(open_questions=[])
    assert cli._clarify(brief) == ""


def test_clarify_collects_user_answers() -> None:
    brief = _japan_brief(open_questions=["Trip dates?", "Currency?"])
    inputs = iter(["April 10-14", "USD", ""])  # blank line ends input
    result = cli._clarify(brief, prompter=lambda _: next(inputs))
    assert "April 10-14" in result
    assert "USD" in result


def test_clarify_blank_first_line_proceeds_with_assumptions() -> None:
    brief = _japan_brief(open_questions=["Dates?"])
    result = cli._clarify(brief, prompter=lambda _: "")
    assert result == ""


def test_clarify_handles_eof_gracefully() -> None:
    brief = _japan_brief(open_questions=["?"])

    def raise_eof(_):
        raise EOFError

    assert cli._clarify(brief, prompter=raise_eof) == ""


def test_cli_no_clarify_flag_skips_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_trace_dir: Path
) -> None:
    """With --no-clarify, _clarify must NOT be called even if there are open_questions."""
    fake_run_intent_called = {"count": 0}
    fake_complete_plan_called = {"count": 0}

    def fake_run_intent(ctx, *, client=None):
        fake_run_intent_called["count"] += 1
        ctx.brief = _japan_brief(open_questions=["Trip dates?"])

    def fake_complete_plan(ctx, *, client=None):
        fake_complete_plan_called["count"] += 1

    monkeypatch.setattr(cli, "run_intent", fake_run_intent)
    monkeypatch.setattr(cli, "complete_plan", fake_complete_plan)

    sentinel = {"called": False}

    def should_not_call(_):
        sentinel["called"] = True
        return ""

    monkeypatch.setattr(cli, "_clarify", should_not_call)

    rc = cli.run("anything", no_clarify=True)
    assert rc == 0
    assert sentinel["called"] is False
    assert fake_run_intent_called["count"] == 1  # not re-run
    assert fake_complete_plan_called["count"] == 1


def test_cli_clarify_reruns_intent_with_addendum(
    monkeypatch: pytest.MonkeyPatch, tmp_trace_dir: Path
) -> None:
    """When user supplies clarifications, intent runs twice (original + with addendum)."""
    intent_calls: list[str] = []

    def fake_run_intent(ctx, *, client=None):
        intent_calls.append(ctx.user_request)
        ctx.brief = _japan_brief(open_questions=["Dates?"] if len(intent_calls) == 1 else [])

    def fake_complete_plan(ctx, *, client=None):
        pass

    monkeypatch.setattr(cli, "run_intent", fake_run_intent)
    monkeypatch.setattr(cli, "complete_plan", fake_complete_plan)
    monkeypatch.setattr(cli, "_clarify", lambda brief: "April 10-14")

    rc = cli.run("Plan a trip", no_clarify=False)
    assert rc == 0
    assert len(intent_calls) == 2
    assert "April 10-14" in intent_calls[1]


def test_cli_no_clarify_via_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """The --no-clarify flag is parsed off argv before joining."""
    captured: dict = {}

    def fake_run(user_request: str, *, no_clarify: bool = False) -> int:
        captured["request"] = user_request
        captured["no_clarify"] = no_clarify
        return 0

    monkeypatch.setattr(cli, "run", fake_run)
    rc = cli.main(["--no-clarify", "Plan", "Tokyo"])
    assert rc == 0
    assert captured["no_clarify"] is True
    assert "--no-clarify" not in captured["request"]
    assert captured["request"] == "Plan Tokyo"


# ---------- CLI rendering + error path ----------


def test_cli_renders_full_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_trace_dir: Path
) -> None:
    """Full CLI run with mocked agents — verifies all renderers don't raise."""
    def fake_run_intent(ctx, *, client=None):
        ctx.brief = _japan_brief()

    def fake_complete_plan(ctx, *, client=None):
        ctx.itinerary = Itinerary.model_validate(
            {"days": [{"day_number": 1, "city": "Tokyo",
                       "activities": [{"title": "Lunch", "duration_minutes": 60,
                                       "match_reasons": ["likes:food"]}]}],
             "confidence": "medium"}
        )
        ctx.critic_verdicts.append(CriticVerdict(passed=True, revision=0))

    monkeypatch.setattr(cli, "run_intent", fake_run_intent)
    monkeypatch.setattr(cli, "complete_plan", fake_complete_plan)
    rc = cli.run("Plan a Japan trip.", no_clarify=True)
    assert rc == 0


def test_cli_renders_partial_brief_on_planning_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_trace_dir: Path
) -> None:
    """If complete_plan blows up, the CLI should still render the brief."""
    def fake_run_intent(ctx, *, client=None):
        ctx.brief = _japan_brief()

    def boom(ctx, *, client=None):
        raise RuntimeError("itinerary explosion")

    monkeypatch.setattr(cli, "run_intent", fake_run_intent)
    monkeypatch.setattr(cli, "complete_plan", boom)
    rc = cli.run("anything", no_clarify=True)
    assert rc == 1


def test_cli_returns_1_on_intent_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_trace_dir: Path
) -> None:
    def boom(ctx, *, client=None):
        raise RuntimeError("GEMINI_API_KEY is not set.")

    monkeypatch.setattr(cli, "run_intent", boom)
    rc = cli.run("anything", no_clarify=True)
    assert rc == 1


# ---------- Streamlit module smoke ----------


def test_streamlit_module_importable() -> None:
    """The streamlit_app module must at least parse + import without errors.

    We can't run the actual UI without a Streamlit server, but a successful
    import catches typos, missing imports, and syntax errors at minimum.
    """
    import importlib

    mod = importlib.import_module("src.ui.streamlit_app")
    # Sanity: a few helpers exist
    assert hasattr(mod, "_run_pipeline")
    assert hasattr(mod, "_render_brief")
    assert hasattr(mod, "_render_itinerary")
