import io
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone as django_timezone
from PIL import Image

from apps.accounts.models import User, UserSlugHistory
from apps.accounts.services import anonymize_user, process_avatar
from apps.bookings.models import Attendee, Booking
from apps.scheduling.models import EventType


class SettingsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(email="host@example.com", password="password")
        self.user.slug = "old-slug"
        self.user.save()
        self.client.force_login(self.user)

    def test_avatar_upload_strips_exif_and_resizes(self):
        # Create an image with EXIF
        img = Image.new("RGB", (1000, 1000), color="red")

        # Add some mock EXIF data (0x8769 is ExifOffset)
        try:
            exif_bytes = img.getexif().tobytes()
        except AttributeError:
            exif_bytes = b""

        img_bytes = io.BytesIO()
        img.save(img_bytes, format="JPEG", exif=exif_bytes)
        img_bytes.seek(0)

        from django.core.files.uploadedfile import InMemoryUploadedFile

        upload = InMemoryUploadedFile(
            img_bytes, "ImageField", "test.jpg", "image/jpeg", len(img_bytes.getvalue()), None
        )

        processed = process_avatar(upload)

        processed_img = Image.open(processed)
        # Should be resized to 500 max
        self.assertTrue(processed_img.width <= 500 and processed_img.height <= 500)
        # Exif should be stripped (getexif returns an empty Image.Exif or None)
        self.assertFalse(bool(processed_img.getexif()))

    def test_anonymize_user_does_not_cascade(self):
        event_type = EventType.objects.create(
            owner=self.user, title="Test Event", duration_minutes=30
        )

        # Past booking
        past = django_timezone.now() - timedelta(days=1)
        past_booking = Booking.objects.create(
            event_type=event_type,
            host=self.user,
            start_at=past,
            end_at=past + timedelta(minutes=30),
            invitee_timezone="UTC",
            status=Booking.StatusChoices.CONFIRMED,
            invitee_name="Real Name",
            invitee_email="real@example.com",
        )
        Attendee.objects.create(booking=past_booking, name="Real Name", email="real@example.com")

        # Future booking
        future = django_timezone.now() + timedelta(days=1)
        future_booking = Booking.objects.create(
            event_type=event_type,
            host=self.user,
            start_at=future,
            end_at=future + timedelta(minutes=30),
            invitee_timezone="UTC",
            status=Booking.StatusChoices.CONFIRMED,
            invitee_name="Future Real Name",
            invitee_email="future_real@example.com",
        )

        anonymize_user(self.user)

        # Check user is inactive and anonymized
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertNotIn("host@example.com", self.user.email)
        self.assertEqual(self.user.slug, None)

        # Past booking still exists but anonymized
        past_booking.refresh_from_db()
        self.assertEqual(past_booking.invitee_name, "Anonymised")
        self.assertEqual(past_booking.invitee_email, "anonymised@example.com")
        self.assertTrue(Booking.objects.filter(id=past_booking.id).exists())

        # Future booking is cancelled
        future_booking.refresh_from_db()
        self.assertEqual(future_booking.status, Booking.StatusChoices.CANCELLED)

    def test_old_slug_redirects(self):
        # User changes slug
        self.user.slug = "new-slug"
        self.user.save()

        UserSlugHistory.objects.create(user=self.user, old_slug="old-slug")

        # Access old slug public profile
        response = self.client.get(reverse("bookings:public_profile", kwargs={"slug": "old-slug"}))

        self.assertEqual(response.status_code, 302)
