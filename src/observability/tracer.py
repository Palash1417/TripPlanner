"""Per-trip trace logger. Writes JSONL events to traces/<trip_id>.jsonl."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional


def new_trip_id() -> str:
    return f"trip_{uuid.uuid4().hex[:12]}"


@dataclass
class TraceEvent:
    trip_id: str
    agent: str
    event: str
    timestamp: float = field(default_factory=time.time)
    revision: int = 0
    latency_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    payload: dict[str, Any] = field(default_factory=dict)


class Tracer:
    def __init__(self, trip_id: Optional[str] = None, trace_dir: Optional[str] = None) -> None:
        self.trip_id = trip_id or new_trip_id()
        self._dir = Path(trace_dir or os.getenv("TRIP_PLANNER_TRACE_DIR", "traces"))
        self._path = self._dir / f"{self.trip_id}.jsonl"
        self._totals = {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}
        self._lock = threading.Lock()
        self._created = time.time()
        self._ensure_dir()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def totals(self) -> dict[str, Any]:
        return dict(self._totals)

    def _ensure_dir(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"[tracer] could not create {self._dir}: {e}", file=sys.stderr)

    def emit(self, event: TraceEvent) -> None:
        with self._lock:
            if event.cost_usd:
                self._totals["cost_usd"] += event.cost_usd
            if event.input_tokens:
                self._totals["input_tokens"] += event.input_tokens
            if event.output_tokens:
                self._totals["output_tokens"] += event.output_tokens

            try:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(event), default=str) + "\n")
            except OSError as e:
                print(
                    f"[tracer] could not write event for {self.trip_id}: {e}",
                    file=sys.stderr,
                )

    @contextmanager
    def span(
        self,
        agent: str,
        *,
        revision: int = 0,
        input_payload: Optional[dict[str, Any]] = None,
    ) -> Iterator["SpanRecorder"]:
        started = time.perf_counter()
        self.emit(
            TraceEvent(
                trip_id=self.trip_id,
                agent=agent,
                event="span_start",
                revision=revision,
                payload={"input": input_payload or {}},
            )
        )
        recorder = SpanRecorder()
        try:
            yield recorder
        except Exception as e:
            elapsed = int((time.perf_counter() - started) * 1000)
            self.emit(
                TraceEvent(
                    trip_id=self.trip_id,
                    agent=agent,
                    event="span_error",
                    revision=revision,
                    latency_ms=elapsed,
                    payload={"error": repr(e)},
                )
            )
            raise
        else:
            elapsed = int((time.perf_counter() - started) * 1000)
            self.emit(
                TraceEvent(
                    trip_id=self.trip_id,
                    agent=agent,
                    event="span_end",
                    revision=revision,
                    latency_ms=elapsed,
                    input_tokens=recorder.input_tokens,
                    output_tokens=recorder.output_tokens,
                    cost_usd=recorder.cost_usd,
                    payload={"output": recorder.output},
                )
            )

    def summary(self) -> dict[str, Any]:
        return {
            "trip_id": self.trip_id,
            "trace_path": str(self._path),
            **self._totals,
        }

    def emit_trip_summary(self, payload: dict[str, Any]) -> None:
        """Write a single `trip_summary` event capturing run-level totals.

        Called once per trip, after the orchestrator finishes (success or
        graceful failure). Aggregators in `scripts/quality_report.py` rely
        on this event to avoid recomputing totals from per-span rows.
        """
        wall_ms = int((time.time() - self._created) * 1000)
        self.emit(
            TraceEvent(
                trip_id=self.trip_id,
                agent="orchestrator",
                event="trip_summary",
                payload={
                    "wall_ms": wall_ms,
                    "totals": dict(self._totals),
                    **payload,
                },
            )
        )


class SpanRecorder:
    def __init__(self) -> None:
        self.input_tokens: Optional[int] = None
        self.output_tokens: Optional[int] = None
        self.cost_usd: Optional[float] = None
        self.output: dict[str, Any] = {}

    def record_llm(self, response) -> None:
        self.input_tokens = getattr(response, "input_tokens", None)
        self.output_tokens = getattr(response, "output_tokens", None)
        self.cost_usd = getattr(response, "cost_usd", None)

    def set_output(self, output: dict[str, Any]) -> None:
        self.output = output
