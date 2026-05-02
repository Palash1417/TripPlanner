"""Currency conversion using a static rates table.

Phase 6b deliverable. Rates are approximate USD-equivalents loaded from
src/tools/data/currency_rates.json. v2 may add a live FX feed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_RATES_PATH = Path(__file__).parent / "data" / "currency_rates.json"
_rates: Optional[dict[str, float]] = None


def _load() -> dict[str, float]:
    global _rates
    if _rates is None:
        raw = json.loads(_RATES_PATH.read_text(encoding="utf-8"))
        _rates = {k: float(v) for k, v in raw.items() if not k.startswith("_")}
    return _rates


def supported() -> set[str]:
    return set(_load().keys())


def to_usd(amount: float, currency: str) -> float:
    """Convert `amount` in `currency` to USD."""
    code = currency.upper().strip()
    rates = _load()
    if code not in rates:
        raise ValueError(f"unknown currency: {currency!r}")
    return amount * rates[code]


def convert(amount: float, from_ccy: str, to_ccy: str) -> float:
    """Convert `amount` from one currency to another using static rates."""
    src = from_ccy.upper().strip()
    dst = to_ccy.upper().strip()
    if src == dst:
        return amount
    usd_amount = to_usd(amount, src)
    rates = _load()
    if dst not in rates:
        raise ValueError(f"unknown currency: {to_ccy!r}")
    return usd_amount / rates[dst]
