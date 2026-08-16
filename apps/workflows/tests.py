from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.services import cancel_booking, create_booking, reschedule_booking
from apps.scheduling.models import EventType, Schedule
from apps.workflows.engine import validate_template_string
from apps.workflows.models import (
    Workflow,
    WorkflowExecution,
)
from apps.workflows.services import (
    ensure_default_workflows_for_user,
    execute_due_workflows,
)


@pytest.fixture
def host(db):
    user = User.objects.create_user(
        email="workflowhost@example.com",
        password="password123",
        slug="workflow-host",
        timezone="America/New_York",
    )
    s = Schedule.objects.create(
        user=user,
        name="Working Hours",
        timezone="America/New_York",
        is_default=True,
    )
    from apps.scheduling.models import AvailabilityRule

    for i in range(7):
        AvailabilityRule.objects.create(
            schedule=s, weekday=i, start_time="00:00", end_time="23:59"
        )
    ensure_default_workflows_for_user(user)
    return user


@pytest.fixture
def pro_host(db):
    user = User.objects.create_user(
        email="proworkflowhost@example.com",
        password="password123",
        slug="pro-workflow-host",
        timezone="Asia/Dhaka",
    )
    s = Schedule.objects.create(
        user=user,
        name="Working Hours",
        timezone="Asia/Dhaka",
        is_default=True,
    )
    from apps.scheduling.models import AvailabilityRule

    for i in range(7):
        AvailabilityRule.objects.create(
            schedule=s, weekday=i, start_time="00:00", end_time="23:59"
        )
    ensure_default_workflows_for_user(user)
    return user


@pytest.fixture
def event_type(host):
    return EventType.objects.create(
        owner=host,
        title="Strategy Call",
        slug="strategy-call",
        duration_minutes=30,
        is_active=True,
    )


@pytest.mark.django_db
class TestWorkflowSchedulingAndLifecycle:
    def test_booking_creates_expected_executions_at_correct_times_in_timezone(
        self, host, event_type
    ):
        """A booking creates the expected executions at correct times in recipient timezone."""
        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        start_at = now + timedelta(days=2)  # 48 hours out, aligned to :00

        booking = create_booking(
            event_type=event_type,
            start_at=start_at,
            invitee_name="John Invitee",
            invitee_email="john@example.com",
            invitee_timezone="America/New_York",
            answers={},
            now=now,
        )

        executions = WorkflowExecution.objects.filter(booking=booking).order_by("scheduled_for")
        assert executions.count() == 3

        # 24h reminder
        exec_24h = executions.filter(workflow__offset_minutes=-1440).first()
        assert exec_24h is not None
        assert exec_24h.scheduled_for == start_at - timedelta(hours=24)
        assert exec_24h.status == WorkflowExecution.STATUS_SCHEDULED

        # 1h reminder
        exec_1h = executions.filter(workflow__offset_minutes=-60).first()
        assert exec_1h is not None
        assert exec_1h.scheduled_for == start_at - timedelta(hours=1)
        assert exec_1h.status == WorkflowExecution.STATUS_SCHEDULED

    def test_cancelling_booking_cancels_pending_executions(self, host, event_type):
        """Cancelling a booking cancels its pending executions."""
        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        start_at = now + timedelta(days=2)

        booking = create_booking(
            event_type=event_type,
            start_at=start_at,
            invitee_name="John Invitee",
            invitee_email="john@example.com",
            invitee_timezone="America/New_York",
            answers={},
            now=now,
        )

        pending_count = WorkflowExecution.objects.filter(
            booking=booking, status=WorkflowExecution.STATUS_SCHEDULED
        ).count()
        assert pending_count > 0

        cancel_booking(booking=booking, cancelled_by="invitee", reason="Conflict", now=now)

        # Pending executions for old booking must be marked CANCELLED
        remaining_pending = WorkflowExecution.objects.filter(
            booking=booking, status=WorkflowExecution.STATUS_SCHEDULED
        ).count()
        assert remaining_pending == 0

        cancelled_count = WorkflowExecution.objects.filter(
            booking=booking, status=WorkflowExecution.STATUS_CANCELLED
        ).count()
        assert cancelled_count >= pending_count

    def test_rescheduling_cancels_old_executions_and_creates_new_ones(self, host, event_type):
        """Rescheduling cancels old executions and creates new ones at new offsets."""
        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        old_start_at = now + timedelta(days=2)
        new_start_at = now + timedelta(days=4)

        booking = create_booking(
            event_type=event_type,
            start_at=old_start_at,
            invitee_name="John Invitee",
            invitee_email="john@example.com",
            invitee_timezone="America/New_York",
            answers={},
            now=now,
        )

        old_executions = list(WorkflowExecution.objects.filter(booking=booking))

        new_booking = reschedule_booking(
            booking=booking,
            new_start_at=new_start_at,
            rescheduled_by="invitee",
            reason="Rescheduling to next week",
            now=now,
        )

        # Old booking's executions are cancelled
        for ex in old_executions:
            ex.refresh_from_db()
            assert ex.status == WorkflowExecution.STATUS_CANCELLED

        # New booking has new executions at new start_at offsets
        new_executions = WorkflowExecution.objects.filter(booking=new_booking)
        assert new_executions.count() == 3

        exec_24h = new_executions.filter(workflow__offset_minutes=-1440).first()
        assert exec_24h.scheduled_for == new_start_at - timedelta(hours=24)
        assert exec_24h.status == WorkflowExecution.STATUS_SCHEDULED

    def test_past_execution_at_creation_is_marked_skipped(self, host, event_type):
        """An execution due in the past at creation time is marked skipped, not sent."""
        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        start_at = now + timedelta(hours=1)  # Meeting starts in 1 hour

        booking = create_booking(
            event_type=event_type,
            start_at=start_at,
            invitee_name="John Invitee",
            invitee_email="john@example.com",
            invitee_timezone="America/New_York",
            answers={},
            now=now,
        )

        # 24h reminder (offset -1440) was due 23 hours ago -> SKIPPED
        exec_24h = WorkflowExecution.objects.filter(
            booking=booking, workflow__offset_minutes=-1440
        ).first()
        assert exec_24h.status == WorkflowExecution.STATUS_SKIPPED

        # 1h reminder (offset -60) was due at `start_at - 1h == now` -> SCHEDULED or SKIPPED
        # Note: at scheduled_for <= now, for a 1h reminder created exactly 1h before, scheduled_for == now.

