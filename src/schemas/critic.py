"""Critic schemas — output of the Critic Agent. See architecture.md §7.1."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import StrictModel

CriticRule = Literal[
    "budget", "coverage", "avoidance", "geo_feasibility", "day_balance", "other"
]
ViolationSeverity = Literal["fail", "warn"]
GuiltyAgent = Literal[
    "intent", "destination", "accommodation", "transport", "budget", "itinerary"
]


class Violation(StrictModel):
    rule: CriticRule
    severity: ViolationSeverity
    agent: GuiltyAgent
    message: str = Field(min_length=1)


class CriticVerdict(StrictModel):
    passed: bool
    revision: int = Field(default=0, ge=0)
    violations: list[Violation] = Field(default_factory=list)
