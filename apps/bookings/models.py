import datetime
import uuid

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db import models
from django.db.models import Q
from psycopg.types.range import Range

from apps.scheduling.models import EventType
from apps.teams.models import Team

BLOCKING_STATUSES = ("pending", "pending_payment", "confirmed")


class Booking(models.Model):
    class StatusChoices(models.TextChoices):
        PENDING = "pending", "Pending"
        PENDING_PAYMENT = "pending_payment", "Pending Payment"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"
        REJECTED = "rejected", "Rejected"
        NO_SHOW = "no_show", "No Show"

    class SyncStatusChoices(models.TextChoices):
        PENDING = "pending", "Pending"
        SYNCED = "synced", "Synced"
        FAILED = "failed", "Failed"
        NOT_APPLICABLE = "not_applicable", "Not Applicable"

    class CancelledByChoices(models.TextChoices):
        HOST = "host", "Host"
        INVITEE = "invitee", "Invitee"
        SYSTEM = "system", "System"

    class LocationTypeChoices(models.TextChoices):
        GOOGLE_MEET = "google_meet", "Google Meet"
        ZOOM = "zoom", "Zoom"
        MS_TEAMS = "ms_teams", "MS Teams"
        PHONE_HOST_CALLS = "phone_host_calls", "Phone (Host calls invitee)"
        PHONE_INVITEE_CALLS = "phone_invitee_calls", "Phone (Invitee calls host)"
        IN_PERSON = "in_person", "In Person"
        CUSTOM_LINK = "custom_link", "Custom Link"
        ASK_INVITEE = "ask_invitee", "Ask Invitee"

    uid = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    event_type = models.ForeignKey(EventType, on_delete=models.PROTECT, related_name="bookings")

    # 2. People & Identity
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="hosted_bookings"
    )
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True)
    resource_id = models.UUIDField(null=True, blank=True)
    client_id = models.UUIDField(null=True, blank=True)

    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    buffered_period = DateTimeRangeField()
    invitee_timezone = models.CharField(max_length=64)

    status = models.CharField(
        max_length=20, choices=StatusChoices.choices, default=StatusChoices.CONFIRMED
    )

    invitee_name = models.CharField(max_length=150)
    invitee_email = models.EmailField(db_index=True)
    invitee_notes = models.TextField(blank=True)
    answers = models.JSONField(default=dict, blank=True)

    location_type = models.CharField(max_length=30, choices=LocationTypeChoices.choices, blank=True)
    location_value = models.CharField(max_length=500, blank=True)
    meeting_url = models.URLField(blank=True)

    cancellation_reason = models.TextField(blank=True)
    cancelled_by = models.CharField(max_length=20, choices=CancelledByChoices.choices, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    rescheduled_from = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="rescheduled_to"
    )

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    rejected_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sync_status = models.CharField(
        max_length=20, choices=SyncStatusChoices.choices, default=SyncStatusChoices.NOT_APPLICABLE
    )

    class Meta:
        indexes = [
            models.Index(fields=["host", "start_at"]),
            models.Index(fields=["invitee_name"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_at__gt=models.F("start_at")), name="booking_end_time_gt_start_time"
            ),
            # Note: This constraint prevents double-booking for the host.
            # Group events with seats_per_slot > 1 will need different handling later.
            ExclusionConstraint(
                name="no_overlapping_bookings_per_host",
                expressions=[
                    ("host", RangeOperators.EQUAL),
                    ("buffered_period", RangeOperators.OVERLAPS),
                ],
                condition=Q(status__in=BLOCKING_STATUSES),
            ),
        ]

    def save(self, *args, **kwargs):
        # Calculate buffered_period
        if self.start_at and self.end_at and self.event_type_id:
            try:
                et = self.event_type
                buffer_before = datetime.timedelta(minutes=et.buffer_before_minutes)
                buffer_after = datetime.timedelta(minutes=et.buffer_after_minutes)

                # Use half-open range '[)'
                self.buffered_period = Range(
                    self.start_at - buffer_before, self.end_at + buffer_after, bounds="[)"
                )
            except EventType.DoesNotExist:
                # Fallback if somehow event_type is not available in memory and we can't fetch it
                self.buffered_period = Range(self.start_at, self.end_at, bounds="[)")
        elif self.start_at and self.end_at:
            self.buffered_period = Range(self.start_at, self.end_at, bounds="[)")

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invitee_name} with {self.host.email} on {self.start_at.strftime('%Y-%m-%d %H:%M')}"

    @property
    def reschedule_chain(self):
        """Returns the full reschedule chain, walking rescheduled_from backwards. Depth capped at 10 to guard against loops."""
        chain = []
        current = self.rescheduled_from
        depth = 0
        while current and depth < 10:
            chain.append(current)
            current = current.rescheduled_from
            depth += 1
        return chain


