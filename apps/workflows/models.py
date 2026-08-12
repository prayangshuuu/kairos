from django.conf import settings
from django.db import models

from apps.bookings.models import Booking
from apps.scheduling.models import EventType


class Workflow(models.Model):
    TRIGGER_BEFORE_EVENT = "before_event"
    TRIGGER_AFTER_EVENT = "after_event"
    TRIGGER_ON_BOOKING_CREATED = "on_booking_created"
    TRIGGER_ON_BOOKING_CANCELLED = "on_booking_cancelled"
    TRIGGER_ON_BOOKING_RESCHEDULED = "on_booking_rescheduled"
    TRIGGER_ON_NO_SHOW = "on_no_show"

    TRIGGER_CHOICES = [
        (TRIGGER_BEFORE_EVENT, "Before Event"),
        (TRIGGER_AFTER_EVENT, "After Event"),
        (TRIGGER_ON_BOOKING_CREATED, "On Booking Created"),
        (TRIGGER_ON_BOOKING_CANCELLED, "On Booking Cancelled"),
        (TRIGGER_ON_BOOKING_RESCHEDULED, "On Booking Rescheduled"),
        (TRIGGER_ON_NO_SHOW, "On Mark No-Show"),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workflows"
    )
    name = models.CharField(max_length=150)
    event_types = models.ManyToManyField(
        EventType, blank=True, related_name="workflows", help_text="Empty = applies to all event types"
    )
    trigger = models.CharField(max_length=50, choices=TRIGGER_CHOICES, default=TRIGGER_BEFORE_EVENT)
    offset_minutes = models.IntegerField(
        default=0, help_text="Negative = before event, positive = after event"
    )
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError
        from apps.subscriptions.entitlements import has_feature

        # Gating: Custom workflows beyond the 3 defaults require Pro
        if self.pk is None and not self.is_default and self.owner_id:
            custom_count = Workflow.objects.filter(owner=self.owner, is_default=False).count()
            total_count = Workflow.objects.filter(owner=self.owner).count()
            if total_count >= 3 and not has_feature(self.owner, "workflows_reminders"):
                raise ValidationError(
                    "Custom workflows beyond the 3 default reminders require a Pro subscription."
                )

    def __str__(self):
        return f"{self.name} ({self.owner.email})"


class WorkflowStep(models.Model):
    CHANNEL_EMAIL = "email"
    CHANNEL_SMS = "sms"
    CHANNEL_WHATSAPP = "whatsapp"

    CHANNEL_CHOICES = [
        (CHANNEL_EMAIL, "Email"),
        (CHANNEL_SMS, "SMS"),
        (CHANNEL_WHATSAPP, "WhatsApp"),
    ]

    RECIPIENT_INVITEE = "invitee"
    RECIPIENT_HOST = "host"
    RECIPIENT_BOTH = "both"

    RECIPIENT_CHOICES = [
        (RECIPIENT_INVITEE, "Invitee"),
        (RECIPIENT_HOST, "Host"),
        (RECIPIENT_BOTH, "Both"),
    ]

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveIntegerField(default=1)
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default=CHANNEL_EMAIL)
    recipient = models.CharField(max_length=20, choices=RECIPIENT_CHOICES, default=RECIPIENT_INVITEE)
    subject_template = models.CharField(max_length=255, blank=True)
    body_template = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]

    def clean(self):
        super().clean()
        from apps.workflows.engine import validate_template_string

        if self.subject_template:
            validate_template_string(self.subject_template)
        if self.body_template:
            validate_template_string(self.body_template)

    def __str__(self):
        return f"Step {self.order} [{self.get_channel_display()}] for {self.workflow.name}"


class WorkflowExecution(models.Model):
    STATUS_SCHEDULED = "scheduled"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_SKIPPED = "skipped"

    STATUS_CHOICES = [
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_SKIPPED, "Skipped"),
    ]

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="executions")
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="workflow_executions")
    step = models.ForeignKey(WorkflowStep, on_delete=models.CASCADE, related_name="executions")
    scheduled_for = models.DateTimeField(db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_SCHEDULED, db_index=True
    )
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["booking", "step"], name="unique_workflow_execution_per_booking_step"
            )
        ]
        ordering = ["scheduled_for"]

    def __str__(self):
        return f"Execution {self.id}: {self.workflow.name} -> Booking {self.booking.uid} ({self.status})"


class WorkflowOptOut(models.Model):
    booking = models.OneToOneField(
        Booking, on_delete=models.CASCADE, related_name="workflow_opt_out"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Opt-Out for Booking {self.booking.uid}"
