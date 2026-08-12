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
    import uuid
    from datetime import timedelta
    from django.core.signing import Signer
    from django.utils import timezone

    now = timezone.now()
    future = now + timedelta(days=2)
    start_at = future.replace(hour=10, minute=0, second=0, microsecond=0)

    signer = Signer()
    ts_token = signer.sign(str(int(now.timestamp()) - 5))
    event = EventType.objects.get(slug="30-min")

    res = client.post(
        reverse("bookings:booking_stub", kwargs={"host_slug": "host", "event_slug": "30-min"}),
        {
            "idempotency_token": str(uuid.uuid4()),
            "timestamp_token": ts_token,
            "event_type_id": str(event.id),
            "slot_time": start_at.isoformat(),
            "invitee_name": "Invitee",
            "invitee_email": "invitee@example.com",
            "tz": "America/Los_Angeles",
        },
    )

    # Booking is created and returns HX-Redirect to confirmation
    assert res.status_code == 200
    assert Booking.objects.count() == 1

    booking = Booking.objects.first()
    assert booking.invitee_email == "invitee@example.com"
    assert "booking/" in res.headers["HX-Redirect"]
