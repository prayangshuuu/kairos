import json

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.scheduling.models import AvailabilityRule, Schedule

pytestmark = pytest.mark.django_db(transaction=True)


def test_saving_weekly_grid_atomically(client):
    user = User.objects.create_user(email="test@example.com", password="password", slug="test")
    client.login(email="test@example.com", password="password")

    schedule = Schedule.objects.create(user=user, name="Test Schedule", timezone="UTC")
    # Initial rules
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=0, start_time="09:00", end_time="17:00"
    )

    url = reverse("scheduling:schedule_update_rules", kwargs={"pk": schedule.id})

    # 1. Valid save replaces rules
    valid_grid = [
        {
            "dayIndex": 0,
            "dayName": "Monday",
            "enabled": True,
            "ranges": [{"start": "10:00", "end": "14:00"}],
        },
        {"dayIndex": 1, "dayName": "Tuesday", "enabled": False, "ranges": []},
    ]
    response = client.post(url, {"grid_json": json.dumps(valid_grid)})
    assert (
        response.status_code == 302 or response.status_code == 200
    )  # Redirects or returns 200 for HTMX

    # Check rules updated
    assert schedule.rules.count() == 1
    assert schedule.rules.first().start_time.strftime("%H:%M") == "10:00"

    # 2. Invalid save with overlap leaves rules untouched
    invalid_grid = [
        {
            "dayIndex": 0,
            "dayName": "Monday",
            "enabled": True,
            "ranges": [
                {"start": "09:00", "end": "12:00"},
                {"start": "11:00", "end": "15:00"},  # Overlap!
            ],
        },
    ]
    response = client.post(url, {"grid_json": json.dumps(invalid_grid)})
    assert response.status_code == 400

    # Rules untouched
    assert schedule.rules.count() == 1
    assert schedule.rules.first().start_time.strftime("%H:%M") == "10:00"
