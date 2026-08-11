from django import template
import zoneinfo
from datetime import datetime
from django.utils import timezone as django_timezone

register = template.Library()

@register.filter
def astimezone(value, tz_name):
    if not isinstance(value, datetime):
        return value
    if not tz_name:
        tz_name = 'UTC'
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
        return value.astimezone(tz)
    except Exception:
        return value

@register.filter
def relative_time(value):
    if not isinstance(value, datetime):
        return ""
    now = django_timezone.now()
    diff = value - now
    if diff.total_seconds() > 0:
        if diff.days == 1:
            return "tomorrow"
        elif diff.days == 0:
            hours = int(diff.total_seconds() / 3600)
            if hours > 0:
                return f"in {hours} hours"
            return "in less than an hour"
        else:
            return f"in {diff.days} days"
    else:
        diff = now - value
        if diff.days == 0:
            return "earlier today"
        elif diff.days == 1:
            return "yesterday"
        else:
            return f"{diff.days} days ago"
