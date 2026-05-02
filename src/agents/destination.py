"""Destination Research Agent — finds POIs matching preferences.

Phase 4a deliverable. LLM-only (Phase 6 will add web search + place details).
"""

from __future__ import annotations

import json
from typing import Optional, TYPE_CHECKING

from src.llm import LLMClient
from src.schemas import DestinationCatalog
from src.tools import web_search

if TYPE_CHECKING:
    from src.orchestrator.trip_context import TripContext


SYSTEM_PROMPT = """You are the Destination Research Agent.

Given a TripBrief (JSON) and OPTIONAL `search_hints` (real web-search results), produce a list of POIs (points of interest) covering each city in `destinations` — attractions, neighborhoods, landmarks, museums, food destinations, parks.

When search_hints are provided, prefer POIs that appear in them — those are grounded in current real data. Set their `confidence` to "high". Use your world knowledge to fill gaps, with `confidence` "medium".

HARD RULES:
1. Return 4-8 POIs per city in the brief's `destinations`.
2. Every POI MUST include ≥ 1 entry in `match_reasons` that references a `like`, `dislike`, or destination from the brief. Format like "likes:food", "dislikes:crowds → off-peak", "in Kyoto".
3. POI names must be REAL well-known places. Do not invent fictional locations.
4. POI `city` MUST exactly match one of the brief's `destinations`.

QUALITY:
5. `est_visit_minutes`: realistic per-POI duration (60-180 typical for attractions, 90-180 for museums, 30-60 for shrines/parks).
6. `est_cost_usd`: 0 for free attractions; otherwise typical entry/dining cost in USD.
7. `tags`: short labels — "outdoor", "early-morning", "rainy-day", "family-friendly", "vegetarian", "wheelchair-accessible".
8. `lat`/`lon`: only set if confident in rough coordinates (3-4 decimals); otherwise leave null.
9. `confidence`: "high" if you know the POI well; "medium" or "low" otherwise.
10. Honor dislikes — if "crowds" is a dislike, prefer lesser-known POIs OR add tag "off-peak" with a match_reason explaining timing.
11. Honor accessibility — if accessibility list is non-empty, only include POIs that meet those needs.

OUTPUT:
- Return ONLY the DestinationCatalog JSON object — no prose, no fences.
"""


def run(
    context: "TripContext",
    *,
    client: Optional[LLMClient] = None,
) -> DestinationCatalog:
    if context.brief is None:
        raise RuntimeError("destination requires a parsed TripBrief; run intent first")

    client = client or LLMClient()
    brief_json = context.brief.model_dump_json(indent=2)
    search_hints = _gather_search_hints(context.brief)

    payload = {"brief": json.loads(brief_json)}
    if search_hints:
        payload["search_hints"] = search_hints
    user = json.dumps(payload, indent=2, default=str)

    with context.tracer.span(
        "destination",
        input_payload={
            "brief": context.brief.model_dump(mode="json"),
            "search_hint_count": sum(len(h["results"]) for h in search_hints),
        },
    ) as span:
        response = client.call(
            system=SYSTEM_PROMPT,
            user=f"Inputs:\n{user}",
            tier="smart",
            schema=DestinationCatalog,
            max_tokens=8192,
        )
        span.record_llm(response)
        catalog: DestinationCatalog = response.parsed  # type: ignore[assignment]
        span.set_output(catalog.model_dump(mode="json"))

    context.destination_catalog = catalog
    by_city = catalog.by_city()
    grounding = " (web-grounded)" if search_hints else ""
    context.log_decision(
        f"destination: {len(catalog.pois)} POI(s) across {len(by_city)} city/cities{grounding}"
    )
    return catalog


def _gather_search_hints(brief) -> list[dict]:
    """If a search provider is configured, ground POI suggestions in real results.
    No-op when no key is set — caller falls back to LLM-only output.
    """
    if not web_search.is_available():
        return []
    hints: list[dict] = []
    likes = brief.preferences.likes[:2] if brief.preferences.likes else ["things to do"]
    for city in brief.destinations[:3]:
        for like in likes:
            query = f"top {like} in {city}"
            results = web_search.search(query, max_results=3)
            if results:
                hints.append({"city": city, "query": query, "results": results})
    return hints
