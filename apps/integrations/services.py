import logging
from collections import defaultdict
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
    return fetch_external_busy_for_users([user], start, end).get(user.id, [])

def fetch_external_busy_for_users(users: list, start: datetime, end: datetime) -> dict[int, list[Interval]]:
    blocks = BusyBlock.objects.filter(
        connection__user__in=users,
        connection__is_active=True,
        calendar__is_busy_source=True,
        period__overlap=(start, end),
    ).select_related("connection")

    busy_intervals = defaultdict(list)
    for block in blocks:
        if block.period.lower and block.period.upper:
            busy_intervals[block.connection.user_id].append((block.period.lower, block.period.upper))

    return dict(busy_intervals)


def check_live_conflict(user, start: datetime, end: datetime) -> bool:
    """
    Checks if there is a live conflict on any of the user's active busy-source calendars.
    Returns True if a conflict exists, False otherwise.
    Logs a warning and returns False if the API call fails.
    """
    return bool(check_live_conflict_for_users([user], start, end))

def check_live_conflict_for_users(users: list, start: datetime, end: datetime) -> list:
    """
    Returns a list of users who have a live conflict.
    """
    conflicting_users = []
    connections = CalendarConnection.objects.filter(user__in=users, is_active=True).prefetch_related(
        Prefetch(
            "calendars",
            queryset=BusyBlock.calendar.field.related_model.objects.filter(is_busy_source=True),
        )
    )

    for connection in connections:
        if connection.user in conflicting_users:
            continue
            
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
                if cal_data.get("busy", []):
                    conflicting_users.append(connection.user)
                    break
        except Exception as e:
            logger.warning(f"Live freebusy check failed for connection {connection.id}: {e}")
            continue

    return conflicting_users
