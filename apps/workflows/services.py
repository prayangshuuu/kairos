import logging
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.bookings.models import Booking
from apps.core.mail import send_kairos_email
from apps.workflows.engine import render_workflow_template
from apps.workflows.models import (
    Workflow,
    WorkflowExecution,
    WorkflowOptOut,
    WorkflowStep,
)

logger = logging.getLogger(__name__)


def ensure_default_workflows_for_user(user) -> list[Workflow]:
    """
    Ensure the 3 default workflows are created for the user if none exist.
    """
    if not user or not user.is_authenticated:
        return []

    if Workflow.objects.filter(owner=user).exists():
        return list(Workflow.objects.filter(owner=user, is_default=True))

    workflows = []
    with transaction.atomic():
        # 1. 24-Hour Email Reminder to Invitee
        w1 = Workflow.objects.create(
            owner=user,
            name="24-Hour Email Reminder to Invitee",
            trigger=Workflow.TRIGGER_BEFORE_EVENT,
            offset_minutes=-1440,  # 24 hours before
            is_active=True,
            is_default=True,
        )
        WorkflowStep.objects.create(
            workflow=w1,
            order=1,
            channel=WorkflowStep.CHANNEL_EMAIL,
            recipient=WorkflowStep.RECIPIENT_INVITEE,
            subject_template="Reminder: {event_title} with {host_name} tomorrow",
            body_template=(
                "Hi {invitee_name},\n\n"
                "This is a friendly reminder for your upcoming meeting '{event_title}' with {host_name} tomorrow at {start_time}.\n\n"
                "Meeting URL: {meeting_url}\n\n"
                "Need to reschedule or cancel? Use the links below:\n"
                "Reschedule: {reschedule_link}\n"
                "Cancel: {cancel_link}\n\n"
                "To stop receiving reminders for this meeting: {opt_out_link}"
            ),
            is_active=True,
        )
        workflows.append(w1)

        # 2. 1-Hour Email Reminder to Invitee
        w2 = Workflow.objects.create(
            owner=user,
            name="1-Hour Email Reminder to Invitee",
            trigger=Workflow.TRIGGER_BEFORE_EVENT,
            offset_minutes=-60,  # 1 hour before
            is_active=True,
            is_default=True,
        )
        WorkflowStep.objects.create(
            workflow=w2,
            order=1,
            channel=WorkflowStep.CHANNEL_EMAIL,
            recipient=WorkflowStep.RECIPIENT_INVITEE,
            subject_template="Starting soon: {event_title} in 1 hour",
            body_template=(
                "Hi {invitee_name},\n\n"
                "Your meeting '{event_title}' with {host_name} starts in 1 hour at {start_time}.\n\n"
                "Meeting URL: {meeting_url}\n\n"
                "To stop receiving reminders for this meeting: {opt_out_link}"
            ),
            is_active=True,
        )
        workflows.append(w2)

        # 3. 1-Hour Email Reminder to Host
        w3 = Workflow.objects.create(
            owner=user,
            name="1-Hour Email Reminder to Host",
            trigger=Workflow.TRIGGER_BEFORE_EVENT,
            offset_minutes=-60,  # 1 hour before
            is_active=True,
            is_default=True,
        )
        WorkflowStep.objects.create(
            workflow=w3,
            order=1,
            channel=WorkflowStep.CHANNEL_EMAIL,
            recipient=WorkflowStep.RECIPIENT_HOST,
            subject_template="Upcoming meeting: {event_title} with {invitee_name} in 1 hour",
            body_template=(
                "Hi {host_name},\n\n"
                "Your meeting '{event_title}' with {invitee_name} starts in 1 hour at {start_time}.\n\n"
                "Meeting URL: {meeting_url}"
            ),
            is_active=True,
        )
        workflows.append(w3)

    return workflows


def schedule_workflow_executions_for_booking(
    booking: Booking, trigger: str | None = None, now: Any = None
) -> list[WorkflowExecution]:
    """
    Compute scheduled_for time for every applicable workflow step on booking creation/trigger
    and create WorkflowExecution rows. Skip executions whose scheduled_for has passed.
    """
    now = now or timezone.now()
    host = booking.event_type.owner
    ensure_default_workflows_for_user(host)

    workflows = Workflow.objects.filter(owner=host, is_active=True)

    executions = []
    for wf in workflows:
        if trigger and wf.trigger != trigger:
            continue

        # Check if workflow applies to this event_type
        if wf.event_types.exists() and not wf.event_types.filter(id=booking.event_type_id).exists():
            continue

        # Compute scheduled_for time
        if wf.trigger == Workflow.TRIGGER_BEFORE_EVENT:
            scheduled_for = booking.start_at + timedelta(minutes=wf.offset_minutes)
        elif wf.trigger == Workflow.TRIGGER_AFTER_EVENT:
            scheduled_for = booking.end_at + timedelta(minutes=wf.offset_minutes)
        else:
            # Event-driven triggers (on_booking_created, on_booking_cancelled, etc.)
            scheduled_for = now + timedelta(minutes=wf.offset_minutes)

        for step in wf.steps.filter(is_active=True):
            # Check if scheduled_for has passed
            status = WorkflowExecution.STATUS_SCHEDULED
            if wf.trigger in (Workflow.TRIGGER_BEFORE_EVENT, Workflow.TRIGGER_AFTER_EVENT):
                if scheduled_for <= now:
                    status = WorkflowExecution.STATUS_SKIPPED

            exec_obj, created = WorkflowExecution.objects.get_or_create(
                booking=booking,
                step=step,
                defaults={
                    "workflow": wf,
                    "scheduled_for": scheduled_for,
                    "status": status,
                },
            )
            if created:
                executions.append(exec_obj)

    return executions


