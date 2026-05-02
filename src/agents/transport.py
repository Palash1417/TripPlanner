"""Transport Agent — plans inter-city movement.

Phase 4c deliverable. Phase 6e: enriched with curated route data when known.
"""

from __future__ import annotations

import json
from itertools import combinations
from typing import Optional, TYPE_CHECKING

from src.llm import LLMClient
from src.schemas import TransportPlan
from src.tools import transport_lookup

if TYPE_CHECKING:
    from src.orchestrator.trip_context import TripContext


SYSTEM_PROMPT = """You are the Transport Agent.

Given a TripBrief (JSON) and OPTIONAL `curated_routes` data (real-world numbers
for known city pairs), produce inter-city TransportLegs that connect the
destinations in a sensible order.

HARD RULES:
1. The legs MUST form a connected path through every city in `destinations`.
   Pick the geographically efficient ordering — don't zigzag.
2. NEVER invent an origin city. If `brief.origin` is null, generate ONLY
   inter-city legs between cities in `destinations`. For a single destination
   with null origin, return a single self-loop leg (origin == destination ==
   that city, mode "walk", duration_minutes 0, cost_usd 0, notes "no inter-city
   travel — origin not specified") so the schema's min_length=1 constraint is
   satisfied without fabricating a real journey.
3. If `brief.origin` IS set, include the outbound and return legs to/from origin.
4. `mode` MUST be one of: flight, train, bus, car, ferry, walk, subway, taxi.
5. `duration_minutes` and `cost_usd` are realistic estimates.

GROUNDING:
5. If a city pair appears in `curated_routes`, USE those exact mode/duration
   and pick a `cost_usd` within the [cost_usd_min, cost_usd_max] range.
   Set the leg's `confidence` to "high" and copy the `notes` verbatim.
6. For city pairs NOT in `curated_routes`, estimate from your world knowledge.
   Set `confidence` to "medium".
7. Cross-country / international legs are typically flights; short hops train/bus;
   island/coastal ferry.

OUTPUT:
- Return ONLY the TransportPlan JSON object — no prose, no fences.
"""


def _scrub_invented_origin(plan: TransportPlan, brief) -> TransportPlan:
    """Drop legs that reference a city outside `destinations` when origin is null.

    Belt-and-suspenders against the LLM hallucinating an origin (e.g. inferring
    Mumbai for a Goa trip). The Intent agent should have surfaced an
    open_question before we got here, but if it didn't and origin is genuinely
    null, do not let the Transport agent invent one.
    """
    if brief.origin:
        return plan
    allowed = {c.lower() for c in brief.destinations}
    kept = [
        leg for leg in plan.legs
        if leg.origin.lower() in allowed and leg.destination.lower() in allowed
    ]
    if not kept:
        # Schema requires min_length=1; emit a no-op self-loop so we never crash.
        first_city = brief.destinations[0]
        from src.schemas import TransportLeg
        kept = [
            TransportLeg(
                origin=first_city,
                destination=first_city,
                mode="walk",
                duration_minutes=0,
                cost_usd=0.0,
                notes="no inter-city travel — origin not specified",
                confidence="low",
            )
        ]
    return TransportPlan(legs=kept)


def _curated_for(brief) -> dict:
    """Look up curated routes for every pairwise combination of destinations."""
    cities = list(brief.destinations)
    if brief.origin:
        cities.append(brief.origin)
    out = {}
    for a, b in combinations(set(cities), 2):
        record = transport_lookup.lookup(a, b)
        if record is not None:
            key = f"{a} ↔ {b}"
            out[key] = {
                "mode": record.mode,
                "duration_minutes": record.duration_minutes,
                "cost_usd_min": record.cost_usd_min,
                "cost_usd_max": record.cost_usd_max,
                "notes": record.notes,
            }
    return out


def run(
    context: "TripContext",
    *,
    client: Optional[LLMClient] = None,
) -> TransportPlan:
    if context.brief is None:
        raise RuntimeError("transport requires a parsed TripBrief; run intent first")

    client = client or LLMClient()
    brief_json = context.brief.model_dump_json(indent=2)
    curated = _curated_for(context.brief)

    payload = {
        "brief": json.loads(brief_json),
        "curated_routes": curated,
    }
    user = json.dumps(payload, indent=2, default=str)

    with context.tracer.span(
        "transport",
        input_payload={
            "brief": context.brief.model_dump(mode="json"),
            "curated_route_count": len(curated),
        },
    ) as span:
        response = client.call(
            system=SYSTEM_PROMPT,
            user=f"Inputs:\n{user}",
            tier="smart",
            schema=TransportPlan,
            max_tokens=2048,
        )
        span.record_llm(response)
        plan: TransportPlan = response.parsed  # type: ignore[assignment]
        plan = _scrub_invented_origin(plan, context.brief)
        span.set_output(plan.model_dump(mode="json"))

    context.transport_plan = plan
    context.log_decision(
        f"transport: {len(plan.legs)} leg(s), total ~${plan.total_usd:.0f}"
        + (f", {len(curated)} curated route(s) used" if curated else "")
    )
    return plan
