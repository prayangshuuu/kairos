from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from apps.bookings.models import BLOCKING_STATUSES, Booking
from apps.scheduling.intervals import Interval, clamp, contains, normalize, subtract, intersect
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
        return u0

def expand_schedule_for_event(event_type: EventType, start_date: date, end_date: date) -> list[Interval]:
    return expand_schedule(event_type.effective_schedule, start_date, end_date)

def expand_schedule(schedule: Schedule, start_date: date, end_date: date) -> list[Interval]:
    tz = schedule.zoneinfo
    overrides = DateOverride.objects.filter(schedule=schedule, date__range=(start_date, end_date))
    overrides_by_date = defaultdict(list)
    for override in overrides:
        overrides_by_date[override.date].append(override)

    rules = AvailabilityRule.objects.filter(schedule=schedule)
    rules_by_weekday = defaultdict(list)
    for rule in rules:
        rules_by_weekday[rule.weekday].append(rule)

    intervals = []
    curr_date = start_date
    while curr_date <= end_date:
        daily_rules = []
        if curr_date in overrides_by_date:
            day_overrides = overrides_by_date[curr_date]
            if any(o.is_unavailable for o in day_overrides):
                curr_date += timedelta(days=1)
                continue
            for o in day_overrides:
                if o.start_time and o.end_time:
                    daily_rules.append((o.start_time, o.end_time))
        else:
            for r in rules_by_weekday[curr_date.weekday()]:
                daily_rules.append((r.start_time, r.end_time))

        for start_time, end_time in daily_rules:
            u_start = resolve_time(curr_date, start_time, tz)
            u_end = resolve_time(curr_date, end_time, tz)
            if u_start < u_end:
                intervals.append((u_start, u_end))
        curr_date += timedelta(days=1)

    return normalize(intervals)

