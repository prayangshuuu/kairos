from celery import shared_task
from django.utils import timezone
from datetime import timedelta
import logging

from apps.integrations.models import CalendarConnection, NotificationLog

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
