import contextlib
import io

from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import transaction
from django.utils import timezone as django_timezone
from PIL import Image

from apps.accounts.models import User, UserSlugHistory
from apps.bookings.models import Booking


def process_avatar(image_file):
    """
    Strip EXIF data and resize the image to a max dimension (e.g. 500x500).
    """
    try:
        img = Image.open(image_file)
    except Exception:
        return image_file

    # Strip EXIF by re-creating the image data
    data = list(img.getdata())
    image_without_exif = Image.new(img.mode, img.size)
    image_without_exif.putdata(data)

    # Resize
    max_size = (500, 500)
    image_without_exif.thumbnail(max_size, Image.Resampling.LANCZOS)

    # Save back to memory
    output = io.BytesIO()
    fmt = img.format if img.format else "JPEG"
    if fmt == "MPO":
        fmt = "JPEG"
    image_without_exif.save(output, format=fmt)
    output.seek(0)

    return InMemoryUploadedFile(
        output,
        "ImageField",
        image_file.name,
        f"image/{fmt.lower()}",
        output.getbuffer().nbytes,
        None,
    )


def anonymize_user(user: User):
    """
    Anonymise user data instead of cascade deleting:
    1. Cancel future bookings.
    2. Scrub PII from past bookings.
    3. Move slug to history to prevent reuse.
    4. Deactivate and anonymise user row.
    """
    from apps.bookings.services import AlreadyCancelled, cancel_booking

    now = django_timezone.now()

    with transaction.atomic():
        # Cancel future bookings
        future_bookings = Booking.objects.filter(
            host=user, start_at__gte=now, status=Booking.StatusChoices.CONFIRMED
        )
        for booking in future_bookings:
            with contextlib.suppress(AlreadyCancelled):
                cancel_booking(
                    booking=booking, cancelled_by="host", reason="Host account deactivated", now=now
                )

        # Scrub PII from past bookings
        past_bookings = Booking.objects.filter(host=user, start_at__lt=now)
        for booking in past_bookings:
            booking.invitee_name = "Anonymised"
            booking.invitee_email = "anonymised@example.com"
            booking.invitee_notes = ""
            booking.answers = {}
            booking.save(
                update_fields=[
                    "invitee_name",
                    "invitee_email",
                    "invitee_notes",
                    "answers",
                    "updated_at",
                ]
            )

            # Scrub attendees
            for attendee in booking.attendees.all():
                if not attendee.is_organizer:
                    attendee.name = "Anonymised"
                    attendee.email = "anonymised@example.com"
                    attendee.save(update_fields=["name", "email"])

        # Move slug to history if exists
        if user.slug:
            UserSlugHistory.objects.get_or_create(user=user, old_slug=user.slug)

        # Anonymise user
        user.is_active = False
        user.email = f"anonymised_{user.id}@example.com"
        user.slug = None
        user.display_name = "Anonymised User"
        user.bio = ""
        user.avatar.delete(save=False)
        user.save()
