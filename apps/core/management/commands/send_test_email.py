from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.tasks import send_email_async


class Command(BaseCommand):
    help = "Renders every email template with dummy data and sends it to the specified address."

    def add_arguments(self, parser):
        parser.add_argument("email", type=str, help="The email address to send the tests to.")

    def handle(self, *args, **options):
        email = options["email"]
        now = timezone.now()
        start_at = now + timedelta(days=2)
        end_at = start_at + timedelta(minutes=30)

        # Base context that works across most templates
        base_context = {
            "booking_uid": "test-uid-1234",
            "host_name": "Prayangshu Host",
            "host_slug": "prayangshu",
            "invitee_name": "Test Invitee",
            "event_title": "30 Minute Strategy Call",
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "old_start_at": (start_at - timedelta(days=1)).isoformat(),
            "new_start_at": start_at.isoformat(),
            "host_tz": "America/Los_Angeles",
            "invitee_tz": "Europe/London",
            "location_type": "video_conference",
            "meeting_url": "https://meet.google.com/abc-defg-hij",
            "reason": "Something came up on my end.",
            "cancelled_by": "host",
            "rescheduled_by": "host",
            "branding_color": "#10b981",  # Test branding color
            "window": "24h",
            "currency": "$",
            "amount": "100.00",
            "payment_date": now,
            "receipt_url": "https://stripe.com/receipt/test",
            "provider": "Google",
            "user_name": "Test User",
        }

        templates = [
            ("booking_confirmed_invitee", f"Confirmed: {base_context['event_title']}"),
            ("booking_confirmed_host", f"New Booking: {base_context['invitee_name']}"),
            (
                "booking_pending_host",
                f"Action Required: Pending booking from {base_context['invitee_name']}",
            ),
            ("booking_pending_invitee", f"Request sent: {base_context['event_title']}"),
            ("booking_rejected", f"Update: {base_context['event_title']} was declined"),
            ("booking_cancelled_invitee", f"Cancelled: {base_context['event_title']}"),
            ("booking_cancelled_host", f"Cancelled: {base_context['event_title']} (Host view)"),
            ("booking_rescheduled_invitee", f"Rescheduled: {base_context['event_title']}"),
            ("booking_rescheduled_host", f"Rescheduled: {base_context['event_title']} (Host view)"),
            ("booking_reminder", f"Reminder: {base_context['event_title']} in 24h"),
            ("payment_receipt", "Your Payment Receipt"),
            ("payment_failed", "Action Required: Payment Failed"),
            ("refund_issued", "Refund Issued"),
            ("calendar_disconnected", "Action Required: Reconnect your calendar"),
            ("account_data_export", "Your Data Export"),
            ("booking_approved", f"Approved: {base_context['event_title']}"),
            ("account_email_verification", "Verify your Kairos email address"),
            ("account_password_reset", "Reset your Kairos password"),
            ("account_welcome", "Welcome to Kairos!"),
        ]

        self.stdout.write(self.style.SUCCESS(f"Sending {len(templates)} test emails to {email}..."))

        # Test ICS data
        ics_data = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Kairos//EN\nBEGIN:VEVENT\nSUMMARY:Test Meeting\nEND:VEVENT\nEND:VCALENDAR"

        for template_name, subject in templates:
            try:
                # Add ICS to confirmation emails for testing
                attachment = ics_data if "confirmed" in template_name else None

                send_email_async.delay(
                    to_email=email,
                    subject=f"[TEST] {subject}",
                    template_name=template_name,
                    context=base_context,
                    ics_data=attachment,
                    is_transactional=True,
                )
                self.stdout.write(self.style.SUCCESS(f"Queued {template_name}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed to queue {template_name}: {e}"))

        self.stdout.write(
            self.style.SUCCESS(
                "\nAll templates queued. Note: You need a running celery worker to actually send them, or run with CELERY_TASK_ALWAYS_EAGER=True."
            )
        )
