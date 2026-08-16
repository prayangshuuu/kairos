import logging
from datetime import timedelta
from django.utils import timezone
from celery import shared_task
from django.db.models import Sum, Count, Q
from django.db.models.functions import Coalesce

from .models import BookingFunnelEvent, DailyMetric
from apps.bookings.models import Booking

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def record_funnel_event(
    host_id,
    session_id,
    step,
    event_type_id=None,
    referrer="",
    utm_source="",
    utm_medium="",
    utm_campaign="",
    country=None,
    device_type="",
):
    BookingFunnelEvent.objects.create(
        host_id=host_id,
        session_id=session_id,
        step=step,
        event_type_id=event_type_id,
        referrer=referrer[:500],
        utm_source=utm_source[:255],
        utm_medium=utm_medium[:255],
        utm_campaign=utm_campaign[:255],
        country=country,
        device_type=device_type[:50],
    )


@shared_task(ignore_result=True)
def cleanup_old_events():
    # Delete funnel events older than 12 months
    cutoff = timezone.now() - timedelta(days=365)
    deleted, _ = BookingFunnelEvent.objects.filter(timestamp__lt=cutoff).delete()
    logger.info(f"Cleaned up {deleted} old funnel events")


@shared_task(ignore_result=True)
def rollup_daily_metrics(target_date_str=None):
    from datetime import date
    
    if target_date_str:
        target_date = date.fromisoformat(target_date_str)
    else:
        # Default to yesterday if running nightly
        target_date = (timezone.now() - timedelta(days=1)).date()

    logger.info(f"Running daily metric rollup for {target_date}")
    
    # 1. Gather all unique (host, event_type) combinations that had activity on target_date
    # We need to look at both FunnelEvents and Bookings
    
    # Get all hosts and event types from funnel events on the date
    funnel_combos = BookingFunnelEvent.objects.filter(
        timestamp__date=target_date
    ).values("host_id", "event_type_id").distinct()
    
    # Get all hosts and event types from bookings created/updated on the date
    # (Since we track created, cancelled, rescheduled, completed, no_shows)
    booking_combos = Booking.objects.filter(
        Q(created_at__date=target_date) | 
        Q(updated_at__date=target_date) |
        Q(start_at__date=target_date) # For completed/no-show
    ).values("host_id", "event_type_id", "event_type__currency").distinct()

    # Create a set of (host_id, event_type_id, currency) to process
    combos_to_process = set()
    for combo in funnel_combos:
        combos_to_process.add((combo["host_id"], combo["event_type_id"], None))
    for combo in booking_combos:
        combos_to_process.add((combo["host_id"], combo["event_type_id"], combo["event_type__currency"]))
        
    for host_id, event_type_id, currency in combos_to_process:
        # Compute funnel metrics
        funnel_qs = BookingFunnelEvent.objects.filter(
            host_id=host_id,
            event_type_id=event_type_id,
            timestamp__date=target_date
        )
        
        funnel_counts = funnel_qs.values("step").annotate(count=Count("id"))
        counts_dict = {item["step"]: item["count"] for item in funnel_counts}
        
        # Compute booking metrics
        # For this we need to be careful about currencies
        booking_base_qs = Booking.objects.filter(
            host_id=host_id,
            event_type_id=event_type_id
        )
        if currency:
            booking_base_qs = booking_base_qs.filter(event_type__currency=currency)
            
        created_count = booking_base_qs.filter(created_at__date=target_date).count()
        cancelled_count = booking_base_qs.filter(updated_at__date=target_date, status=Booking.StatusChoices.CANCELLED).count()
        rescheduled_count = booking_base_qs.filter(created_at__date=target_date, rescheduled_from__isnull=False).count()
        
        # Completed: started on target_date and status is confirmed (not cancelled/rejected etc)
        # Note: We consider it completed if the end_at is passed, but for daily metrics, counting based on start_at date is typical.
        completed_count = booking_base_qs.filter(
            start_at__date=target_date,
            status__in=[Booking.StatusChoices.CONFIRMED]
        ).count()
        
        no_shows_count = booking_base_qs.filter(start_at__date=target_date, status=Booking.StatusChoices.NO_SHOW).count()
        
        # Revenue: sum of price_cents for bookings created on this date that are not cancelled?
        # Actually revenue is usually tied to creation date or payment date. Let's use created_at.
        revenue = booking_base_qs.filter(
            created_at__date=target_date,
            status__in=[Booking.StatusChoices.CONFIRMED, Booking.StatusChoices.PENDING_PAYMENT]
        ).aggregate(total=Coalesce(Sum("event_type__price_cents"), 0))["total"]
        
        # Upsert DailyMetric
        DailyMetric.objects.update_or_create(
            host_id=host_id,
            event_type_id=event_type_id,
            date=target_date,
            currency=currency,
            defaults={
                "views": counts_dict.get("booking_page_viewed", 0) + counts_dict.get("profile_viewed", 0),
                "bookings_created": created_count,
                "bookings_cancelled": cancelled_count,
                "bookings_rescheduled": rescheduled_count,
                "bookings_completed": completed_count,
                "no_shows": no_shows_count,
                "revenue_cents": revenue,
                "profile_viewed_count": counts_dict.get("profile_viewed", 0),
                "booking_page_viewed_count": counts_dict.get("booking_page_viewed", 0),
                "date_selected_count": counts_dict.get("date_selected", 0),
                "slot_selected_count": counts_dict.get("slot_selected", 0),
                "form_started_count": counts_dict.get("form_started", 0),
                "form_submitted_count": counts_dict.get("form_submitted", 0),
                "payment_started_count": counts_dict.get("payment_started", 0),
                "booking_completed_count": counts_dict.get("booking_completed", 0),
                "booking_abandoned_count": counts_dict.get("booking_abandoned", 0),
            }
        )

