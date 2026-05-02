"""Geographic utilities — Haversine great-circle distance.

Phase 6a deliverable. No API key needed. Used by Critic to validate
intra-day distance feasibility when POIs carry coordinates.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

EARTH_RADIUS_KM = 6371.0088


def haversine_km(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Great-circle distance in kilometers between two (lat, lon) points."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def distance_between(
    p1: Optional[Tuple[Optional[float], Optional[float]]],
    p2: Optional[Tuple[Optional[float], Optional[float]]],
) -> Optional[float]:
    """Return distance in km, or None if either point is missing coordinates."""
    if p1 is None or p2 is None:
        return None
    lat1, lon1 = p1
    lat2, lon2 = p2
    if any(v is None for v in (lat1, lon1, lat2, lon2)):
        return None
    return haversine_km(lat1, lon1, lat2, lon2)
