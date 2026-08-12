import math
from decimal import Decimal
from typing import Any

PLANS: dict[str, dict[str, Any]] = {
    "free": {
        "code": "free",
        "name": "Free",
        "description": "Essential scheduling features for individuals.",
        "prices": {
            "USD": Decimal("0.00"),
            "BDT": Decimal("0.00"),
        },
        "interval": "month",
        "is_public": True,
        "features": {
            "paid_bookings": False,
            "workflows_reminders": False,
            "remove_branding": False,
            "unlimited_custom_questions": False,
            "analytics": False,
            "team_scheduling": False,
        },
        "limits": {
            "max_event_types": 1,
            "max_schedules": 1,
            "max_custom_questions": 3,
        },
    },
    "pro": {
        "code": "pro",
        "name": "Pro",
        "description": "Advanced features, payments, and custom branding.",
        "prices": {
            "USD": Decimal("12.00"),
            "BDT": Decimal("1200.00"),
        },
        "interval": "month",
        "is_public": True,
        "features": {
            "paid_bookings": True,
            "workflows_reminders": True,
            "remove_branding": True,
            "unlimited_custom_questions": True,
            "analytics": True,
            "team_scheduling": False,
        },
        "limits": {
            "max_event_types": math.inf,
            "max_schedules": math.inf,
            "max_custom_questions": math.inf,
        },
    },
    "team": {
        "code": "team",
        "name": "Team",
        "description": "Collective & round-robin scheduling for teams.",
        "prices": {
            "USD": Decimal("25.00"),
            "BDT": Decimal("2500.00"),
        },
        "interval": "month",
        "is_public": False,  # Gated off until teams ship in a later task
        "features": {
            "paid_bookings": True,
            "workflows_reminders": True,
            "remove_branding": True,
            "unlimited_custom_questions": True,
            "analytics": True,
            "team_scheduling": True,
        },
        "limits": {
            "max_event_types": math.inf,
            "max_schedules": math.inf,
            "max_custom_questions": math.inf,
        },
    },
}


def get_plan(plan_code: str) -> dict[str, Any]:
    return PLANS.get(plan_code, PLANS["free"])


def get_plan_feature(plan_code: str, feature_code: str) -> bool:
    plan = get_plan(plan_code)
    return plan["features"].get(feature_code, False)


def get_plan_limit(plan_code: str, limit_code: str) -> Any:
    plan = get_plan(plan_code)
    return plan["limits"].get(limit_code, 0)
