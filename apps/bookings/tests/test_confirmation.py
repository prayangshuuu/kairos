import pytest
from datetime import datetime, timedelta, timezone
from django.urls import reverse
from apps.accounts.models import User
from apps.scheduling.models import EventType, Schedule
from apps.bookings.models import Booking
from apps.bookings.tokens import make_manage_token

pytestmark = pytest.mark.django_db

def test_confirmation_page_valid_token(client):
    user = User.objects.create_user(email="host@example.com", password="password", slug="host")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(owner=user, schedule=schedule, title="Test Event", slug="test", duration_minutes=30)
    
    start_at = datetime.now(timezone.utc) + timedelta(days=1)
    end_at = start_at + timedelta(minutes=30)
    
    booking = Booking.objects.create(
        event_type=event,
        host=user,
        start_at=start_at,
        end_at=end_at,
        invitee_timezone="UTC",
        status=Booking.StatusChoices.CONFIRMED,
        invitee_name="Test Invitee",
        invitee_email="test@example.com",
    )
    
    token = make_manage_token(booking)
    url = reverse("bookings:booking_confirmation", kwargs={"uid": booking.uid})
    
    response = client.get(f"{url}?t={token}")
    assert response.status_code == 200
    assert b'<meta name="robots" content="noindex">' in response.content
    assert b"Test Event" in response.content
    assert b"Booking Confirmed!" in response.content

def test_confirmation_page_tampered_token_404s(client):
    user = User.objects.create_user(email="host2@example.com", password="password", slug="host2")
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC")
    event = EventType.objects.create(owner=user, schedule=schedule, title="Test Event", slug="test", duration_minutes=30)
    
    start_at = datetime.now(timezone.utc) + timedelta(days=1)
    end_at = start_at + timedelta(minutes=30)
    
    booking = Booking.objects.create(
        event_type=event,
        host=user,
        start_at=start_at,
        end_at=end_at,
        invitee_timezone="UTC",
        status=Booking.StatusChoices.CONFIRMED,
        invitee_name="Test Invitee",
        invitee_email="test@example.com",
    )
    
    url = reverse("bookings:booking_confirmation", kwargs={"uid": booking.uid})
    
    # Missing token
    response = client.get(url)
    assert response.status_code == 404
    
    # Tampered token
    token = make_manage_token(booking) + "bad"
    response = client.get(f"{url}?t={token}")
    assert response.status_code == 404
