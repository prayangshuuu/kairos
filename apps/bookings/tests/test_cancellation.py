from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.bookings.services import (
    AlreadyCancelled,
    CancellationNotAllowed,
    SlotUnavailable,
    cancel_booking,
    create_booking,
)
from apps.scheduling.models import EventType, Schedule

pytestmark = pytest.mark.django_db(transaction=True)


@patch("apps.bookings.services.is_slot_available", return_value=True)
def test_cancelling_frees_the_slot(mock_is_slot_available):
    user = User.objects.create_user(email="host@example.com", password="password", slug="host")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user, schedule=schedule, title="Test", slug="test", duration_minutes=30
    )

    start_at = datetime.now(UTC) + timedelta(days=1)
    now = datetime.now(UTC)

    # 1. First booking succeeds
    b1 = create_booking(
        event_type=event,
        start_at=start_at,
        invitee_name="First",
        invitee_email="1@test.com",
        invitee_timezone="UTC",
        answers={},
        now=now,
    )

    # 2. Second booking on identical slot raises SlotUnavailable from DB constraint
    with pytest.raises(SlotUnavailable):
        # We need a separate connection if we do this natively in postgres without savepoints,
        # but since test uses django's db, create_booking's inner atomic handles it smoothly
        create_booking(
            event_type=event,
            start_at=start_at,
            invitee_name="Second",
            invitee_email="2@test.com",
            invitee_timezone="UTC",
            answers={},
            now=now,
        )

    # 3. Cancel the first booking
    cancel_booking(booking=b1, cancelled_by="host", now=now)

    # 4. Second booking now succeeds because the constraint ignores cancelled status
    b2 = create_booking(
        event_type=event,
        start_at=start_at,
        invitee_name="Second",
        invitee_email="2@test.com",
        invitee_timezone="UTC",
        answers={},
        now=now,
    )
    assert b2.status == Booking.StatusChoices.CONFIRMED


def test_cancelling_twice_raises_already_cancelled():
    user = User.objects.create_user(email="host2@example.com", password="password", slug="host2")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(owner=user, schedule=schedule, title="Test", slug="test")

    b = Booking.objects.create(
        event_type=event,
        host=user,
        start_at=datetime.now(UTC) + timedelta(days=1),
        end_at=datetime.now(UTC) + timedelta(days=1, minutes=30),
        status=Booking.StatusChoices.CONFIRMED,
        invitee_email="test@test.com",
    )

    now1 = datetime.now(UTC)
    cancel_booking(booking=b, cancelled_by="invitee", now=now1)

    assert b.status == Booking.StatusChoices.CANCELLED

    now2 = now1 + timedelta(minutes=5)
    with pytest.raises(AlreadyCancelled):
        cancel_booking(booking=b, cancelled_by="invitee", now=now2)

    b.refresh_from_db()
    assert b.cancelled_at == now1  # not now2


def test_cancellation_permissions():
    user = User.objects.create_user(email="host3@example.com", password="password", slug="host3")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user, schedule=schedule, title="Test", slug="test", allow_cancellation=False
    )

    b = Booking.objects.create(
        event_type=event,
        host=user,
        start_at=datetime.now(UTC) + timedelta(days=1),
        end_at=datetime.now(UTC) + timedelta(days=1, minutes=30),
        status=Booking.StatusChoices.CONFIRMED,
        invitee_email="test@test.com",
    )

    # Invitee cannot cancel
    with pytest.raises(CancellationNotAllowed):
        cancel_booking(booking=b, cancelled_by="invitee", now=datetime.now(UTC))

    # Host CAN cancel
    cancel_booking(booking=b, cancelled_by="host", now=datetime.now(UTC))
    assert b.status == Booking.StatusChoices.CANCELLED


def test_cancellation_cutoff():
    user = User.objects.create_user(email="host4@example.com", password="password", slug="host4")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user,
        schedule=schedule,
        title="Test",
        slug="test",
        allow_cancellation=True,
        cancellation_cutoff_hours=24,
    )

    b = Booking.objects.create(
        event_type=event,
        host=user,
        start_at=datetime.now(UTC) + timedelta(hours=20),
        end_at=datetime.now(UTC) + timedelta(hours=20, minutes=30),
        status=Booking.StatusChoices.CONFIRMED,
        invitee_email="test@test.com",
    )

    now = datetime.now(UTC)

    # Invitee is blocked (within 24 hours)
    with pytest.raises(CancellationNotAllowed):
        cancel_booking(booking=b, cancelled_by="invitee", now=now)

    # Host is NOT blocked by cutoff
    cancel_booking(booking=b, cancelled_by="host", now=now)
    assert b.status == Booking.StatusChoices.CANCELLED
