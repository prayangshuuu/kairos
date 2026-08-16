from django.contrib import admin

from apps.workflows.models import Workflow, WorkflowExecution, WorkflowOptOut, WorkflowStep


class WorkflowStepInline(admin.TabularInline):
    model = WorkflowStep
    extra = 1


@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "trigger", "offset_minutes", "is_active", "is_default", "created_at"]
    list_filter = ["trigger", "is_active", "is_default", "created_at"]
    search_fields = ["name", "owner__email"]
    inlines = [WorkflowStepInline]


@admin.register(WorkflowExecution)
class WorkflowExecutionAdmin(admin.ModelAdmin):
    list_display = ["id", "workflow", "booking", "step", "scheduled_for", "status", "sent_at", "created_at"]
    list_filter = ["status", "scheduled_for", "created_at"]
    search_fields = ["workflow__name", "booking__uid", "booking__invitee_email"]


@admin.register(WorkflowOptOut)
class WorkflowOptOutAdmin(admin.ModelAdmin):
    list_display = ["booking", "created_at"]
    search_fields = ["booking__uid", "booking__invitee_email"]
