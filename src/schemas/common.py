"""Shared types used across multiple schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]
Pace = Literal["slow", "moderate", "fast"]
TransportMode = Literal["flight", "train", "bus", "car", "ferry", "walk", "subway", "taxi"]
BudgetCategory = Literal["lodging", "transport", "food", "activities", "buffer", "other"]


class StrictModel(BaseModel):
    """Base model that rejects unknown fields — catches schema drift early."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Money(StrictModel):
    amount: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217 code, e.g. USD")
