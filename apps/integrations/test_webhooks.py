import contextlib
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from psycopg.types.range import Range

from apps.accounts.models import User
from apps.integrations.models import BusyBlock, CalendarConnection, SelectedCalendar, WatchChannel


@pytest.fixture
def user():
    return User.objects.create_user(email="test@example.com", password="password")


@pytest.fixture
def connection(user):
    return CalendarConnection.objects.create(
        user=user, provider="google", external_account_id="test-google", is_active=True
    )


@pytest.fixture
def selected_calendar(connection):
    return SelectedCalendar.objects.create(
        connection=connection,
        external_calendar_id="cal-123",
        name="Test Calendar",
        is_busy_source=True,
        sync_token="sync-token-123",
    )


@pytest.fixture
def watch_channel(selected_calendar):
    return WatchChannel.objects.create(
        connection=selected_calendar.connection,
        calendar=selected_calendar,
        channel_id=uuid.uuid4(),
        resource_id="res-123",
        token="secret-token",
        expires_at=timezone.now() + timedelta(days=7),
    )


@pytest.mark.django_db
def test_webhook_mismatched_token_returns_404(client, watch_channel):
    url = reverse("integrations:google_webhook")
    headers = {
        "HTTP_X_GOOG_CHANNEL_ID": str(watch_channel.channel_id),
        "HTTP_X_GOOG_RESOURCE_ID": watch_channel.resource_id,
        "HTTP_X_GOOG_CHANNEL_TOKEN": "wrong-token",
        "HTTP_X_GOOG_RESOURCE_STATE": "exists",
    }

    response = client.post(url, **headers)
    assert response.status_code == 404


@pytest.mark.django_db
def test_webhook_sync_state_acknowledged(client, watch_channel):
    url = reverse("integrations:google_webhook")
    headers = {
        "HTTP_X_GOOG_CHANNEL_ID": str(watch_channel.channel_id),
        "HTTP_X_GOOG_RESOURCE_ID": watch_channel.resource_id,
        "HTTP_X_GOOG_CHANNEL_TOKEN": watch_channel.token,
        "HTTP_X_GOOG_RESOURCE_STATE": "sync",
    }

    with patch("apps.integrations.tasks.sync_calendar_incremental.apply_async") as mock_task:
        response = client.post(url, **headers)
        assert response.status_code == 200
        mock_task.assert_not_called()


@pytest.mark.django_db
def test_webhook_burst_debounce(client, watch_channel):
    url = reverse("integrations:google_webhook")
    headers = {
        "HTTP_X_GOOG_CHANNEL_ID": str(watch_channel.channel_id),
        "HTTP_X_GOOG_RESOURCE_ID": watch_channel.resource_id,
        "HTTP_X_GOOG_CHANNEL_TOKEN": watch_channel.token,
        "HTTP_X_GOOG_RESOURCE_STATE": "exists",
    }

    from django.core.cache import cache

    cache.clear()

    with patch("apps.integrations.tasks.sync_calendar_incremental.apply_async") as mock_task:
        # Send burst of 3 webhooks
        client.post(url, **headers)
        client.post(url, **headers)
        client.post(url, **headers)

        # Should only be called once due to 5-second debounce lock
        assert mock_task.call_count == 1


@pytest.mark.django_db
def test_incremental_sync_410_discards_token_and_resyncs(selected_calendar):
    from googleapiclient.errors import HttpError
    from httplib2 import Response

    from apps.integrations.tasks import sync_calendar_incremental

    # Mock GoogleCalendarClient
    with patch("apps.integrations.google.client.GoogleCalendarClient") as MockClient:
        mock_instance = MockClient.return_value

        # Raise 410 on list()
        error_resp = Response({"status": 410})
        mock_instance.service.events.return_value.list.return_value.execute.side_effect = HttpError(
            error_resp, b"Gone"
        )

        with patch("apps.integrations.tasks.sync_busy_time") as mock_sync_busy:
            # We also mock sync_calendar_incremental calling itself to avoid infinite loop / errors
            # Let's just catch the first one
            with contextlib.suppress(RecursionError):
                sync_calendar_incremental(selected_calendar.id)

            selected_calendar.refresh_from_db()
            assert selected_calendar.sync_token is None

            mock_sync_busy.assert_called_once_with(selected_calendar.connection_id)


@pytest.mark.django_db
def test_incremental_sync_cancelled_event_removes_block(selected_calendar):
    from apps.integrations.tasks import sync_calendar_incremental

    # Create a BusyBlock first
    BusyBlock.objects.create(
        connection=selected_calendar.connection,
        calendar=selected_calendar,
        period=Range(timezone.now(), timezone.now() + timedelta(hours=1), "[)"),
        external_event_id="event-123",
    )

    assert BusyBlock.objects.count() == 1

    with patch("apps.integrations.google.client.GoogleCalendarClient") as MockClient:
        mock_instance = MockClient.return_value

        # Return a cancelled event
        mock_instance.service.events.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "event-123", "status": "cancelled"}],
            "nextSyncToken": "new-sync-token",
        }

        sync_calendar_incremental(selected_calendar.id)

        selected_calendar.refresh_from_db()
        assert selected_calendar.sync_token == "new-sync-token"
        assert BusyBlock.objects.count() == 0
