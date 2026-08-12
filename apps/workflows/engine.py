import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError

ALLOWED_TEMPLATE_VARIABLES: dict[str, str] = {
    "invitee_name": "Full name of the invitee",
    "invitee_email": "Email address of the invitee",
    "host_name": "Full name or display name of the host",
    "host_email": "Email address of the host",
    "event_title": "Title of the scheduled event type",
    "start_time": "Meeting start date & time in recipient's timezone",
    "end_time": "Meeting end date & time in recipient's timezone",
    "duration": "Meeting duration (e.g. '30 minutes')",
    "location": "Location description or conferencing details",
    "meeting_url": "Direct video meeting link or location URL",
    "cancel_link": "Link to cancel the booking",
    "reschedule_link": "Link to reschedule the booking",
    "custom_questions": "Formatted custom question answers provided by invitee",
    "opt_out_link": "Link for invitee to opt out of reminders for this booking",
}

PLACEHOLDER_REGEX = re.compile(r"\{\{?\s*([a-zA-Z0-9_]+)\s*\}?\}")


def validate_template_string(template_str: str) -> None:
    """
    Validate that all placeholders in template_str are within ALLOWED_TEMPLATE_VARIABLES.
    Raises ValidationError on unknown variables.
    """
    if not template_str:
        return

    found_vars = PLACEHOLDER_REGEX.findall(template_str)
    invalid_vars = [var for var in found_vars if var not in ALLOWED_TEMPLATE_VARIABLES]

    if invalid_vars:
        unique_invalid = sorted(set(invalid_vars))
        allowed_list = ", ".join(sorted(ALLOWED_TEMPLATE_VARIABLES.keys()))
        raise ValidationError(
            f"Unknown template variable(s): {', '.join(unique_invalid)}. "
            f"Allowed variables are: {allowed_list}"
        )


def _format_datetime_in_tz(dt: datetime, tz_name: str | None) -> str:
    if not dt:
        return ""
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")

    dt_local = dt.astimezone(tz)
    return dt_local.strftime("%A, %B %d, %Y at %I:%M %p %Z")


def _format_custom_questions(answers: Any) -> str:
    if not answers or not isinstance(answers, dict):
        return ""
    lines = []
    for label, val in answers.items():
        if isinstance(val, list):
            val_str = ", ".join(str(v) for v in val)
        else:
            val_str = str(val)
        lines.append(f"• {label}: {val_str}")
    return "\n".join(lines)


def render_workflow_template(
    template_str: str,
    booking: Any,
    recipient_type: str = "invitee",
    recipient_tz: str | None = None,
    opt_out_url: str | None = None,
    base_url: str = "",
) -> str:
    """
    Render a workflow template using restricted named placeholder substitution.
    """
    if not template_str:
        return ""

    if not base_url:
        site_url = getattr(settings, "SITE_URL", "http://localhost:8000")
        base_url = site_url.rstrip("/")

    # Determine timezone
    if not recipient_tz:
        if recipient_type == "host":
            recipient_tz = getattr(booking.host, "timezone", "UTC")
        else:
            recipient_tz = getattr(booking, "invitee_timezone", "UTC") or "UTC"

    host_name = ""
    if hasattr(booking, "host") and booking.host:
        host_name = booking.host.get_full_name() or getattr(booking.host, "display_name", "") or booking.host.email
    elif hasattr(booking.event_type, "owner"):
        host_name = booking.event_type.owner.get_full_name() or booking.event_type.owner.email

    start_formatted = _format_datetime_in_tz(booking.start_at, recipient_tz)
    end_formatted = _format_datetime_in_tz(booking.end_at, recipient_tz)

    meeting_url = booking.location_value or ""
    cancellation_url = f"{base_url}/b/{booking.uid}/cancel"
    reschedule_url = f"{base_url}/b/{booking.uid}/reschedule"
    opt_out_full_url = opt_out_url or f"{base_url}/b/{booking.uid}/opt-out/"

    context = {
        "invitee_name": booking.invitee_name or "",
        "invitee_email": booking.invitee_email or "",
        "host_name": host_name,
        "host_email": booking.host.email if hasattr(booking, "host") and booking.host else "",
        "event_title": booking.event_type.title if hasattr(booking, "event_type") else "",
        "start_time": start_formatted,
        "end_time": end_formatted,
        "duration": f"{booking.event_type.duration_minutes} minutes" if hasattr(booking, "event_type") else "",
        "location": booking.location_value or (booking.get_location_type_display() if hasattr(booking, "get_location_type_display") else booking.location_type),
        "meeting_url": meeting_url,
        "cancel_link": cancellation_url,
        "reschedule_link": reschedule_url,
        "custom_questions": _format_custom_questions(booking.answers),
        "opt_out_link": opt_out_full_url,
    }

    # Replace both {var} and {{var}}
    def _replacer(match):
        var_name = match.group(1)
        return str(context.get(var_name, match.group(0)))

    return PLACEHOLDER_REGEX.sub(_replacer, template_str)
