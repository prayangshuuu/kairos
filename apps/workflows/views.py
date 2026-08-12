import logging
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.bookings.models import Booking
from apps.workflows.engine import ALLOWED_TEMPLATE_VARIABLES, render_workflow_template
from apps.workflows.forms import WorkflowForm, WorkflowStepForm
from apps.workflows.models import (
    Workflow,
    WorkflowExecution,
    WorkflowOptOut,
    WorkflowStep,
)
from apps.workflows.services import (
    ensure_default_workflows_for_user,
    send_workflow_execution,
)

logger = logging.getLogger(__name__)


class WorkflowListView(LoginRequiredMixin, View):
    """
    List all workflows for the current host and display recent workflow execution history.
    """

    def get(self, request):
        ensure_default_workflows_for_user(request.user)

        workflows = (
            Workflow.objects.filter(owner=request.user)
            .prefetch_related("steps", "event_types")
            .order_by("-is_default", "created_at")
        )

        executions = (
            WorkflowExecution.objects.filter(workflow__owner=request.user)
            .select_related("workflow", "step", "booking", "booking__event_type")
            .order_by("-created_at")[:50]
        )

        context = {
            "workflows": workflows,
            "executions": executions,
            "can_create_custom": True,
        }
        return render(request, "workflows/workflow_list.html", context)


class WorkflowCreateView(LoginRequiredMixin, View):
    """
    Create a new custom workflow.
    """

    def get(self, request):
        workflow_form = WorkflowForm(user=request.user)
        step_form = WorkflowStepForm()

        context = {
            "workflow_form": workflow_form,
            "step_form": step_form,
            "allowed_variables": ALLOWED_TEMPLATE_VARIABLES,
            "is_edit": False,
        }
        return render(request, "workflows/workflow_form.html", context)

    def post(self, request):
        workflow_form = WorkflowForm(request.POST, user=request.user)
        step_form = WorkflowStepForm(request.POST)

        if workflow_form.is_valid() and step_form.is_valid():
            with transaction.atomic():
                workflow = workflow_form.save(commit=False)
                workflow.owner = request.user
                workflow.save()
                workflow_form.save_m2m()

                step = step_form.save(commit=False)
                step.workflow = workflow
                step.order = 1
                step.save()

            messages.success(request, f"Workflow '{workflow.name}' created successfully!")
            return redirect("workflows:list")

        context = {
            "workflow_form": workflow_form,
            "step_form": step_form,
            "allowed_variables": ALLOWED_TEMPLATE_VARIABLES,
            "is_edit": False,
        }
        return render(request, "workflows/workflow_form.html", context)


class WorkflowUpdateView(LoginRequiredMixin, View):
    """
    Edit an existing workflow and its primary step.
    """

    def get(self, request, pk):
        workflow = get_object_or_404(Workflow, owner=request.user, pk=pk)
        step = workflow.steps.first()

        workflow_form = WorkflowForm(instance=workflow, user=request.user)
        step_form = WorkflowStepForm(instance=step) if step else WorkflowStepForm()

        context = {
            "workflow": workflow,
            "workflow_form": workflow_form,
            "step_form": step_form,
            "allowed_variables": ALLOWED_TEMPLATE_VARIABLES,
            "is_edit": True,
        }
        return render(request, "workflows/workflow_form.html", context)

    def post(self, request, pk):
        workflow = get_object_or_404(Workflow, owner=request.user, pk=pk)
        step = workflow.steps.first()

        workflow_form = WorkflowForm(request.POST, instance=workflow, user=request.user)
        step_form = WorkflowStepForm(request.POST, instance=step) if step else WorkflowStepForm(request.POST)

        if workflow_form.is_valid() and step_form.is_valid():
            with transaction.atomic():
                workflow = workflow_form.save()
                step_obj = step_form.save(commit=False)
                step_obj.workflow = workflow
                step_obj.save()

            messages.success(request, f"Workflow '{workflow.name}' updated successfully.")
            return redirect("workflows:list")

        context = {
            "workflow": workflow,
            "workflow_form": workflow_form,
            "step_form": step_form,
            "allowed_variables": ALLOWED_TEMPLATE_VARIABLES,
            "is_edit": True,
        }
        return render(request, "workflows/workflow_form.html", context)


class WorkflowDuplicateView(LoginRequiredMixin, View):
    """
    Duplicate an existing workflow.
    """

    def post(self, request, pk):
        workflow = get_object_or_404(Workflow, owner=request.user, pk=pk)

        with transaction.atomic():
            new_wf = Workflow.objects.create(
                owner=request.user,
                name=f"{workflow.name} (Copy)",
                trigger=workflow.trigger,
                offset_minutes=workflow.offset_minutes,
                is_active=workflow.is_active,
                is_default=False,
            )
            new_wf.event_types.set(workflow.event_types.all())

            for step in workflow.steps.all():
                WorkflowStep.objects.create(
                    workflow=new_wf,
                    order=step.order,
                    channel=step.channel,
                    recipient=step.recipient,
                    subject_template=step.subject_template,
                    body_template=step.body_template,
                    is_active=step.is_active,
                )

        messages.success(request, f"Workflow '{new_wf.name}' duplicated.")
        return redirect("workflows:list")


