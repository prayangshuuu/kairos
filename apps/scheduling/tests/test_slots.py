from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.scheduling.engine import get_slots, is_slot_available
from apps.scheduling.models import AvailabilityRule, EventType, Schedule

pytestmark = pytest.mark.django_db(transaction=True)


def utc_dt(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


@pytest.fixture
def owner():
    return User.objects.create_user(email="host@example.com", password="password", timezone="UTC")


@pytest.fixture
def dhaka_owner():
    return User.objects.create_user(
        email="dhaka@example.com", password="password", timezone="Asia/Dhaka"
    )


@pytest.fixture
def schedule(owner):
    s = Schedule.objects.create(user=owner, name="Default", timezone="UTC", is_default=True)
    # Mon-Fri 09:00 - 17:00
    for i in range(5):
        AvailabilityRule.objects.create(
            schedule=s, weekday=i, start_time=time(9, 0), end_time=time(17, 0)
        )
    return s


@pytest.fixture
def event_type(owner, schedule):
    return EventType.objects.create(
        owner=owner,
        slug="30-min",
        title="30 Min",
        duration_minutes=30,
        schedule=schedule,
        window_type="rolling",
        rolling_days=30,
    )


def test_clean_day(event_type):
    now = utc_dt(2026, 1, 1, 0, 0)  # Thursday
    d = date(2026, 1, 1)
    slots = get_slots(event_type, d, d, now)
    # 09:00 to 17:00 = 8 hours = 16 slots of 30 mins
    assert len(slots) == 16
    assert slots[0] == utc_dt(2026, 1, 1, 9, 0)
    assert slots[-1] == utc_dt(2026, 1, 1, 16, 30)


def test_existing_booking_removes_candidates(event_type):
    now = utc_dt(2026, 1, 1, 0, 0)
    d = date(2026, 1, 1)

    Booking.objects.create(
        host=event_type.owner,
        event_type=event_type,
        start_at=utc_dt(2026, 1, 1, 10, 0),
        end_at=utc_dt(2026, 1, 1, 10, 30),
        status="confirmed",
        invitee_name="Test",
        invitee_email="test@test.com",
        invitee_timezone="UTC",
    )

    slots = get_slots(event_type, d, d, now)
    # 16 total - 1 blocked = 15
    assert len(slots) == 15
    assert utc_dt(2026, 1, 1, 10, 0) not in slots


def test_buffers_block_following_candidate(event_type):
    event_type.buffer_after_minutes = 15
    event_type.save()

    now = utc_dt(2026, 1, 1, 0, 0)
    d = date(2026, 1, 1)

    Booking.objects.create(
        host=event_type.owner,
        event_type=event_type,
        start_at=utc_dt(2026, 1, 1, 10, 0),
        end_at=utc_dt(2026, 1, 1, 10, 30),
        status="confirmed",
        invitee_name="Test",
        invitee_email="test@test.com",
        invitee_timezone="UTC",
    )

    slots = get_slots(event_type, d, d, now)
    # 10:00 is booked. It has 15m after buffer, so 10:00 to 10:45 is blocked.
    # The 10:30 slot candidate requires 10:30 to 11:15 (dur 30 + after 15), but 10:30 is in the busy block.
    assert utc_dt(2026, 1, 1, 10, 0) not in slots
    assert utc_dt(2026, 1, 1, 10, 30) not in slots
    assert utc_dt(2026, 1, 1, 11, 0) in slots


def test_slot_excluded_at_end_of_interval_due_to_buffers(event_type):
    event_type.buffer_after_minutes = 15
    event_type.save()

    now = utc_dt(2026, 1, 1, 0, 0)
    d = date(2026, 1, 1)

    slots = get_slots(event_type, d, d, now)
    # Interval ends at 17:00.
    # Candidate 16:30 needs 30m duration + 15m buffer = 16:30 to 17:15.
    # It must be excluded because it exceeds 17:00.
    assert utc_dt(2026, 1, 1, 16, 30) not in slots
    assert utc_dt(2026, 1, 1, 16, 0) in slots


def test_minimum_notice_cuts_off(event_type):
    event_type.minimum_notice_minutes = 120  # 2 hours
    event_type.save()

    now = utc_dt(2026, 1, 1, 8, 0)  # 08:00
    d = date(2026, 1, 1)

    slots = get_slots(event_type, d, d, now)
    # Notice requires 2h, so available from 10:00.
    # 09:00 and 09:30 should be dropped.
    assert utc_dt(2026, 1, 1, 9, 0) not in slots
    assert utc_dt(2026, 1, 1, 9, 30) not in slots
    assert utc_dt(2026, 1, 1, 10, 0) in slots


def test_rolling_days_truncates(event_type):
    event_type.rolling_days = 2
    event_type.save()

    now = utc_dt(2026, 1, 1, 0, 0)  # Thursday

    # Thursday (0), Friday (1), Saturday (2)
    slots = get_slots(event_type, date(2026, 1, 1), date(2026, 1, 5), now)

    # 2 days rolling: window ends at Jan 3 00:00.
    # So slots on Jan 1 and Jan 2 should be there. Jan 3 should not (it's weekend anyway).
    # But let's check rolling exactly.
    assert slots[-1].date() == date(2026, 1, 2)


def test_max_bookings_per_day(event_type):
    event_type.max_bookings_per_day = 1
    event_type.save()

    now = utc_dt(2026, 1, 1, 0, 0)
    d = date(2026, 1, 1)

    Booking.objects.create(
        host=event_type.owner,
        event_type=event_type,
        start_at=utc_dt(2026, 1, 1, 10, 0),
        end_at=utc_dt(2026, 1, 1, 10, 30),
        status="confirmed",
        invitee_name="Test",
        invitee_email="test@test.com",
        invitee_timezone="UTC",
    )

    slots = get_slots(event_type, d, d + timedelta(days=1), now)
    # Jan 1 is capped. Jan 2 is free.
    assert len([s for s in slots if s.date() == date(2026, 1, 1)]) == 0
    assert len([s for s in slots if s.date() == date(2026, 1, 2)]) == 16


def test_dhaka_host_grouping(dhaka_owner):
    s = Schedule.objects.create(
        user=dhaka_owner, name="Dhaka", timezone="Asia/Dhaka", is_default=True
    )
    # Available Mon-Fri 09:00 - 17:00
    for i in range(5):
        AvailabilityRule.objects.create(
            schedule=s, weekday=i, start_time=time(9, 0), end_time=time(17, 0)
        )

    et = EventType.objects.create(
        owner=dhaka_owner,
        slug="dhk",
        title="Dhk",
        duration_minutes=30,
        schedule=s,
        max_bookings_per_day=1,
    )

    now = utc_dt(2026, 1, 1, 0, 0)  # Thursday

    # Booking at 23:00 UTC on Jan 1
    # This is 05:00 local time on Jan 2 in Dhaka!
    Booking.objects.create(
        host=dhaka_owner,
        event_type=et,
        start_at=utc_dt(2026, 1, 1, 23, 0),
        end_at=utc_dt(2026, 1, 1, 23, 30),
        status="confirmed",
        invitee_name="Test",
        invitee_email="test@test.com",
        invitee_timezone="UTC",
    )

    slots = get_slots(et, date(2026, 1, 1), date(2026, 1, 2), now)

    # Jan 1 should have slots. Jan 2 should have 0 slots because it hit the per-day cap locally.
    jan1_slots = [
        sl for sl in slots if sl.astimezone(ZoneInfo("Asia/Dhaka")).date() == date(2026, 1, 1)
    ]
    jan2_slots = [
        sl for sl in slots if sl.astimezone(ZoneInfo("Asia/Dhaka")).date() == date(2026, 1, 2)
    ]

    assert len(jan1_slots) == 16
    assert len(jan2_slots) == 0


def test_slot_interval_smaller_than_duration(event_type):
    event_type.duration_minutes = 60
    event_type.slot_interval_minutes = 30
    event_type.save()

    now = utc_dt(2026, 1, 1, 0, 0)
    d = date(2026, 1, 1)

    # 09:00, 09:30, 10:00 candidate starts overlap each other.
    Booking.objects.create(
        host=event_type.owner,
        event_type=event_type,
        start_at=utc_dt(2026, 1, 1, 10, 0),
        end_at=utc_dt(2026, 1, 1, 11, 0),
        status="confirmed",
        invitee_name="Test",
        invitee_email="test@test.com",
        invitee_timezone="UTC",
    )

    slots = get_slots(event_type, d, d, now)
    # 09:00 to 10:00 is fine.
    assert utc_dt(2026, 1, 1, 9, 0) in slots
    # 09:30 to 10:30 overlaps the booking, so it is removed!
    assert utc_dt(2026, 1, 1, 9, 30) not in slots
    # 10:00 is booked
    assert utc_dt(2026, 1, 1, 10, 0) not in slots
    # 10:30 overlaps 10:00-11:00 booking
    assert utc_dt(2026, 1, 1, 10, 30) not in slots
    # 11:00 is fine
    assert utc_dt(2026, 1, 1, 11, 0) in slots


def test_external_busy(event_type):
    now = utc_dt(2026, 1, 1, 0, 0)
    d = date(2026, 1, 1)

    busy = [(utc_dt(2026, 1, 1, 10, 0), utc_dt(2026, 1, 1, 11, 0))]

    slots = get_slots(event_type, d, d, now, external_busy=busy)

    assert utc_dt(2026, 1, 1, 9, 30) in slots
    assert utc_dt(2026, 1, 1, 10, 0) not in slots
    assert utc_dt(2026, 1, 1, 10, 30) not in slots
    assert utc_dt(2026, 1, 1, 11, 0) in slots


def test_split_shift(owner):
    s = Schedule.objects.create(user=owner, name="Split", timezone="UTC")
    AvailabilityRule.objects.create(
        schedule=s, weekday=3, start_time=time(9, 0), end_time=time(12, 0)
    )
    AvailabilityRule.objects.create(
        schedule=s, weekday=3, start_time=time(14, 0), end_time=time(17, 0)
    )

    et = EventType.objects.create(
        owner=owner, slug="split", title="Split", duration_minutes=30, schedule=s
    )

    now = utc_dt(2026, 1, 1, 0, 0)
    d = date(2026, 1, 1)  # Thursday

    slots = get_slots(et, d, d, now)

    # 9-12 = 6 slots
    # 14-17 = 6 slots
    assert len(slots) == 12
    assert utc_dt(2026, 1, 1, 11, 30) in slots
    assert utc_dt(2026, 1, 1, 12, 0) not in slots
    assert utc_dt(2026, 1, 1, 14, 0) in slots


def test_determinism(event_type):
    now = utc_dt(2026, 1, 1, 0, 0)
    d = date(2026, 1, 1)

    slots1 = get_slots(event_type, d, d, now)
    slots2 = get_slots(event_type, d, d, now)

    assert slots1 == slots2


def test_is_slot_available(event_type):
    now = utc_dt(2026, 1, 1, 0, 0)
    slot = utc_dt(2026, 1, 1, 10, 0)

    assert is_slot_available(event_type, slot, now) is True

    Booking.objects.create(
        host=event_type.owner,
        event_type=event_type,
        start_at=utc_dt(2026, 1, 1, 10, 0),
        end_at=utc_dt(2026, 1, 1, 10, 30),
        status="confirmed",
        invitee_name="Test",
        invitee_email="test@test.com",
        invitee_timezone="UTC",
    )

    assert is_slot_available(event_type, slot, now) is False
