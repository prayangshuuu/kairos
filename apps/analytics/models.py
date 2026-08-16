from django.conf import settings
from django.db import models


class BookingFunnelEvent(models.Model):
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="funnel_events"
    )
    event_type = models.ForeignKey(
        "scheduling.EventType",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="funnel_events",
    )
    session_id = models.CharField(max_length=255, db_index=True)
    step = models.CharField(max_length=50, db_index=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    referrer = models.CharField(max_length=500, blank=True)
    utm_source = models.CharField(max_length=255, blank=True)
    utm_medium = models.CharField(max_length=255, blank=True)
    utm_campaign = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=2, blank=True, null=True)
    device_type = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["host", "timestamp"]),
            models.Index(fields=["session_id", "step"]),
        ]

    def __str__(self):
        return f"{self.step} for {self.host.email} at {self.timestamp}"


class DailyMetric(models.Model):
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_metrics"
    )
    event_type = models.ForeignKey(
        "scheduling.EventType",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="daily_metrics",
    )
    date = models.DateField(db_index=True)
    views = models.PositiveIntegerField(default=0)
    bookings_created = models.PositiveIntegerField(default=0)
    bookings_cancelled = models.PositiveIntegerField(default=0)
    bookings_rescheduled = models.PositiveIntegerField(default=0)
    bookings_completed = models.PositiveIntegerField(default=0)
    no_shows = models.PositiveIntegerField(default=0)
    revenue_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, blank=True, null=True)

    profile_viewed_count = models.PositiveIntegerField(default=0)
    booking_page_viewed_count = models.PositiveIntegerField(default=0)
    date_selected_count = models.PositiveIntegerField(default=0)
    slot_selected_count = models.PositiveIntegerField(default=0)
    form_started_count = models.PositiveIntegerField(default=0)
    form_submitted_count = models.PositiveIntegerField(default=0)
    payment_started_count = models.PositiveIntegerField(default=0)
    booking_completed_count = models.PositiveIntegerField(default=0)
    booking_abandoned_count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = (("host", "event_type", "date", "currency"),)
        ordering = ["-date"]

    def __str__(self):
        return f"Metrics for {self.host.email} on {self.date}"
