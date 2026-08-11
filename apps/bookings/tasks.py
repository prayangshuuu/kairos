from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from apps.bookings.models import Booking, NotificationLog, BookingReference
from apps.bookings.services import reject_booking
import logging
from celery import chain

logger = logging.getLogger(__name__)

@shared_task
def process_booking_confirmation(booking_id: int):
    from apps.integrations.tasks import create_calendar_event
    chain(
        create_calendar_event.s(booking_id),
        create_conference_link.s(),
        send_booking_confirmation_emails.s()
    ).apply_async()

@shared_task
def create_conference_link(booking_id: int):
    try:
        booking = Booking.objects.get(id=booking_id)
    except Booking.DoesNotExist:
        return booking_id
        
    if booking.location_type in ['jitsi', 'zoom', 'ms_teams']:
        if BookingReference.objects.filter(booking=booking, kind="video_conference").exists():
            return booking_id
            
        from apps.integrations.conferencing.providers import PROVIDERS
        provider = PROVIDERS.get(booking.location_type)
        if provider:
            try:
                meeting_details = provider.create_meeting(booking)
                booking.meeting_url = meeting_details.url
                booking.save(update_fields=['meeting_url'])
                
                BookingReference.objects.create(
                    booking=booking,
                    external_event_id=meeting_details.id,
                    external_calendar_id="",
                    kind="video_conference",
                    meeting_url=meeting_details.url
                )
            except NotImplementedError:
                pass
    return booking_id

@shared_task
def send_booking_confirmation_emails(booking_id: int):
    try:
        booking = Booking.objects.get(id=booking_id)
    except Booking.DoesNotExist:
        return
        
    # [HOOK: actually send the email here]
    logger.info(f"Sending confirmation emails for booking {booking.uid} with URL: {booking.meeting_url}")
    return booking_id

@shared_task
def auto_reject_expired_pending_bookings():
    now = timezone.now()
    
    # 1. Reject pending bookings whose start time has passed
    expired_bookings = Booking.objects.filter(
        status=Booking.StatusChoices.PENDING,
        start_at__lt=now
    )
    for booking in expired_bookings:
        try:
            reject_booking(
                booking=booking, 
                rejected_by=None, 
                reason="Auto-rejected because the start time has passed.", 
                now=now
            )
        except Exception as e:
            logger.error(f"Failed to auto-reject past booking {booking.uid}: {e}")

    # 2. Reject pending bookings that exceeded the confirmation_deadline_hours
    # We can't do a simple F-expression filter easily if confirmation_deadline_hours is on the related event_type
    # and we need to add hours to created_at. So we'll iterate or use database-specific expressions.
    # Iterating is fine for this task.
    pending_deadline_bookings = Booking.objects.filter(
        status=Booking.StatusChoices.PENDING,
        event_type__confirmation_deadline_hours__isnull=False
    ).select_related('event_type')
    
    for booking in pending_deadline_bookings:
        deadline = booking.created_at + timedelta(hours=booking.event_type.confirmation_deadline_hours)
        if now > deadline:
            try:
                reject_booking(
                    booking=booking, 
                    rejected_by=None, 
                    reason="Auto-rejected because the host did not respond in time.", 
                    now=now
                )
            except Exception as e:
                logger.error(f"Failed to auto-reject booking {booking.uid} due to deadline: {e}")

@shared_task
def nudge_host_pending_bookings():
    now = timezone.now()
    twenty_four_hours_ago = now - timedelta(hours=24)
    
    pending_bookings = Booking.objects.filter(
        status=Booking.StatusChoices.PENDING,
        created_at__lt=twenty_four_hours_ago
    )
    
    for booking in pending_bookings:
        # Check if we already sent a nudge
        log_exists = NotificationLog.objects.filter(
            booking=booking,
            kind="host_nudge"
        ).exists()
        
        if not log_exists:
            # [HOOK: Send email nudge to host]
            logger.info(f"Nudging host {booking.host.email} about pending booking {booking.uid}")
            
            NotificationLog.objects.create(
                booking=booking,
                kind="host_nudge"
            )
