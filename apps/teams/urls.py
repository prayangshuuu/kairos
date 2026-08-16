from django.urls import path
from .views import SwitchContextView

app_name = "teams"

urlpatterns = [
    path("switch-context/", SwitchContextView.as_view(), name="switch_context"),
]
