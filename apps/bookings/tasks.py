import logging
from datetime import timedelta

from celery import chain, shared_task
from django.utils import timezone

from apps.bookings.models import Booking, BookingReference, NotificationLog
from apps.bookings.services import reject_booking

logger = logging.getLogger(__name__)


@shared_task
def process_booking_confirmation(booking_id: int):
    from apps.integrations.tasks import create_calendar_event

    chain(
        create_calendar_event.s(booking_id),
        create_conference_link.s(),
        send_booking_confirmation_emails.s(),
    ).apply_async()


@shared_task(bind=True, max_retries=5)
def create_conference_link(self, booking_id: int):
    try:
        booking = Booking.objects.get(id=booking_id)
    except Booking.DoesNotExist:
        return booking_id

    if BookingReference.objects.filter(booking=booking, kind="video_conference").exists():
        return booking_id

    from apps.integrations.conferencing.providers import PROVIDERS

    provider = PROVIDERS.get(booking.location_type)
    if provider:
        try:
            meeting_details = provider.create_meeting(booking)
            booking.meeting_url = meeting_details.url
            booking.save(update_fields=["meeting_url"])

            BookingReference.objects.create(
                booking=booking,
                external_event_id=meeting_details.id,
                external_calendar_id="",
                kind="video_conference",
                meeting_url=meeting_details.url,
            )
        except NotImplementedError:
            provider = PROVIDERS.get("jitsi")
            meeting_details = provider.create_meeting(booking)
            booking.meeting_url = meeting_details.url
            booking.save(update_fields=["meeting_url"])
            BookingReference.objects.create(
                booking=booking,
                external_event_id=meeting_details.id,
                external_calendar_id="",
                kind="video_conference",
                meeting_url=meeting_details.url,
            )
        except Exception as e:
            if str(e) == "pending":
                logger.info(f"Conference creation pending for booking {booking.uid}, retrying...")
                try:
                    raise self.retry(countdown=5)
                except self.MaxRetriesExceededError:
                    logger.warning(
                        f"Max retries exceeded for pending conference on booking {booking.uid}. Falling back to Jitsi."
                    )

            logger.error(f"Failed to create conference for booking {booking.uid}: {e}")
            # Fallback to Jitsi if creation fails (e.g. no calendar connected for Meet)
            try:
                provider = PROVIDERS.get("jitsi")
                meeting_details = provider.create_meeting(booking)
                booking.meeting_url = meeting_details.url
                booking.save(update_fields=["meeting_url"])
                BookingReference.objects.create(
                    booking=booking,
                    external_event_id=meeting_details.id,
                    external_calendar_id="",
                    kind="video_conference",
                    meeting_url=meeting_details.url,
                )
            except Exception as jitsi_err:
                logger.error(f"Fallback to Jitsi failed: {jitsi_err}")
                booking.location_value = "Conference creation failed. Host will contact you."
                booking.save(update_fields=["location_value"])

    return booking_id


@shared_task
def send_booking_confirmation_emails(booking_id: int):
    try:
        booking = Booking.objects.get(id=booking_id)
    except Booking.DoesNotExist:
        return

    from django.template.defaultfilters import date as date_filter

    from apps.bookings.ics import generate_ics_for_booking
    from apps.core.tasks import send_email_async

    # Generate ICS
    ics_data = generate_ics_for_booking(booking)

    # Pre-render some common context
    host_tz = booking.host.timezone
    invitee_tz = booking.invitee_timezone or "UTC"

    # We use Django's date template filter in Python or we can format manually.
    # It's cleaner to pass the object and timezones to the template and let the template format it.

    context = {
        "booking_uid": str(booking.uid),
        "host_name": booking.host.display_name or booking.host.email,
        "invitee_name": booking.invitee_name,
        "event_title": booking.event_type.title,
        "start_at": booking.start_at.isoformat(),
        "end_at": booking.end_at.isoformat(),
        "location_type": booking.location_type,
        "location_value": booking.location_value,
        "meeting_url": booking.meeting_url,
        "host_tz": host_tz,
        "invitee_tz": invitee_tz,
        "branding_color": booking.event_type.owner.branding_color
        if hasattr(booking.event_type.owner, "branding_color")
        else "#0f172a",
    }

    host_start = booking.start_at.astimezone(timezone.zoneinfo.ZoneInfo(host_tz))
    invitee_start = booking.start_at.astimezone(timezone.zoneinfo.ZoneInfo(invitee_tz))

    # Invitee email
    send_email_async.delay(
        to_email=booking.invitee_email,
        subject=f"Confirmed: {booking.event_type.title} with {context['host_name']} — {date_filter(invitee_start, 'D j M, g:i A')}",
        template_name="booking_confirmed_invitee",
        context=context,
        reply_to=booking.host.email,
        booking_id=booking.id,
        notification_kind="booking_confirmed_invitee",
        ics_data=ics_data,
    )

    # Host email
    send_email_async.delay(
        to_email=booking.host.email,
        subject=f"New Booking: {booking.invitee_name} — {date_filter(host_start, 'D j M, g:i A')}",
        template_name="booking_confirmed_host",
        context=context,
        reply_to=booking.invitee_email,
        booking_id=booking.id,
        notification_kind="booking_confirmed_host",
        ics_data=ics_data,
    )

    if booking.meeting_url:
        logger.info(
            f"Sending confirmation emails for booking {booking.uid} with URL: {booking.meeting_url}"
        )
    else:
        logger.info(f"Queued confirmation emails for booking {booking.uid}")

    return booking_id


