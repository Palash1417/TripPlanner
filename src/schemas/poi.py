"""POI schema — output of Destination Research Agent. See architecture.md §3.2."""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from .common import Confidence, StrictModel


class POI(StrictModel):
    name: str = Field(min_length=1)
    city: str = Field(min_length=1)
    category: str = Field(min_length=1)
    match_reasons: list[str] = Field(min_length=1)
    est_visit_minutes: int = Field(ge=0, le=24 * 60)
    est_cost_usd: float = Field(default=0.0, ge=0)
    tags: list[str] = Field(default_factory=list)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    confidence: Confidence = "medium"


class DestinationCatalog(StrictModel):
    """Output of the Destination Research Agent — flat list, each POI carries its city."""

    pois: list[POI] = Field(min_length=1)

    def by_city(self) -> dict[str, list[POI]]:
        grouped: dict[str, list[POI]] = {}
        for p in self.pois:
            grouped.setdefault(p.city, []).append(p)
        return grouped
