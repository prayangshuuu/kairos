import pytest
from datetime import timedelta
from django.utils import timezone
from apps.analytics.tasks import rollup_daily_metrics
from apps.analytics.models import DailyMetric, BookingFunnelEvent
from apps.bookings.models import Booking

@pytest.mark.django_db
def test_rollup_daily_metrics(host_with_schedule, event_type):
    now = timezone.now()
    today = now.date()
    yesterday = today - timedelta(days=1)
    
    # Use the host from event_type, or host_with_schedule
    # Assuming event_type fixture is tied to some host, let's just use event_type.owner
    host = event_type.owner
    
    # Create some funnel events for yesterday
    BookingFunnelEvent.objects.create(
        host=host, session_id="123", step="profile_viewed", event_type=None
    )
    BookingFunnelEvent.objects.create(
        host=host, session_id="456", step="booking_page_viewed", event_type=event_type
    )
    BookingFunnelEvent.objects.create(
        host=host, session_id="456", step="date_selected", event_type=event_type
    )
    # Update timestamps to yesterday
    BookingFunnelEvent.objects.update(timestamp=now - timedelta(days=1))
    
    # Create some bookings for yesterday
    Booking.objects.create(
        host=host,
        event_type=event_type,
        start_at=now - timedelta(days=1),
        end_at=now - timedelta(days=1, hours=-1),
        invitee_name="Test Invitee",
        invitee_email="test@example.com",
        status=Booking.StatusChoices.CONFIRMED,
        uid="11111111-1111-1111-1111-111111111111"
    )
    
    # Note: created_at and updated_at might auto-set to now() when using .create(),
    # so we update them to yesterday explicitly.
    Booking.objects.update(created_at=now - timedelta(days=1), updated_at=now - timedelta(days=1))
    
    # Run rollup for yesterday
    rollup_daily_metrics(target_date_str=yesterday.isoformat())
    
    # Check DailyMetric for host, event_type
    metric = DailyMetric.objects.filter(host=host, event_type=event_type, date=yesterday).first()
    assert metric is not None
    assert metric.bookings_created == 1
    assert metric.bookings_completed == 1
    assert metric.booking_page_viewed_count == 1
    assert metric.date_selected_count == 1
    assert metric.views == 1
    
    # Check DailyMetric for host, None (profile view)
    metric_profile = DailyMetric.objects.filter(host=host, event_type=None, date=yesterday).first()
    assert metric_profile is not None
    assert metric_profile.profile_viewed_count == 1
    assert metric_profile.views == 1
    assert metric_profile.bookings_created == 0
