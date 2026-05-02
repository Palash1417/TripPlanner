"""ItineraryDay and Itinerary schemas — output of Itinerary Builder."""

from __future__ import annotations

from datetime import time
from typing import Optional

from pydantic import Field, model_validator

from .common import Confidence, StrictModel
from .transport import TransportLeg


class Activity(StrictModel):
    title: str = Field(min_length=1)
    start_time: Optional[time] = None
    duration_minutes: int = Field(ge=0, le=24 * 60)
    poi_name: Optional[str] = None
    notes: Optional[str] = None
    match_reasons: list[str] = Field(default_factory=list)
    est_cost_usd: float = Field(default=0.0, ge=0)


class ItineraryDay(StrictModel):
    day_number: int = Field(ge=1)
    city: str = Field(min_length=1)
    activities: list[Activity] = Field(default_factory=list)
    transport_leg: Optional[TransportLeg] = None
    notes: Optional[str] = None

    @property
    def total_activity_minutes(self) -> int:
        base = sum(a.duration_minutes for a in self.activities)
        if self.transport_leg is not None:
            base += self.transport_leg.duration_minutes
        return base


class Itinerary(StrictModel):
    days: list[ItineraryDay] = Field(min_length=1)
    summary: Optional[str] = None
    confidence: Confidence = "medium"

    @model_validator(mode="after")
    def _check_day_numbering(self) -> "Itinerary":
        expected = list(range(1, len(self.days) + 1))
        actual = [d.day_number for d in self.days]
        if actual != expected:
            raise ValueError(
                f"day_numbers must be sequential starting at 1; got {actual}, expected {expected}"
            )
        return self
