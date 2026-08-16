from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from apps.bookings.models import BLOCKING_STATUSES, Booking
from apps.scheduling.intervals import Interval, clamp, contains, normalize, subtract
from apps.scheduling.models import AvailabilityRule, DateOverride, EventType, Schedule


def resolve_time(d: date, t: time, tz: ZoneInfo) -> datetime:
    """
    Combines a date and a time into a UTC datetime, handling DST correctly.
    - Ambiguous times (fall back) resolve to the first occurrence (fold=0).
    - Nonexistent times (spring forward) map to the exact UTC instant of the transition.
    """
    dt_naive = datetime.combine(d, t)
    dt0 = dt_naive.replace(tzinfo=tz, fold=0)
    dt1 = dt_naive.replace(tzinfo=tz, fold=1)

    u0 = dt0.astimezone(UTC)
    u1 = dt1.astimezone(UTC)

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
    overrides = DateOverride.objects.filter(schedule=schedule, date__range=(start_date, end_date))
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


def expand_schedule_for_event(
    event_type: EventType, start_date: date, end_date: date
) -> list[Interval]:
    """
    Resolves the effective schedule for an event type and expands it.
    """
    return expand_schedule(event_type.effective_schedule, start_date, end_date)


def get_slots(
    event_type: EventType,
    from_date: date,
    to_date: date,
    now: datetime,
    external_busy: list[Interval] | None = None,
    exclude_booking_id: int | None = None,
) -> list[datetime]:
    """
    Produces bookable start times.
    Performance: Issues 2 queries for schedule, 1 query for overlapping bookings, 1 query for limits (if any).
    Total query count <= 4, independent of window length.
    """
    tz = event_type.effective_schedule.zoneinfo

    # 1. Expand availability
    # Pad by one day either side
    start_d = from_date - timedelta(days=1)
    end_d = to_date + timedelta(days=1)
    available_intervals = expand_schedule_for_event(event_type, start_d, end_d)

    # 2. Apply booking window
    window_start = now

    if event_type.window_type == "rolling":
        if event_type.rolling_business_days_only:
            days_added = 0
            cur = now
            while days_added < event_type.rolling_days:
                cur += timedelta(days=1)
                if cur.astimezone(tz).weekday() < 5:
                    days_added += 1
            window_end = cur
        else:
            window_end = now + timedelta(days=event_type.rolling_days)
    elif event_type.window_type == "fixed_range":
        rs = datetime.combine(event_type.range_start, time.min, tzinfo=tz).astimezone(UTC)
        re = datetime.combine(event_type.range_end, time.max, tzinfo=tz).astimezone(UTC)
        window_start = max(window_start, rs)
        window_end = re
    else:
        window_end = datetime.max.replace(tzinfo=UTC)

    # Clamp to requested from_date/to_date (mapped to schedule timezone)
    fd = datetime.combine(from_date, time.min, tzinfo=tz).astimezone(UTC)
    td = datetime.combine(to_date, time.max, tzinfo=tz).astimezone(UTC)

    window_start = max(window_start, fd)
    window_end = min(window_end, td)

    # 3. Apply minimum notice
    min_notice = now + timedelta(minutes=event_type.minimum_notice_minutes)
    window_start = max(window_start, min_notice)

    if window_start >= window_end:
        return []

    available_intervals = clamp(available_intervals, window_start, window_end)
    if not available_intervals:
        return []

    # 4. Subtract busy time
    limit_qs = Booking.objects.filter(
        host=event_type.owner,
        status__in=BLOCKING_STATUSES,
        buffered_period__overlap=(window_start, window_end),
    )
    if exclude_booking_id:
        limit_qs = limit_qs.exclude(id=exclude_booking_id)

    busy_intervals = []
    for bp in limit_qs.values_list("buffered_period", flat=True):
        if bp.lower and bp.upper:
            busy_intervals.append((bp.lower, bp.upper))

    # Also block waitlist offered slots
    from apps.bookings.models import WaitlistEntry
    waitlist_qs = WaitlistEntry.objects.filter(
        event_type__owner=event_type.owner,
        status=WaitlistEntry.StatusChoices.OFFERED,
        offer_expires_at__gt=now,
        offered_booking_slot__gte=window_start - timedelta(minutes=1440),
        offered_booking_slot__lte=window_end + timedelta(minutes=1440),
    ).select_related('event_type')
    
    for entry in waitlist_qs:
        slot_start = entry.offered_booking_slot
        b_before = timedelta(minutes=entry.event_type.buffer_before_minutes)
        dur = timedelta(minutes=entry.event_type.duration_minutes)
        b_after = timedelta(minutes=entry.event_type.buffer_after_minutes)
        busy_intervals.append((slot_start - b_before, slot_start + dur + b_after))

    if external_busy:
        busy_intervals.extend(external_busy)

    available_intervals = subtract(available_intervals, busy_intervals)
    if not available_intervals:
        return []

    # 5. Slice into candidate start times
    slot_interval = event_type.effective_slot_interval
    dur = timedelta(minutes=event_type.duration_minutes)
    b_before = timedelta(minutes=event_type.buffer_before_minutes)
    b_after = timedelta(minutes=event_type.buffer_after_minutes)

    local_window_start = window_start.astimezone(tz)
    local_window_end = window_end.astimezone(tz)

    curr_d = local_window_start.date()
    end_grid_date = local_window_end.date() + timedelta(days=1)

    raw_candidates = []
    while curr_d <= end_grid_date:
        t_minutes = 0
        while t_minutes < 1440:
            h = t_minutes // 60
            m = t_minutes % 60
            t = time(h, m)

            dt_naive = datetime.combine(curr_d, t)
            dt0 = dt_naive.replace(tzinfo=tz, fold=0)
            u0 = dt0.astimezone(UTC)

            dt1 = dt_naive.replace(tzinfo=tz, fold=1)
            u1 = dt1.astimezone(UTC)

            if u0 > u1:
                # Nonexistent time, no slot can start exactly here.
                pass
            elif u0 < u1:
                # Ambiguous time
                if window_start <= u0 < window_end:
                    raw_candidates.append(u0)
                if window_start <= u1 < window_end:
                    raw_candidates.append(u1)
            else:
                if window_start <= u0 < window_end:
                    raw_candidates.append(u0)

            t_minutes += slot_interval

        curr_d += timedelta(days=1)

    raw_candidates = sorted(set(raw_candidates))

    valid_candidates = []
    for u_start in raw_candidates:
        req_start = u_start - b_before
        req_end = u_start + dur + b_after

        if contains(available_intervals, req_start, req_end):
            valid_candidates.append(u_start)

    if not valid_candidates:
        return []

    # 6. Apply booking limits
    if (
        event_type.max_bookings_per_day
        or event_type.max_bookings_per_week
        or event_type.max_bookings_per_month
    ):
        host_tz = event_type.owner.zoneinfo
        min_c = valid_candidates[0].astimezone(host_tz)
        max_c = valid_candidates[-1].astimezone(host_tz)

        month_start = min_c.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        next_month = max_c.replace(day=28) + timedelta(days=4)
        month_end = next_month - timedelta(days=next_month.day)
        month_end = month_end.replace(hour=23, minute=59, second=59)

        q_start = month_start.astimezone(UTC)
        q_end = month_end.astimezone(UTC)

        limit_bookings_qs = Booking.objects.filter(
            host=event_type.owner,
            status__in=BLOCKING_STATUSES,
            start_at__gte=q_start,
            start_at__lte=q_end,
        )
        if exclude_booking_id:
            limit_bookings_qs = limit_bookings_qs.exclude(id=exclude_booking_id)

        limit_bookings = list(limit_bookings_qs.values_list("start_at", flat=True))

        bookings_by_day = defaultdict(int)
        bookings_by_week = defaultdict(int)
        bookings_by_month = defaultdict(int)

        week_start_day = event_type.owner.week_start

        for b_start in limit_bookings:
            local_b = b_start.astimezone(host_tz)

            day_key = local_b.date()
            bookings_by_day[day_key] += 1

            month_key = (local_b.year, local_b.month)
            bookings_by_month[month_key] += 1

            days_since = (local_b.weekday() - week_start_day) % 7
            week_key = local_b.date() - timedelta(days=days_since)
            bookings_by_week[week_key] += 1

        final_candidates = []
        for u_start in valid_candidates:
            local_c = u_start.astimezone(host_tz)
            day_key = local_c.date()
            month_key = (local_c.year, local_c.month)
            days_since = (local_c.weekday() - week_start_day) % 7
            week_key = local_c.date() - timedelta(days=days_since)

            if (
                event_type.max_bookings_per_day
                and bookings_by_day[day_key] >= event_type.max_bookings_per_day
            ):
                continue
            if (
                event_type.max_bookings_per_week
                and bookings_by_week[week_key] >= event_type.max_bookings_per_week
            ):
                continue
            if (
                event_type.max_bookings_per_month
                and bookings_by_month[month_key] >= event_type.max_bookings_per_month
            ):
                continue

            final_candidates.append(u_start)

        valid_candidates = final_candidates

    return valid_candidates


def is_slot_available(
    event_type: EventType,
    start_at: datetime,
    now: datetime,
    external_busy: list[Interval] | None = None,
    exclude_booking_id: int | None = None,
) -> bool:
    """
    Validates one specific start time by reusing get_slots for that single day.
    """
    tz = event_type.effective_schedule.zoneinfo
    d = start_at.astimezone(tz).date()
    slots = get_slots(event_type, d, d, now, external_busy, exclude_booking_id)
    return start_at in slots
