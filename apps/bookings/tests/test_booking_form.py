from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.core.signing import Signer
from django.urls import reverse

from apps.accounts.models import User
from apps.scheduling.models import BookingQuestion, EventType

pytestmark = pytest.mark.django_db


@patch("apps.bookings.services.is_slot_available", return_value=True)
def test_booking_form_validation(mock_is_slot_available, client):
    user = User.objects.create_user(
        email="host@example.com", password="password", slug="host_slug", is_active=True
    )

    event = EventType.objects.create(
        owner=user, slug="test", title="Test Event", is_active=True, duration_minutes=30
    )

    # Add a required question
    q = BookingQuestion.objects.create(
        event_type=event,
        label="What is your favourite colour?",
        field_type="text",
        is_required=True,
    )

    url = reverse("bookings:booking_stub", kwargs={"host_slug": "host_slug", "event_slug": "test"})

    # Generate timestamp token that is old enough
    signer = Signer()
    old_timestamp = (datetime.now(UTC) - timedelta(seconds=10)).timestamp()
    token = signer.sign(str(old_timestamp))

    valid_slot = (datetime.now(UTC) + timedelta(days=1)).isoformat()

    # Submit without the required question
    post_data = {
        "invitee_name": "Test User",
        "invitee_email": "test@example.com",
        "slot_time": valid_slot,
        "tz": "UTC",
        "event_type_id": event.id,
        "timestamp_token": token,
        "idempotency_token": "token123",
        "website": "",  # Honeypot empty
    }

    response = client.post(url, data=post_data)

    # Form should re-render with errors
    assert response.status_code == 200
    assert b"This field is required." in response.content

    # Data should survive
    assert b"Test User" in response.content
    assert b"test@example.com" in response.content

    # Now submit with the required question
    post_data[f"question_{q.id}"] = "Blue"
    response_valid = client.post(url, data=post_data)

    # Should get placeholder success
    assert response_valid.status_code == 200
    assert "HX-Redirect" in response_valid.headers
    assert "/booking/" in response_valid.headers["HX-Redirect"]
