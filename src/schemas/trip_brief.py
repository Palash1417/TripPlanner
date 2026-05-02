"""TripBrief — output of the Intent Agent. See architecture.md §3.1."""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import Field, field_validator, model_validator

from .common import Money, Pace, StrictModel


class Travelers(StrictModel):
    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)


class Preferences(StrictModel):
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    pace: Optional[Pace] = "moderate"
    accessibility: list[str] = Field(default_factory=list)


class TripDates(StrictModel):
    start: Optional[date] = None
    end: Optional[date] = None
    flexible: bool = True

    @model_validator(mode="after")
    def _check_order(self) -> "TripDates":
        if self.start and self.end and self.end < self.start:
            raise ValueError("end date is before start date")
        return self


class TripBrief(StrictModel):
    origin: Optional[str] = None
    destinations: list[str] = Field(min_length=1)
    duration_days: Optional[int] = Field(default=None, ge=1, le=90)
    budget: Optional[Money] = None
    travelers: Travelers = Field(default_factory=Travelers)
    preferences: Preferences = Field(default_factory=Preferences)
    dates: TripDates = Field(default_factory=TripDates)
    open_questions: list[str] = Field(default_factory=list)

    @field_validator("destinations")
    @classmethod
    def _strip_destinations(cls, v: list[str]) -> list[str]:
        cleaned = [d.strip() for d in v if d and d.strip()]
        if not cleaned:
            raise ValueError("destinations cannot be empty after stripping")
        return cleaned
