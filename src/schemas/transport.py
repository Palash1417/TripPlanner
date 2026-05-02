"""TransportLeg schema — output of Transport Agent."""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import Field

from .common import Confidence, StrictModel, TransportMode


class TransportLeg(StrictModel):
    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    mode: TransportMode
    duration_minutes: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    travel_date: Optional[date] = None
    match_reasons: list[str] = Field(default_factory=list)
    notes: Optional[str] = None
    confidence: Confidence = "medium"


class TransportPlan(StrictModel):
    """Output of the Transport Agent — inter-city legs in order."""

    legs: list[TransportLeg] = Field(min_length=1)

    @property
    def total_usd(self) -> float:
        return round(sum(leg.cost_usd for leg in self.legs), 2)
