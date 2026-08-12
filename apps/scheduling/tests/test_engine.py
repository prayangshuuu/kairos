from datetime import UTC, date, datetime, time, timedelta

import pytest

from apps.accounts.models import User
from apps.scheduling.engine import expand_schedule, expand_schedule_for_event
from apps.scheduling.models import AvailabilityRule, DateOverride, EventType, Schedule

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def owner():
    return User.objects.create_user(email="test@example.com", password="password", timezone="UTC")


@pytest.fixture
def schedule(owner):
    return Schedule.objects.create(
        user=owner, name="Test Schedule", timezone="Asia/Dhaka", is_default=True
    )


@pytest.fixture
def event_type(owner):
    return EventType.objects.create(
        owner=owner, slug="test", title="Test Event", duration_minutes=30
    )


def test_plain_dhaka_schedule(schedule):
    # Asia/Dhaka +06:00
    # Mon-Fri 09:00-17:00
    for day in range(5):
        AvailabilityRule.objects.create(
            schedule=schedule, weekday=day, start_time=time(9, 0), end_time=time(17, 0)
        )

    # Oct 1, 2026 is Thursday (weekday 3)
    d = date(2026, 10, 1)
    intervals = expand_schedule(schedule, d, d)

    assert len(intervals) == 1
    start, end = intervals[0]

    # 09:00 Dhaka = 03:00 UTC
    # 17:00 Dhaka = 11:00 UTC
    assert start == datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 1, 11, 0, tzinfo=UTC)
    assert (end - start) == timedelta(hours=8)


def test_new_york_dst_spring_forward(owner):
    # America/New_York
    # Spring forward is March 8, 2026 (2:00 AM -> 3:00 AM)
    schedule = Schedule.objects.create(user=owner, name="NY", timezone="America/New_York")

    # Sunday (weekday 6) 09:00 - 17:00
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=6, start_time=time(9, 0), end_time=time(17, 0)
    )

    d = date(2026, 3, 8)
    intervals = expand_schedule(schedule, d, d)

    assert len(intervals) == 1
    start, end = intervals[0]

    # At 09:00 EDT (UTC-4) -> 13:00 UTC
    # At 17:00 EDT (UTC-4) -> 21:00 UTC
    assert start == datetime(2026, 3, 8, 13, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 8, 21, 0, tzinfo=UTC)
    # The day has 7 hours between 2 AM and 10 AM, but 09:00-17:00 is exactly 8 hours because both are post-transition!
    # Wait, the prompt says "a 09:00-17:00 rule yields an 8-hour interval on ordinary days, but 7 hours on spring-forward day".
    # Wait, if a shift is 00:00 to 08:00, it's 7 hours! 09:00 to 17:00 is 8 hours because it's entirely after the gap!
    # Let me add a 00:00 to 08:00 rule to see the 7-hour effect.
    pass


def test_new_york_dst_spring_forward_7_hours(owner):
    schedule = Schedule.objects.create(user=owner, name="NY", timezone="America/New_York")
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=6, start_time=time(0, 0), end_time=time(8, 0)
    )

    d = date(2026, 3, 8)
    intervals = expand_schedule(schedule, d, d)

    assert len(intervals) == 1
    start, end = intervals[0]
    assert (end - start) == timedelta(hours=7)


def test_new_york_dst_fall_back_9_hours(owner):
    # America/New_York
    # Fall back is November 1, 2026 (2:00 AM -> 1:00 AM)
    schedule = Schedule.objects.create(user=owner, name="NY", timezone="America/New_York")
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=6, start_time=time(0, 0), end_time=time(8, 0)
    )

    d = date(2026, 11, 1)
    intervals = expand_schedule(schedule, d, d)

    assert len(intervals) == 1
    start, end = intervals[0]
    assert (end - start) == timedelta(hours=9)


def test_new_york_nonexistent_time(owner):
    # Spring forward March 8, 2026 (2:00 AM -> 3:00 AM)
    schedule = Schedule.objects.create(user=owner, name="NY", timezone="America/New_York")
    # Rule starting at 02:30 (inside the gap) and ending at 04:00
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=6, start_time=time(2, 30), end_time=time(4, 0)
    )

    d = date(2026, 3, 8)
    intervals = expand_schedule(schedule, d, d)

    assert len(intervals) == 1
    start, end = intervals[0]

    # Gap is skipped at exactly 07:00 UTC (02:00 EST / 03:00 EDT)
    # The start time should shift to 07:00 UTC
    assert start == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
    # 04:00 EDT is 08:00 UTC
    assert end == datetime(2026, 3, 8, 8, 0, tzinfo=UTC)


