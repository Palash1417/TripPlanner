"""Phase 0 smoke test — every module must be importable, tracer must work end-to-end."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

PHASE0_MODULES = [
    "src",
    "src.main",
    "src.llm",
    "src.llm.client",
    "src.observability",
    "src.observability.tracer",
]

PLACEHOLDER_MODULES = [
    "src.agents",
    "src.agents.intent",
    "src.agents.destination",
    "src.agents.accommodation",
    "src.agents.transport",
    "src.agents.budget",
    "src.agents.itinerary",
    "src.agents.critic",
    "src.orchestrator",
    "src.orchestrator.graph",
    "src.orchestrator.trip_context",
    "src.schemas",
    "src.schemas.trip_brief",
    "src.schemas.poi",
    "src.schemas.stay",
    "src.schemas.transport",
    "src.schemas.budget",
    "src.schemas.itinerary",
    "src.tools",
    "src.tools.web_search",
    "src.tools.place_details",
    "src.tools.geo",
    "src.tools.currency",
    "src.tools.transport_lookup",
    "src.ui",
    "src.ui.cli",
]


@pytest.mark.parametrize("module_name", PHASE0_MODULES + PLACEHOLDER_MODULES)
def test_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)


def test_tracer_writes_jsonl(tmp_trace_dir: Path) -> None:
    from src.observability import Tracer, TraceEvent

    tracer = Tracer(trace_dir=str(tmp_trace_dir))
    tracer.emit(
        TraceEvent(
            trip_id=tracer.trip_id,
            agent="test",
            event="manual",
            payload={"hello": "world"},
        )
    )

    trace_file = tmp_trace_dir / f"{tracer.trip_id}.jsonl"
    assert trace_file.exists()
    line = trace_file.read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["agent"] == "test"
    assert record["payload"] == {"hello": "world"}


def test_tracer_span_records_latency_and_output(tmp_trace_dir: Path) -> None:
    from src.observability import Tracer

    tracer = Tracer(trace_dir=str(tmp_trace_dir))
    with tracer.span("phase0_smoke", input_payload={"x": 1}) as span:
        span.set_output({"y": 2})

    lines = (tmp_trace_dir / f"{tracer.trip_id}.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert [e["event"] for e in events] == ["span_start", "span_end"]
    assert events[1]["latency_ms"] is not None
    assert events[1]["payload"]["output"] == {"y": 2}


def test_tracer_span_records_error(tmp_trace_dir: Path) -> None:
    from src.observability import Tracer

    tracer = Tracer(trace_dir=str(tmp_trace_dir))
    with pytest.raises(ValueError):
        with tracer.span("explody"):
            raise ValueError("boom")

    events = [
        json.loads(line)
        for line in (tmp_trace_dir / f"{tracer.trip_id}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["event"] == "span_error"
    assert "ValueError" in events[-1]["payload"]["error"]


def test_llm_client_raises_without_api_key() -> None:
    from src.llm import LLMClient

    client = LLMClient(api_key=None)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        client.call(system="s", user="u")


def test_generate_with_retry_succeeds_after_transient_503(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from src.llm import client as client_mod

    monkeypatch.setattr(client_mod.time, "sleep", lambda _: None)

    transient = type("E", (Exception,), {"code": 503})("503 UNAVAILABLE: high demand")
    success = MagicMock(name="response")

    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = [transient, transient, success]

    result = client_mod._generate_with_retry(
        fake_client, model="m", contents="x", config=None
    )
    assert result is success
    assert fake_client.models.generate_content.call_count == 3


def test_generate_with_retry_propagates_non_transient(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from src.llm import client as client_mod

    monkeypatch.setattr(client_mod.time, "sleep", lambda _: None)

    perm = type("E", (Exception,), {"code": 400})("400 INVALID_ARGUMENT")
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = perm

    with pytest.raises(Exception, match="400"):
        client_mod._generate_with_retry(fake_client, model="m", contents="x", config=None)
    assert fake_client.models.generate_content.call_count == 1


def test_generate_with_retry_gives_up_after_max(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from src.llm import client as client_mod

    monkeypatch.setattr(client_mod.time, "sleep", lambda _: None)

    transient = type("E", (Exception,), {"code": 503})("503")
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = [transient] * 10

    with pytest.raises(Exception, match="503"):
        client_mod._generate_with_retry(fake_client, model="m", contents="x", config=None)
    assert fake_client.models.generate_content.call_count == client_mod._MAX_RETRIES + 1


def test_generate_with_retry_raises_quota_exhausted_on_daily_429(monkeypatch) -> None:
    """Daily-cap 429s (quotaId contains `PerDay`) must fail fast, not retry."""
    from unittest.mock import MagicMock

    from src.llm import client as client_mod
    from src.llm import QuotaExhaustedError

    monkeypatch.setattr(client_mod.time, "sleep", lambda _: None)

    daily_msg = (
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, "
        "'details': [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure', "
        "'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/"
        "generate_content_free_tier_requests', 'quotaId': "
        "'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}, "
        "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
        "'retryDelay': '59s'}]}}"
    )
    daily_429 = type("E", (Exception,), {"code": 429})(daily_msg)
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = daily_429

    with pytest.raises(QuotaExhaustedError):
        client_mod._generate_with_retry(fake_client, model="gemini-2.5-flash", contents="x", config=None)
    # Critical: did NOT retry — quota won't reset within retry budget.
    assert fake_client.models.generate_content.call_count == 1


def test_llm_client_parses_json_with_fences() -> None:
    """JSON extraction tolerates ```json fences."""
    from pydantic import BaseModel

    from src.llm.client import LLMClient

    class Demo(BaseModel):
        x: int
        y: str

    fenced = '```json\n{"x": 1, "y": "hi"}\n```'
    parsed = LLMClient._parse_or_raise(fenced, Demo)
    assert parsed.x == 1 and parsed.y == "hi"


def test_pydantic_to_gemini_schema_strips_unsupported_keys() -> None:
    """Gemini rejects additionalProperties and $refs — sanitizer must remove them."""
    from src.llm.client import _pydantic_to_gemini_schema
    from src.schemas import TripBrief

    schema = _pydantic_to_gemini_schema(TripBrief)

    def walk(node):
        if isinstance(node, dict):
            assert "additionalProperties" not in node
            assert "$ref" not in node
            assert "$defs" not in node
            assert "definitions" not in node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    # Sanity: nested model fields like `budget` got inlined
    assert "budget" in schema["properties"]
    assert "type" in schema["properties"]["budget"] or "anyOf" in schema["properties"]["budget"]


def test_main_prints_usage_when_no_args(capsys: pytest.CaptureFixture[str]) -> None:
    from src.main import main

    rc = main([])
    assert rc == 2
    assert "usage" in capsys.readouterr().out.lower()
