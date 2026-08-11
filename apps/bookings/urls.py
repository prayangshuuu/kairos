from django.urls import path
from .views import PublicProfileView, BookingPageView, BookingStubView

app_name = "bookings"

urlpatterns = [
    path('<slug:slug>/', PublicProfileView.as_view(), name='public_profile'),
    path('<slug:host_slug>/<slug:event_slug>/', BookingPageView.as_view(), name='booking_page'),
    path('<slug:host_slug>/<slug:event_slug>/stub/', BookingStubView.as_view(), name='booking_stub'),
]
