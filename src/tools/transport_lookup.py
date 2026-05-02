"""Transport route lookup — curated common city pairs.

Phase 6e deliverable. Bidirectional, case-insensitive.
Returns None for routes not in the table; the agent then estimates with
medium confidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_ROUTES_PATH = Path(__file__).parent / "data" / "transport_routes.json"
_routes: Optional[dict] = None


@dataclass(frozen=True)
class RouteInfo:
    mode: str
    duration_minutes: int
    cost_usd_min: int
    cost_usd_max: int
    notes: str

    @property
    def cost_usd_mid(self) -> float:
        return round((self.cost_usd_min + self.cost_usd_max) / 2, 2)


def _load() -> dict:
    global _routes
    if _routes is None:
        raw = json.loads(_ROUTES_PATH.read_text(encoding="utf-8"))
        _routes = {k: v for k, v in raw.items() if not k.startswith("_")}
    return _routes


def _key(a: str, b: str) -> str:
    return "|".join(sorted([a.lower().strip(), b.lower().strip()]))


def lookup(origin: str, destination: str) -> Optional[RouteInfo]:
    """Return route info if curated; None otherwise."""
    record = _load().get(_key(origin, destination))
    if record is None:
        return None
    return RouteInfo(
        mode=record["mode"],
        duration_minutes=int(record["duration_minutes"]),
        cost_usd_min=int(record["cost_usd_min"]),
        cost_usd_max=int(record["cost_usd_max"]),
        notes=str(record["notes"]),
    )


def known_pairs() -> list[tuple[str, str]]:
    """For testing/diagnostics: list all curated city pairs."""
    return [tuple(k.split("|")) for k in _load().keys()]