class Attendee(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="attendees")
    name = models.CharField(max_length=150)
    email = models.EmailField()
    is_organizer = models.BooleanField(default=False)
    response_status = models.CharField(max_length=50, blank=True)

    def __str__(self):
        return self.name


class NotificationLog(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="notifications", null=True, blank=True)
    waitlist_entry = models.ForeignKey("WaitlistEntry", on_delete=models.CASCADE, related_name="notifications", null=True, blank=True)
    kind = models.CharField(max_length=100)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["booking", "kind"], name="unique_notification_kind_per_booking",
                condition=models.Q(booking__isnull=False)
            ),
            models.UniqueConstraint(
                fields=["waitlist_entry", "kind"], name="unique_notification_kind_per_waitlist_entry",
                condition=models.Q(waitlist_entry__isnull=False)
            )
        ]

    def __str__(self):
        if self.booking:
            return f"{self.kind} for {self.booking_id}"
        return f"{self.kind} for waitlist entry {self.waitlist_entry_id}"


class BookingReference(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="references")
    connection = models.ForeignKey(
        "integrations.CalendarConnection", on_delete=models.SET_NULL, null=True, blank=True
    )
    external_event_id = models.CharField(max_length=255)
    external_calendar_id = models.CharField(max_length=255)
    meeting_url = models.URLField(blank=True)
    kind = models.CharField(
        max_length=50,
        choices=(("calendar_event", "Calendar Event"), ("video_conference", "Video Conference")),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["booking", "kind"], name="unique_booking_reference_kind"
            )
        ]

    def __str__(self):
        return f"{self.kind} for booking {self.booking.uid}"


class WaitlistEntry(models.Model):
    class StatusChoices(models.TextChoices):
        WAITING = "waiting", "Waiting"
        OFFERED = "offered", "Offered"
        CLAIMED = "claimed", "Claimed"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"
        REMOVED = "removed", "Removed"

    event_type = models.ForeignKey(EventType, on_delete=models.CASCADE, related_name="waitlist_entries")
    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="waitlist_entries")

    invitee_name = models.CharField(max_length=150)
    invitee_email = models.EmailField(db_index=True)
    invitee_phone = models.CharField(max_length=50, blank=True, null=True)
    invitee_timezone = models.CharField(max_length=64)
    notes = models.TextField(blank=True)
    answers = models.JSONField(default=dict, blank=True)

    preferred_dates = models.JSONField(default=list, blank=True)
    preferred_time_ranges = models.JSONField(default=list, blank=True)

    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.WAITING)

    offered_at = models.DateTimeField(null=True, blank=True)
    offer_expires_at = models.DateTimeField(null=True, blank=True)
    offered_booking_slot = models.DateTimeField(null=True, blank=True)

    claim_token = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["event_type", "status", "created_at"]),
        ]

    @property
    def position(self):
        if self.status != self.StatusChoices.WAITING:
            return None
        return WaitlistEntry.objects.filter(
            event_type=self.event_type,
            status=self.StatusChoices.WAITING,
            created_at__lt=self.created_at
        ).count() + 1

    def __str__(self):
        return f"{self.invitee_name} - {self.event_type.title} ({self.status})"
