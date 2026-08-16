import logging
from datetime import datetime, timedelta

from django.db import IntegrityError, OperationalError, transaction

from apps.accounts.models import User
from apps.bookings.models import Attendee, Booking, BLOCKING_STATUSES
from apps.scheduling.engine import is_slot_available
from apps.scheduling.models import EventType

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
        booking.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])

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
        booking.save(
            update_fields=[
                "status",
                "rejected_by",
                "cancellation_reason",
                "rejected_at",
                "updated_at",
            ]
        )

        # Send rejection notification
        from apps.core.tasks import send_email_async

        host_tz = booking.host.timezone
        invitee_tz = booking.invitee_timezone or "UTC"
        context = {
            "booking_uid": str(booking.uid),
            "host_name": booking.host.display_name or booking.host.email,
            "invitee_name": booking.invitee_name,
            "event_title": booking.event_type.title,
            "start_at": booking.start_at.isoformat(),
            "host_tz": host_tz,
            "invitee_tz": invitee_tz,
            "reason": reason,
            "branding_color": booking.event_type.owner.branding_color
            if hasattr(booking.event_type.owner, "branding_color")
            else "#0f172a",
        }
        transaction.on_commit(
            lambda: send_email_async.delay(
                to_email=booking.invitee_email,
                subject=f"Update: {booking.event_type.title} with {context['host_name']} was declined",
                template_name="booking_rejected",
                context=context,
                reply_to=booking.host.email,
                booking_id=booking.id,
                notification_kind="booking_rejected",
            )
        )
        
        from apps.bookings.tasks import autofill_waitlist
        transaction.on_commit(lambda: autofill_waitlist.delay(booking.event_type_id, booking.start_at.isoformat()))


        logger.info(
            f"Booking {booking.uid} rejected by {rejected_by.email if rejected_by else 'system'}"
        )

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
            exclude_booking_id=booking.id,
        ):
            raise SlotUnavailable("The selected slot is no longer available.")

        original_status = booking.status

        # Cancel old booking BEFORE inserting new one
        cancel_booking(
            booking=booking,
            cancelled_by="system",
            reason=f"Rescheduled by {rescheduled_by}" + (f": {reason}" if reason else ""),
            now=now,
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
            rescheduled_from=booking,
        )

        try:
            new_booking.save()
        except IntegrityError:
            # If the exclusion constraint still catches us (e.g. race condition),
            # this will bubble up. Actually, we should catch it to raise SlotUnavailable
            raise SlotUnavailable("The selected slot is no longer available.")

        # Copy Attendees
        from apps.bookings.models import Attendee
        from apps.workflows.services import schedule_workflow_executions_for_booking

        schedule_workflow_executions_for_booking(new_booking, now=now)
        schedule_workflow_executions_for_booking(new_booking, trigger="on_booking_rescheduled", now=now)

        for attendee in booking.attendees.all():
            Attendee.objects.create(
                booking=new_booking,
                name=attendee.name,
                email=attendee.email,
                is_organizer=attendee.is_organizer,
                response_status=attendee.response_status,
            )

        # Queue Reschedule emails
        from apps.bookings.ics import generate_ics_for_booking
        from apps.core.tasks import send_email_async

        ics_res = generate_ics_for_booking(new_booking)
        ics_data = ics_res.decode("utf-8") if isinstance(ics_res, bytes) else ics_res
        host_tz = new_booking.host.timezone
        invitee_tz = new_booking.invitee_timezone or "UTC"
        context = {
            "booking_uid": str(new_booking.uid),
            "host_name": new_booking.host.display_name or new_booking.host.email,
            "invitee_name": new_booking.invitee_name,
            "event_title": new_booking.event_type.title,
            "old_start_at": booking.start_at.isoformat(),
            "new_start_at": new_booking.start_at.isoformat(),
            "host_tz": host_tz,
            "invitee_tz": invitee_tz,
            "reason": reason,
            "rescheduled_by": rescheduled_by,
            "branding_color": new_booking.event_type.owner.branding_color
            if hasattr(new_booking.event_type.owner, "branding_color")
            else "#0f172a",
        }

        def _trigger_reschedule_notifications(b_id, ics):
            send_email_async.delay(
                to_email=new_booking.host.email,
                subject=f"Rescheduled: {new_booking.event_type.title} with {new_booking.invitee_name}",
                template_name="booking_rescheduled_host",
                context=context,
                reply_to=new_booking.invitee_email,
                booking_id=b_id,
                notification_kind="booking_rescheduled_host",
                ics_data=ics,
            )
            send_email_async.delay(
                to_email=new_booking.invitee_email,
                subject=f"Rescheduled: {new_booking.event_type.title} with {context['host_name']}",
                template_name="booking_rescheduled_invitee",
                context=context,
                reply_to=new_booking.host.email,
                booking_id=b_id,
                notification_kind="booking_rescheduled_invitee",
                ics_data=ics,
            )

        transaction.on_commit(lambda: _trigger_reschedule_notifications(new_booking.id, ics_data))

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
            cutoff = booking.start_at - timedelta(
                hours=booking.event_type.cancellation_cutoff_hours
            )
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
        booking.save(
            update_fields=[
                "status",
                "cancelled_by",
                "cancellation_reason",
                "cancelled_at",
                "updated_at",
            ]
        )

        # Cancel pending workflow executions
        from apps.workflows.services import (
            cancel_workflow_executions_for_booking,
            schedule_workflow_executions_for_booking,
        )
        cancel_workflow_executions_for_booking(booking)
        schedule_workflow_executions_for_booking(booking, trigger="on_booking_cancelled", now=now)

        # [HOOK: Refund logic goes here in a later task]
        # [HOOK: Google Calendar event deletion goes here in a later task]
        from apps.bookings.models import BookingReference
        from apps.core.tasks import send_email_async
        from apps.integrations.tasks import delete_calendar_event

        host_tz = booking.host.timezone
        invitee_tz = booking.invitee_timezone or "UTC"
        context = {
            "booking_uid": str(booking.uid),
            "host_name": booking.host.display_name or booking.host.email,
            "invitee_name": booking.invitee_name,
            "event_title": booking.event_type.title,
            "start_at": booking.start_at.isoformat(),
            "host_tz": host_tz,
            "invitee_tz": invitee_tz,
            "reason": reason,
            "cancelled_by": cancelled_by,
            "branding_color": booking.event_type.owner.branding_color
            if hasattr(booking.event_type.owner, "branding_color")
            else "#0f172a",
        }

        def _trigger_cancellations(b_id):
            for ref in BookingReference.objects.filter(booking_id=b_id):
                delete_calendar_event.delay(ref.id)

            # Send to host
            send_email_async.delay(
                to_email=booking.host.email,
                subject=f"Cancelled: {booking.event_type.title} with {booking.invitee_name}",
                template_name="booking_cancelled_host",
                context=context,
                reply_to=booking.invitee_email,
                booking_id=b_id,
                notification_kind="booking_cancelled_host",
            )
            # Send to invitee
            send_email_async.delay(
                to_email=booking.invitee_email,
                subject=f"Cancelled: {booking.event_type.title} with {context['host_name']}",
                template_name="booking_cancelled_invitee",
                context=context,
                reply_to=booking.host.email,
                booking_id=b_id,
                notification_kind="booking_cancelled_invitee",
            )

        transaction.on_commit(lambda: _trigger_cancellations(booking.id))
        
        from apps.bookings.tasks import autofill_waitlist
        transaction.on_commit(lambda: autofill_waitlist.delay(booking.event_type_id, booking.start_at.isoformat()))


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
        pass  # Allow paid events

    # 1. Determine participating hosts
    strategy = event_type.assignment_strategy
    if strategy == "single":
        participating_hosts = [event_type.owner]
        hosts_qs = None
    else:
        hosts_qs = event_type.hosts.filter(is_active=True).select_related("user")
        if strategy == "collective":
            hosts_qs = hosts_qs.filter(is_required=True)
            
        participating_hosts = [h.user for h in hosts_qs]
        if not participating_hosts:
            participating_hosts = [event_type.owner]

    # 2. Fast path check
    from apps.integrations.services import check_live_conflict_for_users, fetch_external_busy_for_users

    external_busy_dict = fetch_external_busy_for_users(
        participating_hosts, now, now + timedelta(days=365)
    )

    if not is_slot_available(event_type, start_at, now, external_busy=external_busy_dict):
        logger.info(f"Slot {start_at} unavailable during fast path check for event {event_type.id}")
        raise SlotUnavailable("Slot is no longer available.")

    end_at = start_at + timedelta(minutes=event_type.duration_minutes)

    # 3. Live check before writing
    conflicting_users = check_live_conflict_for_users(participating_hosts, start_at, end_at)
    
    # Also check internal conflicts for the exact slot
    b_before = event_type.buffer_before_minutes
    b_after = event_type.buffer_after_minutes
    if strategy != "single":
        b_before = max(b_before, max((h.buffer_before_minutes for h in hosts_qs), default=0))
        b_after = max(b_after, max((h.buffer_after_minutes for h in hosts_qs), default=0))
    
    req_start = start_at - timedelta(minutes=b_before)
    req_end = end_at + timedelta(minutes=b_after)
    
    internal_conflicts = Booking.objects.filter(
        host__in=participating_hosts,
        status__in=BLOCKING_STATUSES,
        buffered_period__overlap=(req_start, req_end),
    ).values_list("host_id", flat=True)
    
    internally_conflicting_ids = set(internal_conflicts)
    available_hosts = [u for u in participating_hosts if u not in conflicting_users and u.id not in internally_conflicting_ids]

    if strategy == "collective" or strategy == "single":
        if conflicting_users or internally_conflicting_ids:
            logger.info(
                f"Slot {start_at} unavailable due to live conflict for hosts {conflicting_users} or {internally_conflicting_ids}"
            )
            raise SlotUnavailable("Slot was just booked on a host's calendar.")
        assigned_host = event_type.owner
    else:
        # Round Robin assignment
        if not available_hosts:
            logger.info(f"Slot {start_at} unavailable due to live conflict for all round-robin hosts.")
            raise SlotUnavailable("Slot was just booked on the hosts' calendars.")
            
        # We need to lock the EventTypeHosts to assign fairly
        # Fetch the EventTypeHosts for the available users
        available_user_ids = [u.id for u in available_hosts]
        
        # Determine who is outside working hours to implement "fair timezone rotation"
        # We will increment outside_working_hours_count if they are booked outside 9-5 local time.
        # But we need to pick the host.
        # Sort by: priority (lower is better), assignment_count (lower is better), last_assigned_at (older is better)
        eths = list(event_type.hosts.filter(
            user_id__in=available_user_ids
        ).select_for_update().order_by(
            "priority",
            "assignment_count",
            "last_assigned_at",
        ))
        
        if not eths:
            assigned_host = available_hosts[0]
        else:
            selected_eth = eths[0]
            assigned_host = selected_eth.user
            
            # Check if this slot is outside their working hours (assuming 9 to 5 local time)
            local_start = start_at.astimezone(assigned_host.zoneinfo)
            if local_start.hour < 9 or local_start.hour >= 17:
                selected_eth.outside_working_hours_count += 1
                
            selected_eth.assignment_count += 1
            selected_eth.last_assigned_at = now
            selected_eth.save(update_fields=["assignment_count", "last_assigned_at", "outside_working_hours_count"])

    if event_type.price_cents > 0:
        status = Booking.StatusChoices.PENDING_PAYMENT
    else:
        status = (
            Booking.StatusChoices.PENDING
            if event_type.requires_confirmation
            else Booking.StatusChoices.CONFIRMED
        )

    booking = Booking(
        event_type=event_type,
        host=assigned_host,
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

    try:
        with transaction.atomic():
            from apps.clients.models import Client
            client, created = Client.objects.get_or_create(
                host=event_type.owner,
                email=invitee_email.lower().strip(),
                defaults={
                    'name': invitee_name,
                    'first_seen_at': now,
                    'last_seen_at': start_at,
                    'timezone': invitee_timezone,
                    'source': 'booking_flow',
                }
            )
            if not created:
                update_fields = []
                if invitee_name and invitee_name != client.name:
                    if invitee_name not in client.known_names:
                        client.known_names.append(invitee_name)
                        update_fields.append('known_names')
                if start_at > client.last_seen_at:
                    client.last_seen_at = start_at
                    update_fields.append('last_seen_at')
                if update_fields:
                    client.save(update_fields=update_fields)
                    
            booking.client = client
            booking.save()

            # Create Attendees
            Attendee.objects.create(
                booking=booking, name=invitee_name, email=invitee_email, is_organizer=False
            )

            host_name = event_type.owner.get_full_name()
            if not host_name:
                host_name = event_type.owner.email

            Attendee.objects.create(
                booking=booking, name=host_name, email=event_type.owner.email, is_organizer=True
            )

            for guest_email in guest_emails:
                Attendee.objects.create(
                    booking=booking, name=guest_email, email=guest_email, is_organizer=False
                )

            # Schedule Workflow Executions
            from apps.workflows.services import schedule_workflow_executions_for_booking
            schedule_workflow_executions_for_booking(booking, now=now)

            if status == Booking.StatusChoices.CONFIRMED:
                from apps.bookings.tasks import process_booking_confirmation

                transaction.on_commit(lambda: process_booking_confirmation.delay(booking.id))
            else:
                from apps.core.tasks import send_email_async

                host_tz = event_type.owner.timezone
                invitee_tz = invitee_timezone or "UTC"

                context = {
                    "booking_uid": str(booking.uid),
                    "host_name": event_type.owner.display_name or event_type.owner.email,
                    "invitee_name": invitee_name,
                    "event_title": event_type.title,
                    "start_at": start_at.isoformat(),
                    "host_tz": host_tz,
                    "invitee_tz": invitee_tz,
                    "branding_color": event_type.owner.branding_color
                    if hasattr(event_type.owner, "branding_color")
                    else "#0f172a",
                }

                def _send_pending_emails(b_id):
                    send_email_async.delay(
                        to_email=invitee_email,
                        subject=f"Request sent: {event_type.title} with {context['host_name']}",
                        template_name="booking_pending_invitee",
                        context=context,
                        reply_to=event_type.owner.email,
                        booking_id=b_id,
                        notification_kind="booking_pending_invitee",
                    )
                    send_email_async.delay(
                        to_email=event_type.owner.email,
                        subject=f"Action Required: Pending booking from {invitee_name}",
                        template_name="booking_pending_host",
                        context=context,
                        reply_to=invitee_email,
                        booking_id=b_id,
                        notification_kind="booking_pending_host",
                    )

                transaction.on_commit(lambda: _send_pending_emails(booking.id))
    except (IntegrityError, OperationalError) as e:
        if "no_overlapping_bookings_per_host" in str(e) or "deadlock detected" in str(e):
            logger.info(
                f"Slot {start_at} unavailable due to constraint or deadlock for host {event_type.owner_id}"
            )
            raise SlotUnavailable("Slot was booked by someone else.")
        raise

    logger.info(
        f"Booking {booking.uid} created for event {event_type.id} and host {event_type.owner_id}"
    )
    return booking


def mark_booking_no_show(*, booking: Booking, marked_by: User, now: datetime) -> Booking:
    with transaction.atomic():
        if booking.status != Booking.StatusChoices.CONFIRMED:
            raise InvalidTransition("Only confirmed bookings can be marked as no-show.")
        if booking.start_at >= now:
            raise InvalidTransition("Only past bookings can be marked as no-show.")

        booking.status = Booking.StatusChoices.NO_SHOW
        booking.save(update_fields=["status", "updated_at"])

        logger.info(f"Booking {booking.uid} marked as no-show by {marked_by.email}")

    return booking
