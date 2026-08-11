from celery import shared_task
from django.utils import timezone
from datetime import timedelta
import logging

from apps.integrations.models import CalendarConnection, NotificationLog, BusyBlock, SelectedCalendar
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
        connection.save(update_fields=['is_active', 'last_error', 'last_error_at'])
        
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
        connection_id=connection_id,
        kind="disconnection",
        sent_at__gte=cutoff
    ).exists()
    
    if recent_log:
        logger.info(f"Skipping disconnection email for connection_id={connection_id}, already sent recently.")
        return
        
    try:
        connection = CalendarConnection.objects.get(id=connection_id)
        user = connection.user
        
        # Log it so we don't send again
        NotificationLog.objects.create(
            connection=connection,
            kind="disconnection"
        )
        
        # Simulate sending email
        logger.info(f"Sending disconnection email to {user.email} for connection {connection.external_account_email}. Reconnect link: /settings/integrations")
        
    except CalendarConnection.DoesNotExist:
        pass


@shared_task
def check_stale_connections():
    """
    Hourly Celery beat task checking connections not synced in over 24 hours.
    Attempts a lightweight API call, marking failures.
    """
    cutoff = timezone.now() - timedelta(hours=24)
    stale_connections = CalendarConnection.objects.filter(
        is_active=True,
        provider='google'
    ).filter(
        last_synced_at__lt=cutoff
    ) | CalendarConnection.objects.filter(is_active=True, provider='google', last_synced_at__isnull=True)
    
    for connection in stale_connections:
        try:
            from apps.integrations.google.client import GoogleCalendarClient
            client = GoogleCalendarClient(connection)
            # Lightweight API call
            client.service.calendarList().list(maxResults=1).execute()
            
            # Update last_synced_at
            connection.last_synced_at = timezone.now()
            connection.save(update_fields=['last_synced_at'])
        except Exception as e:
            logger.error(f"Health check failed for connection_id={connection.id}: {e}")

@shared_task
def sync_busy_time(connection_id: int):
    """
    Syncs busy blocks for all calendars in a connection.
    Window: now - 1 day to now + max booking window (capped 12 mos).
    """
    from apps.integrations.google.client import GoogleCalendarClient
    from psycopg.types.range import Range
    from django.db import transaction
    from zoneinfo import ZoneInfo
    from datetime import datetime, timezone as dt_timezone
    
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
        elif et.window_type == "fixed_range":
            if et.range_end:
                diff = (et.range_end - now.date()).days
                if diff > max_days:
                    max_days = diff
                    
    # Cap at 12 months (365 days)
    if max_days > 365:
        max_days = 365
    elif max_days == 0:
        max_days = 30 # fallback if no event types or max_days is 0
        
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
                    response = client.service.events().list(
                        calendarId=cal.external_calendar_id,
                        timeMin=window_start.isoformat(),
                        timeMax=window_end.isoformat(),
                        singleEvents=True,
                        pageToken=page_token
                    ).execute()
                    
                    cal_tz_str = response.get('timeZone', 'UTC')
                    cal_tz = ZoneInfo(cal_tz_str)
                    
                    events = response.get('items', [])
                    for event in events:
                        # Exclude transparent
                        if event.get('transparency') == 'transparent':
                            continue
                            
                        # Exclude declined
                        declined = False
                        if 'attendees' in event:
                            for attendee in event['attendees']:
                                if attendee.get('self') and attendee.get('responseStatus') == 'declined':
                                    declined = True
                                    break
                        if declined:
                            continue
                            
                        is_all_day = False
                        
                        start_info = event.get('start', {})
                        end_info = event.get('end', {})
                        
                        if 'date' in start_info:
                            is_all_day = True
                            # All day event
                            start_date = datetime.fromisoformat(start_info['date']).date()
                            end_date = datetime.fromisoformat(end_info['date']).date()
                            
                            start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=cal_tz).astimezone(dt_timezone.utc)
                            end_dt = datetime.combine(end_date, datetime.min.time(), tzinfo=cal_tz).astimezone(dt_timezone.utc)
                        else:
                            start_dt = datetime.fromisoformat(start_info['dateTime']).astimezone(dt_timezone.utc)
                            end_dt = datetime.fromisoformat(end_info['dateTime']).astimezone(dt_timezone.utc)
                            
                        blocks_to_create.append(BusyBlock(
                            connection=connection,
                            calendar=cal,
                            period=Range(start_dt, end_dt, '[)'),
                            external_event_id=event.get('id', ''),
                            is_all_day=is_all_day,
                            synced_at=now
                        ))
                        
                    page_token = response.get('nextPageToken')
                    if not page_token:
                        break
            except Exception as e:
                logger.error(f"Failed to fetch events for calendar {cal.external_calendar_id}: {e}")
                
        BusyBlock.objects.bulk_create(blocks_to_create)
        connection.last_synced_at = now
        connection.save(update_fields=['last_synced_at'])