@shared_task
def auto_reject_expired_pending_bookings():
    now = timezone.now()

    # 1. Reject pending bookings whose start time has passed
    expired_bookings = Booking.objects.filter(
        status=Booking.StatusChoices.PENDING, start_at__lt=now
    )
    for booking in expired_bookings:
        try:
            reject_booking(
                booking=booking,
                rejected_by=None,
                reason="Auto-rejected because the start time has passed.",
                now=now,
            )
        except Exception as e:
            logger.error(f"Failed to auto-reject past booking {booking.uid}: {e}")

    # 2. Reject pending bookings that exceeded the confirmation_deadline_hours
    # We can't do a simple F-expression filter easily if confirmation_deadline_hours is on the related event_type
    # and we need to add hours to created_at. So we'll iterate or use database-specific expressions.
    # Iterating is fine for this task.
    pending_deadline_bookings = Booking.objects.filter(
        status=Booking.StatusChoices.PENDING, event_type__confirmation_deadline_hours__isnull=False
    ).select_related("event_type")

    for booking in pending_deadline_bookings:
        deadline = booking.created_at + timedelta(
            hours=booking.event_type.confirmation_deadline_hours
        )
        if now > deadline:
            try:
                reject_booking(
                    booking=booking,
                    rejected_by=None,
                    reason="Auto-rejected because the host did not respond in time.",
                    now=now,
                )
            except Exception as e:
                logger.error(f"Failed to auto-reject booking {booking.uid} due to deadline: {e}")


@shared_task
def nudge_host_pending_bookings():
    now = timezone.now()
    twenty_four_hours_ago = now - timedelta(hours=24)

    pending_bookings = Booking.objects.filter(
        status=Booking.StatusChoices.PENDING, created_at__lt=twenty_four_hours_ago
    )

    for booking in pending_bookings:
        # Check if we already sent a nudge
        log_exists = NotificationLog.objects.filter(booking=booking, kind="host_nudge").exists()

        if not log_exists:
            from apps.core.tasks import send_email_async

            host_tz = booking.host.timezone
            booking.start_at.astimezone(timezone.zoneinfo.ZoneInfo(host_tz))

            context = {
                "booking_uid": str(booking.uid),
                "host_name": booking.host.display_name or booking.host.email,
                "invitee_name": booking.invitee_name,
                "event_title": booking.event_type.title,
                "start_at": booking.start_at.isoformat(),
                "end_at": booking.end_at.isoformat(),
                "branding_color": booking.event_type.owner.branding_color
                if hasattr(booking.event_type.owner, "branding_color")
                else "#0f172a",
            }

            send_email_async.delay(
                to_email=booking.host.email,
                subject=f"Action Required: Pending booking from {booking.invitee_name}",
                template_name="booking_pending_host",
                context=context,
                reply_to=booking.invitee_email,
                booking_id=booking.id,
                notification_kind="host_nudge",
            )

            logger.info(f"Nudging host {booking.host.email} about pending booking {booking.uid}")


@shared_task
def send_booking_reminders():
    now = timezone.now()

    # We want to find upcoming confirmed bookings.
    # 24h reminder: between 23h and 25h from now
    # 1h reminder: between 50m and 70m from now

    # 1. 24h reminders
    window_24h_start = now + timedelta(hours=23)
    window_24h_end = now + timedelta(hours=25)

    bookings_24h = Booking.objects.filter(
        status=Booking.StatusChoices.CONFIRMED,
        start_at__gte=window_24h_start,
        start_at__lte=window_24h_end,
    )

    for booking in bookings_24h:
        if not NotificationLog.objects.filter(booking=booking, kind="reminder_24h").exists():
            _queue_reminder(booking, "24h")
            NotificationLog.objects.create(booking=booking, kind="reminder_24h")

    # 2. 1h reminders
    window_1h_start = now + timedelta(minutes=50)
    window_1h_end = now + timedelta(minutes=70)

    bookings_1h = Booking.objects.filter(
        status=Booking.StatusChoices.CONFIRMED,
        start_at__gte=window_1h_start,
        start_at__lte=window_1h_end,
    )

    for booking in bookings_1h:
        if not NotificationLog.objects.filter(booking=booking, kind="reminder_1h").exists():
            _queue_reminder(booking, "1h")
            NotificationLog.objects.create(booking=booking, kind="reminder_1h")


