from django.urls import path

from .availability_views import (
    ScheduleCreateView,
    ScheduleDateOverrideDeleteView,
    ScheduleDateOverrideUpdateView,
    ScheduleDeleteView,
    ScheduleDetailView,
    ScheduleDuplicateView,
    ScheduleListView,
    SchedulePreviewView,
    ScheduleRenameView,
    ScheduleRulesUpdateView,
    ScheduleSetDefaultView,
    ScheduleUpdateTimezoneView,
)
from .views import (
    EventTypeCheckSlugView,
    EventTypeCreateView,
    EventTypeDeleteView,
    EventTypeDuplicateView,
    EventTypeListView,
    EventTypeToggleActiveView,
    EventTypeUpdateView,
    EventTypeEmbedCodeView,
)

app_name = "scheduling"

urlpatterns = [
    # Event Types
    path("dashboard/event-types/", EventTypeListView.as_view(), name="eventtype_list"),
    path("dashboard/event-types/new/", EventTypeCreateView.as_view(), name="eventtype_create"),
    path(
        "dashboard/event-types/check-slug/",
        EventTypeCheckSlugView.as_view(),
        name="eventtype_check_slug",
    ),
    path(
        "dashboard/event-types/<slug:slug>/", EventTypeUpdateView.as_view(), name="eventtype_edit"
    ),
    path("dashboard/event-types/<slug:slug>/embed/", EventTypeEmbedCodeView.as_view(), name="eventtype_embed"),
    path(
        "dashboard/event-types/<slug:slug>/duplicate/",
        EventTypeDuplicateView.as_view(),
        name="eventtype_duplicate",
    ),
    path(
        "dashboard/event-types/<slug:slug>/toggle-active/",
        EventTypeToggleActiveView.as_view(),
        name="eventtype_toggle_active",
    ),
    path(
        "dashboard/event-types/<slug:slug>/delete/",
        EventTypeDeleteView.as_view(),
        name="eventtype_delete",
    ),
    # Availability
    path("dashboard/availability/", ScheduleListView.as_view(), name="schedule_list"),
    path("dashboard/availability/new/", ScheduleCreateView.as_view(), name="schedule_create"),
    path("dashboard/availability/<int:pk>/", ScheduleDetailView.as_view(), name="schedule_detail"),
    path(
        "dashboard/availability/<int:pk>/duplicate/",
        ScheduleDuplicateView.as_view(),
        name="schedule_duplicate",
    ),
    path(
        "dashboard/availability/<int:pk>/delete/",
        ScheduleDeleteView.as_view(),
        name="schedule_delete",
    ),
    path(
        "dashboard/availability/<int:pk>/default/",
        ScheduleSetDefaultView.as_view(),
        name="schedule_set_default",
    ),
    path(
        "dashboard/availability/<int:pk>/rename/",
        ScheduleRenameView.as_view(),
        name="schedule_rename",
    ),
    path(
        "dashboard/availability/<int:pk>/timezone/",
        ScheduleUpdateTimezoneView.as_view(),
        name="schedule_update_timezone",
    ),
    path(
        "dashboard/availability/<int:pk>/rules/",
        ScheduleRulesUpdateView.as_view(),
        name="schedule_update_rules",
    ),
    path(
        "dashboard/availability/<int:pk>/overrides/",
        ScheduleDateOverrideUpdateView.as_view(),
        name="schedule_update_override",
    ),
    path(
        "dashboard/availability/<int:pk>/overrides/<int:override_id>/delete/",
        ScheduleDateOverrideDeleteView.as_view(),
        name="schedule_delete_override",
    ),
    path(
        "dashboard/availability/<int:pk>/preview/",
        SchedulePreviewView.as_view(),
        name="schedule_preview",
    ),
]