@shared_task
def scheduled_sync_all():
    """
    Every 15 mins. Skips if synced within 5 mins.
    """
    import random
    
    cutoff = timezone.now() - timedelta(minutes=5)
    connections = CalendarConnection.objects.filter(
        is_active=True, provider='google'
    ).filter(
        last_synced_at__lt=cutoff
    ) | CalendarConnection.objects.filter(is_active=True, provider='google', last_synced_at__isnull=True)
    
    for conn in connections:
        # Jitter up to 60 seconds
        delay = random.randint(0, 60)
        sync_busy_time.apply_async(args=[conn.id], countdown=delay)

@shared_task(bind=True, max_retries=5)
def create_calendar_event(self, booking_id: int):
    from apps.bookings.models import Booking, BookingReference
    from apps.integrations.models import CalendarConnection, SelectedCalendar
    from apps.integrations.google.client import GoogleCalendarClient
    from googleapiclient.errors import HttpError
    from django.db import IntegrityError
    
    try:
        booking = Booking.objects.get(id=booking_id)
    except Booking.DoesNotExist:
        return
        
    if BookingReference.objects.filter(booking=booking, kind="calendar_event").exists():
        return
        
    write_target = SelectedCalendar.objects.filter(
        connection__user=booking.host,
        connection__is_active=True,
        connection__provider='google',
        is_write_target=True
    ).select_related('connection').first()
    
    if not write_target:
        booking.sync_status = Booking.SyncStatusChoices.NOT_APPLICABLE
        booking.save(update_fields=['sync_status'])
        return
        
    try:
        client = GoogleCalendarClient(write_target.connection)
        
        # Generate idempotent deterministic ID based on booking UUID
        # Google accepts base32hex for IDs, we can just remove hyphens from UUID
        import uuid
        event_id = "kairos" + booking.uid.hex
        
        description = ""
        if booking.invitee_notes:
            description += f"Notes:\n{booking.invitee_notes}\n\n"
        if booking.answers:
            description += "Questions:\n"
            for q, a in booking.answers.items():
                description += f"- {q}: {a}\n"
                
        attendees = [
            {'email': booking.host.email, 'responseStatus': 'accepted'},
            {'email': booking.invitee_email, 'responseStatus': 'needsAction'}
        ]
        
        for attendee in booking.attendees.filter(is_organizer=False).exclude(email=booking.invitee_email):
            attendees.append({'email': attendee.email, 'responseStatus': 'needsAction'})
            
        event_body = {
            'id': event_id,
            'summary': f"{booking.event_type.title} with {booking.invitee_name}",
            'description': description,
            'start': {
                'dateTime': booking.start_at.isoformat(),
                'timeZone': booking.invitee_timezone,
            },
            'end': {
                'dateTime': booking.end_at.isoformat(),
                'timeZone': booking.invitee_timezone,
            },
            'attendees': attendees,
            'extendedProperties': {
                'private': {
                    'kairos_booking_uid': str(booking.uid)
                }
            },
            'source': {
                'title': 'Kairos Booking',
                'url': f"https://joinkairos.me/booking/{booking.uid}/" # Example URL
            }
        }
        
        if booking.location_value:
            event_body['location'] = booking.location_value
            
        try:
            created_event = client.service.events().insert(
                calendarId=write_target.external_calendar_id,
                body=event_body,
                sendUpdates='none'
            ).execute()
        except HttpError as e:
            if e.resp.status == 409:
                # Idempotency hit at Google side
                pass
            else:
                raise
                
        try:
            BookingReference.objects.create(
                booking=booking,
                connection=write_target.connection,
                external_event_id=event_id,
                external_calendar_id=write_target.external_calendar_id,
                kind="calendar_event"
            )
        except IntegrityError:
            pass # Someone else beat us to it
            
        booking.sync_status = Booking.SyncStatusChoices.SYNCED
        booking.save(update_fields=['sync_status'])
        
    except Exception as e:
        logger.error(f"Failed to create Google Calendar event for booking {booking.uid}: {e}")
        try:
            self.retry(countdown=2 ** self.request.retries)
        except self.MaxRetriesExceededError:
            booking.sync_status = Booking.SyncStatusChoices.FAILED
            booking.save(update_fields=['sync_status'])

@shared_task(bind=True, max_retries=5)
def delete_calendar_event(self, reference_id: int):
    from apps.bookings.models import BookingReference
    from apps.integrations.google.client import GoogleCalendarClient
    from googleapiclient.errors import HttpError
    
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
                sendUpdates='none'
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
            self.retry(countdown=2 ** self.request.retries)
        except self.MaxRetriesExceededError:
            pass # Best effort, do not fail cancellation
