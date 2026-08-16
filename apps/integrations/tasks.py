import logging
from datetime import UTC, timedelta

from celery import shared_task
from django.utils import timezone

from apps.integrations.models import (
    BusyBlock,
    CalendarConnection,
    NotificationLog,
    SelectedCalendar,
)
from apps.scheduling.models import EventType

logger = logging.getLogger(__name__)


def handle_terminal_connection_error(connection_id: int, error_msg: str):
    """
    Handle terminal connection errors (e.g. invalid_grant).
    This runs synchronously from within the exception handler to ensure the connection
    is deactivated immediately, but we queue the email task.
    """
    try:
        connection = CalendarConnection.objects.get(id=connection_id)
        connection.is_active = False
        connection.last_error = error_msg
        connection.last_error_at = timezone.now()
        connection.save(update_fields=["is_active", "last_error", "last_error_at"])

        # Queue email task
        send_disconnection_email.delay(connection.id)
    except CalendarConnection.DoesNotExist:
        pass


@shared_task
def send_disconnection_email(connection_id: int):
    """
    Sends a disconnection email to the user.
    Guarded by a NotificationLog to ensure at most one per week per connection.
    """
    cutoff = timezone.now() - timedelta(days=7)

    # Check if we sent one recently
    recent_log = NotificationLog.objects.filter(
        connection_id=connection_id, kind="disconnection", sent_at__gte=cutoff
    ).exists()

    if recent_log:
        logger.info(
            f"Skipping disconnection email for connection_id={connection_id}, already sent recently."
        )
        return

    try:
        connection = CalendarConnection.objects.get(id=connection_id)
        user = connection.user

        # Log it so we don't send again
        NotificationLog.objects.create(connection=connection, kind="disconnection")

        # Simulate sending email
        logger.info(
            f"Sending disconnection email to {user.email} for connection {connection.external_account_email}. Reconnect link: /settings/integrations"
        )

    except CalendarConnection.DoesNotExist:
        pass


@shared_task
def check_stale_connections():
    """
    Hourly Celery beat task checking connections not synced in over 24 hours.
    Attempts a lightweight API call, marking failures.
    """
    cutoff = timezone.now() - timedelta(hours=24)
    stale_connections = CalendarConnection.objects.filter(is_active=True, provider="google").filter(
        last_synced_at__lt=cutoff
    ) | CalendarConnection.objects.filter(
        is_active=True, provider="google", last_synced_at__isnull=True
    )

    for connection in stale_connections:
        try:
            from apps.integrations.google.client import GoogleCalendarClient

            client = GoogleCalendarClient(connection)
            # Lightweight API call
            client.service.calendarList().list(maxResults=1).execute()

            # Update last_synced_at
            connection.last_synced_at = timezone.now()
            connection.save(update_fields=["last_synced_at"])
        except Exception as e:
            logger.error(f"Health check failed for connection_id={connection.id}: {e}")


