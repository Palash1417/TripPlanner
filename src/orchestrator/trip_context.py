"""TripContext — shared blackboard for one planning run. See architecture.md §6.1.

Phase 2: brief.
Phase 3: + itinerary.
Phase 4: + destination_catalog, accommodation_plan, transport_plan, budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.observability import Tracer
from src.schemas import (
    AccommodationPlan,
    BudgetBreakdown,
    CriticVerdict,
    DestinationCatalog,
    Itinerary,
    TransportPlan,
    TripBrief,
)


@dataclass
class TripContext:
    user_request: str
    tracer: Tracer = field(default_factory=Tracer)
    brief: Optional[TripBrief] = None
    destination_catalog: Optional[DestinationCatalog] = None
    accommodation_plan: Optional[AccommodationPlan] = None
    transport_plan: Optional[TransportPlan] = None
    budget: Optional[BudgetBreakdown] = None
    itinerary: Optional[Itinerary] = None
    critic_verdicts: list[CriticVerdict] = field(default_factory=list)
    current_revision: int = 0
    decisions: list[str] = field(default_factory=list)

    @property
    def trip_id(self) -> str:
        return self.tracer.trip_id

    def log_decision(self, message: str) -> None:
        self.decisions.append(message)

    @property
    def latest_verdict(self) -> Optional[CriticVerdict]:
        return self.critic_verdicts[-1] if self.critic_verdicts else None

    def summary(self) -> dict:
        latest = self.latest_verdict
        return {
            "trip_id": self.trip_id,
            "request": self.user_request,
            "brief_extracted": self.brief is not None,
            "pois_found": len(self.destination_catalog.pois) if self.destination_catalog else 0,
            "stays_found": len(self.accommodation_plan.stays) if self.accommodation_plan else 0,
            "transport_legs": len(self.transport_plan.legs) if self.transport_plan else 0,
            "budget_built": self.budget is not None,
            "itinerary_built": self.itinerary is not None,
            "destinations": self.brief.destinations if self.brief else None,
            "open_questions": self.brief.open_questions if self.brief else None,
            "days": len(self.itinerary.days) if self.itinerary else None,
            "critic_passed": latest.passed if latest else None,
            "critic_revisions": len(self.critic_verdicts),
            "critic_violations": [v.message for v in latest.violations] if latest else [],
            "decisions": list(self.decisions),
            **self.tracer.totals,
        }
