from django.urls import path
from .views import SwitchContextView, TeamListView, TeamCreateView, TeamUpdateView, TeamDeleteView

app_name = "teams"

urlpatterns = [
    path("switch-context/", SwitchContextView.as_view(), name="switch_context"),
    path("", TeamListView.as_view(), name="team_list"),
    path("new/", TeamCreateView.as_view(), name="team_create"),
    path("<int:pk>/edit/", TeamUpdateView.as_view(), name="team_update"),
    path("<int:pk>/delete/", TeamDeleteView.as_view(), name="team_delete"),
]