class WorkflowDeleteView(LoginRequiredMixin, View):
    """
    Delete a workflow.
    """

    def post(self, request, pk):
        workflow = get_object_or_404(Workflow, owner=request.user, pk=pk)
        name = workflow.name
        workflow.delete()
        messages.success(request, f"Workflow '{name}' deleted.")
        return redirect("workflows:list")


class WorkflowToggleView(LoginRequiredMixin, View):
    """
    Toggle is_active on a workflow.
    """

    def post(self, request, pk):
        workflow = get_object_or_404(Workflow, owner=request.user, pk=pk)
        workflow.is_active = not workflow.is_active
        workflow.save(update_fields=["is_active", "updated_at"])

        if request.htmx:
            status_text = "Active" if workflow.is_active else "Inactive"
            cls = "bg-green-100 text-green-800" if workflow.is_active else "bg-gray-100 text-gray-600"
            return HttpResponse(
                f'<span class="px-2.5 py-0.5 rounded-full text-xs font-medium uppercase {cls}">{status_text}</span>'
            )
        return redirect("workflows:list")


class WorkflowExecutionRetryView(LoginRequiredMixin, View):
    """
    Manually retry a failed workflow execution.
    """

    def post(self, request, pk):
        execution = get_object_or_404(WorkflowExecution, pk=pk, workflow__owner=request.user)

        execution.status = WorkflowExecution.STATUS_SCHEDULED
        execution.error = ""
        execution.scheduled_for = timezone.now()
        execution.save(update_fields=["status", "error", "scheduled_for"])

        send_workflow_execution(execution)

        if execution.status == WorkflowExecution.STATUS_SENT:
            messages.success(request, f"Workflow execution #{execution.id} re-sent successfully.")
        else:
            messages.error(request, f"Workflow execution #{execution.id} failed: {execution.error}")

        return redirect("workflows:list")


class WorkflowPreviewView(LoginRequiredMixin, View):
    """
    HTMX live preview renderer with sample data.
    """

    def post(self, request):
        subject_template = request.POST.get("subject_template", "")
        body_template = request.POST.get("body_template", "")

        mock_event_type = SimpleNamespace(
            title="30 Minute Discovery Call",
            duration_minutes=30,
        )
        mock_host = SimpleNamespace(
            get_full_name=lambda: request.user.get_full_name() or request.user.email,
            email=request.user.email,
            timezone=request.user.timezone or "UTC",
        )
        mock_booking = SimpleNamespace(
            uid="preview-12345",
            invitee_name="Sarah Connor",
            invitee_email="sarah@example.com",
            invitee_timezone="America/New_York",
            host=mock_host,
            event_type=mock_event_type,
            start_at=timezone.now() + timezone.timedelta(days=1),
            end_at=timezone.now() + timezone.timedelta(days=1, minutes=30),
            location_type="google_meet",
            location_value="https://meet.google.com/abc-defg-hij",
            get_location_type_display=lambda: "Google Meet",
            answers={"What is your primary goal?": "Evaluate Kairos for team scheduling"},
        )

        rendered_subject = ""
        rendered_body = ""
        error_message = ""

        try:
            rendered_subject = render_workflow_template(
                subject_template, mock_booking, base_url="https://kairos.app"
            )
            rendered_body = render_workflow_template(
                body_template, mock_booking, base_url="https://kairos.app"
            )
        except Exception as e:
            error_message = str(e)

        return render(
            request,
            "workflows/partials/preview.html",
            {
                "rendered_subject": rendered_subject,
                "rendered_body": rendered_body,
                "error_message": error_message,
            },
        )


class WorkflowOptOutView(View):
    """
    Public opt-out page allowing an invitee to opt out of workflow reminders for a specific booking.
    """

    def get(self, request, token):
        booking = get_object_or_404(Booking, uid=token)
        already_opted_out = WorkflowOptOut.objects.filter(booking=booking).exists()
        return render(
            request,
            "workflows/opt_out.html",
            {"booking": booking, "already_opted_out": already_opted_out},
        )

    def post(self, request, token):
        booking = get_object_or_404(Booking, uid=token)
        WorkflowOptOut.objects.get_or_create(booking=booking)
        messages.success(
            request, f"You have successfully opted out of reminder notifications for '{booking.event_type.title}'."
        )
        return render(
            request,
            "workflows/opt_out.html",
            {"booking": booking, "already_opted_out": True},
        )
