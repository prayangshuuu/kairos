import logging
from datetime import datetime, timedelta
from django.db import transaction, IntegrityError, OperationalError
from apps.bookings.models import Booking, Attendee
from apps.scheduling.models import EventType
from apps.scheduling.engine import is_slot_available

logger = logging.getLogger(__name__)

class SlotUnavailable(Exception):
    pass

class AlreadyCancelled(Exception):
    pass
    
class CancellationNotAllowed(Exception):
    pass

def cancel_booking(
    *,
    booking: Booking,
    cancelled_by: str,
    reason: str = "",
    now: datetime,
) -> Booking:
    if booking.status in [Booking.StatusChoices.CANCELLED, Booking.StatusChoices.REJECTED]:
        raise AlreadyCancelled("Booking is already cancelled or rejected.")
        
    if cancelled_by == "invitee":
        if not booking.event_type.allow_cancellation:
            raise CancellationNotAllowed("This event type does not allow cancellation by invitees.")
        
        if booking.event_type.cancellation_cutoff_hours is not None:
            cutoff = booking.start_at - timedelta(hours=booking.event_type.cancellation_cutoff_hours)
            if now > cutoff:
                raise CancellationNotAllowed("It is too late to cancel this booking.")
                
    with transaction.atomic():
        # Because the Postgres exclusion constraint excludes rows where
        # status IN ('cancelled', 'rejected'), changing the status to 'cancelled'
        # inherently releases the slot without any extra deletion logic.
        booking.status = Booking.StatusChoices.CANCELLED
        booking.cancelled_by = cancelled_by
        booking.cancellation_reason = reason
        booking.cancelled_at = now
        booking.save(update_fields=['status', 'cancelled_by', 'cancellation_reason', 'cancelled_at', 'updated_at'])
        
        # [HOOK: Refund logic goes here in a later task]
        # [HOOK: Google Calendar event deletion goes here in a later task]
        
        # [HOOK: Notifications go here in task 18. Call celery tasks here]
        logger.info(f"Booking {booking.uid} cancelled by {cancelled_by}. Notifications pending.")
        
    return booking

def create_booking(
    *,
    event_type: EventType,
    start_at: datetime,
    invitee_name: str,
    invitee_email: str,
    invitee_timezone: str,
    answers: dict,
    notes: str = "",
    guest_emails: list[str] | None = None,
    now: datetime,
) -> Booking:
    if guest_emails is None:
        guest_emails = []

    if event_type.price_cents > 0:
        raise NotImplementedError("Paid events are not supported yet.")

    # 1. Fast path check
    if not is_slot_available(event_type, start_at, now):
        logger.info(f"Slot {start_at} unavailable during fast path check for event {event_type.id}")
        raise SlotUnavailable("Slot is no longer available.")

    end_at = start_at + timedelta(minutes=event_type.duration_minutes)

    status = Booking.StatusChoices.PENDING if event_type.requires_confirmation else Booking.StatusChoices.CONFIRMED

    booking = Booking(
        event_type=event_type,
        host=event_type.owner,
        start_at=start_at,
        end_at=end_at,
        invitee_timezone=invitee_timezone,
        status=status,
        invitee_name=invitee_name,
        invitee_email=invitee_email,
        invitee_notes=notes,
        answers=answers,
        location_type=event_type.location_type,
        location_value=event_type.location_value,
    )

    # Use a nested atomic block so the caller's transaction is not poisoned
    # if a constraint violation occurs.
    try:
        with transaction.atomic():
            booking.save()
            
            # Create Attendees
            Attendee.objects.create(
                booking=booking,
                name=invitee_name,
                email=invitee_email,
                is_organizer=False
            )
            
            host_name = event_type.owner.get_full_name()
            if not host_name:
                host_name = event_type.owner.email
                
            Attendee.objects.create(
                booking=booking,
                name=host_name,
                email=event_type.owner.email,
                is_organizer=True
            )
            
            for guest_email in guest_emails:
                Attendee.objects.create(
                    booking=booking,
                    name=guest_email,
                    email=guest_email,
                    is_organizer=False
                )
    except (IntegrityError, OperationalError) as e:
        if "no_overlapping_bookings_per_host" in str(e) or "deadlock detected" in str(e):
            logger.info(f"Slot {start_at} unavailable due to constraint or deadlock for host {event_type.owner_id}")
            raise SlotUnavailable("Slot was booked by someone else.")
        raise

    logger.info(f"Booking {booking.uid} created for event {event_type.id} and host {event_type.owner_id}")
    return booking
