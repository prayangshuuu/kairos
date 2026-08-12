import pytest
from django.template.loader import render_to_string
from django.utils import timezone
from datetime import timedelta
import re

TEMPLATES = [
    "booking_confirmed_invitee",
    "booking_confirmed_host",
    "booking_pending_host",
    "booking_pending_invitee",
    "booking_rejected",
    "booking_cancelled_invitee",
    "booking_cancelled_host",
    "booking_rescheduled_invitee",
    "booking_rescheduled_host",
    "booking_reminder",
    "payment_receipt",
    "payment_failed",
    "refund_issued",
    "calendar_disconnected",
    "account_data_export",
]

@pytest.fixture
def dummy_context():
    now = timezone.now()
    start_at = now + timedelta(days=2)
    end_at = start_at + timedelta(minutes=30)
    
    return {
        "booking_uid": "test-uid-1234",
        "host_name": "Prayangshu Host",
        "host_slug": "prayangshu",
        "invitee_name": "Test Invitee",
        "event_title": "30 Minute Strategy Call",
        "start_at": start_at,
        "end_at": end_at,
        "old_start_at": start_at - timedelta(days=1),
        "new_start_at": start_at,
        "host_tz": "America/Los_Angeles",
        "invitee_tz": "Europe/London",
        "location_type": "video_conference",
        "meeting_url": "https://meet.google.com/abc-defg-hij",
        "reason": "Something came up on my end.",
        "cancelled_by": "host",
        "rescheduled_by": "host",
        "branding_color": "#10b981",
        "window": "24h",
        "currency": "$",
        "amount": "100.00",
        "payment_date": now,
        "receipt_url": "https://stripe.com/receipt/test",
        "provider": "Google",
        "user_name": "Test User",
    }

@pytest.mark.parametrize("template_name", TEMPLATES)
def test_templates_render_without_error(template_name, dummy_context):
    html_content = render_to_string(f"emails/{template_name}.html", dummy_context)
    txt_content = render_to_string(f"emails/{template_name}.txt", dummy_context)
    
    assert html_content
    assert txt_content

@pytest.mark.parametrize("template_name", TEMPLATES)
def test_templates_no_style_blocks_or_flexbox(template_name, dummy_context):
    html_content = render_to_string(f"emails/{template_name}.html", dummy_context).lower()
    
    # We must not have <style> tags anywhere in the body because clients strip them
    # Ensure there's no <style> except maybe if we allow it in head, but prompt says "no template contains a <style> block"
    assert "<style>" not in html_content
    assert "display: flex" not in html_content
    assert "display: grid" not in html_content

@pytest.mark.django_db
def test_send_kairos_email_attaches_ics():
    from apps.core.mail import send_kairos_email
    from django.core import mail
    
    context = {"host_name": "Test", "invitee_name": "Test", "start_at": timezone.now(), "end_at": timezone.now()}
    
    send_kairos_email(
        to_email="test@example.com",
        subject="Test with ICS",
        template_name="booking_confirmed_invitee",
        context=context,
        ics_data="BEGIN:VCALENDAR...END:VCALENDAR"
    )
    
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    
    # Text body
    assert "Test" in msg.body
    
    # HTML alternative and ICS alternative
    assert len(msg.alternatives) == 2
    assert msg.alternatives[1][0] == "BEGIN:VCALENDAR...END:VCALENDAR"
    assert msg.alternatives[1][1] == "text/calendar; method=REQUEST"
    
    # No direct attachment
    assert len(msg.attachments) == 0
