from django.urls import path
from .views import PublicProfileView, BookingPageView, BookingStubView, BookingConfirmationView, BookingICSView, BookingCancelView, DashboardBookingCancelView, BookingRescheduleView, DashboardBookingRescheduleView, BookingApproveView, BookingRejectView, DashboardBookingApproveView, DashboardBookingRejectView, DashboardBookingsView, DashboardBookingNoShowView

app_name = "bookings"

urlpatterns = [
    path("booking/<uuid:uid>/", BookingConfirmationView.as_view(), name="booking_confirmation"),
    path("booking/<uuid:uid>/ics/", BookingICSView.as_view(), name="booking_ics"),
    path("booking/<uuid:uid>/cancel/", BookingCancelView.as_view(), name="booking_cancel"),
    path("booking/<uuid:uid>/reschedule/", BookingRescheduleView.as_view(), name="booking_reschedule"),
    path("booking/<uuid:uid>/approve/", BookingApproveView.as_view(), name="booking_approve"),
    path("booking/<uuid:uid>/reject/", BookingRejectView.as_view(), name="booking_reject"),
    
    path("dashboard/bookings/", DashboardBookingsView.as_view(), name="dashboard_bookings"),
    path("dashboard/bookings/<uuid:uid>/cancel/", DashboardBookingCancelView.as_view(), name="dashboard_booking_cancel"),
    path("dashboard/bookings/<uuid:uid>/reschedule/", DashboardBookingRescheduleView.as_view(), name="dashboard_booking_reschedule"),
    path("dashboard/bookings/<uuid:uid>/approve/", DashboardBookingApproveView.as_view(), name="dashboard_booking_approve"),
    path("dashboard/bookings/<uuid:uid>/reject/", DashboardBookingRejectView.as_view(), name="dashboard_booking_reject"),
    path("dashboard/bookings/<uuid:uid>/no-show/", DashboardBookingNoShowView.as_view(), name="dashboard_booking_no_show"),
    
    path('<slug:host_slug>/<slug:event_slug>/', BookingPageView.as_view(), name='booking_page'),
    path('<slug:host_slug>/<slug:event_slug>/stub/', BookingStubView.as_view(), name='booking_stub'),
    
    # Catch-all slug MUST be last
    path('<slug:slug>/', PublicProfileView.as_view(), name='public_profile'),
]
