import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from apps.bookings.models import Booking
from apps.bookings.tasks import send_booking_confirmation_emails

try:
    booking = Booking.objects.first()
    send_booking_confirmation_emails(booking.id)
    print("Success")
except Exception as e:
    import traceback
    traceback.print_exc()
