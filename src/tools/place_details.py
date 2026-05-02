"""Place details — gated by GOOGLE_MAPS_API_KEY.

Phase 6d v1: stub returning None when no key is set, so callers fall back to
LLM-only output (and label confidence accordingly per edge case 8.4).
v2 will hit the real Maps API for hours / coordinates / business_status.
"""

from __future__ import annotations

import os
from typing import Optional, TypedDict


class PlaceDetails(TypedDict):
    name: str
    lat: float
    lon: float
    business_status: str  # "OPERATIONAL" | "CLOSED_PERMANENTLY" | "UNKNOWN"
    confidence: str


def is_available() -> bool:
    return bool(os.getenv("GOOGLE_MAPS_API_KEY"))


def lookup(name: str, city: str) -> Optional[PlaceDetails]:
    """Return details for a named place; None if no Maps key configured."""
    if not is_available():
        return None
    # Phase 6d v2: actually call Google Places API.
    return None
