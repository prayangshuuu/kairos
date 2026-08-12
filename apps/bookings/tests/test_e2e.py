import pytest
from django.urls import reverse

from apps.bookings.models import Booking
from apps.scheduling.models import EventType


@pytest.mark.django_db
def test_e2e_happy_path(client, host_with_schedule):
    # 1. Visitor loads profile
    res = client.get(reverse("bookings:public_profile", kwargs={"slug": "host"}))
    assert res.status_code == 200

    # 2. Opens booking page
    res = client.get(
        reverse("bookings:booking_page", kwargs={"host_slug": "host", "event_slug": "30-min"})
    )
    assert res.status_code == 200

    # 4. Submits form
    EventType.objects.get(slug="30-min")
    from datetime import timedelta

    from django.utils import timezone

    now = timezone.now()
    future = now + timedelta(days=2)
    start_at = future.replace(hour=10, minute=0, second=0, microsecond=0)

    res = client.post(
        reverse("bookings:booking_page", kwargs={"host_slug": "host", "event_slug": "30-min"}),
        {
            "start_time": start_at.isoformat(),
            "invitee_name": "Invitee",
            "invitee_email": "invitee@example.com",
            "invitee_timezone": "America/Los_Angeles",
        },
    )

    # Booking is created and redirects to confirmation
    assert res.status_code == 302
    assert Booking.objects.count() == 1

    booking = Booking.objects.first()
    assert booking.invitee_email == "invitee@example.com"
    assert "booking/" in res.url
