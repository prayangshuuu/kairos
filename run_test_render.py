import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")
django.setup()

from django.template.loader import render_to_string
from django.utils import timezone
context = {
    "host_name": "Test",
    "invitee_name": "Test",
    "start_at": timezone.now(),
    "end_at": timezone.now(),
    "invitee_tz": "Europe/London",
    "host_tz": "America/New_York",
    "event_title": "Test event"
}
html = render_to_string("emails/booking_approved.html", context)
print("OUT:", repr(html))
