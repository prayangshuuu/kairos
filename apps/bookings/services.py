import logging
from datetime import datetime, timedelta
from django.db import transaction, IntegrityError, OperationalError
from apps.bookings.models import Booking, Attendee
from apps.scheduling.models import EventType
from apps.scheduling.engine import is_slot_available

logger = logging.getLogger(__name__)

class SlotUnavailable(Exception):
    pass

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
