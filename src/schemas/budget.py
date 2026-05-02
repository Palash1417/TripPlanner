"""BudgetLine and BudgetBreakdown schemas — output of Budget Agent."""

from __future__ import annotations

from typing import Optional

from pydantic import Field, model_validator

from .common import BudgetCategory, Confidence, StrictModel


class BudgetLine(StrictModel):
    category: BudgetCategory
    estimate_usd: float = Field(ge=0)
    notes: Optional[str] = None
    confidence: Confidence = "medium"


class BudgetBreakdown(StrictModel):
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217 code")
    lines: list[BudgetLine] = Field(min_length=1)
    total_estimate_usd: float = Field(ge=0)
    budget_amount_usd: Optional[float] = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_total(self) -> "BudgetBreakdown":
        summed = round(sum(line.estimate_usd for line in self.lines), 2)
        if abs(summed - self.total_estimate_usd) > 0.01:
            raise ValueError(
                f"total_estimate_usd ({self.total_estimate_usd}) "
                f"does not match sum of lines ({summed})"
            )
        return self