@shared_task
def sync_busy_time(connection_id: int):
    """
    Syncs busy blocks for all calendars in a connection.
    Window: now - 1 day to now + max booking window (capped 12 mos).
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from django.db import transaction
    from psycopg.types.range import Range

    from apps.integrations.google.client import GoogleCalendarClient

    try:
        connection = CalendarConnection.objects.get(id=connection_id, is_active=True)
    except CalendarConnection.DoesNotExist:
        return

    now = timezone.now()
    window_start = now - timedelta(days=1)

    # Calculate max window
    max_days = 0
    event_types = EventType.objects.filter(owner=connection.user, is_active=True)
    for et in event_types:
        if et.window_type == "rolling":
            if et.rolling_days > max_days:
                max_days = et.rolling_days
        elif et.window_type == "fixed_range" and et.range_end:
            diff = (et.range_end - now.date()).days
            if diff > max_days:
                max_days = diff

    # Cap at 12 months (365 days)
    if max_days > 365:
        max_days = 365
    elif max_days == 0:
        max_days = 30  # fallback if no event types or max_days is 0

    window_end = now + timedelta(days=max_days)

    try:
        client = GoogleCalendarClient(connection)
    except Exception as e:
        logger.error(f"Failed to init GoogleCalendarClient for {connection_id}: {e}")
        return

    calendars = connection.calendars.all()

    with transaction.atomic():
        # Atomically delete and recreate
        BusyBlock.objects.filter(connection=connection).delete()

        blocks_to_create = []
        for cal in calendars:
            try:
                page_token = None
                while True:
                    response = (
                        client.service.events()
                        .list(
                            calendarId=cal.external_calendar_id,
                            timeMin=window_start.isoformat(),
                            timeMax=window_end.isoformat(),
                            singleEvents=True,
                            pageToken=page_token,
                        )
                        .execute()
                    )

                    cal_tz_str = response.get("timeZone", "UTC")
                    cal_tz = ZoneInfo(cal_tz_str)

                    events = response.get("items", [])
                    for event in events:
                        # Exclude transparent
                        if event.get("transparency") == "transparent":
                            continue

                        # Exclude declined
                        declined = False
                        if "attendees" in event:
                            for attendee in event["attendees"]:
                                if (
                                    attendee.get("self")
                                    and attendee.get("responseStatus") == "declined"
                                ):
                                    declined = True
                                    break
                        if declined:
                            continue

                        is_all_day = False

                        start_info = event.get("start", {})
                        end_info = event.get("end", {})

                        if "date" in start_info:
                            is_all_day = True
                            # All day event
                            start_date = datetime.fromisoformat(start_info["date"]).date()
                            end_date = datetime.fromisoformat(end_info["date"]).date()

                            start_dt = datetime.combine(
                                start_date, datetime.min.time(), tzinfo=cal_tz
                            ).astimezone(UTC)
                            end_dt = datetime.combine(
                                end_date, datetime.min.time(), tzinfo=cal_tz
                            ).astimezone(UTC)
                        else:
                            start_dt = datetime.fromisoformat(start_info["dateTime"]).astimezone(
                                UTC
                            )
                            end_dt = datetime.fromisoformat(end_info["dateTime"]).astimezone(UTC)

                        blocks_to_create.append(
                            BusyBlock(
                                connection=connection,
                                calendar=cal,
                                period=Range(start_dt, end_dt, "[)"),
                                external_event_id=event.get("id", ""),
                                is_all_day=is_all_day,
                                synced_at=now,
                            )
                        )

                    page_token = response.get("nextPageToken")
                    if not page_token:
                        break
            except Exception as e:
                logger.error(f"Failed to fetch events for calendar {cal.external_calendar_id}: {e}")

        BusyBlock.objects.bulk_create(blocks_to_create)
        connection.last_synced_at = now
        connection.save(update_fields=["last_synced_at"])


@shared_task
def scheduled_sync_all():
    """
    Every 15 mins. Skips if synced within 5 mins.
    Also skips if connection has healthy watch channels for all its busy-source calendars.
    """
    import random

    cutoff = timezone.now() - timedelta(minutes=5)
    connections = CalendarConnection.objects.filter(is_active=True, provider="google").filter(
        last_synced_at__lt=cutoff
    ) | CalendarConnection.objects.filter(
        is_active=True, provider="google", last_synced_at__isnull=True
    )

    for conn in connections:
        # Check if we should skip due to watch channels
        # A connection is "healthy" if every busy-source calendar has an active watch channel
        cals = conn.calendars.filter(is_busy_source=True)
        all_watched = True
        for cal in cals:
            if not cal.watch_channels.filter(expires_at__gt=timezone.now()).exists():
                all_watched = False
                break

        if (
            all_watched
            and conn.last_synced_at
            and conn.last_synced_at > (timezone.now() - timedelta(hours=24))
        ):
            # Skip 15-minute polling if watches are active and we synced in the last 24h as a sanity check
            continue

        # Jitter up to 60 seconds
        delay = random.randint(0, 60)
        sync_busy_time.apply_async(args=[conn.id], countdown=delay)


@shared_task(bind=True, max_retries=5)
def create_calendar_event(self, booking_id: int):
    from django.db import IntegrityError
    from googleapiclient.errors import HttpError

    from apps.bookings.models import Booking, BookingReference
    from apps.integrations.google.client import GoogleCalendarClient

    try:
        booking = Booking.objects.get(id=booking_id)
    except Booking.DoesNotExist:
        return booking_id

    if BookingReference.objects.filter(booking=booking, kind="calendar_event").exists():
        return booking_id

    hosts_to_sync = []
    if booking.event_type.assignment_strategy == "collective":
        # All active, required hosts
        hosts_to_sync = [h.user for h in booking.event_type.hosts.filter(is_active=True, is_required=True).select_related("user")]
        if not hosts_to_sync:
            hosts_to_sync = [booking.host]
    else:
        # Single or round robin (booking.host is the assigned host)
        hosts_to_sync = [booking.host]

    write_targets = list(
        SelectedCalendar.objects.filter(
            connection__user__in=hosts_to_sync,
            connection__is_active=True,
            connection__provider="google",
            is_write_target=True,
        ).select_related("connection")
    )

    if not write_targets:
        booking.sync_status = Booking.SyncStatusChoices.NOT_APPLICABLE
        booking.save(update_fields=["sync_status"])
        return booking_id

    try:
        for write_target in write_targets:
            client = GoogleCalendarClient(write_target.connection)

            # Generate idempotent deterministic ID based on booking UUID
            # Google accepts base32hex for IDs, we can just remove hyphens from UUID
            # Add host ID to prevent collisions across multiple targets for the same booking
            event_id = f"kairos{booking.uid.hex}{write_target.connection.user_id}"

            description = ""
            if booking.invitee_notes:
                description += f"Notes:\n{booking.invitee_notes}\n\n"
            if booking.answers:
                description += "Questions:\n"
                for q, a in booking.answers.items():
                    description += f"- {q}: {a}\n"

            attendees = [
                {"email": write_target.connection.user.email, "responseStatus": "accepted"},
                {"email": booking.invitee_email, "responseStatus": "needsAction"},
            ]
            
            # Also add the other hosts as attendees? The prompt says "on ALL required hosts' calendars".
            # By creating it on each host's calendar, they each own their copy.
            # No need to add them as attendees to each other's copies.

            for attendee in booking.attendees.filter(is_organizer=False).exclude(
                email=booking.invitee_email
            ):
                attendees.append({"email": attendee.email, "responseStatus": "needsAction"})

            event_body = {
                "id": event_id,
                "summary": f"{booking.event_type.title} with {booking.invitee_name}",
                "description": description,
                "start": {
                    "dateTime": booking.start_at.isoformat(),
                    "timeZone": booking.invitee_timezone,
                },
                "end": {
                    "dateTime": booking.end_at.isoformat(),
                    "timeZone": booking.invitee_timezone,
                },
                "attendees": attendees,
                "extendedProperties": {"private": {"kairos_booking_uid": str(booking.uid)}},
                "source": {
                    "title": "Kairos Booking",
                    "url": f"https://joinkairos.tech/booking/{booking.uid}/",  # Example URL
                },
            }

            if booking.location_value:
                event_body["location"] = booking.location_value

            try:
                client.service.events().get(
                    calendarId=write_target.external_calendar_id, eventId=event_id
                ).execute()
            except HttpError as e:
                if e.resp.status == 404:
                    try:
                        client.service.events().insert(
                            calendarId=write_target.external_calendar_id,
                            body=event_body,
                            sendUpdates="all",
                        ).execute()
                    except HttpError as insert_e:
                        if insert_e.resp.status == 409:
                            client.service.events().get(
                                calendarId=write_target.external_calendar_id, eventId=event_id
                            ).execute()
                        else:
                            raise insert_e
                elif e.resp.status == 409:
                    client.service.events().get(
                        calendarId=write_target.external_calendar_id, eventId=event_id
                    ).execute()
                else:
                    raise

            try:
                BookingReference.objects.create(
                    booking=booking,
                    connection=write_target.connection,
                    external_event_id=event_id,
                    external_calendar_id=write_target.external_calendar_id,
                    kind="calendar_event",
                )
            except IntegrityError:
                pass  # Someone else beat us to it

        booking.sync_status = Booking.SyncStatusChoices.SYNCED
        booking.save(update_fields=["sync_status"])
        return booking_id

    except Exception as e:
        logger.error(f"Failed to create Google Calendar event for booking {booking.uid}: {e}")
        try:
            self.retry(countdown=2**self.request.retries)
        except self.MaxRetriesExceededError:
            booking.sync_status = Booking.SyncStatusChoices.FAILED
            booking.save(update_fields=["sync_status"])
            return booking_id


@shared_task(bind=True, max_retries=5)
def delete_calendar_event(self, reference_id: int):
    from googleapiclient.errors import HttpError

    from apps.bookings.models import BookingReference
    from apps.integrations.google.client import GoogleCalendarClient

    try:
        ref = BookingReference.objects.get(id=reference_id)
    except BookingReference.DoesNotExist:
        return

    if not ref.connection:
        # Connection was deleted
        ref.delete()
        return

    try:
        client = GoogleCalendarClient(ref.connection)

        try:
            client.service.events().delete(
                calendarId=ref.external_calendar_id,
                eventId=ref.external_event_id,
                sendUpdates="all",
            ).execute()
        except HttpError as e:
            if e.resp.status in (404, 410):
                # Already deleted
                pass
            else:
                raise

        ref.delete()

    except Exception as e:
        logger.error(f"Failed to delete Google Calendar event {ref.external_event_id}: {e}")
        try:
            self.retry(countdown=2**self.request.retries)
        except self.MaxRetriesExceededError:
            pass  # Best effort, do not fail cancellation


@shared_task
def register_watch(calendar_id: int):
    import uuid
    from datetime import timedelta

    from django.conf import settings

    from apps.integrations.google.client import GoogleCalendarClient
    from apps.integrations.models import WatchChannel

    try:
        cal = SelectedCalendar.objects.get(
            id=calendar_id, is_busy_source=True, connection__is_active=True
        )
    except SelectedCalendar.DoesNotExist:
        return

    client = GoogleCalendarClient(cal.connection)

    # We need a publicly reachable webhook URL.
    # For local dev, this must be an ngrok URL.
    webhook_url = (
        getattr(settings, "WEBHOOK_BASE_URL", "https://api.joinkairos.tech")
        + "/webhook/google/calendar/"
    )

    channel_id = str(uuid.uuid4())
    token = str(uuid.uuid4())

    body = {"id": channel_id, "type": "web_hook", "address": webhook_url, "token": token}

    try:
        res = (
            client.service.events().watch(calendarId=cal.external_calendar_id, body=body).execute()
        )
    except Exception as e:
        logger.error(f"Failed to register watch for calendar {cal.id}: {e}")
        return

    # Store channel details
    import datetime

    from django.utils import timezone

    # Google channels typically expire in 7 days or less.
    # The API returns 'expiration' as a Unix timestamp in milliseconds as a string.
    expiration_ms = int(res.get("expiration", 0))
    if expiration_ms > 0:
        expires_at = datetime.datetime.fromtimestamp(expiration_ms / 1000.0, tz=datetime.UTC)
    else:
        expires_at = timezone.now() + timedelta(days=7)

    WatchChannel.objects.create(
        connection=cal.connection,
        calendar=cal,
        channel_id=channel_id,
        resource_id=res.get("resourceId"),
        token=token,
        expires_at=expires_at,
    )


@shared_task
def sync_calendar_incremental(calendar_id: int):
    import datetime
    from zoneinfo import ZoneInfo

    from django.utils import timezone
    from googleapiclient.errors import HttpError
    from psycopg.types.range import Range

    from apps.integrations.google.client import GoogleCalendarClient
    from apps.integrations.models import BusyBlock

    try:
        cal = SelectedCalendar.objects.get(id=calendar_id, connection__is_active=True)
    except SelectedCalendar.DoesNotExist:
        return

    client = GoogleCalendarClient(cal.connection)

    if not cal.sync_token:
        # Full sync without singleEvents=True to get a sync token
        # This is expensive but necessary once.
        sync_busy_time(cal.connection.id)

        try:
            page_token = None
            # Use timeMin to avoid fetching history, we only care about future/recent events
            time_min = (timezone.now() - datetime.timedelta(days=1)).isoformat()
            while True:
                res = (
                    client.service.events()
                    .list(
                        calendarId=cal.external_calendar_id, timeMin=time_min, pageToken=page_token
                    )
                    .execute()
                )

                page_token = res.get("nextPageToken")
                if not page_token:
                    cal.sync_token = res.get("nextSyncToken")
                    cal.save(update_fields=["sync_token"])
                    break
        except Exception as e:
            logger.error(f"Failed to initialize sync token for cal {cal.id}: {e}")
        return

    # Incremental sync using sync_token
    page_token = None
    needs_full_sync = False

    while True:
        try:
            res = (
                client.service.events()
                .list(
                    calendarId=cal.external_calendar_id,
                    syncToken=cal.sync_token,
                    pageToken=page_token,
                )
                .execute()
            )
        except HttpError as e:
            if e.resp.status == 410:
                # Sync token invalid
                cal.sync_token = None
                cal.save(update_fields=["sync_token"])
                BusyBlock.objects.filter(calendar=cal).delete()
                # And re-init token (which does a full sync internally)
                sync_calendar_incremental(cal.id)
                return
            raise e

        cal_tz_str = res.get("timeZone", "UTC")
        cal_tz = ZoneInfo(cal_tz_str)

        events = res.get("items", [])
        for event in events:
            event_id = event.get("id")

            # Handle deletions
            if event.get("status") == "cancelled":
                BusyBlock.objects.filter(calendar=cal, external_event_id=event_id).delete()
                continue

            # If it's a recurring event master, it has 'recurrence'
            if "recurrence" in event:
                # Trade-off: Incremental sync doesn't expand recurrences.
                # When a recurring master changes, we drop the calendar's blocks and do a full sync.
                # This is simpler than manual recurrence expansion or bounded partial syncs.
                needs_full_sync = True
                break

            # Exclude transparent
            if event.get("transparency") == "transparent":
                BusyBlock.objects.filter(calendar=cal, external_event_id=event_id).delete()
                continue

            # Exclude declined
            declined = False
            if "attendees" in event:
                for attendee in event["attendees"]:
                    if attendee.get("self") and attendee.get("responseStatus") == "declined":
                        declined = True
                        break
            if declined:
                BusyBlock.objects.filter(calendar=cal, external_event_id=event_id).delete()
                continue

            # Upsert BusyBlock
            start_info = event.get("start", {})
            end_info = event.get("end", {})

            if "date" in start_info:
                is_all_day = True
                start_date = datetime.datetime.fromisoformat(start_info["date"]).date()
                end_date = datetime.datetime.fromisoformat(end_info["date"]).date()
                start_dt = datetime.datetime.combine(
                    start_date, datetime.datetime.min.time(), tzinfo=cal_tz
                ).astimezone(datetime.UTC)
                end_dt = datetime.datetime.combine(
                    end_date, datetime.datetime.min.time(), tzinfo=cal_tz
                ).astimezone(datetime.UTC)
            elif "dateTime" in start_info:
                is_all_day = False
                start_dt = datetime.datetime.fromisoformat(start_info["dateTime"]).astimezone(
                    datetime.UTC
                )
                end_dt = datetime.datetime.fromisoformat(end_info["dateTime"]).astimezone(
                    datetime.UTC
                )
            else:
                continue  # Missing start/end?

            BusyBlock.objects.update_or_create(
                calendar=cal,
                external_event_id=event_id,
                defaults={
                    "connection": cal.connection,
                    "period": Range(start_dt, end_dt, "[)"),
                    "is_all_day": is_all_day,
                    "synced_at": timezone.now(),
                },
            )

        if needs_full_sync:
            break

        page_token = res.get("nextPageToken")
        if not page_token:
            cal.sync_token = res.get("nextSyncToken")
            cal.save(update_fields=["sync_token"])
            break

    if needs_full_sync:
        sync_busy_time(cal.connection.id)
        # Fetch a new sync token
        client = GoogleCalendarClient(cal.connection)
        try:
            pt = None
            time_min = (timezone.now() - datetime.timedelta(days=1)).isoformat()
            while True:
                res = (
                    client.service.events()
                    .list(calendarId=cal.external_calendar_id, timeMin=time_min, pageToken=pt)
                    .execute()
                )
                pt = res.get("nextPageToken")
                if not pt:
                    cal.sync_token = res.get("nextSyncToken")
                    cal.save(update_fields=["sync_token"])
                    break
        except Exception:
            pass


@shared_task
def renew_watch_channels():
    from datetime import timedelta

    from django.utils import timezone

    from apps.integrations.models import WatchChannel

    # Renew channels expiring within 48 hours
    cutoff = timezone.now() + timedelta(hours=48)
    expiring = WatchChannel.objects.filter(expires_at__lt=cutoff)

    for channel in expiring:
        cal_id = channel.calendar_id
        # We can't really "renew" a Google channel, we just create a new one.
        # But we don't strictly need to delete the old one, though it's polite.
        channel.delete()
        register_watch.delay(cal_id)

    # Find active busy source calendars without any watch channel
    cals_without_watch = SelectedCalendar.objects.filter(
        is_busy_source=True, connection__is_active=True
    ).exclude(watch_channels__isnull=False)

    for cal in cals_without_watch:
        register_watch.delay(cal.id)
