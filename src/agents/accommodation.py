"""Accommodation Agent — recommends stay neighborhoods + properties.

Phase 4b deliverable. LLM-only.
Runs in parallel with Destination/Transport so it uses only the brief.
"""

from __future__ import annotations

import json
from typing import Optional, TYPE_CHECKING

from src.llm import LLMClient
from src.schemas import AccommodationPlan
from src.tools import web_search

if TYPE_CHECKING:
    from src.orchestrator.trip_context import TripContext


SYSTEM_PROMPT = """You are the Accommodation Agent.

Given a TripBrief (JSON) and OPTIONAL `search_hints` (real web-search results for neighborhoods), produce 1-2 stay options per destination city — at the neighborhood level, with property type and a typical nightly price band.

When search_hints are provided, prefer neighborhoods that appear in them (real grounding). Set those stays' confidence to "high".

HARD RULES:
1. Every city in the brief's `destinations` MUST have ≥ 1 StayOption.
2. Every StayOption MUST include ≥ 1 entry in `match_reasons` referencing a like, dislike, accessibility need, or destination.
3. `nights`: distribute the brief's `duration_days` across cities sensibly (e.g., 5 days for Tokyo+Kyoto → 3 Tokyo + 2 Kyoto).
4. The total `price_per_night_usd * nights` summed across all stays SHOULD fit within ~30-40% of `budget.amount` if specified (lodging is typically the biggest line item but not the only one).

QUALITY:
5. `name`: a real neighborhood label or property name (e.g., "Asakusa boutique hotel", "Higashiyama machiya").
6. `neighborhood`: the actual neighborhood (e.g., "Asakusa", "Higashiyama", "Shibuya").
7. `property_type`: hotel | hostel | apartment | ryokan | guesthouse | resort | other.
8. `accessibility_features`: populate if the brief lists accessibility needs.
9. Honor "hate crowds" → prefer quieter neighborhoods over tourist hubs.
10. `confidence`: "high" if a typical real range; "low" if you're guessing.

OUTPUT:
- Return ONLY the AccommodationPlan JSON object — no prose, no fences.
"""


def run(
    context: "TripContext",
    *,
    client: Optional[LLMClient] = None,
) -> AccommodationPlan:
    if context.brief is None:
        raise RuntimeError("accommodation requires a parsed TripBrief; run intent first")

    client = client or LLMClient()
    brief_json = context.brief.model_dump_json(indent=2)
    search_hints = _gather_search_hints(context.brief)

    payload = {"brief": json.loads(brief_json)}
    if search_hints:
        payload["search_hints"] = search_hints
    user = json.dumps(payload, indent=2, default=str)

    with context.tracer.span(
        "accommodation",
        input_payload={
            "brief": context.brief.model_dump(mode="json"),
            "search_hint_count": sum(len(h["results"]) for h in search_hints),
        },
    ) as span:
        response = client.call(
            system=SYSTEM_PROMPT,
            user=f"Inputs:\n{user}",
            tier="smart",
            schema=AccommodationPlan,
            max_tokens=4096,
        )
        span.record_llm(response)
        plan: AccommodationPlan = response.parsed  # type: ignore[assignment]
        span.set_output(plan.model_dump(mode="json"))

    context.accommodation_plan = plan
    grounding = " (web-grounded)" if search_hints else ""
    context.log_decision(
        f"accommodation: {len(plan.stays)} stay(s), total ~${plan.total_usd:.0f}{grounding}"
    )
    return plan


def _gather_search_hints(brief) -> list[dict]:
    if not web_search.is_available():
        return []
    hints: list[dict] = []
    for city in brief.destinations[:3]:
        query = f"best neighborhoods to stay in {city} for travelers"
        results = web_search.search(query, max_results=3)
        if results:
            hints.append({"city": city, "query": query, "results": results})
    return hints
