import pytest
from datetime import datetime, timedelta, timezone
from apps.scheduling.intervals import (
    normalize, merge, subtract, intersect, clamp, total_duration, contains, _assert_utc
)

def utc_dt(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)

def test_assert_utc_accepts_utc():
    _assert_utc(utc_dt(2026, 1, 1, 10))

def test_assert_utc_rejects_naive():
    with pytest.raises(ValueError, match="timezone-aware and in UTC"):
        _assert_utc(datetime(2026, 1, 1, 10))

def test_assert_utc_rejects_non_utc():
    tz = timezone(timedelta(hours=2))
    dt = datetime(2026, 1, 1, 10, tzinfo=tz)
    with pytest.raises(ValueError, match="timezone-aware and in UTC"):
        _assert_utc(dt)

def test_normalize_discards_invalid():
    t1 = utc_dt(2026, 1, 1, 10)
    t2 = utc_dt(2026, 1, 1, 11)
    # zero-length
    res = normalize([(t1, t1)])
    assert res == []
    # inverted
    res = normalize([(t2, t1)])
    assert res == []

def test_normalize_coalesces_adjacent_and_overlapping():
    t1 = utc_dt(2026, 1, 1, 9)
    t2 = utc_dt(2026, 1, 1, 10)
    t3 = utc_dt(2026, 1, 1, 11)
    t4 = utc_dt(2026, 1, 1, 12)
    t5 = utc_dt(2026, 1, 1, 13)
    
    intervals = [
        (t1, t2),  # 9-10
        (t2, t3),  # 10-11 (adjacent)
        (t2, t4),  # 10-12 (overlapping)
        (t1, t5)   # 9-13 (subsumes all)
    ]
    assert normalize(intervals) == [(t1, t5)]
    assert merge(intervals) == [(t1, t5)]

def test_subtract_empty():
    t1 = utc_dt(2026, 1, 1, 9)
    t2 = utc_dt(2026, 1, 1, 10)
    
    # Both empty
    assert subtract([], []) == []
    # Empty base
    assert subtract([], [(t1, t2)]) == []
    # Empty blocks
    assert subtract([(t1, t2)], []) == [(t1, t2)]

def test_subtract_splits_interval():
    # Base: 9-12, Block: 10-11 -> Result: 9-10, 11-12
    t9 = utc_dt(2026, 1, 1, 9)
    t10 = utc_dt(2026, 1, 1, 10)
    t11 = utc_dt(2026, 1, 1, 11)
    t12 = utc_dt(2026, 1, 1, 12)
    
    base = [(t9, t12)]
    blocks = [(t10, t11)]
    assert subtract(base, blocks) == [(t9, t10), (t11, t12)]

def test_subtract_spanning_consecutive():
    # Base: 9-10, 11-12, 13-14
    # Block: 9:30 - 13:30
    # Result: 9-9:30, 13:30-14
    t9 = utc_dt(2026, 1, 1, 9)
    t930 = utc_dt(2026, 1, 1, 9, 30)
    t10 = utc_dt(2026, 1, 1, 10)
    t11 = utc_dt(2026, 1, 1, 11)
    t12 = utc_dt(2026, 1, 1, 12)
    t13 = utc_dt(2026, 1, 1, 13)
    t1330 = utc_dt(2026, 1, 1, 13, 30)
    t14 = utc_dt(2026, 1, 1, 14)
    
    base = [(t9, t10), (t11, t12), (t13, t14)]
    blocks = [(t930, t1330)]
    assert subtract(base, blocks) == [(t9, t930), (t1330, t14)]

def test_subtract_exact_boundary():
    # Base: 9-10. Block: 10-11
    # Result: 9-10 (no effect)
    t9 = utc_dt(2026, 1, 1, 9)
    t10 = utc_dt(2026, 1, 1, 10)
    t11 = utc_dt(2026, 1, 1, 11)
    
    assert subtract([(t9, t10)], [(t10, t11)]) == [(t9, t10)]

def test_intersect():
    t9 = utc_dt(2026, 1, 1, 9)
    t10 = utc_dt(2026, 1, 1, 10)
    t11 = utc_dt(2026, 1, 1, 11)
    t12 = utc_dt(2026, 1, 1, 12)
    
    # Disjoint
    assert intersect([(t9, t10)], [(t11, t12)]) == []
    
    # Identical
    assert intersect([(t9, t10)], [(t9, t10)]) == [(t9, t10)]
    
    # Partial
    assert intersect([(t9, t11)], [(t10, t12)]) == [(t10, t11)]

def test_contains():
    t9 = utc_dt(2026, 1, 1, 9)
    t10 = utc_dt(2026, 1, 1, 10)
    t11 = utc_dt(2026, 1, 1, 11)
    t12 = utc_dt(2026, 1, 1, 12)
    
    intervals = [(t9, t10), (t11, t12)]
    
    # Fits exactly in first
    assert contains(intervals, t9, t10) is True
    # Fits inside first
    assert contains(intervals, utc_dt(2026, 1, 1, 9, 15), utc_dt(2026, 1, 1, 9, 45)) is True
    # Straddles gap
    assert contains(intervals, utc_dt(2026, 1, 1, 9, 30), utc_dt(2026, 1, 1, 11, 30)) is False
    # Entirely outside
    assert contains(intervals, utc_dt(2026, 1, 1, 13), utc_dt(2026, 1, 1, 14)) is False

def test_clamp():
    t8 = utc_dt(2026, 1, 1, 8)
    t9 = utc_dt(2026, 1, 1, 9)
    t10 = utc_dt(2026, 1, 1, 10)
    t11 = utc_dt(2026, 1, 1, 11)
    t12 = utc_dt(2026, 1, 1, 12)
    t13 = utc_dt(2026, 1, 1, 13)
    
    intervals = [(t8, t10), (t11, t13)]
    
    # Clamp to 9-12
    assert clamp(intervals, t9, t12) == [(t9, t10), (t11, t12)]
def test_subtract_block_after_base():
    t9 = utc_dt(2026, 1, 1, 9)
    t10 = utc_dt(2026, 1, 1, 10)
    t11 = utc_dt(2026, 1, 1, 11)
    t12 = utc_dt(2026, 1, 1, 12)
    # Block is entirely after base, triggering blk_start >= b_end break
    assert subtract([(t9, t10)], [(t11, t12)]) == [(t9, t10)]

def test_contains_inverted():
    t9 = utc_dt(2026, 1, 1, 9)
    t10 = utc_dt(2026, 1, 1, 10)
    intervals = [(t9, t10)]
    # Inverted span triggers start >= end
    assert contains(intervals, t10, t9) is False
def test_total_duration():
    t9 = utc_dt(2026, 1, 1, 9)
    t10 = utc_dt(2026, 1, 1, 10)
    t11 = utc_dt(2026, 1, 1, 11)
    
    intervals = [(t9, t10), (t11, t11 + timedelta(minutes=30))]
    assert total_duration(intervals) == timedelta(hours=1, minutes=30)
