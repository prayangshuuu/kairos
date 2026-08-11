from datetime import date, time, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict

from apps.scheduling.models import Schedule, DateOverride, AvailabilityRule
from apps.scheduling.intervals import Interval, normalize

def resolve_time(d: date, t: time, tz: ZoneInfo) -> datetime:
    """
    Combines a date and a time into a UTC datetime, handling DST correctly.
    - Ambiguous times (fall back) resolve to the first occurrence (fold=0).
    - Nonexistent times (spring forward) map to the exact UTC instant of the transition.
    """
    dt_naive = datetime.combine(d, t)
    dt0 = dt_naive.replace(tzinfo=tz, fold=0)
    dt1 = dt_naive.replace(tzinfo=tz, fold=1)
    
    u0 = dt0.astimezone(timezone.utc)
    u1 = dt1.astimezone(timezone.utc)
    
    if u0 > u1:
        # Nonexistent time. u1 is pre-transition offset, u0 is post-transition offset.
        # Find the exact transition moment in UTC using binary search.
        low = u1
        high = u0
        off0 = low.astimezone(tz).utcoffset()
        
        while (high - low).total_seconds() > 1:
            seconds = int((high - low).total_seconds()) // 2
            mid = low + timedelta(seconds=seconds)
            if mid.astimezone(tz).utcoffset() == off0:
                low = mid
            else:
                high = mid
                
        return high
    else:
        # Valid or ambiguous time. fold=0 ensures first occurrence.
        return u0

def expand_schedule(schedule: Schedule, start_date: date, end_date: date) -> list[Interval]:
    """
    Turns stored weekly availability rules and overrides into concrete UTC intervals.
    Issues exactly 2 queries regardless of range length.
    """
    tz = schedule.zoneinfo
    
    # 1. Load overrides and rules in exactly two queries.
    overrides = DateOverride.objects.filter(
        schedule=schedule, 
        date__range=(start_date, end_date)
    )
    overrides_by_date = defaultdict(list)
    for override in overrides:
        overrides_by_date[override.date].append(override)
        
    rules = AvailabilityRule.objects.filter(schedule=schedule)
    rules_by_weekday = defaultdict(list)
    for rule in rules:
        rules_by_weekday[rule.weekday].append(rule)
        
    intervals = []
    
    # 2. Iterate dates
    curr_date = start_date
    while curr_date <= end_date:
        daily_rules = []
        
        if curr_date in overrides_by_date:
            day_overrides = overrides_by_date[curr_date]
            # a. If any override is unavailable, skip the date entirely
            if any(o.is_unavailable for o in day_overrides):
                curr_date += timedelta(days=1)
                continue
                
            # b. Use override time ranges
            for o in day_overrides:
                if o.start_time and o.end_time:
                    daily_rules.append((o.start_time, o.end_time))
        else:
            # c. Use weekly rules matching weekday (0 is Monday)
            for r in rules_by_weekday[curr_date.weekday()]:
                daily_rules.append((r.start_time, r.end_time))
                
        # 3. Build UTC intervals
        for start_time, end_time in daily_rules:
            u_start = resolve_time(curr_date, start_time, tz)
            u_end = resolve_time(curr_date, end_time, tz)
            
            # 5. Drop intervals that fall entirely inside a DST gap (u_start >= u_end)
            if u_start < u_end:
                intervals.append((u_start, u_end))
                
        curr_date += timedelta(days=1)
        
    # 5. Return normalized intervals
    return normalize(intervals)

def expand_schedule_for_event(event_type, start_date: date, end_date: date) -> list[Interval]:
    """
    Resolves the effective schedule for an event type and expands it.
    """
    return expand_schedule(event_type.effective_schedule, start_date, end_date)
