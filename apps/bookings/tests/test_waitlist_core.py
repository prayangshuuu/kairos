import pytest
from datetime import timedelta
from django.utils import timezone
from django.urls import reverse

from apps.accounts.models import User
from apps.scheduling.models import EventType
from apps.bookings.models import Booking, WaitlistEntry
from apps.bookings.services import create_booking, cancel_booking, reject_booking
from apps.bookings.tasks import autofill_waitlist, process_expired_waitlist_offers

@pytest.fixture
def host_user(db):
    user = User.objects.create_user(email="host@kairos.local", password="password")
    user.timezone = "UTC"
    user.save()
    return user

@pytest.fixture
def event_type(host_user):
    return EventType.objects.create(
        owner=host_user,
        slug="test-event",
        title="Test Event",
        duration_minutes=30,
        waitlist_enabled=True,
        waitlist_claim_window_minutes=60,
    )

@pytest.fixture
def booking(event_type, host_user):
    now = timezone.now()
    start_at = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return create_booking(
        event_type=event_type,
        start_at=start_at,
        invitee_name="Invitee 1",
        invitee_email="invitee1@kairos.local",
        invitee_timezone="UTC",
        answers={},
        now=now,
    )

@pytest.fixture
def waitlist_entry(event_type, host_user):
    return WaitlistEntry.objects.create(
        event_type=event_type,
        host=host_user,
        invitee_name="Waitlister",
        invitee_email="waitlister@kairos.local",
        invitee_timezone="UTC",
        status=WaitlistEntry.StatusChoices.WAITING,
    )

@pytest.mark.django_db(transaction=True)
def test_autofill_on_cancel(client, event_type, booking, waitlist_entry):
    now = timezone.now()
    
    # Cancel booking
    cancel_booking(booking=booking, cancelled_by="host", now=now)
    
    # Run celery task manually (since we are testing it)
    autofill_waitlist(event_type.id, booking.start_at.isoformat())
    
    waitlist_entry.refresh_from_db()
    assert waitlist_entry.status == WaitlistEntry.StatusChoices.OFFERED
    assert waitlist_entry.offered_booking_slot == booking.start_at
    assert waitlist_entry.offer_expires_at is not None

@pytest.mark.django_db(transaction=True)
def test_waitlist_claim_view(client, event_type, waitlist_entry):
    now = timezone.now()
    start_at = (now + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    
    waitlist_entry.status = WaitlistEntry.StatusChoices.OFFERED
    waitlist_entry.offered_booking_slot = start_at
    waitlist_entry.offer_expires_at = now + timedelta(minutes=60)
    waitlist_entry.save()
    
    url = reverse('bookings:waitlist_claim', args=[waitlist_entry.claim_token])
    
    # GET
    response = client.get(url)
    assert response.status_code == 200
    
    # POST
    response = client.post(url)
    assert response.status_code == 302 # redirect to confirmation
    
    waitlist_entry.refresh_from_db()
    assert waitlist_entry.status == WaitlistEntry.StatusChoices.CLAIMED
    
    # Booking created
    assert Booking.objects.filter(invitee_email=waitlist_entry.invitee_email).exists()

@pytest.mark.django_db(transaction=True)
def test_waitlist_expiry(client, event_type, waitlist_entry):
    now = timezone.now()
    start_at = (now + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    
    waitlist_entry.status = WaitlistEntry.StatusChoices.OFFERED
    waitlist_entry.offered_booking_slot = start_at
    waitlist_entry.offer_expires_at = now - timedelta(minutes=10) # expired
    waitlist_entry.save()
    
    process_expired_waitlist_offers()
    
    waitlist_entry.refresh_from_db()
    assert waitlist_entry.status == WaitlistEntry.StatusChoices.EXPIRED