def _queue_reminder(booking, window: str):
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
        "meeting_url": booking.meeting_url,
        "location_type": booking.location_type,
        "location_value": booking.location_value,
        "window": window,
        "branding_color": booking.event_type.owner.branding_color
        if hasattr(booking.event_type.owner, "branding_color")
        else "#0f172a",
    }

    send_email_async.delay(
        to_email=booking.invitee_email,
        subject=f"Reminder: {booking.event_type.title} with {context['host_name']} in {window}",
        template_name="booking_reminder",
        context=context,
        reply_to=booking.host.email,
        booking_id=booking.id,
        notification_kind=f"invitee_reminder_{window}",
    )


@shared_task
def send_waitlist_confirmation(entry_id: int):
    try:
        from apps.bookings.models import WaitlistEntry
        entry = WaitlistEntry.objects.get(id=entry_id)
    except WaitlistEntry.DoesNotExist:
        return

    from apps.core.tasks import send_email_async
    
    context = {
        "invitee_name": entry.invitee_name,
        "event_title": entry.event_type.title,
        "host_name": entry.host.display_name or entry.host.email,
        "claim_token": str(entry.claim_token),
        "branding_color": entry.event_type.owner.branding_color if hasattr(entry.event_type.owner, "branding_color") else "#0f172a",
    }

    send_email_async.delay(
        to_email=entry.invitee_email,
        subject=f"You're on the waitlist for {entry.event_type.title}",
        template_name="waitlist_confirmation",
        context=context,
        reply_to=entry.host.email,
    )


@shared_task
def autofill_waitlist(event_type_id: int, freed_start_at_iso: str, cascade_count: int = 0):
    if cascade_count >= 3:
        return

    from django.utils.dateparse import parse_datetime
    from apps.scheduling.models import EventType
    from apps.bookings.models import WaitlistEntry
    from django.utils import timezone
    from apps.scheduling.engine import is_slot_available
    from apps.core.tasks import send_email_async
    from django.db import transaction
    
    freed_start_at = parse_datetime(freed_start_at_iso)
    if not freed_start_at: return

    try:
        event_type = EventType.objects.get(id=event_type_id)
    except EventType.DoesNotExist: return
    
    now = timezone.now()
    if not is_slot_available(event_type, freed_start_at, now): return
    
    with transaction.atomic():
        entry = WaitlistEntry.objects.filter(
            event_type=event_type,
            status=WaitlistEntry.StatusChoices.WAITING
        ).order_by("created_at").select_for_update(skip_locked=True).first()
        
        if not entry:
            return
            
        claim_window = event_type.waitlist_claim_window_minutes
        
        entry.status = WaitlistEntry.StatusChoices.OFFERED
        entry.offered_at = now
        entry.offer_expires_at = now + timedelta(minutes=claim_window)
        entry.offered_booking_slot = freed_start_at
        
        entry.answers["_cascade_count"] = cascade_count
        entry.save(update_fields=["status", "offered_at", "offer_expires_at", "offered_booking_slot", "answers", "updated_at"])
        
        context = {
            "invitee_name": entry.invitee_name,
            "event_title": event_type.title,
            "host_name": entry.host.display_name or entry.host.email,
            "claim_token": str(entry.claim_token),
            "start_at": freed_start_at.isoformat(),
            "expires_at": entry.offer_expires_at.isoformat(),
            "branding_color": event_type.owner.branding_color if hasattr(event_type.owner, "branding_color") else "#0f172a",
        }

        transaction.on_commit(lambda: send_email_async.delay(
            to_email=entry.invitee_email,
            subject=f"An opening is available for {event_type.title}",
            template_name="waitlist_offer",
            context=context,
            reply_to=entry.host.email,
        ))

@shared_task
def process_expired_waitlist_offers():
    from apps.bookings.models import WaitlistEntry
    from django.utils import timezone
    from django.db import transaction
    
    now = timezone.now()
    expired_entries = WaitlistEntry.objects.filter(
        status=WaitlistEntry.StatusChoices.OFFERED,
        offer_expires_at__lte=now
    )
    
    for entry in expired_entries:
        with transaction.atomic():
            locked_entry = WaitlistEntry.objects.filter(id=entry.id).select_for_update().first()
            if not locked_entry or locked_entry.status != WaitlistEntry.StatusChoices.OFFERED:
                continue
            
            locked_entry.status = WaitlistEntry.StatusChoices.EXPIRED
            locked_entry.save(update_fields=["status", "updated_at"])
            
            cascade_count = locked_entry.answers.get("_cascade_count", 0)
            
            transaction.on_commit(lambda e=locked_entry, c=cascade_count: autofill_waitlist.delay(
                e.event_type_id, 
                e.offered_booking_slot.isoformat(), 
                c + 1
            ))

