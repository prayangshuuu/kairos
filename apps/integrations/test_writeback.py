from datetime import time
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from googleapiclient.errors import HttpError

from apps.accounts.models import User
from apps.bookings.models import Attendee, Booking, BookingReference
from apps.bookings.services import create_booking
from apps.integrations.models import CalendarConnection, SelectedCalendar
from apps.integrations.tasks import create_calendar_event, delete_calendar_event
from apps.scheduling.models import AvailabilityRule, EventType, Schedule


@pytest.fixture
def user():
    return User.objects.create_user(email="host@test.com", password="pwd", slug="host")


@pytest.fixture
def connection(user):
    return CalendarConnection.objects.create(
        user=user, provider="google", external_account_id="test@google.com", is_active=True
    )


@pytest.fixture
def selected_calendar(connection):
    return SelectedCalendar.objects.create(
        connection=connection,
        external_calendar_id="primary_cal",
        name="Primary",
        is_busy_source=True,
        is_write_target=True,
    )


@pytest.fixture
def event_type(user):
    schedule = Schedule.objects.create(user=user, timezone="UTC")
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=0, start_time=time(9, 0), end_time=time(17, 0)
    )
    return EventType.objects.create(
        owner=user, title="Test Event", slug="test-event", duration_minutes=60, schedule=schedule
    )


@pytest.fixture
def booking(event_type):
    timezone.now()
    start_at = timezone.now()
    b = Booking(
        event_type=event_type,
        host=event_type.owner,
        start_at=start_at,
        end_at=start_at + timezone.timedelta(hours=1),
        invitee_timezone="UTC",
        invitee_name="Invitee",
        invitee_email="inv@test.com",
    )
    b.save()
    Attendee.objects.create(booking=b, name="Invitee", email="inv@test.com", is_organizer=False)
    Attendee.objects.create(booking=b, name="Host", email=event_type.owner.email, is_organizer=True)
    return b


@pytest.mark.django_db
@patch("apps.integrations.google.client.GoogleCalendarClient")
def test_create_event_idempotent(MockClient, booking, selected_calendar):
    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_service.events.return_value = mock_events
    MockClient.return_value.service = mock_service
    mock_resp = MagicMock()
    mock_resp.status = 404
    mock_events.get.return_value.execute.side_effect = HttpError(
        resp=mock_resp, content=b"Not Found"
    )
    mock_events.insert.return_value.execute.return_value = {"id": f"kairos{booking.uid.hex}"}

    # Run twice
    create_calendar_event(booking.id)
    create_calendar_event(booking.id)

    # Assert called once
    mock_events.insert.assert_called_once()
    assert BookingReference.objects.filter(booking=booking).count() == 1

    booking.refresh_from_db()
    assert booking.sync_status == Booking.SyncStatusChoices.SYNCED


@pytest.mark.django_db
@patch("apps.integrations.google.client.GoogleCalendarClient")
def test_create_event_409_is_success(MockClient, booking, selected_calendar):
    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_service.events.return_value = mock_events
    MockClient.return_value.service = mock_service

    mock_resp = MagicMock()
    mock_resp.status = 409
    mock_events.insert.return_value.execute.side_effect = HttpError(
        resp=mock_resp, content=b"Conflict"
    )

    create_calendar_event(booking.id)

    assert BookingReference.objects.filter(booking=booking).count() == 1
    booking.refresh_from_db()
    assert booking.sync_status == Booking.SyncStatusChoices.SYNCED


@pytest.mark.django_db
@patch("apps.integrations.google.client.GoogleCalendarClient")
def test_delete_event(MockClient, booking, selected_calendar):
    ref = BookingReference.objects.create(
        booking=booking,
        connection=selected_calendar.connection,
        external_calendar_id="cal_id",
        external_event_id="evt_id",
        kind="calendar_event",
    )

    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_service.events.return_value = mock_events
    MockClient.return_value.service = mock_service

    delete_calendar_event(ref.id)

    mock_events.delete.assert_called_once()
    assert not BookingReference.objects.filter(id=ref.id).exists()


@pytest.mark.django_db
@patch("apps.integrations.google.client.GoogleCalendarClient")
def test_delete_event_404_success(MockClient, booking, selected_calendar):
    ref = BookingReference.objects.create(
        booking=booking,
        connection=selected_calendar.connection,
        external_calendar_id="cal_id",
        external_event_id="evt_id",
        kind="calendar_event",
    )

    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_service.events.return_value = mock_events
    MockClient.return_value.service = mock_service

    mock_resp = MagicMock()
    mock_resp.status = 404
    mock_events.delete.return_value.execute.side_effect = HttpError(
        resp=mock_resp, content=b"Not Found"
    )

    delete_calendar_event(ref.id)

    assert not BookingReference.objects.filter(id=ref.id).exists()


@pytest.mark.django_db
@patch("apps.integrations.google.client.GoogleCalendarClient")
@patch("apps.integrations.tasks.create_calendar_event.retry")
def test_create_event_permanently_failed(mock_retry, MockClient, booking, selected_calendar):
    from celery.exceptions import MaxRetriesExceededError

    mock_retry.side_effect = MaxRetriesExceededError()

    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_service.events.return_value = mock_events
    MockClient.return_value.service = mock_service

    mock_resp = MagicMock()
    mock_resp.status = 404
    mock_events.get.return_value.execute.side_effect = HttpError(
        resp=mock_resp, content=b"Not Found"
    )

    mock_resp_500 = MagicMock()
    mock_resp_500.status = 500
    mock_events.insert.return_value.execute.side_effect = HttpError(
        resp=mock_resp_500, content=b"Internal Server Error"
    )

    create_calendar_event(booking.id)

    booking.refresh_from_db()
    assert booking.sync_status == Booking.SyncStatusChoices.FAILED


@pytest.mark.django_db(transaction=True)
@patch("apps.integrations.tasks.create_calendar_event.delay")
@patch("apps.integrations.services.fetch_external_busy", return_value=[])
@patch("apps.integrations.services.check_live_conflict", return_value=False)
def test_rolled_back_transaction_does_not_fire_task(mock_live, mock_fetch, mock_create, event_type):
    from django.db import transaction

    now = timezone.now()
    start_at = timezone.now() + timezone.timedelta(days=1)

    try:
        with transaction.atomic():
            create_booking(
                event_type=event_type,
                start_at=start_at,
                invitee_name="Invitee",
                invitee_email="inv@test.com",
                invitee_timezone="UTC",
                answers={},
                now=now,
            )
            # Rollback to simulate failure
            raise Exception("Force rollback")
    except Exception:
        pass

    mock_create.assert_not_called()
