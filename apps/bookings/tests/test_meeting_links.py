import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import Booking, BookingReference
from apps.bookings.tasks import (
    create_conference_link,
    send_booking_confirmation_emails,
)
from apps.integrations.models import CalendarConnection, SelectedCalendar
from apps.scheduling.models import EventType, Schedule


@pytest.fixture
def user():
    return User.objects.create_user(email="host@test.com", password="pwd", slug="host")


@pytest.fixture
def schedule(user):
    return Schedule.objects.create(user=user, timezone="UTC")


@pytest.fixture
def event_type(user, schedule):
    return EventType.objects.create(
        owner=user,
        title="Test Event",
        slug="test",
        duration_minutes=30,
        schedule=schedule,
        location_type="google_meet",
    )


@pytest.fixture
def booking(user, event_type):
    now = timezone.now()
    return Booking.objects.create(
        host=user,
        event_type=event_type,
        start_at=now + timedelta(days=1),
        end_at=now + timedelta(days=1, minutes=30),
        invitee_name="Invitee",
        invitee_email="invitee@test.com",
        invitee_timezone="UTC",
        status=Booking.StatusChoices.CONFIRMED,
        uid=uuid.uuid4(),
        location_type="google_meet",
    )


@pytest.fixture
def calendar_connection(user):
    conn = CalendarConnection.objects.create(
        user=user, provider="google", external_account_id="test@test.com", is_active=True
    )
    SelectedCalendar.objects.create(
        connection=conn, external_calendar_id="primary", is_write_target=True
    )
    return conn


@pytest.mark.django_db
@patch("apps.integrations.google.client.GoogleCalendarClient")
def test_meet_link_retries_idempotent(MockClient, booking, calendar_connection):
    mock_service = MagicMock()
    MockClient.return_value.service = mock_service

    BookingReference.objects.create(
        booking=booking,
        connection=calendar_connection,
        external_event_id="test_event_id",
        external_calendar_id="primary",
        kind="calendar_event",
    )

    mock_patch = mock_service.events().patch
    mock_patch.return_value.execute.return_value = {
        "conferenceData": {
            "createRequest": {"status": {"statusCode": "success"}},
            "entryPoints": [
                {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"}
            ],
            "conferenceId": "abc-defg-hij",
        }
    }

    create_conference_link(booking.id)
    assert BookingReference.objects.filter(booking=booking, kind="video_conference").count() == 1

    # Second call should be idempotent, no second creation
    create_conference_link(booking.id)
    assert BookingReference.objects.filter(booking=booking, kind="video_conference").count() == 1
    # patch should have been called only once due to the check
    assert mock_patch.call_count == 1


@pytest.mark.django_db
@patch("apps.integrations.google.client.GoogleCalendarClient")
def test_conference_creation_failure_graceful(MockClient, booking, calendar_connection):
    mock_service = MagicMock()
    MockClient.return_value.service = mock_service

    BookingReference.objects.create(
        booking=booking,
        connection=calendar_connection,
        external_event_id="test_event_id",
        external_calendar_id="primary",
        kind="calendar_event",
    )

    mock_service.events().patch.side_effect = Exception("API Down")

    # Should not raise exception
    result_id = create_conference_link(booking.id)
    assert result_id == booking.id

    booking.refresh_from_db()
    assert "meet.jit.si" in booking.meeting_url
    # Confirm still sends email (the chain continues)


@pytest.mark.django_db
@patch("apps.bookings.tasks.logger")
def test_notifications_contain_meeting_url(mock_logger, booking):
    booking.meeting_url = "https://meet.jit.si/test"
    booking.save()

    send_booking_confirmation_emails(booking.id)

    # Check that logger recorded the meeting URL
    mock_logger.info.assert_called_with(
        f"Sending confirmation emails for booking {booking.uid} with URL: https://meet.jit.si/test"
    )


@pytest.mark.django_db
def test_jitsi_urls_deterministic(booking):
    booking.location_type = "jitsi"
    booking.save()

    create_conference_link(booking.id)

    booking.refresh_from_db()
    assert booking.meeting_url.startswith("https://meet.jit.si/Kairos-")

    url1 = booking.meeting_url

    # Run again, should be the same
    # Wait, it returns early if kind="video_conference" exists, so we delete it to test determinism
    BookingReference.objects.filter(booking=booking, kind="video_conference").delete()
    create_conference_link(booking.id)

    booking.refresh_from_db()
    assert booking.meeting_url == url1
