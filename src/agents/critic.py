"""Critic Agent — validates draft plan against TripBrief and feasibility rules.

Phase 5 deliverable. Implements the 5 rules in architecture.md §7.1 as
deterministic Python checks. No LLM call needed for v1 — these are mechanical
properties of the data. Phase 6+ may add an LLM "feels rushed" check for
qualities not expressible as code.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from src.observability.tracer import TraceEvent
from src.schemas import CriticVerdict, Violation
from src.tools import currency, geo

if TYPE_CHECKING:
    from src.orchestrator.trip_context import TripContext


# Tunable thresholds — keep here for visibility.
_DAY_BALANCE_CEILING_MINUTES = 600  # 10h
_BUDGET_TOLERANCE_USD = 0.01
# Activities scattered farther than this within a single day need a transport_leg.
_INTRADAY_DISTANCE_CEILING_KM = 50.0


def run(
    context: "TripContext",
    *,
    client=None,  # accepted for symmetry with other agents; critic is deterministic
) -> CriticVerdict:
    if context.brief is None:
        raise RuntimeError("critic requires a parsed TripBrief")
    if context.itinerary is None:
        raise RuntimeError("critic requires a built Itinerary")

    revision = context.current_revision

    violations: list[Violation] = []
    violations.extend(_check_budget(context))
    violations.extend(_check_coverage(context))
    violations.extend(_check_avoidance(context))
    violations.extend(_check_geo_feasibility(context))
    violations.extend(_check_day_balance(context))

    passed = not any(v.severity == "fail" for v in violations)
    verdict = CriticVerdict(passed=passed, revision=revision, violations=violations)

    # Critic is fast + deterministic; no LLM cost. Emit a compact span manually
    # rather than going through Tracer.span (which assumes LLM-style usage).
    context.tracer.emit(
        TraceEvent(
            trip_id=context.tracer.trip_id,
            agent="critic",
            event="span_end",
            revision=revision,
            payload={"output": verdict.model_dump(mode="json")},
        )
    )

    context.critic_verdicts.append(verdict)
    context.log_decision(
        f"critic r{revision}: passed={passed}, "
        f"{len(violations)} violation(s)"
    )
    return verdict


# --- rule 1: budget --------------------------------------------------------


def _check_budget(ctx: "TripContext") -> list[Violation]:
    if ctx.brief.budget is None or ctx.budget is None:
        return []
    # Edge case 5.2: brief currency may not be USD. Normalize via the static-rates
    # tool before comparing. If the currency is unknown, skip the check rather
    # than crash — the LLM Budget Agent's own warnings still surface.
    try:
        brief_budget_usd = currency.convert(
            ctx.brief.budget.amount, ctx.brief.budget.currency, "USD"
        )
    except ValueError:
        return []
    over = ctx.budget.total_estimate_usd - brief_budget_usd
    if over > _BUDGET_TOLERANCE_USD:
        return [
            Violation(
                rule="budget",
                severity="fail",
                agent="budget",
                message=(
                    f"total ${ctx.budget.total_estimate_usd:.0f} USD exceeds "
                    f"budget ${brief_budget_usd:.0f} USD "
                    f"({ctx.brief.budget.amount:.0f} {ctx.brief.budget.currency}) "
                    f"by ${over:.0f}"
                ),
            )
        ]
    return []


# --- rule 2: coverage ------------------------------------------------------


def _check_coverage(ctx: "TripContext") -> list[Violation]:
    likes = [lk.lower().strip() for lk in ctx.brief.preferences.likes]
    if not likes:
        return []
    covered: set[str] = set()
    haystack = _all_match_reasons(ctx)
    for like in likes:
        for reason in haystack:
            if like in reason.lower():
                covered.add(like)
                break
    missing = [lk for lk in likes if lk not in covered]
    if missing:
        return [
            Violation(
                rule="coverage",
                severity="fail",
                agent="itinerary",
                message=f"likes not covered by any match_reasons: {', '.join(missing)}",
            )
        ]
    return []


# --- rule 3: avoidance -----------------------------------------------------


def _check_avoidance(ctx: "TripContext") -> list[Violation]:
    """A recommendation tagged with a dislike must include explicit mitigation
    (e.g., 'dislikes:crowds → off-peak'). Mitigation = arrow + non-empty text."""
    dislikes = [dk.lower().strip() for dk in ctx.brief.preferences.dislikes]
    if not dislikes:
        return []
    out: list[Violation] = []
    for reason in _all_match_reasons(ctx):
        low = reason.lower()
        for dk in dislikes:
            if dk not in low:
                continue
            # Look for a mitigation clause: "→", "->", "via", "by", "at"
            if not any(marker in low.split(dk, 1)[1] for marker in ("→", "->", " via ", " by ", " at ")):
                out.append(
                    Violation(
                        rule="avoidance",
                        severity="fail",
                        agent="itinerary",
                        message=(
                            f'recommendation references dislike "{dk}" without explicit '
                            f"mitigation: {reason!r}"
                        ),
                    )
                )
    return out


# --- rule 4: geographic feasibility ----------------------------------------


def _check_geo_feasibility(ctx: "TripContext") -> list[Violation]:
    """Two checks:
    1. (Cross-day) If day N's city changes from day N-1, day N must have a transport_leg.
    2. (Intra-day) If activities have coordinates and the maximum pairwise distance
       within a single day exceeds the ceiling, the day needs a transport_leg.
       Skipped silently if coordinates are absent.
    """
    out: list[Violation] = []
    if ctx.itinerary is None:
        return out
    days = ctx.itinerary.days

    # Build coord lookup from POIs so we can resolve activity → (lat, lon) by name.
    poi_coords = _poi_coord_lookup(ctx)

    # Cross-day check
    for i in range(1, len(days)):
        prev, curr = days[i - 1], days[i]
        if prev.city.lower() != curr.city.lower() and curr.transport_leg is None:
            out.append(
                Violation(
                    rule="geo_feasibility",
                    severity="fail",
                    agent="itinerary",
                    message=(
                        f"day {curr.day_number} switches cities "
                        f"({prev.city} → {curr.city}) but has no transport_leg"
                    ),
                )
            )

    # Intra-day check (only when ≥2 activities have known coordinates)
    for day in days:
        coords: list[tuple[float, float]] = []
        for act in day.activities:
            if act.poi_name and act.poi_name in poi_coords:
                coords.append(poi_coords[act.poi_name])
        if len(coords) < 2:
            continue
        max_km = _max_pairwise_km(coords)
        if max_km > _INTRADAY_DISTANCE_CEILING_KM and day.transport_leg is None:
            out.append(
                Violation(
                    rule="geo_feasibility",
                    severity="fail",
                    agent="itinerary",
                    message=(
                        f"day {day.day_number} activities span {max_km:.0f}km "
                        f"(> {_INTRADAY_DISTANCE_CEILING_KM:.0f}km) without a transport_leg"
                    ),
                )
            )
    return out


def _poi_coord_lookup(ctx: "TripContext") -> dict[str, tuple[float, float]]:
    if ctx.destination_catalog is None:
        return {}
    out: dict[str, tuple[float, float]] = {}
    for poi in ctx.destination_catalog.pois:
        if poi.lat is not None and poi.lon is not None:
            out[poi.name] = (poi.lat, poi.lon)
    return out


def _max_pairwise_km(points: list[tuple[float, float]]) -> float:
    biggest = 0.0
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            d = geo.haversine_km(points[i][0], points[i][1], points[j][0], points[j][1])
            if d > biggest:
                biggest = d
    return biggest


# --- rule 5: day balance ---------------------------------------------------


def _check_day_balance(ctx: "TripContext") -> list[Violation]:
    out: list[Violation] = []
    if ctx.itinerary is None:
        return out
    for day in ctx.itinerary.days:
        if day.total_activity_minutes > _DAY_BALANCE_CEILING_MINUTES:
            out.append(
                Violation(
                    rule="day_balance",
                    severity="fail",
                    agent="itinerary",
                    message=(
                        f"day {day.day_number} has {day.total_activity_minutes}m scheduled, "
                        f"over the {_DAY_BALANCE_CEILING_MINUTES}m ceiling"
                    ),
                )
            )
    return out


# --- helpers ---------------------------------------------------------------


def _all_match_reasons(ctx: "TripContext") -> list[str]:
    out: list[str] = []
    if ctx.itinerary:
        for day in ctx.itinerary.days:
            for act in day.activities:
                out.extend(act.match_reasons)
    if ctx.destination_catalog:
        for poi in ctx.destination_catalog.pois:
            out.extend(poi.match_reasons)
    if ctx.accommodation_plan:
        for stay in ctx.accommodation_plan.stays:
            out.extend(stay.match_reasons)
    return out
