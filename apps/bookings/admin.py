from django.contrib import admin

from .models import Attendee, Booking, NotificationLog


class AttendeeInline(admin.TabularInline):
    model = Attendee
    extra = 0


class NotificationLogInline(admin.TabularInline):
    model = NotificationLog
    extra = 0


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("uid", "host", "invitee_email", "start_at", "status")
    list_filter = ("status", "event_type")
    search_fields = ("uid", "host__email", "invitee_email", "invitee_name")
    readonly_fields = ("uid", "created_at", "updated_at")
    inlines = [AttendeeInline, NotificationLogInline]

    def has_add_permission(self, request):
        return False
