from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.bookings.services import (
    SlotUnavailable,
    cancel_booking,
    create_booking,
    reschedule_booking,
)
from apps.scheduling.models import EventType, Schedule

pytestmark = pytest.mark.django_db(transaction=True)


@patch("apps.bookings.services.is_slot_available", return_value=True)
def test_successful_reschedule(mock_is_slot_available):
    user = User.objects.create_user(email="host@example.com", password="password", slug="host")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user, schedule=schedule, title="Test", slug="test", duration_minutes=30
    )

    start_at = datetime.now(UTC) + timedelta(days=1)
    now = datetime.now(UTC)

    b1 = create_booking(
        event_type=event,
        start_at=start_at,
        invitee_name="Test",
        invitee_email="t@t.com",
        invitee_timezone="UTC",
        answers={},
        now=now,
    )

    new_start = start_at + timedelta(days=1)

    b2 = reschedule_booking(
        booking=b1,
        new_start_at=new_start,
        rescheduled_by="invitee",
        reason="Needed change",
        now=now,
    )

    b1.refresh_from_db()

    assert b2.id != b1.id
    assert b2.status == Booking.StatusChoices.CONFIRMED
    assert b1.status == Booking.StatusChoices.CANCELLED
    assert b1.cancellation_reason == "Rescheduled by invitee: Needed change"
    assert b2.rescheduled_from == b1
    assert list(b2.reschedule_chain) == [b1]


def test_reschedule_overlapping_own_slot():
    user = User.objects.create_user(email="host2@example.com", password="password", slug="host2")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user, schedule=schedule, title="Test", slug="test", duration_minutes=60
    )

    start_at = datetime.now(UTC) + timedelta(days=1)
    now = datetime.now(UTC)

    # We patch it locally so the first insert works and the second passes the python check
    with patch("apps.bookings.services.is_slot_available", return_value=True):
        b1 = create_booking(
            event_type=event,
            start_at=start_at,
            invitee_name="Test",
            invitee_email="t@t.com",
            invitee_timezone="UTC",
            answers={},
            now=now,
        )

        # Reschedule to start 30 mins later. This overlaps with the original 60-min slot.
        # The exclude-self logic should allow this.
        new_start = start_at + timedelta(minutes=30)

        b2 = reschedule_booking(
            booking=b1, new_start_at=new_start, rescheduled_by="invitee", reason="Shift", now=now
        )

        assert b2.status == Booking.StatusChoices.CONFIRMED


def test_reschedule_rollback_on_conflict():
    user = User.objects.create_user(email="host3@example.com", password="password", slug="host3")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user, schedule=schedule, title="Test", slug="test", duration_minutes=30
    )

    now = datetime.now(UTC)
    slot1 = now + timedelta(days=1)
    slot2 = now + timedelta(days=2)

    with patch("apps.bookings.services.is_slot_available", return_value=True):
        b1 = create_booking(
            event_type=event,
            start_at=slot1,
            invitee_name="T1",
            invitee_email="1@t.com",
            invitee_timezone="UTC",
            answers={},
            now=now,
        )
        create_booking(
            event_type=event,
            start_at=slot2,
            invitee_name="T2",
            invitee_email="2@t.com",
            invitee_timezone="UTC",
            answers={},
            now=now,
        )

        # b1 tries to reschedule into slot2 (which is occupied)
        with pytest.raises(SlotUnavailable):
            reschedule_booking(
                booking=b1, new_start_at=slot2, rescheduled_by="invitee", reason="", now=now
            )

        b1.refresh_from_db()

        # b1 must still be confirmed, its cancellation was rolled back!
        assert b1.status == Booking.StatusChoices.CONFIRMED
        assert b1.cancelled_at is None


@patch("apps.bookings.services.is_slot_available", return_value=True)
def test_reschedule_chain_property(mock_is_slot_available):
    user = User.objects.create_user(email="host4@example.com", password="password", slug="host4")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user, schedule=schedule, title="Test", slug="test", duration_minutes=30
    )

    now = datetime.now(UTC)

    b1 = create_booking(
        event_type=event,
        start_at=now + timedelta(days=1),
        invitee_name="T",
        invitee_email="t@t.com",
        invitee_timezone="UTC",
        answers={},
        now=now,
    )
    b2 = reschedule_booking(
        booking=b1, new_start_at=now + timedelta(days=2), rescheduled_by="invitee", now=now
    )
    b3 = reschedule_booking(
        booking=b2, new_start_at=now + timedelta(days=3), rescheduled_by="invitee", now=now
    )

    assert list(b3.reschedule_chain) == [b2, b1]


@patch("apps.bookings.services.is_slot_available", return_value=True)
def test_cancelling_new_does_not_resurrect_old(mock_is_slot_available):
    user = User.objects.create_user(email="host5@example.com", password="password", slug="host5")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user, schedule=schedule, title="Test", slug="test", duration_minutes=30
    )

    now = datetime.now(UTC)

    b1 = create_booking(
        event_type=event,
        start_at=now + timedelta(days=1),
        invitee_name="T",
        invitee_email="t@t.com",
        invitee_timezone="UTC",
        answers={},
        now=now,
    )
    b2 = reschedule_booking(
        booking=b1, new_start_at=now + timedelta(days=2), rescheduled_by="invitee", now=now
    )

    cancel_booking(booking=b2, cancelled_by="host", now=now)

    b1.refresh_from_db()
    assert b1.status == Booking.StatusChoices.CANCELLED
    assert b2.status == Booking.StatusChoices.CANCELLED


def test_pending_booking_blocks_slot():
    user = User.objects.create_user(email="host6@example.com", password="password", slug="host6")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user, schedule=schedule, title="Test", slug="test", duration_minutes=30
    )

    now = datetime.now(UTC)
    start_at = now + timedelta(days=1)

    with patch("apps.bookings.services.is_slot_available", return_value=True):
        b1 = create_booking(
            event_type=event,
            start_at=start_at,
            invitee_name="T",
            invitee_email="t@t.com",
            invitee_timezone="UTC",
            answers={},
            now=now,
        )
        b1.status = Booking.StatusChoices.PENDING
        b1.save()

        # Second booking should fail due to exclusion constraint
        with pytest.raises(SlotUnavailable):
            create_booking(
                event_type=event,
                start_at=start_at,
                invitee_name="T2",
                invitee_email="t2@t.com",
                invitee_timezone="UTC",
                answers={},
                now=now,
            )
