from datetime import datetime

from apps.bookings.models import Booking


def generate_ics_for_booking(booking: Booking) -> str:
    """Generates an ICS string for the given booking."""
    dtformat = "%Y%m%dT%H%M%SZ"

    # RFC 5545 specifies that datetime values in UTC should end with 'Z'
    dtstamp = datetime.utcnow().strftime(dtformat)
    dtstart = booking.start_at.strftime(dtformat)
    dtend = booking.end_at.strftime(dtformat)

    from django.conf import settings

    organizer_email = settings.DEFAULT_FROM_EMAIL
    organizer_name = booking.host.get_full_name() or booking.host.email

    summary = f"Meeting with {organizer_name}"
    description = f"Event: {booking.event_type.title}"
    if booking.location_type:
        description += f"\\nLocation Type: {booking.location_type}"
    if booking.location_value:
        description += f"\\nLocation: {booking.location_value}"

    description = description.replace("\n", "\\n")

    location = booking.location_value if booking.location_value else ""

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Kairos//Scheduling//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{booking.uid}@joinkairos.tech",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        f"ORGANIZER;CN={organizer_name}:mailto:{organizer_email}",
    ]

    if location:
        lines.append(f"LOCATION:{location}")

    for attendee in booking.attendees.all():
        role = "REQ-PARTICIPANT"
        lines.append(
            f"ATTENDEE;CUTYPE=INDIVIDUAL;ROLE={role};PARTSTAT=ACCEPTED;CN={attendee.name}:mailto:{attendee.email}"
        )

    lines.extend(["END:VEVENT", "END:VCALENDAR"])

    return "\r\n".join(lines) + "\r\n"
