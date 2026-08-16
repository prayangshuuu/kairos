import json

from celery import shared_task

from apps.accounts.models import User
from apps.accounts.services import anonymize_user
from apps.bookings.models import Booking
from apps.scheduling.models import EventType, Schedule


@shared_task
def run_account_anonymization(user_id):
    try:
        user = User.objects.get(id=user_id)
        if user.is_active:  # If user reactivated, abort (though we didn't build reactivation yet)
            anonymize_user(user)
    except User.DoesNotExist:
        pass


@shared_task
def export_user_data(user_id):
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return

    data = {
        "profile": {
            "email": user.email,
            "display_name": user.display_name,
            "slug": user.slug,
            "timezone": user.timezone,
            "locale": user.locale,
        },
        "event_types": [],
        "schedules": [],
        "bookings": [],
    }

    for et in EventType.objects.filter(owner=user):
        data["event_types"].append(
            {
                "title": et.title,
                "slug": et.slug,
                "duration": et.duration_minutes,
            }
        )

    for sc in Schedule.objects.filter(user=user):
        data["schedules"].append(
            {
                "name": sc.name,
                "timezone": sc.timezone,
            }
        )

    for b in Booking.objects.filter(host=user):
        data["bookings"].append(
            {
                "uid": str(b.uid),
                "start_at": b.start_at.isoformat(),
                "end_at": b.end_at.isoformat(),
                "status": b.status,
                "invitee_email": b.invitee_email,
            }
        )

    from apps.analytics.models import DailyMetric, BookingFunnelEvent
    
    data["analytics_daily_metrics"] = []
    for m in DailyMetric.objects.filter(host=user):
        data["analytics_daily_metrics"].append({
            "date": m.date.isoformat(),
            "event_type": m.event_type.slug if m.event_type else None,
            "views": m.views,
            "bookings_created": m.bookings_created,
            "bookings_completed": m.bookings_completed,
            "bookings_cancelled": m.bookings_cancelled,
        })
        
    data["analytics_funnel_events"] = []
    for e in BookingFunnelEvent.objects.filter(host=user):
        data["analytics_funnel_events"].append({
            "timestamp": e.timestamp.isoformat(),
            "step": e.step,
            "event_type": e.event_type.slug if e.event_type else None,
            "device_type": e.device_type,
            "country": e.country,
        })

    # Queue email with attachment
    json_data = json.dumps(data, indent=2)

    from apps.core.tasks import send_email_async

    send_email_async.delay(
        to_email=user.email,
        subject="Your Kairos Data Export",
        template_name="account_data_export",
        context={"user_name": user.display_name or user.email},
        is_transactional=True,
        attachments=[("kairos_export.json", json_data, "application/json")],
    )
