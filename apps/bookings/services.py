import logging
from datetime import datetime, timedelta
from django.db import transaction, IntegrityError, OperationalError
from apps.bookings.models import Booking, Attendee
from apps.scheduling.models import EventType
from apps.scheduling.engine import is_slot_available
from apps.accounts.models import User

logger = logging.getLogger(__name__)

class SlotUnavailable(Exception):
    pass

class AlreadyCancelled(Exception):
    pass
    
class CancellationNotAllowed(Exception):
    pass

class InvalidTransition(Exception):
    pass

class ReschedulingNotAllowed(Exception):
    pass

def approve_booking(
    *,
    booking: Booking,
    approved_by: User,
    now: datetime,
) -> Booking:
    with transaction.atomic():
        if booking.status != Booking.StatusChoices.PENDING:
            raise InvalidTransition("Only pending bookings can be approved.")
            
        booking.status = Booking.StatusChoices.CONFIRMED
        booking.approved_by = approved_by
        booking.approved_at = now
        booking.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
        
        # [HOOK: Create Google Calendar event]
        from apps.bookings.tasks import process_booking_confirmation
        transaction.on_commit(lambda: process_booking_confirmation.delay(booking.id))
        
        # [HOOK: Send confirmation notifications]
        logger.info(f"Booking {booking.uid} approved by {approved_by.email}")
        
    return booking

def reject_booking(
    *,
    booking: Booking,
    rejected_by: User | None,
    reason: str = "",
    now: datetime,
) -> Booking:
    with transaction.atomic():
        if booking.status != Booking.StatusChoices.PENDING:
            raise InvalidTransition("Only pending bookings can be rejected.")
            
        booking.status = Booking.StatusChoices.REJECTED
        booking.rejected_by = rejected_by
        booking.cancellation_reason = reason
        booking.rejected_at = now
        booking.save(update_fields=['status', 'rejected_by', 'cancellation_reason', 'rejected_at', 'updated_at'])
        
        # [HOOK: Send rejection notifications]
        logger.info(f"Booking {booking.uid} rejected by {rejected_by.email if rejected_by else 'system'}")
        
    return booking

def reschedule_booking(
    *,
    booking: Booking,
    new_start_at: datetime,
    rescheduled_by: str,
    reason: str = "",
    now: datetime,
) -> Booking:
    if booking.status in [Booking.StatusChoices.CANCELLED, Booking.StatusChoices.REJECTED]:
        raise AlreadyCancelled("Booking is already cancelled or rejected.")
        
    if rescheduled_by == "invitee":
        if not booking.event_type.allow_rescheduling:
            raise ReschedulingNotAllowed("This event type does not allow rescheduling by invitees.")
        
        if booking.event_type.reschedule_cutoff_hours is not None:
            cutoff = booking.start_at - timedelta(hours=booking.event_type.reschedule_cutoff_hours)
            if now > cutoff:
                raise ReschedulingNotAllowed("It is too late to reschedule this booking.")
                
    with transaction.atomic():
        # Validate the new slot, EXCLUDING the current booking
        if not is_slot_available(
            event_type=booking.event_type,
            start_at=new_start_at,
            now=now,
            exclude_booking_id=booking.id
        ):
            raise SlotUnavailable("The selected slot is no longer available.")
            
        original_status = booking.status
            
        # Cancel old booking BEFORE inserting new one
        cancel_booking(
            booking=booking, 
            cancelled_by="system", 
            reason=f"Rescheduled by {rescheduled_by}" + (f": {reason}" if reason else ""), 
            now=now
        )
        
        # Create new booking
        end_at = new_start_at + timedelta(minutes=booking.event_type.duration_minutes)
        new_booking = Booking(
            event_type=booking.event_type,
            host=booking.host,
            team=booking.team,
            start_at=new_start_at,
            end_at=end_at,
            invitee_timezone=booking.invitee_timezone,
            status=original_status,
            invitee_name=booking.invitee_name,
            invitee_email=booking.invitee_email,
            invitee_notes=booking.invitee_notes,
            answers=booking.answers,
            location_type=booking.location_type,
            location_value=booking.location_value,
            rescheduled_from=booking
        )
        
        try:
            new_booking.save()
        except IntegrityError:
            # If the exclusion constraint still catches us (e.g. race condition),
            # this will bubble up. Actually, we should catch it to raise SlotUnavailable
            raise SlotUnavailable("The selected slot is no longer available.")
            
        # Copy Attendees
        from apps.bookings.models import Attendee
        for attendee in booking.attendees.all():
            Attendee.objects.create(
                booking=new_booking,
                name=attendee.name,
                email=attendee.email,
                is_organizer=attendee.is_organizer,
                response_status=attendee.response_status
            )
            
        # [HOOK: Notifications for both parties]
        logger.info(f"Booking {booking.uid} rescheduled to {new_booking.uid} by {rescheduled_by}")
        
    return new_booking

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
        from apps.integrations.tasks import delete_calendar_event
        from apps.bookings.models import BookingReference
        
        # Capture the booking ID and look up references when transaction commits
        def _trigger_deletions(b_id):
            for ref in BookingReference.objects.filter(booking_id=b_id):
                delete_calendar_event.delay(ref.id)
                
        transaction.on_commit(lambda: _trigger_deletions(booking.id))
        
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
    from apps.integrations.services import fetch_external_busy, check_live_conflict
    external_busy = fetch_external_busy(event_type.owner, now, now + timedelta(days=365)) # Approximate, but we just need it for the specific slot check
    
    if not is_slot_available(event_type, start_at, now, external_busy=external_busy):
        logger.info(f"Slot {start_at} unavailable during fast path check for event {event_type.id}")
        raise SlotUnavailable("Slot is no longer available.")

    end_at = start_at + timedelta(minutes=event_type.duration_minutes)

    # Live check before writing
    if check_live_conflict(event_type.owner, start_at, end_at):
        logger.info(f"Slot {start_at} unavailable due to live conflict for host {event_type.owner_id}")
        raise SlotUnavailable("Slot was just booked on the host's external calendar.")


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
                
            if status == Booking.StatusChoices.CONFIRMED:
                from apps.bookings.tasks import process_booking_confirmation
                transaction.on_commit(lambda: process_booking_confirmation.delay(booking.id))
    except (IntegrityError, OperationalError) as e:
        if "no_overlapping_bookings_per_host" in str(e) or "deadlock detected" in str(e):
            logger.info(f"Slot {start_at} unavailable due to constraint or deadlock for host {event_type.owner_id}")
            raise SlotUnavailable("Slot was booked by someone else.")
        raise

    logger.info(f"Booking {booking.uid} created for event {event_type.id} and host {event_type.owner_id}")
    return booking

def mark_booking_no_show(*, booking: Booking, marked_by: User, now: datetime) -> Booking:
    with transaction.atomic():
        if booking.status != Booking.StatusChoices.CONFIRMED:
            raise InvalidTransition("Only confirmed bookings can be marked as no-show.")
        if booking.start_at >= now:
            raise InvalidTransition("Only past bookings can be marked as no-show.")
            
        booking.status = Booking.StatusChoices.NO_SHOW
        booking.save(update_fields=['status', 'updated_at'])
        
        logger.info(f"Booking {booking.uid} marked as no-show by {marked_by.email}")
        
    return booking
