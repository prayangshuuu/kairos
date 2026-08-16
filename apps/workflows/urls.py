from django.urls import path

from apps.workflows import views

app_name = "workflows"

urlpatterns = [
    path("dashboard/workflows/", views.WorkflowListView.as_view(), name="list"),
    path("dashboard/workflows/new/", views.WorkflowCreateView.as_view(), name="create"),
    path("dashboard/workflows/<int:pk>/edit/", views.WorkflowUpdateView.as_view(), name="edit"),
    path(
        "dashboard/workflows/<int:pk>/duplicate/",
        views.WorkflowDuplicateView.as_view(),
        name="duplicate",
    ),
    path(
        "dashboard/workflows/<int:pk>/delete/",
        views.WorkflowDeleteView.as_view(),
        name="delete",
    ),
    path(
        "dashboard/workflows/<int:pk>/toggle/",
        views.WorkflowToggleView.as_view(),
        name="toggle",
    ),
    path(
        "dashboard/workflows/executions/<int:pk>/retry/",
        views.WorkflowExecutionRetryView.as_view(),
        name="retry_execution",
    ),
    path("dashboard/workflows/preview/", views.WorkflowPreviewView.as_view(), name="preview"),
    path("b/<str:token>/opt-out/", views.WorkflowOptOutView.as_view(), name="opt_out"),
]
