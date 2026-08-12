import logging
from datetime import datetime

from django.db.models import Prefetch

from apps.integrations.models import BusyBlock, CalendarConnection
from apps.scheduling.intervals import Interval

logger = logging.getLogger(__name__)


def fetch_external_busy(user, start: datetime, end: datetime) -> list[Interval]:
    """
    Fetches external busy intervals from the local cache (BusyBlock)
    for the user across all active connections where the calendar is a busy source.
    """
    blocks = BusyBlock.objects.filter(
        connection__user=user,
        connection__is_active=True,
        calendar__is_busy_source=True,
        period__overlap=(start, end),
    )

    busy_intervals = []
    for block in blocks:
        if block.period.lower and block.period.upper:
            busy_intervals.append((block.period.lower, block.period.upper))

    return busy_intervals


def check_live_conflict(user, start: datetime, end: datetime) -> bool:
    """
    Checks if there is a live conflict on any of the user's active busy-source calendars.
    Returns True if a conflict exists, False otherwise.
    Logs a warning and returns False if the API call fails.
    """
    connections = CalendarConnection.objects.filter(user=user, is_active=True).prefetch_related(
        Prefetch(
            "calendars",
            queryset=BusyBlock.calendar.field.related_model.objects.filter(is_busy_source=True),
        )
    )

    for connection in connections:
        calendars = connection.calendars.all()
        if not calendars:
            continue

        calendar_ids = [cal.external_calendar_id for cal in calendars]

        try:
            from apps.integrations.google.client import GoogleCalendarClient

            client = GoogleCalendarClient(connection)

            body = {
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "items": [{"id": cal_id} for cal_id in calendar_ids],
            }

            response = client.service.freebusy().query(body=body).execute()
            calendars_data = response.get("calendars", {})

            for _cal_id, cal_data in calendars_data.items():
                busy_intervals = cal_data.get("busy", [])
                if busy_intervals:
                    # A conflict exists
                    return True
        except Exception as e:
            logger.warning(f"Live freebusy check failed for connection {connection.id}: {e}")
            # Do not block bookings if the API is down
            continue

    return False
