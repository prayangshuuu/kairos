import pytest
from django.urls import reverse
from apps.bookings.models import WaitlistEntry
from datetime import datetime, UTC
from django.core.signing import Signer
from unittest.mock import patch

@pytest.mark.django_db
def test_join_waitlist_get(client, host_with_schedule, event_type_factory):
    event_type = event_type_factory(owner=host_with_schedule)
    event_type.waitlist_enabled = True
    event_type.is_active = True
    event_type.save()
    host_with_schedule.is_active = True
    host_with_schedule.save()
    url = reverse("bookings:join_waitlist", kwargs={"host_slug": host_with_schedule.slug, "event_slug": event_type.slug})
    response = client.get(url)
    assert response.status_code == 200
    assert "form" in response.context

@pytest.mark.django_db
def test_join_waitlist_post(client, host_with_schedule, event_type_factory):
    event_type = event_type_factory(owner=host_with_schedule)
    event_type.waitlist_enabled = True
    event_type.is_active = True
    event_type.save()
    host_with_schedule.is_active = True
    host_with_schedule.save()
    url = reverse("bookings:join_waitlist", kwargs={"host_slug": host_with_schedule.slug, "event_slug": event_type.slug})
    
    signer = Signer()
    timestamp_token = signer.sign(str(datetime.now(UTC).timestamp() - 3.0))
    
    with patch("apps.bookings.tasks.send_waitlist_confirmation.delay") as mock_task:
        response = client.post(url, {
            "invitee_name": "Test Waitlist",
            "invitee_email": "test@example.com",
            "tz": "UTC",
            "event_type_id": str(event_type.id),
            "timestamp_token": timestamp_token,
        })
        
        assert response.status_code == 200
        print("FORM ERRORS:", response.context.get("form").errors if response.context.get("form") else "No form")
        assert "bookings/join_waitlist_success.html" in [t.name for t in response.templates]
        assert WaitlistEntry.objects.count() == 1
        mock_task.assert_called_once()
    
@pytest.mark.django_db
def test_join_waitlist_full(client, host_with_schedule, event_type_factory):
    event_type = event_type_factory(owner=host_with_schedule)
    event_type.waitlist_enabled = True
    event_type.waitlist_max_size = 1
    event_type.is_active = True
    event_type.save()
    host_with_schedule.is_active = True
    host_with_schedule.save()
    
    WaitlistEntry.objects.create(
        event_type=event_type,
        host=host_with_schedule,
        invitee_name="Existing User",
        invitee_email="existing@example.com"
    )
    
    url = reverse("bookings:join_waitlist", kwargs={"host_slug": host_with_schedule.slug, "event_slug": event_type.slug})
    response = client.get(url)
    assert response.status_code == 200
    assert "bookings/waitlist_full.html" in [t.name for t in response.templates]

@pytest.mark.django_db
def test_leave_waitlist(client, host_with_schedule, event_type_factory):
    event_type = event_type_factory(owner=host_with_schedule)
    entry = WaitlistEntry.objects.create(
        event_type=event_type,
        host=host_with_schedule,
        invitee_name="Existing User",
        invitee_email="existing@example.com"
    )
    url = reverse("bookings:leave_waitlist", kwargs={"uid": entry.claim_token})
    response = client.post(url)
    assert response.status_code == 200
    entry.refresh_from_db()
    assert entry.status == WaitlistEntry.StatusChoices.CANCELLED
