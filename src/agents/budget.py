"""Budget Agent — allocates budget across categories, flags overruns.

Phase 4d deliverable. Consumes the parallel-fan-out outputs (destinations, stays,
transport) and any user budget from the brief.
"""

from __future__ import annotations

import json
from typing import Optional, TYPE_CHECKING

from src.llm import LLMClient
from src.schemas import BudgetBreakdown

if TYPE_CHECKING:
    from src.orchestrator.trip_context import TripContext


SYSTEM_PROMPT = """You are the Budget Agent.

Given a TripBrief and rough cost estimates from the specialist agents (lodging from accommodation_plan, transport from transport_plan, activity costs from destination_pois if listed), produce a BudgetBreakdown.

HARD RULES:
1. `currency` MUST be 3-letter ISO 4217 (USD by default if brief budget is unspecified).
2. `lines` MUST cover at least: lodging, transport, food, activities, buffer (5 lines minimum).
3. `total_estimate_usd` MUST equal sum of all `lines[].estimate_usd` (cross-validated by Pydantic — small rounding errors will fail).
4. If `budget_amount_usd` is set and `total_estimate_usd > budget_amount_usd`, populate `warnings` with a clear message naming the overrun amount.

QUALITY:
5. Use the actual numbers from accommodation_plan.total_usd and transport_plan.total_usd as inputs to lodging and transport lines.
6. Activities: sum `est_cost_usd * suggested_visits` from destination_pois, or estimate $30-80/day if no POIs given.
7. Food: typical $40-100/day per person depending on destination.
8. Buffer: at least 5% of brief budget (or 5% of total_estimate if no brief budget) for incidentals.
9. `notes`: brief justification per line (e.g., "5 nights × $150 avg").
10. `confidence` per line: "high" if from specialist data, "medium" or "low" if estimated.
11. If brief.budget is null, set budget_amount_usd to null and treat this as "estimate-only" mode (no warnings about overrun).

OUTPUT:
- Return ONLY the BudgetBreakdown JSON object — no prose, no fences.
"""


def run(
    context: "TripContext",
    *,
    client: Optional[LLMClient] = None,
) -> BudgetBreakdown:
    if context.brief is None:
        raise RuntimeError("budget requires a parsed TripBrief; run intent first")

    client = client or LLMClient()

    payload: dict = {"brief": context.brief.model_dump(mode="json")}
    if context.accommodation_plan:
        payload["accommodation_plan"] = context.accommodation_plan.model_dump(mode="json")
    if context.transport_plan:
        payload["transport_plan"] = context.transport_plan.model_dump(mode="json")
    if context.destination_catalog:
        payload["destination_pois"] = context.destination_catalog.model_dump(mode="json")

    user = json.dumps(payload, indent=2, default=str)

    with context.tracer.span("budget", input_payload={"specialist_inputs_present": list(payload.keys())}) as span:
        response = client.call(
            system=SYSTEM_PROMPT,
            user=f"Inputs:\n{user}",
            tier="smart",
            schema=BudgetBreakdown,
            max_tokens=2048,
        )
        span.record_llm(response)
        breakdown: BudgetBreakdown = response.parsed  # type: ignore[assignment]
        span.set_output(breakdown.model_dump(mode="json"))

    context.budget = breakdown
    context.log_decision(
        f"budget: total ~${breakdown.total_estimate_usd:.0f} {breakdown.currency}, "
        f"{len(breakdown.warnings)} warning(s)"
    )
    return breakdown