def test_kathmandu_offset(owner):
    # Asia/Kathmandu is +05:45
    schedule = Schedule.objects.create(user=owner, name="KTM", timezone="Asia/Kathmandu")
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=0, start_time=time(10, 0), end_time=time(11, 0)
    )

    d = date(2026, 1, 5)  # Monday
    intervals = expand_schedule(schedule, d, d)

    assert len(intervals) == 1
    start, end = intervals[0]

    # 10:00 KTM -> 04:15 UTC
    assert start == datetime(2026, 1, 5, 4, 15, tzinfo=UTC)


def test_lord_howe_dst(owner):
    # Australia/Lord_Howe has 30-min DST shift (+10:30 to +11:00)
    # Spring forward is October 4, 2026 (02:00 -> 02:30)
    schedule = Schedule.objects.create(user=owner, name="LH", timezone="Australia/Lord_Howe")
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=6, start_time=time(0, 0), end_time=time(4, 0)
    )

    d = date(2026, 10, 4)
    intervals = expand_schedule(schedule, d, d)

    assert len(intervals) == 1
    start, end = intervals[0]

    # Normal is 4 hours, missing 30 mins -> 3.5 hours
    assert (end - start) == timedelta(hours=3, minutes=30)


def test_chatham_extreme(owner):
    # Pacific/Chatham is +12:45/+13:45
    schedule = Schedule.objects.create(user=owner, name="CH", timezone="Pacific/Chatham")
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=6, start_time=time(12, 0), end_time=time(13, 0)
    )

    # Sunday, Sep 27, 2026 (Spring forward)
    d = date(2026, 9, 27)
    intervals = expand_schedule(schedule, d, d)
    assert len(intervals) == 1


def test_date_override_unavailable(schedule):
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=3, start_time=time(9, 0), end_time=time(17, 0)
    )
    d = date(2026, 10, 1)  # Thursday

    DateOverride.objects.create(schedule=schedule, date=d, is_unavailable=True)

    intervals = expand_schedule(schedule, d, d)
    assert intervals == []


def test_date_override_custom_hours(schedule):
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=3, start_time=time(9, 0), end_time=time(17, 0)
    )
    d = date(2026, 10, 1)  # Thursday

    DateOverride.objects.create(
        schedule=schedule, date=d, start_time=time(10, 0), end_time=time(12, 0)
    )
    DateOverride.objects.create(
        schedule=schedule, date=d, start_time=time(14, 0), end_time=time(16, 0)
    )

    intervals = expand_schedule(schedule, d, d)
    assert len(intervals) == 2
    # Check UTC offsets (+6)
    assert intervals[0][0].hour == 4
    assert intervals[1][0].hour == 8


def test_split_shifts(schedule):
    d = date(2026, 10, 1)  # Thursday
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=3, start_time=time(9, 0), end_time=time(12, 0)
    )
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=3, start_time=time(14, 0), end_time=time(17, 0)
    )

    intervals = expand_schedule(schedule, d, d)
    assert len(intervals) == 2


def test_schedule_timezone_wins(owner):
    # Owner timezone is UTC
    schedule = Schedule.objects.create(user=owner, name="Dhaka", timezone="Asia/Dhaka")
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=3, start_time=time(9, 0), end_time=time(17, 0)
    )

    d = date(2026, 10, 1)
    intervals = expand_schedule(schedule, d, d)
    # Should use Dhaka (+6)
    assert intervals[0][0].hour == 3


def test_expand_schedule_for_event(event_type, schedule):
    # The event type has no specific schedule, so it will fall back to default schedule
    assert event_type.effective_schedule == schedule
    AvailabilityRule.objects.create(
        schedule=schedule, weekday=3, start_time=time(9, 0), end_time=time(17, 0)
    )

    d = date(2026, 10, 1)
    intervals = expand_schedule_for_event(event_type, d, d)
    assert len(intervals) == 1
