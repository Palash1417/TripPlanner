"""StayOption schema — output of Accommodation Agent."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from .common import Confidence, StrictModel

PropertyType = Literal["hotel", "hostel", "apartment", "ryokan", "guesthouse", "resort", "other"]


class StayOption(StrictModel):
    name: str = Field(min_length=1, description="Property name OR neighborhood label")
    city: str = Field(min_length=1)
    neighborhood: str = Field(min_length=1)
    property_type: PropertyType = "hotel"
    price_per_night_usd: float = Field(ge=0)
    # nights=0 is valid — represents an "alternative option" stay the user could
    # pick instead of a primary one. The agent often offers multiple choices.
    nights: int = Field(ge=0)
    match_reasons: list[str] = Field(min_length=1)
    accessibility_features: list[str] = Field(default_factory=list)
    confidence: Confidence = "medium"

    @property
    def total_usd(self) -> float:
        return round(self.price_per_night_usd * self.nights, 2)


class AccommodationPlan(StrictModel):
    """Output of the Accommodation Agent."""

    stays: list[StayOption] = Field(min_length=1)

    @property
    def total_usd(self) -> float:
        return round(sum(s.total_usd for s in self.stays), 2)
