"""Execution graph — wires agents together.

Phase 3 (M1): linear intent → itinerary.
Phase 4 (M2): intent → [destination ‖ accommodation ‖ transport] → budget → itinerary.
Phase 5 (M3): + critic; on fail, re-run guilty agents up to 2 revision rounds.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from src.agents import (
    accommodation,
    budget,
    critic,
    destination,
    intent,
    itinerary,
    transport,
)
from src.llm import LLMClient, QuotaExhaustedError
from src.observability import Tracer
from src.orchestrator.trip_context import TripContext

_MAX_REVISIONS = 2


def plan_trip(
    user_request: str,
    *,
    client: Optional[LLMClient] = None,
    tracer: Optional[Tracer] = None,
) -> TripContext:
    """Run the full Phase 4 graph from raw user text to a complete TripContext."""
    context = TripContext(user_request=user_request, tracer=tracer or Tracer())
    return run_graph(context, client=client)


def run_intent(
    context: TripContext,
    *,
    client: Optional[LLMClient] = None,
):
    """Phase 7 split — Intent only.

    Returns control to the caller after parsing so a UI can inspect
    `context.brief.open_questions` and clarify before specialists run.
    """
    client = client or LLMClient()
    return intent.run(context, client=client)


def complete_plan(
    context: TripContext,
    *,
    client: Optional[LLMClient] = None,
) -> TripContext:
    """Run specialists → budget → itinerary → critic loop (with revision cap).

    Requires `context.brief` to be set (i.e. run_intent has already executed).
    """
    if context.brief is None:
        raise RuntimeError("complete_plan requires a parsed TripBrief; run intent first")

    client = client or LLMClient()
    context.current_revision = 0

    try:
        _fan_out_specialists(context, client=client)
        budget.run(context, client=client)
        itinerary.run(context, client=client)
        verdict = critic.run(context, client=client)

        for rev in range(1, _MAX_REVISIONS + 1):
            if verdict.passed:
                break
            context.current_revision = rev
            try:
                _retarget(context, verdict, client=client)
                verdict = critic.run(context, client=client)
            except QuotaExhaustedError as e:
                # Daily quota hit mid-revision — keep the best-effort pre-revision
                # plan (already on `context`) rather than discarding the whole run.
                context.log_decision(
                    f"critic r{rev}: skipped — Gemini daily quota exhausted ({e}); "
                    f"returning pre-revision plan"
                )
                break

        if not verdict.passed:
            context.log_decision(
                f"critic: gave up after {_MAX_REVISIONS} revision(s); "
                f"returning best-effort plan"
            )
    finally:
        emit_trip_summary(context)

    return context


def emit_trip_summary(context: TripContext) -> None:
    """Write the run-level `trip_summary` trace event for `context`.

    Idempotent-ish: callers should invoke this exactly once at the end of a
    planning run. The CLI fires it from its outer error handler when a run
    aborts before `complete_plan` could install its own finally block.
    """
    latest = context.latest_verdict
    context.tracer.emit_trip_summary(
        {
            "request": context.user_request,
            "destinations": context.brief.destinations if context.brief else None,
            "duration_days": context.brief.duration_days if context.brief else None,
            "open_questions": (
                list(context.brief.open_questions) if context.brief else []
            ),
            "itinerary_built": context.itinerary is not None,
            "critic_passed": latest.passed if latest else None,
            "critic_revisions": len(context.critic_verdicts),
            "critic_violations": (
                [
                    {"rule": v.rule, "agent": v.agent, "severity": v.severity}
                    for v in latest.violations
                ]
                if latest
                else []
            ),
            "decisions": list(context.decisions),
        }
    )


def run_graph(
    context: TripContext,
    *,
    client: Optional[LLMClient] = None,
) -> TripContext:
    """Full pipeline: intent → specialists → budget → itinerary → critic loop."""
    client = client or LLMClient()
    run_intent(context, client=client)
    return complete_plan(context, client=client)


def _retarget(
    context: TripContext, verdict, *, client: LLMClient
) -> None:
    """Re-run the agents implicated by the most recent critic verdict.

    Phase 5 v1: simple mapping — budget violations re-run budget; everything
    else (and budget too) re-runs the itinerary, since itinerary is the final
    integrator and most violations live in the day plan.
    """
    guilty = {v.agent for v in verdict.violations if v.severity == "fail"}
    if "budget" in guilty:
        budget.run(context, client=client)
    itinerary.run(context, client=client)


def _fan_out_specialists(context: TripContext, *, client: LLMClient) -> None:
    """Run destination, accommodation, transport — sequentially by default,
    in parallel if TRIP_PLANNER_PARALLEL=1 is set.

    Free-tier Gemini caps Flash at 5 RPM, so 3 parallel calls + intent + budget
    + itinerary easily bursts the limit. Sequential is reliable; parallel is
    opt-in for users on higher quota or running locally with no rate limits.
    """
    specialists = {
        "destination": destination.run,
        "accommodation": accommodation.run,
        "transport": transport.run,
    }

    if os.getenv("TRIP_PLANNER_PARALLEL") == "1":
        _run_parallel(specialists, context, client=client)
    else:
        _run_sequential(specialists, context, client=client)


def _run_sequential(
    specialists: dict, context: TripContext, *, client: LLMClient
) -> None:
    errors: dict[str, BaseException] = {}
    for name, fn in specialists.items():
        try:
            fn(context, client=client)
        except BaseException as e:  # noqa: BLE001 — collected, re-raised at end
            errors[name] = e
    _raise_if_any(errors)


def _run_parallel(
    specialists: dict, context: TripContext, *, client: LLMClient
) -> None:
    errors: dict[str, BaseException] = {}
    with ThreadPoolExecutor(max_workers=len(specialists)) as executor:
        futures = {
            executor.submit(fn, context, client=client): name
            for name, fn in specialists.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                future.result()
            except BaseException as e:  # noqa: BLE001
                errors[name] = e
    _raise_if_any(errors)


def _raise_if_any(errors: dict[str, BaseException]) -> None:
    if not errors:
        return
    joined = "; ".join(f"{n}: {type(e).__name__}: {e}" for n, e in errors.items())
    first = next(iter(errors))
    raise RuntimeError(f"specialist fan-out failed [{joined}]") from errors[first]
