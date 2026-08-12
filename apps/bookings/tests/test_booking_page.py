import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.scheduling.models import EventType

pytestmark = pytest.mark.django_db


def test_booking_page_timezone_and_slots(client):
    user = User.objects.create_user(
        email="host@example.com", password="password", slug="host_slug", is_active=True
    )

    EventType.objects.create(
        owner=user, slug="test", title="Test Event", is_active=True, duration_minutes=30
    )

    url = reverse("bookings:booking_page", kwargs={"host_slug": "host_slug", "event_slug": "test"})

    # Render with UTC
    response = client.get(url, {"tz": "UTC"})
    assert response.status_code == 200
    assert b"UTC" in response.content

    # Render with America/New_York
    response_ny = client.get(url, {"tz": "America/New_York"})
    assert response_ny.status_code == 200
    assert b"America/New_York" in response_ny.content

    # HTMX request
    response_htmx = client.get(
        url, {"tz": "America/New_York", "partial": "calendar"}, HTTP_HX_REQUEST="true"
    )
    assert response_htmx.status_code == 200
    assert b'id="calendar-partial"' in response_htmx.content
    assert b'id="slots-partial"' not in response_htmx.content
