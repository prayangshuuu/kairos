from django.conf import settings
from django.db import models


class BookingFunnelEvent(models.Model):
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="funnel_events", null=True, blank=True
    )
    team = models.ForeignKey(
        "teams.Team", on_delete=models.CASCADE, related_name="funnel_events", null=True, blank=True
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
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(host__isnull=False, team__isnull=True)
                    | models.Q(host__isnull=True, team__isnull=False)
                ),
                name="funnel_event_host_or_team",
            )
        ]
        indexes = [
            models.Index(fields=["host", "timestamp"]),
            models.Index(fields=["team", "timestamp"]),
            models.Index(fields=["session_id", "step"]),
        ]

    def __str__(self):
        owner_name = self.host.email if self.host else self.team.name
        return f"{self.step} for {owner_name} at {self.timestamp}"


class DailyMetric(models.Model):
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_metrics", null=True, blank=True
    )
    team = models.ForeignKey(
        "teams.Team", on_delete=models.CASCADE, related_name="daily_metrics", null=True, blank=True
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
        constraints = [
            models.UniqueConstraint(fields=["host", "event_type", "date", "currency"], condition=models.Q(team__isnull=True), name="unique_daily_metric_host"),
            models.UniqueConstraint(fields=["team", "event_type", "date", "currency"], condition=models.Q(host__isnull=True), name="unique_daily_metric_team"),
            models.CheckConstraint(
                condition=(
                    models.Q(host__isnull=False, team__isnull=True)
                    | models.Q(host__isnull=True, team__isnull=False)
                ),
                name="daily_metric_host_or_team",
            )
        ]
        ordering = ["-date"]

    def __str__(self):
        owner_name = self.host.email if self.host else self.team.name
        return f"Metrics for {owner_name} on {self.date}"