def get_slots(
    event_type: EventType,
    from_date: date,
    to_date: date,
    now: datetime,
    external_busy: list[Interval] | dict[int, list[Interval]] | None = None,
    exclude_booking_id: int | None = None,
) -> list[datetime]:
    tz = event_type.effective_schedule.zoneinfo

    start_d = from_date - timedelta(days=1)
    end_d = to_date + timedelta(days=1)
    
    strategy = event_type.assignment_strategy
    
    if strategy == "single":
        hosts = [event_type.owner]
        host_schedules = {event_type.owner: event_type.effective_schedule}
        b_before = event_type.buffer_before_minutes
        b_after = event_type.buffer_after_minutes
    else:
        hosts_qs = event_type.hosts.filter(is_active=True).select_related("user")
        if strategy == "collective":
            hosts_qs = hosts_qs.filter(is_required=True)
            
        if not hosts_qs.exists():
            return []
            
        hosts = [h.user for h in hosts_qs]
        host_schedules = {h.user: h.user.get_default_schedule() for h in hosts_qs}
        b_before = max(event_type.buffer_before_minutes, max((h.buffer_before_minutes for h in hosts_qs), default=0))
        b_after = max(event_type.buffer_after_minutes, max((h.buffer_after_minutes for h in hosts_qs), default=0))

    schedule_ids = [s.id for s in host_schedules.values()]
    overrides = DateOverride.objects.filter(schedule_id__in=schedule_ids, date__range=(start_d, end_d))
    overrides_by_sched_date = defaultdict(list)
    for o in overrides:
        overrides_by_sched_date[(o.schedule_id, o.date)].append(o)

    rules = AvailabilityRule.objects.filter(schedule_id__in=schedule_ids)
    rules_by_sched_weekday = defaultdict(list)
    for r in rules:
        rules_by_sched_weekday[(r.schedule_id, r.weekday)].append(r)
        
    host_availabilities = {}
    for host in hosts:
        sched = host_schedules[host]
        host_tz = sched.zoneinfo
        intervals = []
        curr_date = start_d
        while curr_date <= end_d:
            daily_rules = []
            day_overrides = overrides_by_sched_date.get((sched.id, curr_date))
            if day_overrides:
                if any(o.is_unavailable for o in day_overrides):
                    curr_date += timedelta(days=1)
                    continue
                for o in day_overrides:
                    if o.start_time and o.end_time:
                        daily_rules.append((o.start_time, o.end_time))
            else:
                for r in rules_by_sched_weekday.get((sched.id, curr_date.weekday()), []):
                    daily_rules.append((r.start_time, r.end_time))
                    
            for start_time, end_time in daily_rules:
                u_start = resolve_time(curr_date, start_time, host_tz)
                u_end = resolve_time(curr_date, end_time, host_tz)
                if u_start < u_end:
                    intervals.append((u_start, u_end))
            curr_date += timedelta(days=1)
            
        host_availabilities[host] = normalize(intervals)

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

    fd = datetime.combine(from_date, time.min, tzinfo=tz).astimezone(UTC)
    td = datetime.combine(to_date, time.max, tzinfo=tz).astimezone(UTC)
    window_start = max(window_start, fd)
    window_end = min(window_end, td)

    min_notice = now + timedelta(minutes=event_type.minimum_notice_minutes)
    window_start = max(window_start, min_notice)

    if window_start >= window_end:
        return []

    for host in hosts:
        host_availabilities[host] = clamp(host_availabilities[host], window_start, window_end)

    limit_qs = Booking.objects.filter(
        host__in=hosts,
        status__in=BLOCKING_STATUSES,
        buffered_period__overlap=(window_start, window_end),
    )
    if exclude_booking_id:
        limit_qs = limit_qs.exclude(id=exclude_booking_id)

    busy_by_host = defaultdict(list)
    for host_id, bp in limit_qs.values_list("host_id", "buffered_period"):
        if bp.lower and bp.upper:
            busy_by_host[host_id].append((bp.lower, bp.upper))

    from apps.bookings.models import WaitlistEntry
    waitlist_qs = WaitlistEntry.objects.filter(
        event_type=event_type,
        status=WaitlistEntry.StatusChoices.OFFERED,
        offer_expires_at__gt=now,
        offered_booking_slot__gte=window_start - timedelta(minutes=1440),
        offered_booking_slot__lte=window_end + timedelta(minutes=1440),
    ).select_related('event_type')
    
    for entry in waitlist_qs:
        slot_start = entry.offered_booking_slot
        dur = timedelta(minutes=event_type.duration_minutes)
        for host in hosts:
            busy_by_host[host.id].append((slot_start - timedelta(minutes=b_before), slot_start + dur + timedelta(minutes=b_after)))

    if external_busy:
        if isinstance(external_busy, dict):
            for host in hosts:
                if host.id in external_busy:
                    busy_by_host[host.id].extend(external_busy[host.id])
        else:
            for host in hosts:
                busy_by_host[host.id].extend(external_busy)

    for host in hosts:
        if busy_by_host[host.id]:
            host_availabilities[host] = subtract(host_availabilities[host], busy_by_host[host.id])

    slot_interval = event_type.effective_slot_interval
    dur = timedelta(minutes=event_type.duration_minutes)
    
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
                pass
            elif u0 < u1:
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

    # Pre-calculate limits
    host_tzs = {host: host.zoneinfo for host in hosts}
    bookings_by_host_day = defaultdict(int)
    bookings_by_host_week = defaultdict(int)
    bookings_by_host_month = defaultdict(int)
    
    if (event_type.max_bookings_per_day or event_type.max_bookings_per_week or event_type.max_bookings_per_month) and raw_candidates:
        min_c = raw_candidates[0]
        max_c = raw_candidates[-1]
        q_start = (min_c - timedelta(days=2)).astimezone(UTC)
        q_end = (max_c + timedelta(days=32)).astimezone(UTC)
        limit_bookings_qs = Booking.objects.filter(
            host__in=hosts,
            status__in=BLOCKING_STATUSES,
            start_at__gte=q_start,
            start_at__lte=q_end,
        )
        if exclude_booking_id:
            limit_bookings_qs = limit_bookings_qs.exclude(id=exclude_booking_id)

        for b in limit_bookings_qs.values("host_id", "start_at"):
            host_id = b["host_id"]
            host = next((h for h in hosts if h.id == host_id), None)
            if not host:
                continue
            local_b = b["start_at"].astimezone(host_tzs[host])
            day_key = local_b.date()
            bookings_by_host_day[(host_id, day_key)] += 1
            month_key = (local_b.year, local_b.month)
            bookings_by_host_month[(host_id, month_key)] += 1
            days_since = (local_b.weekday() - host.week_start) % 7
            week_key = local_b.date() - timedelta(days=days_since)
            bookings_by_host_week[(host_id, week_key)] += 1

    valid_candidates_per_host = {host: set() for host in hosts}
    t_b_before = timedelta(minutes=b_before)
    t_b_after = timedelta(minutes=b_after)
    
    for u_start in raw_candidates:
        req_start = u_start - t_b_before
        req_end = u_start + dur + t_b_after
        
        for host in hosts:
            if not contains(host_availabilities[host], req_start, req_end):
                continue
                
            local_c = u_start.astimezone(host_tzs[host])
            day_key = local_c.date()
            month_key = (local_c.year, local_c.month)
            days_since = (local_c.weekday() - host.week_start) % 7
            week_key = local_c.date() - timedelta(days=days_since)

            if event_type.max_bookings_per_day and bookings_by_host_day[(host.id, day_key)] >= event_type.max_bookings_per_day:
                continue
            if event_type.max_bookings_per_week and bookings_by_host_week[(host.id, week_key)] >= event_type.max_bookings_per_week:
                continue
            if event_type.max_bookings_per_month and bookings_by_host_month[(host.id, month_key)] >= event_type.max_bookings_per_month:
                continue
                
            valid_candidates_per_host[host].add(u_start)

    if strategy == "collective" or strategy == "single":
        final_candidates = set(valid_candidates_per_host[hosts[0]])
        for host in hosts[1:]:
            final_candidates.intersection_update(valid_candidates_per_host[host])
    else: # round_robin
        final_candidates = set()
        for host in hosts:
            final_candidates.update(valid_candidates_per_host[host])
            
    return sorted(list(final_candidates))

def is_slot_available(
    event_type: EventType,
    start_at: datetime,
    now: datetime,
    external_busy: list[Interval] | dict[int, list[Interval]] | None = None,
    exclude_booking_id: int | None = None,
) -> bool:
    tz = event_type.effective_schedule.zoneinfo
    d = start_at.astimezone(tz).date()
    slots = get_slots(event_type, d, d, now, external_busy, exclude_booking_id)
    return start_at in slots
