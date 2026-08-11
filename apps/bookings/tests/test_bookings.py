import pytest
import datetime
import threading
import django
from django.db import IntegrityError, transaction
from apps.accounts.models import User
from apps.scheduling.models import EventType
from apps.bookings.models import Booking

pytestmark = pytest.mark.django_db(transaction=True)

@pytest.fixture
def host1():
    return User.objects.create_user(email="host1@example.com", password="password")

@pytest.fixture
def host2():
    return User.objects.create_user(email="host2@example.com", password="password")

@pytest.fixture
def event_type(host1):
    return EventType.objects.create(
        owner=host1,
        slug="30-min",
        title="30 Min",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0
    )

@pytest.fixture
def event_type_with_buffer(host1):
    return EventType.objects.create(
        owner=host1,
        slug="15-min-buf",
        title="15 Min Buf",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=15
    )

def test_overlapping_bookings_raise_integrity_error(host1, event_type):
    Booking.objects.create(
        host=host1,
        event_type=event_type,
        start_at=datetime.datetime(2026, 1, 1, 10, 0, tzinfo=datetime.timezone.utc),
        end_at=datetime.datetime(2026, 1, 1, 10, 30, tzinfo=datetime.timezone.utc),
        status="confirmed",
        invitee_name="Invitee 1",
        invitee_email="invitee1@example.com",
        invitee_timezone="UTC"
    )

    with pytest.raises(IntegrityError):
        Booking.objects.create(
            host=host1,
            event_type=event_type,
            start_at=datetime.datetime(2026, 1, 1, 10, 15, tzinfo=datetime.timezone.utc),
            end_at=datetime.datetime(2026, 1, 1, 10, 45, tzinfo=datetime.timezone.utc),
            status="confirmed",
            invitee_name="Invitee 2",
            invitee_email="invitee2@example.com",
            invitee_timezone="UTC"
        )

def test_overlapping_booking_succeeds_if_cancelled(host1, event_type):
    Booking.objects.create(
        host=host1,
        event_type=event_type,
        start_at=datetime.datetime(2026, 1, 1, 10, 0, tzinfo=datetime.timezone.utc),
        end_at=datetime.datetime(2026, 1, 1, 10, 30, tzinfo=datetime.timezone.utc),
        status="cancelled",
        invitee_name="Invitee 1",
        invitee_email="invitee1@example.com",
        invitee_timezone="UTC"
    )

    # Should succeed because the first one is cancelled
    b2 = Booking.objects.create(
        host=host1,
        event_type=event_type,
        start_at=datetime.datetime(2026, 1, 1, 10, 15, tzinfo=datetime.timezone.utc),
        end_at=datetime.datetime(2026, 1, 1, 10, 45, tzinfo=datetime.timezone.utc),
        status="confirmed",
        invitee_name="Invitee 2",
        invitee_email="invitee2@example.com",
        invitee_timezone="UTC"
    )
    assert b2.id is not None

def test_touching_boundaries_succeed(host1, event_type):
    Booking.objects.create(
        host=host1,
        event_type=event_type,
        start_at=datetime.datetime(2026, 1, 1, 10, 0, tzinfo=datetime.timezone.utc),
        end_at=datetime.datetime(2026, 1, 1, 10, 30, tzinfo=datetime.timezone.utc),
        status="confirmed",
        invitee_name="Invitee 1",
        invitee_email="invitee1@example.com",
        invitee_timezone="UTC"
    )

    # Should succeed because 10:30 doesn't overlap due to half-open range '[)'
    b2 = Booking.objects.create(
        host=host1,
        event_type=event_type,
        start_at=datetime.datetime(2026, 1, 1, 10, 30, tzinfo=datetime.timezone.utc),
        end_at=datetime.datetime(2026, 1, 1, 11, 0, tzinfo=datetime.timezone.utc),
        status="confirmed",
        invitee_name="Invitee 2",
        invitee_email="invitee2@example.com",
        invitee_timezone="UTC"
    )
    assert b2.id is not None

def test_buffer_blocks_subsequent_booking(host1, event_type_with_buffer):
    # This booking is 30 mins, but has a 15-min after buffer. Total blocked: 10:00 to 10:45
    Booking.objects.create(
        host=host1,
        event_type=event_type_with_buffer,
        start_at=datetime.datetime(2026, 1, 1, 10, 0, tzinfo=datetime.timezone.utc),
        end_at=datetime.datetime(2026, 1, 1, 10, 30, tzinfo=datetime.timezone.utc),
        status="confirmed",
        invitee_name="Invitee 1",
        invitee_email="invitee1@example.com",
        invitee_timezone="UTC"
    )

    # Starts 10 minutes later (10:40) which overlaps the 15-min buffer!
    with pytest.raises(IntegrityError):
        Booking.objects.create(
            host=host1,
            event_type=event_type_with_buffer,
            start_at=datetime.datetime(2026, 1, 1, 10, 40, tzinfo=datetime.timezone.utc),
            end_at=datetime.datetime(2026, 1, 1, 11, 10, tzinfo=datetime.timezone.utc),
            status="confirmed",
            invitee_name="Invitee 2",
            invitee_email="invitee2@example.com",
            invitee_timezone="UTC"
        )

def test_overlapping_bookings_different_hosts_succeed(host1, host2, event_type):
    # For host1
    Booking.objects.create(
        host=host1,
        event_type=event_type,
        start_at=datetime.datetime(2026, 1, 1, 10, 0, tzinfo=datetime.timezone.utc),
        end_at=datetime.datetime(2026, 1, 1, 10, 30, tzinfo=datetime.timezone.utc),
        status="confirmed",
        invitee_name="Invitee 1",
        invitee_email="invitee1@example.com",
        invitee_timezone="UTC"
    )

    # For host2 (different host) - event type owner doesn't matter for the constraint, just host
    b2 = Booking.objects.create(
        host=host2,
        event_type=event_type,
        start_at=datetime.datetime(2026, 1, 1, 10, 15, tzinfo=datetime.timezone.utc),
        end_at=datetime.datetime(2026, 1, 1, 10, 45, tzinfo=datetime.timezone.utc),
        status="confirmed",
        invitee_name="Invitee 2",
        invitee_email="invitee2@example.com",
        invitee_timezone="UTC"
    )
    assert b2.id is not None

def test_concurrency_exclusion_constraint(host1, event_type):
    errors = []

    def insert_booking():
        try:
            with transaction.atomic():
                Booking.objects.create(
                    host=host1,
                    event_type=event_type,
                    start_at=datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.timezone.utc),
                    end_at=datetime.datetime(2026, 1, 1, 12, 30, tzinfo=datetime.timezone.utc),
                    status="confirmed",
                    invitee_name="Invitee Concurrency",
                    invitee_email="conc@example.com",
                    invitee_timezone="UTC"
                )
        except (IntegrityError, django.db.utils.OperationalError):
            errors.append("IntegrityErrorOrDeadlock")

    t1 = threading.Thread(target=insert_booking)
    t2 = threading.Thread(target=insert_booking)

    t1.start()
    t2.start()

    t1.join()
    t2.join()

    # Exactly one should have succeeded and one should have failed with IntegrityError
    assert len(errors) == 1
    assert Booking.objects.filter(host=host1).count() == 1
