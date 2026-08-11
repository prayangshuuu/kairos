from django.urls import path
from .views import PublicProfileView

app_name = "bookings"

urlpatterns = [
    path('<slug:slug>/', PublicProfileView.as_view(), name='public_profile'),
]
