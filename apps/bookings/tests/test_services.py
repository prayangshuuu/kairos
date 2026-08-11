import pytest
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from django.db import connection, transaction
from apps.accounts.models import User
from apps.scheduling.models import EventType, Schedule
from apps.bookings.models import Booking, Attendee
from apps.bookings.services import create_booking, SlotUnavailable

pytestmark = pytest.mark.django_db(transaction=True)

@patch('apps.bookings.services.is_slot_available', return_value=True)
def test_booking_creates_booking_and_attendees(mock_is_slot_available):
    user = User.objects.create_user(email="host@example.com", password="pw", slug="host")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    
    event = EventType.objects.create(
        owner=user, schedule=schedule, title="Test", slug="test", duration_minutes=30
    )
    
    start_at = datetime.now(timezone.utc) + timedelta(days=1)
    
    booking = create_booking(
        event_type=event,
        start_at=start_at,
        invitee_name="Test Invitee",
        invitee_email="test@example.com",
        invitee_timezone="UTC",
        answers={},
        guest_emails=["guest1@example.com", "guest2@example.com"],
        now=datetime.now(timezone.utc)
    )
    
    assert booking.status == Booking.StatusChoices.CONFIRMED
    assert booking.end_at == start_at + timedelta(minutes=30)
    
    attendees = list(booking.attendees.order_by('id'))
    assert len(attendees) == 4
    
    assert attendees[0].email == "test@example.com"
    assert not attendees[0].is_organizer
    
    assert attendees[1].email == "host@example.com"
    assert attendees[1].is_organizer
    
    assert attendees[2].email == "guest1@example.com"
    assert not attendees[2].is_organizer
    
    assert attendees[3].email == "guest2@example.com"
    assert not attendees[3].is_organizer

@patch('apps.bookings.services.is_slot_available', return_value=True)
def test_booking_requires_confirmation(mock_is_slot_available):
    user = User.objects.create_user(email="host2@example.com", password="pw", slug="host2")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user, schedule=schedule, title="Test", slug="test", duration_minutes=30,
        requires_confirmation=True
    )
    
    start_at = datetime.now(timezone.utc) + timedelta(days=1)
    
    booking = create_booking(
        event_type=event,
        start_at=start_at,
        invitee_name="Test Invitee",
        invitee_email="test@example.com",
        invitee_timezone="UTC",
        answers={},
        now=datetime.now(timezone.utc)
    )
    
    assert booking.status == Booking.StatusChoices.PENDING

@patch('apps.bookings.services.is_slot_available', return_value=True)
def test_paid_event_raises_not_implemented(mock_is_slot_available):
    user = User.objects.create_user(email="host3@example.com", password="pw", slug="host3")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user, schedule=schedule, title="Test", slug="test", duration_minutes=30,
        price_cents=1000
    )
    
    start_at = datetime.now(timezone.utc) + timedelta(days=1)
    
    with pytest.raises(NotImplementedError):
        create_booking(
            event_type=event,
            start_at=start_at,
            invitee_name="Test Invitee",
            invitee_email="test@example.com",
            invitee_timezone="UTC",
            answers={},
            now=datetime.now(timezone.utc)
        )

@patch('apps.bookings.services.is_slot_available', return_value=True)
def test_booking_concurrency_exclusion_constraint(mock_is_slot_available):
    user = User.objects.create_user(email="host4@example.com", password="pw", slug="host4")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user, schedule=schedule, title="Test", slug="test", duration_minutes=30
    )
    
    start_at = datetime.now(timezone.utc) + timedelta(days=1)
    
    results = []
    
    def book_slot(name):
        # We need a separate connection for thread concurrency
        try:
            with transaction.atomic():
                b = create_booking(
                    event_type=event,
                    start_at=start_at,
                    invitee_name=name,
                    invitee_email=f"{name}@example.com",
                    invitee_timezone="UTC",
                    answers={},
                    now=datetime.now(timezone.utc)
                )
                results.append(b)
        except SlotUnavailable:
            results.append("Unavailable")
        finally:
            connection.close()

    t1 = threading.Thread(target=book_slot, args=("Thread1",))
    t2 = threading.Thread(target=book_slot, args=("Thread2",))
    
    t1.start()
    t2.start()
    
    t1.join()
    t2.join()
    
    assert len(results) == 2
    successes = [r for r in results if isinstance(r, Booking)]
    failures = [r for r in results if r == "Unavailable"]
    
    assert len(successes) == 1
    assert len(failures) == 1
    
    assert Booking.objects.filter(host=user).count() == 1

@patch('apps.bookings.services.is_slot_available', return_value=True)
def test_querying_after_slot_unavailable_succeeds(mock_is_slot_available):
    user = User.objects.create_user(email="host5@example.com", password="pw", slug="host5")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(
        owner=user, schedule=schedule, title="Test", slug="test", duration_minutes=30
    )
    
    start_at = datetime.now(timezone.utc) + timedelta(days=1)
    
    # Create first booking
    create_booking(
        event_type=event,
        start_at=start_at,
        invitee_name="First",
        invitee_email="first@example.com",
        invitee_timezone="UTC",
        answers={},
        now=datetime.now(timezone.utc)
    )
    
    # Try second booking (same slot), which will raise SlotUnavailable inside an outer atomic
    with transaction.atomic():
        try:
            create_booking(
                event_type=event,
                start_at=start_at,
                invitee_name="Second",
                invitee_email="second@example.com",
                invitee_timezone="UTC",
                answers={},
                now=datetime.now(timezone.utc)
            )
            pytest.fail("Should have raised SlotUnavailable")
        except SlotUnavailable:
            pass
            
        # Prove the transaction isn't poisoned by doing a query
        count = Booking.objects.count()
        assert count > 0