@shared_task(ignore_result=True)
def send_monthly_analytics_summary():
    from apps.accounts.models import User
    from apps.core.tasks import send_email_async
    from django.conf import settings
    from datetime import date
    
    # Get first day of current month, then subtract 1 day to get last day of previous month
    today = timezone.now().date()
    first_day_current = today.replace(day=1)
    last_day_prev = first_day_current - timedelta(days=1)
    first_day_prev = last_day_prev.replace(day=1)
    
    # Month before previous month
    last_day_prev_prev = first_day_prev - timedelta(days=1)
    first_day_prev_prev = last_day_prev_prev.replace(day=1)

    hosts = User.objects.filter(is_active=True)
    
    for host in hosts:
        # Get metrics for previous month
        prev_qs = DailyMetric.objects.filter(
            host=host, 
            date__gte=first_day_prev, 
            date__lte=last_day_prev
        )
        prev_totals = prev_qs.aggregate(
            bookings_created=Coalesce(Sum('bookings_created'), 0),
            bookings_completed=Coalesce(Sum('bookings_completed'), 0),
            bookings_cancelled=Coalesce(Sum('bookings_cancelled'), 0),
            no_shows=Coalesce(Sum('no_shows'), 0),
            revenue_cents=Coalesce(Sum('revenue_cents'), 0),
            booking_page_viewed_count=Coalesce(Sum('booking_page_viewed_count'), 0),
            date_selected_count=Coalesce(Sum('date_selected_count'), 0),
            form_started_count=Coalesce(Sum('form_started_count'), 0),
        )
        
        if prev_totals['bookings_created'] == 0 and prev_totals['booking_page_viewed_count'] == 0:
            continue # Skip dormant hosts who didn't even get views
            
        # Get metrics for month before that (for PoP comparison)
        prev_prev_qs = DailyMetric.objects.filter(
            host=host, 
            date__gte=first_day_prev_prev, 
            date__lte=last_day_prev_prev
        )
        prev_prev_totals = prev_prev_qs.aggregate(
            bookings_created=Coalesce(Sum('bookings_created'), 0)
        )
        
        # Calculate percentages
        tb = prev_totals['bookings_created']
        completion_rate = round((prev_totals['bookings_completed'] / tb) * 100) if tb else 0
        cancellation_rate = round((prev_totals['bookings_cancelled'] / tb) * 100) if tb else 0
        no_show_rate = round((prev_totals['no_shows'] / tb) * 100) if tb else 0
        
        pop_bookings = 0
        tb_prev = prev_prev_totals['bookings_created']
        if tb_prev > 0:
            pop_bookings = round(((tb - tb_prev) / tb_prev) * 100)
        elif tb > 0:
            pop_bookings = 100

        context = {
            "host": {
                "first_name": host.first_name,
                "email": host.email
            },
            "current_month_name": last_day_prev.strftime("%B %Y"),
            "totals": prev_totals,
            "completion_rate": completion_rate,
            "cancellation_rate": cancellation_rate,
            "no_show_rate": no_show_rate,
            "pop_bookings": pop_bookings,
            "dashboard_url": f"{settings.BASE_URL}/dashboard/insights/",
        }
        
        send_email_async.delay(
            to_email=host.email,
            subject=f"Your {last_day_prev.strftime('%B')} Analytics Summary",
            template_name="analytics_summary",
            context=context,
            notification_kind="analytics_summary"
        )

