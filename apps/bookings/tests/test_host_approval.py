from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.bookings.services import (
    InvalidTransition,
    approve_booking,
    create_booking,
    reject_booking,
)
from apps.bookings.tokens import make_approve_token
from apps.scheduling.models import EventType, Schedule

pytestmark = pytest.mark.django_db(transaction=True)


@patch("apps.bookings.services.is_slot_available", return_value=True)
def test_approving_booking(mock_is_slot_available):
    user = User.objects.create_user(email="host1@example.com", password="password", slug="host1")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user,
        schedule=schedule,
        title="Test",
        slug="test",
        duration_minutes=30,
        requires_confirmation=True,
    )

    now = datetime.now(UTC)
    start_at = now + timedelta(days=1)

    b1 = create_booking(
        event_type=event,
        start_at=start_at,
        invitee_name="T",
        invitee_email="t@t.com",
        invitee_timezone="UTC",
        answers={},
        now=now,
    )
    assert b1.status == Booking.StatusChoices.PENDING

    approve_booking(booking=b1, approved_by=user, now=now)
    b1.refresh_from_db()

    assert b1.status == Booking.StatusChoices.CONFIRMED
    assert b1.approved_by == user
    assert b1.approved_at == now


@patch("apps.bookings.services.is_slot_available", return_value=True)
def test_rejecting_booking(mock_is_slot_available):
    user = User.objects.create_user(email="host2@example.com", password="password", slug="host2")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user,
        schedule=schedule,
        title="Test",
        slug="test",
        duration_minutes=30,
        requires_confirmation=True,
    )

    now = datetime.now(UTC)
    start_at = now + timedelta(days=1)

    b1 = create_booking(
        event_type=event,
        start_at=start_at,
        invitee_name="T",
        invitee_email="t@t.com",
        invitee_timezone="UTC",
        answers={},
        now=now,
    )

    reject_booking(booking=b1, rejected_by=user, reason="No time", now=now)
    b1.refresh_from_db()

    assert b1.status == Booking.StatusChoices.REJECTED
    assert b1.rejected_by == user
    assert b1.cancellation_reason == "No time"


@patch("apps.bookings.services.is_slot_available", return_value=True)
def test_approving_already_cancelled_raises_invalid_transition(mock_is_slot_available):
    user = User.objects.create_user(email="host3@example.com", password="password", slug="host3")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user,
        schedule=schedule,
        title="Test",
        slug="test",
        duration_minutes=30,
        requires_confirmation=True,
    )

    now = datetime.now(UTC)
    start_at = now + timedelta(days=1)

    b1 = create_booking(
        event_type=event,
        start_at=start_at,
        invitee_name="T",
        invitee_email="t@t.com",
        invitee_timezone="UTC",
        answers={},
        now=now,
    )
    b1.status = Booking.StatusChoices.CANCELLED
    b1.save()

    with pytest.raises(InvalidTransition):
        approve_booking(booking=b1, approved_by=user, now=now)


def test_get_on_email_action_does_not_mutate(client):
    user = User.objects.create_user(email="host4@example.com", password="password", slug="host4")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user,
        schedule=schedule,
        title="Test",
        slug="test",
        duration_minutes=30,
        requires_confirmation=True,
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

    url = reverse("bookings:booking_approve", kwargs={"uid": b1.uid})
    token = make_approve_token(b1)

    response = client.get(f"{url}?t={token}")
    assert response.status_code == 200

    b1.refresh_from_db()
    assert b1.status == Booking.StatusChoices.PENDING

    # Now POST
    response = client.post(f"{url}?t={token}")
    assert response.status_code == 200
    b1.refresh_from_db()
    assert b1.status == Booking.StatusChoices.CONFIRMED


def test_auto_reject_expired_pending_bookings():
    from apps.bookings.tasks import auto_reject_expired_pending_bookings

    user = User.objects.create_user(
        email="host_auto@example.com", password="password", slug="hostauto"
    )
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user,
        schedule=schedule,
        title="Test",
        slug="test",
        duration_minutes=30,
        requires_confirmation=True,
        confirmation_deadline_hours=24,
    )

    now = datetime.now(UTC)
    past_start = now - timedelta(hours=1)
    future_start = now + timedelta(days=2)

    with patch("apps.bookings.services.is_slot_available", return_value=True):
        b_past = create_booking(
            event_type=event,
            start_at=past_start,
            invitee_name="P",
            invitee_email="p@t.com",
            invitee_timezone="UTC",
            answers={},
            now=past_start - timedelta(days=2),
        )
        b_future = create_booking(
            event_type=event,
            start_at=future_start,
            invitee_name="F",
            invitee_email="f@t.com",
            invitee_timezone="UTC",
            answers={},
            now=now,
        )

        # Another booking created far in the past, but for the future. The deadline has passed.
        b_deadline = create_booking(
            event_type=event,
            start_at=future_start + timedelta(days=1),
            invitee_name="D",
            invitee_email="d@t.com",
            invitee_timezone="UTC",
            answers={},
            now=now - timedelta(days=2),
        )
        Booking.objects.filter(id=b_deadline.id).update(created_at=now - timedelta(days=2))

    auto_reject_expired_pending_bookings()

    b_past.refresh_from_db()
    b_future.refresh_from_db()
    b_deadline.refresh_from_db()

    assert b_past.status == Booking.StatusChoices.REJECTED
    assert b_past.cancellation_reason == "Auto-rejected because the start time has passed."

    assert b_future.status == Booking.StatusChoices.PENDING

    assert b_deadline.status == Booking.StatusChoices.REJECTED
    assert (
        b_deadline.cancellation_reason == "Auto-rejected because the host did not respond in time."
    )
