"""Pydantic data contracts shared across agents. See architecture.md §3."""

from .budget import BudgetBreakdown, BudgetLine
from .common import (
    BudgetCategory,
    Confidence,
    Money,
    Pace,
    StrictModel,
    TransportMode,
)
from .critic import CriticRule, CriticVerdict, GuiltyAgent, Violation, ViolationSeverity
from .itinerary import Activity, Itinerary, ItineraryDay
from .poi import POI, DestinationCatalog
from .stay import AccommodationPlan, PropertyType, StayOption
from .transport import TransportLeg, TransportPlan
from .trip_brief import Preferences, Travelers, TripBrief, TripDates

__all__ = [
    "AccommodationPlan",
    "Activity",
    "BudgetBreakdown",
    "BudgetCategory",
    "BudgetLine",
    "Confidence",
    "CriticRule",
    "CriticVerdict",
    "DestinationCatalog",
    "GuiltyAgent",
    "Itinerary",
    "ItineraryDay",
    "Money",
    "POI",
    "Pace",
    "Preferences",
    "PropertyType",
    "StayOption",
    "StrictModel",
    "TransportLeg",
    "TransportMode",
    "TransportPlan",
    "Travelers",
    "TripBrief",
    "TripDates",
    "Violation",
    "ViolationSeverity",
]
