from datetime import UTC, datetime, time, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from psycopg.types.range import Range

from apps.accounts.models import User
from apps.bookings.services import SlotUnavailable, create_booking
from apps.integrations.models import BusyBlock, CalendarConnection, SelectedCalendar
from apps.integrations.services import fetch_external_busy
from apps.integrations.tasks import sync_busy_time
from apps.scheduling.engine import get_slots
from apps.scheduling.models import EventType, Schedule


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
    from apps.scheduling.models import AvailabilityRule

    AvailabilityRule.objects.create(
        schedule=schedule,
        weekday=0,  # Monday
        start_time=time(9, 0),
        end_time=time(17, 0),
    )
    return EventType.objects.create(
        owner=user, title="Test Event", slug="test-event", duration_minutes=60, schedule=schedule
    )


@pytest.mark.django_db
@patch("apps.integrations.google.client.GoogleCalendarClient")
def test_sync_ignores_transparent_and_declined(MockClient, connection, selected_calendar):
    mock_service = MagicMock()
    MockClient.return_value.service = mock_service
    mock_service.events().list().execute.return_value = {
        "timeZone": "UTC",
        "items": [
            {
                "id": "evt1",
                "transparency": "transparent",
                "start": {"dateTime": "2026-08-12T10:00:00Z"},
                "end": {"dateTime": "2026-08-12T11:00:00Z"},
            },
            {
                "id": "evt2",
                "attendees": [{"self": True, "responseStatus": "declined"}],
                "start": {"dateTime": "2026-08-12T11:00:00Z"},
                "end": {"dateTime": "2026-08-12T12:00:00Z"},
            },
        ],
    }

    sync_busy_time(connection.id)

    assert BusyBlock.objects.count() == 0


@pytest.mark.django_db
@patch("apps.integrations.google.client.GoogleCalendarClient")
def test_sync_all_day_event(MockClient, connection, selected_calendar):
    mock_service = MagicMock()
    MockClient.return_value.service = mock_service
    mock_service.events().list().execute.return_value = {
        "timeZone": "America/New_York",
        "items": [
            {"id": "evt_allday", "start": {"date": "2026-08-12"}, "end": {"date": "2026-08-13"}}
        ],
    }

    sync_busy_time(connection.id)

    blocks = BusyBlock.objects.all()
    assert len(blocks) == 1
    block = blocks[0]

    assert block.is_all_day is True
    # 2026-08-12 America/New_York is UTC-4. So start should be 04:00 UTC on Aug 12.
    expected_start = datetime(2026, 8, 12, 4, 0, tzinfo=UTC)
    expected_end = datetime(2026, 8, 13, 4, 0, tzinfo=UTC)

    assert block.period.lower == expected_start
    assert block.period.upper == expected_end


@pytest.mark.django_db
def test_busy_blocks_remove_slots(user, connection, selected_calendar, event_type):
    # Create a busy block
    now = timezone.now()
    # Let's say next Monday
    next_monday = now.date() + timedelta(days=(0 - now.weekday()) % 7)
    if next_monday == now.date():
        next_monday += timedelta(days=7)

    start = datetime.combine(next_monday, time(10, 0), tzinfo=UTC)
    end = datetime.combine(next_monday, time(11, 0), tzinfo=UTC)

    BusyBlock.objects.create(
        connection=connection,
        calendar=selected_calendar,
        period=Range(start, end, "[)"),
        external_event_id="evt1",
    )

    u_start = datetime.combine(next_monday, time.min, tzinfo=UTC)
    u_end = datetime.combine(next_monday, time.max, tzinfo=UTC)
    external_busy = fetch_external_busy(user, u_start, u_end)

    slots = get_slots(event_type, next_monday, next_monday, now, external_busy=external_busy)

    # 10:00 slot should be missing
    assert start not in slots
    # 09:00 slot should be present
    assert datetime.combine(next_monday, time(9, 0), tzinfo=UTC) in slots
    # 11:00 slot should be present
    assert datetime.combine(next_monday, time(11, 0), tzinfo=UTC) in slots


@pytest.mark.django_db
def test_busy_source_false_ignores_blocks(user, connection, event_type):
    selected_calendar = SelectedCalendar.objects.create(
        connection=connection, external_calendar_id="other_cal", name="Other", is_busy_source=False
    )

    now = timezone.now()
    next_monday = now.date() + timedelta(days=(0 - now.weekday()) % 7)
    if next_monday == now.date():
        next_monday += timedelta(days=7)

    start = datetime.combine(next_monday, time(10, 0), tzinfo=UTC)
    end = datetime.combine(next_monday, time(11, 0), tzinfo=UTC)

    BusyBlock.objects.create(
        connection=connection,
        calendar=selected_calendar,
        period=Range(start, end, "[)"),
        external_event_id="evt1",
    )

    u_start = datetime.combine(next_monday, time.min, tzinfo=UTC)
    u_end = datetime.combine(next_monday, time.max, tzinfo=UTC)
    external_busy = fetch_external_busy(user, u_start, u_end)

    assert len(external_busy) == 0

    slots = get_slots(event_type, next_monday, next_monday, now, external_busy=external_busy)
    assert start in slots


@pytest.mark.django_db
@patch("apps.integrations.services.check_live_conflict")
def test_live_check_raises_slot_unavailable(
    mock_live_check, user, connection, selected_calendar, event_type
):
    # Setup live check to return True (conflict exists)
    mock_live_check.return_value = True

    now = timezone.now()
    next_monday = now.date() + timedelta(days=(0 - now.weekday()) % 7)
    if next_monday == now.date():
        next_monday += timedelta(days=7)

    start_at = datetime.combine(next_monday, time(10, 0), tzinfo=UTC)

    with pytest.raises(SlotUnavailable):
        create_booking(
            event_type=event_type,
            start_at=start_at,
            invitee_name="Invitee",
            invitee_email="inv@test.com",
            invitee_timezone="UTC",
            answers={},
            now=now,
        )


@pytest.mark.django_db
@patch("apps.integrations.google.client.GoogleCalendarClient")
def test_live_check_api_error_allows_booking(
    MockClient, user, connection, selected_calendar, event_type
):
    # Mock API to raise an exception
    mock_service = MagicMock()
    MockClient.return_value.service = mock_service
    mock_service.freebusy().query().execute.side_effect = Exception("Google is down")

    now = timezone.now()
    next_monday = now.date() + timedelta(days=(0 - now.weekday()) % 7)
    if next_monday == now.date():
        next_monday += timedelta(days=7)

    start_at = datetime.combine(next_monday, time(10, 0), tzinfo=UTC)

    # Should not raise SlotUnavailable
    booking = create_booking(
        event_type=event_type,
        start_at=start_at,
        invitee_name="Invitee",
        invitee_email="inv@test.com",
        invitee_timezone="UTC",
        answers={},
        now=now,
    )

    assert booking.id is not None
