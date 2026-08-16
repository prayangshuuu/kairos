from django.urls import path

from .views import DashboardInsightsView, ExportInsightsView

app_name = "analytics"

urlpatterns = [
    path("dashboard/insights/", DashboardInsightsView.as_view(), name="insights"),
    path("dashboard/insights/export/", ExportInsightsView.as_view(), name="export_insights"),
]
