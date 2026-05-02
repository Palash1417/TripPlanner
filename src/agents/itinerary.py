"""Itinerary Builder Agent — stitches a TripBrief into a day-by-day plan.

Phase 3 deliverable (M1): LLM-only, no specialists, no critic. Uses Gemini Pro
for the heavier reasoning. Phase 4 update: consume specialist outputs.
"""

from __future__ import annotations

import json
from typing import Optional, TYPE_CHECKING

from src.llm import LLMClient
from src.schemas import Itinerary

if TYPE_CHECKING:
    from src.orchestrator.trip_context import TripContext


SYSTEM_PROMPT = """You are the Itinerary Builder Agent for a travel planning system.

Given a TripBrief and OPTIONAL specialist outputs (destination_pois, accommodation_plan, transport_plan, budget), produce a day-by-day Itinerary. If specialists are present, prefer their concrete data over your general knowledge. If they're absent, fall back to your knowledge.

HARD RULES (output will be schema-validated and rejected if violated):
1. Total days MUST equal `duration_days`. If `duration_days` is null, pick a sensible default (5 days for one international city, 7 for two cities, etc.) and note the assumption in `summary`.
2. `day_number` MUST be sequential starting at 1.
3. Each `Activity.duration_minutes` is an integer in minutes between 0 and 1440.
4. Total scheduled minutes per day (including any transport_leg) MUST be ≤ 600 minutes (10 hours).

SOFT RULES (quality):
5. Distribute days across destinations sensibly given travel time between them. Don't put a transit day at the start or end of a city visit if it wastes the day.
6. Each day SHOULD have ≥ 2 activities, except arrival/departure days which may have 1 lighter activity.
7. Every activity SHOULD have ≥ 1 entry in `match_reasons` that ties to a `like`, `dislike`, or destination from the brief. Format like "likes:food", "dislikes:crowds → off-peak", "in Kyoto".
8. Honor dislikes: if "crowds" is a dislike, schedule popular POIs early morning (7-9 AM) or suggest off-peak alternatives.
9. Inter-city transit: when the city changes between consecutive days, set the LATER day's `transport_leg` with mode/duration/cost (e.g., Shinkansen Tokyo→Kyoto, ~135 min, ~$130 USD).
10. Respect budget: keep the implied total (transport + activities; lodging handled separately) loosely within `budget.amount` if specified. If you can't fit, set `confidence` to "low" and explain in `summary`.

OUTPUT:
- Return ONLY the Itinerary JSON object — no prose, no fences.
- `start_time` is "HH:MM" 24-hour format or null.
- `summary` is 1-3 sentences explaining the overall shape and any assumptions.
- Set `confidence` based on how confident you are in the plan (high if all constraints fit, low if you had to compromise).
"""


def run(
    context: "TripContext",
    *,
    client: Optional[LLMClient] = None,
) -> Itinerary:
    """Build an Itinerary from the context's TripBrief and store it on the context."""
    if context.brief is None:
        raise RuntimeError("itinerary requires a parsed TripBrief; run intent first")

    client = client or LLMClient()

    payload: dict = {"brief": context.brief.model_dump(mode="json")}
    if context.destination_catalog:
        payload["destination_pois"] = context.destination_catalog.model_dump(mode="json")
    if context.accommodation_plan:
        payload["accommodation_plan"] = context.accommodation_plan.model_dump(mode="json")
    if context.transport_plan:
        payload["transport_plan"] = context.transport_plan.model_dump(mode="json")
    if context.budget:
        payload["budget"] = context.budget.model_dump(mode="json")

    user = json.dumps(payload, indent=2, default=str)

    with context.tracer.span(
        "itinerary",
        input_payload={"specialist_inputs_present": list(payload.keys())},
    ) as span:
        response = client.call(
            system=SYSTEM_PROMPT,
            user=f"Inputs:\n{user}",
            tier="smart",
            schema=Itinerary,
            max_tokens=8192,
        )
        span.record_llm(response)
        itin: Itinerary = response.parsed  # type: ignore[assignment]
        span.set_output(itin.model_dump(mode="json"))

    context.itinerary = itin
    context.log_decision(
        f"itinerary: {len(itin.days)} day(s), confidence={itin.confidence}"
    )
    return itin
