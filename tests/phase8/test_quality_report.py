"""Phase 8 unit tests — trip-summary emission + quality_report aggregation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import quality_report
from src.llm.client import LLMResponse
from src.observability import Tracer
from src.orchestrator import TripContext, complete_plan, emit_trip_summary, run_graph
from src.schemas import (
    AccommodationPlan,
    BudgetBreakdown,
    DestinationCatalog,
    Itinerary,
    TransportPlan,
    TripBrief,
)


# ---------- helpers (kept self-contained; phase 5 file isn't importable) ----------


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
                "title": "Temple visit",
                "duration_minutes": 90,
                "match_reasons": ["likes:temples", "dislikes:crowds → early"],
            },
        ],
    }


def _build_fixtures() -> dict:
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
                {
                    "name": "Fushimi Inari",
                    "city": "Kyoto",
                    "category": "temple",
                    "match_reasons": ["likes:temples"],
                    "est_visit_minutes": 90,
                },
            ]
        }
    )
    accommodation = AccommodationPlan.model_validate(
        {
            "stays": [
                {
                    "name": "Asakusa Hotel",
                    "city": "Tokyo",
                    "neighborhood": "Asakusa",
                    "property_type": "hotel",
                    "price_per_night_usd": 150.0,
                    "nights": 3,
                    "match_reasons": ["dislikes:crowds → quieter"],
                },
                {
                    "name": "Higashiyama Ryokan",
                    "city": "Kyoto",
                    "neighborhood": "Higashiyama",
                    "property_type": "ryokan",
                    "price_per_night_usd": 200.0,
                    "nights": 2,
                    "match_reasons": ["likes:temples → walkable"],
                },
            ]
        }
    )
    transport = TransportPlan.model_validate(
        {
            "legs": [
                {
                    "origin": "Tokyo",
                    "destination": "Kyoto",
                    "mode": "train",
                    "duration_minutes": 135,
                    "cost_usd": 130.0,
                    "match_reasons": [],
                    "notes": "Shinkansen",
                    "confidence": "high",
                },
            ]
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
    day4 = _good_day(4, "Kyoto")
    day4["transport_leg"] = {
        "origin": "Tokyo",
        "destination": "Kyoto",
        "mode": "train",
        "duration_minutes": 135,
        "cost_usd": 130.0,
        "match_reasons": [],
        "notes": "Shinkansen",
        "confidence": "high",
    }
    itinerary = Itinerary.model_validate(
        {
            "days": [
                _good_day(1, "Tokyo"),
                _good_day(2, "Tokyo"),
                _good_day(3, "Tokyo"),
                day4,
                _good_day(5, "Kyoto"),
            ],
            "summary": "test",
            "confidence": "medium",
        }
    )
    return {
        "brief": _japan_brief(),
        "catalog": catalog,
        "accommodation": accommodation,
        "transport": transport,
        "budget": in_budget,
        "itinerary": itinerary,
    }


def _patched_client(fixtures: dict) -> MagicMock:
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
            return _mock_response(fixtures["budget"])
        if schema is Itinerary:
            return _mock_response(fixtures["itinerary"])
        raise AssertionError(f"unexpected schema {schema}")

    fake.call.side_effect = fake_call
    return fake


def _read_summary(trace_path: Path) -> dict:
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("event") == "trip_summary":
            return event
    raise AssertionError(f"no trip_summary event in {trace_path}")


# ---------- trip-summary emission ----------


def test_trip_summary_emitted_after_full_run(tmp_trace_dir: Path) -> None:
    ctx = TripContext(user_request="japan", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    fake = _patched_client(_build_fixtures())

    run_graph(ctx, client=fake)

    summary = _read_summary(Path(ctx.tracer.path))
    payload = summary["payload"]
    assert summary["agent"] == "orchestrator"
    assert payload["critic_passed"] is True
    assert payload["itinerary_built"] is True
    assert payload["destinations"] == ["Tokyo", "Kyoto"]
    assert payload["duration_days"] == 5
    assert payload["wall_ms"] >= 0
    # Totals reflect the mocked cost — 6 spec calls × $0.001 each
    assert payload["totals"]["cost_usd"] == pytest.approx(0.006, rel=1e-3)
    assert payload["totals"]["input_tokens"] == 6 * 300


def test_trip_summary_emitted_even_when_complete_plan_raises(
    tmp_trace_dir: Path,
) -> None:
    """Failure mid-pipeline must still emit a trip_summary so the run shows
    up in quality_report aggregates instead of silently disappearing."""
    ctx = TripContext(user_request="japan", tracer=Tracer(trace_dir=str(tmp_trace_dir)))
    ctx.brief = _japan_brief()  # skip intent — go straight to specialists

    fake = MagicMock()
    fake.call.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        complete_plan(ctx, client=fake)

    summary = _read_summary(Path(ctx.tracer.path))
    payload = summary["payload"]
    assert payload["itinerary_built"] is False
    assert payload["critic_passed"] is None  # no verdict was reached
    assert payload["destinations"] == ["Tokyo", "Kyoto"]


def test_emit_trip_summary_records_open_questions(tmp_trace_dir: Path) -> None:
    """Intent-only paths (no specialists run) should still produce a useful summary."""
    tracer = Tracer(trace_dir=str(tmp_trace_dir))
    ctx = TripContext(user_request="weekend somewhere warm", tracer=tracer)
    ctx.brief = TripBrief.model_validate(
        {
            "destinations": ["TBD"],
            "open_questions": ["Where exactly?", "How many travelers?"],
        }
    )

    emit_trip_summary(ctx)

    summary = _read_summary(Path(tracer.path))
    assert summary["payload"]["open_questions"] == [
        "Where exactly?",
        "How many travelers?",
    ]
    assert summary["payload"]["itinerary_built"] is False


# ---------- quality_report aggregation ----------


def _write_summary_only_trace(
    dir_: Path,
    trip_id: str,
    *,
    cost_usd: float = 0.005,
    wall_ms: int = 12_000,
    critic_passed: bool | None = True,
    revisions: int = 0,
    violations: list[dict] | None = None,
) -> Path:
    path = dir_ / f"{trip_id}.jsonl"
    event = {
        "trip_id": trip_id,
        "agent": "orchestrator",
        "event": "trip_summary",
        "payload": {
            "wall_ms": wall_ms,
            "totals": {
                "cost_usd": cost_usd,
                "input_tokens": 1500,
                "output_tokens": 2000,
            },
            "critic_passed": critic_passed,
            "critic_revisions": revisions,
            "critic_violations": violations or [],
            "itinerary_built": critic_passed is not None,
            "destinations": ["Tokyo"],
            "duration_days": 5,
            "open_questions": [],
            "decisions": [],
            "request": "test",
        },
    }
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    return path


def test_quality_report_aggregates_pass_rate_and_costs(tmp_path: Path) -> None:
    _write_summary_only_trace(tmp_path, "trip_a", cost_usd=0.004, wall_ms=10_000)
    _write_summary_only_trace(tmp_path, "trip_b", cost_usd=0.006, wall_ms=14_000)
    _write_summary_only_trace(
        tmp_path,
        "trip_c",
        cost_usd=0.010,
        wall_ms=20_000,
        critic_passed=False,
        revisions=2,
        violations=[
            {"rule": "budget", "agent": "budget", "severity": "fail"},
            {"rule": "coverage", "agent": "destination", "severity": "fail"},
        ],
    )

    report = quality_report.build_report(quality_report.load_trips(tmp_path))

    assert report["trips_total"] == 3
    assert report["trips_judged"] == 3
    assert report["critic_pass_rate"] == pytest.approx(2 / 3)
    assert report["cost_usd"]["total"] == pytest.approx(0.020)
    assert report["cost_usd"]["mean"] == pytest.approx(0.020 / 3)
    assert report["wall_ms"]["max"] == 20_000
    assert dict(report["top_failure_rules"]) == {"budget": 1, "coverage": 1}
    assert dict(report["top_failure_agents"]) == {"budget": 1, "destination": 1}
    assert report["revisions"]["max"] == 2
    assert report["revisions"]["rev0_pass"] == 2  # trip_a + trip_b


def test_quality_report_handles_empty_directory(tmp_path: Path) -> None:
    report = quality_report.build_report(quality_report.load_trips(tmp_path))
    assert report["trips_total"] == 0
    assert report["critic_pass_rate"] is None
    assert report["cost_usd"]["mean"] is None
    assert report["top_failure_rules"] == []


def test_quality_report_skips_traces_without_summary(tmp_path: Path) -> None:
    """Old traces (pre-Phase-8) lack trip_summary events — they should be
    counted in `trips_total` but not pollute the pass-rate or cost stats."""
    _write_summary_only_trace(tmp_path, "trip_new")
    legacy = tmp_path / "trip_old.jsonl"
    legacy.write_text(
        json.dumps(
            {
                "trip_id": "trip_old",
                "agent": "intent",
                "event": "span_end",
                "payload": {"output": {}},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = quality_report.build_report(quality_report.load_trips(tmp_path))

    assert report["trips_total"] == 2
    assert report["trips_with_summary"] == 1
    assert report["trips_judged"] == 1
    assert report["critic_pass_rate"] == 1.0


def test_quality_report_counts_error_spans(tmp_path: Path) -> None:
    path = tmp_path / "trip_err.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "trip_id": "trip_err",
                        "agent": "destination",
                        "event": "span_error",
                        "payload": {"error": "boom"},
                    }
                ),
                json.dumps(
                    {
                        "trip_id": "trip_err",
                        "agent": "orchestrator",
                        "event": "trip_summary",
                        "payload": {
                            "wall_ms": 500,
                            "totals": {"cost_usd": 0, "input_tokens": 0, "output_tokens": 0},
                            "critic_passed": None,
                            "critic_revisions": 0,
                            "critic_violations": [],
                            "itinerary_built": False,
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = quality_report.build_report(quality_report.load_trips(tmp_path))
    assert report["trips_with_error_span"] == 1
    assert report["trips_judged"] == 0  # critic never reached


def test_quality_report_cli_renders_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_summary_only_trace(tmp_path, "trip_a")
    rc = quality_report.main([str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Trip Planner - Quality Report" in out
    assert "Critic pass rate" in out


def test_quality_report_cli_emits_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_summary_only_trace(tmp_path, "trip_a")
    rc = quality_report.main([str(tmp_path), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["trips_total"] == 1
    assert parsed["critic_pass_rate"] == 1.0
