from django.urls import path
from .views import PublicProfileView, BookingPageView, BookingStubView, BookingConfirmationView, BookingICSView

app_name = "bookings"

urlpatterns = [
    path("booking/<uuid:uid>/", BookingConfirmationView.as_view(), name="booking_confirmation"),
    path("booking/<uuid:uid>/ics/", BookingICSView.as_view(), name="booking_ics"),
    path('<slug:host_slug>/<slug:event_slug>/', BookingPageView.as_view(), name='booking_page'),
    path('<slug:host_slug>/<slug:event_slug>/stub/', BookingStubView.as_view(), name='booking_stub'),
    
    # Catch-all slug MUST be last
    path('<slug:slug>/', PublicProfileView.as_view(), name='public_profile'),
]
