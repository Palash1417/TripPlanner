"""Phase 1 schema tests — round-trip + validation per implementation.md."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas import (
    POI,
    Activity,
    BudgetBreakdown,
    BudgetLine,
    Itinerary,
    ItineraryDay,
    Money,
    Preferences,
    StayOption,
    TransportLeg,
    Travelers,
    TripBrief,
    TripDates,
)


# ---------- fixtures ----------


JAPAN_BRIEF: dict = {
    "origin": "San Francisco",
    "destinations": ["Tokyo", "Kyoto"],
    "duration_days": 5,
    "budget": {"amount": 3000, "currency": "USD"},
    "travelers": {"adults": 1, "children": 0},
    "preferences": {
        "likes": ["food", "temples"],
        "dislikes": ["crowds"],
        "pace": "moderate",
        "accessibility": [],
    },
    "dates": {"start": "2026-04-10", "end": "2026-04-14", "flexible": False},
    "open_questions": [],
}


FUSHIMI_POI: dict = {
    "name": "Fushimi Inari Shrine",
    "city": "Kyoto",
    "category": "temple",
    "match_reasons": ["likes:temples", "dislikes:crowds → visit early AM"],
    "est_visit_minutes": 120,
    "est_cost_usd": 0.0,
    "tags": ["sunrise-friendly", "outdoor"],
    "lat": 34.9671,
    "lon": 135.7727,
    "confidence": "high",
}


HIGASHIYAMA_STAY: dict = {
    "name": "Sample Ryokan",
    "city": "Kyoto",
    "neighborhood": "Higashiyama",
    "property_type": "ryokan",
    "price_per_night_usd": 180.0,
    "nights": 2,
    "match_reasons": ["dislikes:crowds → quieter neighborhood"],
    "accessibility_features": [],
    "confidence": "medium",
}


SHINKANSEN_LEG: dict = {
    "origin": "Tokyo",
    "destination": "Kyoto",
    "mode": "train",
    "duration_minutes": 135,
    "cost_usd": 130.0,
    "travel_date": "2026-04-13",
    "match_reasons": [],
    "notes": "Shinkansen Nozomi",
    "confidence": "high",
}


BUDGET_LINES = [
    {"category": "lodging", "estimate_usd": 750.0, "notes": None, "confidence": "medium"},
    {"category": "transport", "estimate_usd": 260.0, "notes": None, "confidence": "high"},
    {"category": "food", "estimate_usd": 500.0, "notes": None, "confidence": "low"},
    {"category": "activities", "estimate_usd": 200.0, "notes": None, "confidence": "medium"},
    {"category": "buffer", "estimate_usd": 290.0, "notes": "5–10% buffer", "confidence": "high"},
]


BUDGET_BREAKDOWN: dict = {
    "currency": "USD",
    "lines": BUDGET_LINES,
    "total_estimate_usd": 2000.0,
    "budget_amount_usd": 3000.0,
    "warnings": [],
}


ITINERARY: dict = {
    "days": [
        {
            "day_number": 1,
            "city": "Tokyo",
            "activities": [
                {
                    "title": "Arrival + check-in",
                    "start_time": "16:00",
                    "duration_minutes": 90,
                    "poi_name": None,
                    "notes": "Light first day",
                    "match_reasons": [],
                    "est_cost_usd": 0.0,
                }
            ],
            "transport_leg": None,
            "notes": None,
        },
        {
            "day_number": 2,
            "city": "Tokyo",
            "activities": [
                {
                    "title": "Tsukiji Outer Market food crawl",
                    "start_time": "07:00",
                    "duration_minutes": 180,
                    "poi_name": "Tsukiji Outer Market",
                    "notes": None,
                    "match_reasons": ["likes:food"],
                    "est_cost_usd": 50.0,
                }
            ],
            "transport_leg": None,
            "notes": None,
        },
    ],
    "summary": "Food + temples, crowd-avoiding pace.",
    "confidence": "medium",
}


# ---------- round-trip tests ----------


@pytest.mark.parametrize(
    ("model_cls", "fixture"),
    [
        (TripBrief, JAPAN_BRIEF),
        (POI, FUSHIMI_POI),
        (TransportLeg, SHINKANSEN_LEG),
        (BudgetBreakdown, BUDGET_BREAKDOWN),
        (Itinerary, ITINERARY),
    ],
)
def test_round_trip(model_cls, fixture) -> None:
    instance = model_cls.model_validate(fixture)
    dumped = instance.model_dump(mode="json")
    re_validated = model_cls.model_validate(dumped)
    assert re_validated == instance


def test_stay_round_trip_and_total() -> None:
    """StayOption round-trips cleanly; total_usd is a derived property, not a stored field."""
    stay = StayOption.model_validate(HIGASHIYAMA_STAY)
    assert stay.total_usd == 360.0
    dumped = stay.model_dump(mode="json")
    assert "total_usd" not in dumped
    assert StayOption.model_validate(dumped) == stay


# ---------- TripBrief validation ----------


def test_trip_brief_rejects_empty_destinations() -> None:
    bad = {**JAPAN_BRIEF, "destinations": []}
    with pytest.raises(ValidationError):
        TripBrief.model_validate(bad)


def test_trip_brief_rejects_negative_duration() -> None:
    bad = {**JAPAN_BRIEF, "duration_days": 0}
    with pytest.raises(ValidationError):
        TripBrief.model_validate(bad)


def test_trip_brief_rejects_unknown_field() -> None:
    bad = {**JAPAN_BRIEF, "secret_field": "leak"}
    with pytest.raises(ValidationError):
        TripBrief.model_validate(bad)


def test_trip_brief_strips_destination_whitespace() -> None:
    brief = TripBrief.model_validate({**JAPAN_BRIEF, "destinations": ["  Tokyo ", "Kyoto"]})
    assert brief.destinations == ["Tokyo", "Kyoto"]


def test_trip_brief_minimal_request() -> None:
    """Underspecified request (edge case 1.1) — only destinations + open_questions."""
    minimal = {
        "destinations": ["somewhere warm"],
        "open_questions": ["destination unspecified", "duration unspecified", "budget unspecified"],
    }
    brief = TripBrief.model_validate(minimal)
    assert brief.budget is None
    assert brief.duration_days is None
    assert brief.travelers == Travelers()
    assert brief.preferences == Preferences()


def test_trip_dates_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError):
        TripDates.model_validate({"start": "2026-04-14", "end": "2026-04-10", "flexible": False})


def test_money_rejects_bad_currency_length() -> None:
    with pytest.raises(ValidationError):
        Money.model_validate({"amount": 100, "currency": "DOLLARS"})


# ---------- POI / Stay / Transport validation ----------


def test_poi_requires_match_reasons() -> None:
    bad = {**FUSHIMI_POI, "match_reasons": []}
    with pytest.raises(ValidationError):
        POI.model_validate(bad)


def test_poi_rejects_negative_visit_minutes() -> None:
    bad = {**FUSHIMI_POI, "est_visit_minutes": -1}
    with pytest.raises(ValidationError):
        POI.model_validate(bad)


def test_poi_rejects_out_of_range_lat_lon() -> None:
    bad = {**FUSHIMI_POI, "lat": 200.0}
    with pytest.raises(ValidationError):
        POI.model_validate(bad)


def test_stay_requires_match_reasons() -> None:
    bad = {**HIGASHIYAMA_STAY, "match_reasons": []}
    with pytest.raises(ValidationError):
        StayOption.model_validate(bad)


def test_transport_leg_rejects_unknown_mode() -> None:
    bad = {**SHINKANSEN_LEG, "mode": "teleport"}
    with pytest.raises(ValidationError):
        TransportLeg.model_validate(bad)


# ---------- Budget validation ----------


def test_budget_rejects_total_mismatch() -> None:
    bad = {**BUDGET_BREAKDOWN, "total_estimate_usd": 9999.0}
    with pytest.raises(ValidationError, match="does not match sum"):
        BudgetBreakdown.model_validate(bad)


def test_budget_line_rejects_unknown_category() -> None:
    with pytest.raises(ValidationError):
        BudgetLine.model_validate({"category": "champagne", "estimate_usd": 100.0})


# ---------- Itinerary validation ----------


def test_itinerary_rejects_non_sequential_days() -> None:
    bad = {
        **ITINERARY,
        "days": [
            {**ITINERARY["days"][0], "day_number": 1},
            {**ITINERARY["days"][1], "day_number": 3},
        ],
    }
    with pytest.raises(ValidationError, match="sequential"):
        Itinerary.model_validate(bad)


def test_itinerary_day_total_minutes_includes_transport() -> None:
    day = ItineraryDay.model_validate(
        {
            "day_number": 1,
            "city": "Tokyo",
            "activities": [
                {"title": "Lunch", "duration_minutes": 60, "match_reasons": []},
            ],
            "transport_leg": SHINKANSEN_LEG,
        }
    )
    assert day.total_activity_minutes == 60 + 135


def test_activity_rejects_oversized_duration() -> None:
    with pytest.raises(ValidationError):
        Activity.model_validate({"title": "Marathon", "duration_minutes": 24 * 60 + 1})
