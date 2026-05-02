"""Phase 5 live test — full graph including critic against real Gemini.

Gated by @pytest.mark.live; auto-skipped unless RUN_LIVE=1.
"""

from __future__ import annotations

import pytest

from src.orchestrator import plan_trip

pytestmark = pytest.mark.live


def test_japan_passes_critic_or_fails_gracefully(sample_requests: dict) -> None:
    """Phase 5 acceptance — Japan example should usually pass critic on rev 0 or 1.
    If it fails after the cap, we still get a usable plan + a verdict explaining why.
    """
    fixture = sample_requests["japan_happy_path"]
    ctx = plan_trip(fixture["request"])

    assert ctx.brief is not None
    assert ctx.itinerary is not None
    assert ctx.critic_verdicts, "critic must produce at least one verdict"

    final = ctx.critic_verdicts[-1]
    if not final.passed:
        # Best-effort path — verify each violation is well-formed for downstream use
        for v in final.violations:
            assert v.severity in {"fail", "warn"}
            assert v.rule in {
                "budget", "coverage", "avoidance", "geo_feasibility", "day_balance", "other"
            }
            assert v.message