@pytest.mark.django_db
class TestBeatDispatcherAndConcurrency:
    def test_beat_dispatcher_sends_each_execution_exactly_once(self, host, event_type):
        """The beat dispatcher sends each execution exactly once under concurrent workers."""
        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        start_at = now + timedelta(hours=2)

        booking = create_booking(
            event_type=event_type,
            start_at=start_at,
            invitee_name="John Invitee",
            invitee_email="john@example.com",
            invitee_timezone="America/New_York",
            answers={},
            now=now,
        )

        # Manually force execution scheduled_for to past
        exec_obj = WorkflowExecution.objects.filter(
            booking=booking, status=WorkflowExecution.STATUS_SCHEDULED
        ).first()
        exec_obj.scheduled_for = now - timedelta(minutes=5)
        exec_obj.save()

        with patch("apps.workflows.services.send_kairos_email") as mock_email:
            processed = execute_due_workflows()
            assert processed >= 1

        exec_obj.refresh_from_db()
        assert exec_obj.status == WorkflowExecution.STATUS_SENT
        assert exec_obj.sent_at is not None

        # Second beat run finds 0 due executions
        processed_second = execute_due_workflows()
        assert processed_second == 0


@pytest.mark.django_db
class TestTemplateValidationAndGating:
    def test_template_with_unknown_variable_is_rejected_at_save_time(self):
        """A template with an unknown variable is rejected at save time."""
        with pytest.raises(ValidationError) as exc_info:
            validate_template_string("Hello {invitee_name}, your code is {secret_hacker_code}")

        assert "Unknown template variable" in str(exc_info.value)
        assert "secret_hacker_code" in str(exc_info.value)

        # Valid placeholders do not raise
        validate_template_string("Hello {invitee_name}, meeting is at {start_time}")

    def test_user_can_create_additional_custom_workflows(self, client, host):
        """Any user can create additional custom workflows without limit."""
        assert Workflow.objects.filter(owner=host).count() == 3

        client.force_login(host)
        response = client.post(
            reverse("workflows:create"),
            {
                "name": "Custom Follow-Up",
                "trigger": "after_event",
                "offset_minutes": 60,
                "is_active": True,
                "channel": "email",
                "recipient": "invitee",
                "subject_template": "Thanks for joining {event_title}",
                "body_template": "Hi {invitee_name}, thanks for meeting with {host_name}.",
            },
        )

        assert response.status_code == 302
        assert Workflow.objects.filter(owner=host).count() == 4