def cancel_workflow_executions_for_booking(booking: Booking) -> int:
    """
    Cancel all pending (scheduled) workflow executions for a cancelled or rescheduled booking.
    """
    return WorkflowExecution.objects.filter(
        booking=booking, status=WorkflowExecution.STATUS_SCHEDULED
    ).update(status=WorkflowExecution.STATUS_CANCELLED)


def execute_due_workflows() -> int:
    """
    Celery beat task function running every minute:
    Selects executions due now (scheduled_for <= now) and dispatches them with select_for_update(skip_locked=True).
    """
    now = timezone.now()
    processed_count = 0

    with transaction.atomic():
        due_executions = list(
            WorkflowExecution.objects.filter(
                status=WorkflowExecution.STATUS_SCHEDULED, scheduled_for__lte=now
            )
            .select_for_update(skip_locked=True)
            .select_related("workflow", "step", "booking", "booking__event_type", "booking__host")
        )

        for execution in due_executions:
            send_workflow_execution(execution)
            processed_count += 1

    return processed_count


def send_workflow_execution(execution: WorkflowExecution) -> None:
    """
    Dispatch a single workflow execution via its channel.
    """
    booking = execution.booking
    step = execution.step
    now = timezone.now()

    # 1. Check if booking was cancelled
    if booking.status in (Booking.StatusChoices.CANCELLED, Booking.StatusChoices.REJECTED):
        execution.status = WorkflowExecution.STATUS_CANCELLED
        execution.error = "Booking was cancelled prior to execution."
        execution.save(update_fields=["status", "error"])
        return

    # 2. Check channel
    if step.channel == WorkflowStep.CHANNEL_EMAIL:
        # Check invitee opt-out
        is_opted_out = WorkflowOptOut.objects.filter(booking=booking).exists()

        recipients = []
        if step.recipient in (WorkflowStep.RECIPIENT_INVITEE, WorkflowStep.RECIPIENT_BOTH):
            if not is_opted_out:
                recipients.append(("invitee", booking.invitee_email, booking.invitee_timezone))
        if step.recipient in (WorkflowStep.RECIPIENT_HOST, WorkflowStep.RECIPIENT_BOTH):
            recipients.append(("host", booking.host.email, booking.host.timezone))

        if not recipients:
            execution.status = WorkflowExecution.STATUS_SKIPPED
            execution.error = "Invitee opted out of workflow notifications for this booking."
            execution.save(update_fields=["status", "error"])
            return

        try:
            for r_type, email_addr, r_tz in recipients:
                subj = render_workflow_template(
                    step.subject_template, booking, recipient_type=r_type, recipient_tz=r_tz
                )
                body = render_workflow_template(
                    step.body_template, booking, recipient_type=r_type, recipient_tz=r_tz
                )

                send_kairos_email(
                    to_email=email_addr,
                    subject=subj or f"Workflow Notice: {booking.event_type.title}",
                    template_name="workflow_reminder",
                    context={"content": body, "subject": subj},
                )

            execution.status = WorkflowExecution.STATUS_SENT
            execution.sent_at = now
            execution.error = ""
            execution.save(update_fields=["status", "sent_at", "error"])

        except Exception as e:
            logger.error(f"Error dispatching WorkflowExecution {execution.id}: {e}")
            execution.status = WorkflowExecution.STATUS_FAILED
            execution.error = str(e)
            execution.save(update_fields=["status", "error"])

    elif step.channel == WorkflowStep.CHANNEL_SMS:
        sms_enabled = getattr(settings, "KAIROS_ENABLE_SMS_WORKFLOWS", False)
        if not sms_enabled:
            execution.status = WorkflowExecution.STATUS_FAILED
            execution.error = "SMS workflow channel is disabled in server configuration (KAIROS_ENABLE_SMS_WORKFLOWS=False)."
            execution.save(update_fields=["status", "error"])
        else:
            # Provider integration point (e.g. SSL Wireless / Greenweb / Twilio)
            execution.status = WorkflowExecution.STATUS_SENT
            execution.sent_at = now
            execution.save(update_fields=["status", "sent_at"])

    elif step.channel == WorkflowStep.CHANNEL_WHATSAPP:
        execution.status = WorkflowExecution.STATUS_FAILED
        execution.error = "WhatsApp channel requires Meta WhatsApp Business API setup and host approval."
        execution.save(update_fields=["status", "error"])
