"""
Pure Python set-arithmetic over time intervals.

Every interval is a tuple (start, end) of timezone-aware datetimes in UTC.
All intervals are HALF-OPEN: [start, end). An interval ending at 10:00 and one starting
at 10:00 do not overlap.
"""

from datetime import datetime, timedelta

Interval = tuple[datetime, datetime]


def _assert_utc(dt: datetime) -> None:
    if (
        dt.tzinfo is None
        or dt.tzinfo.utcoffset(dt) is None
        or dt.tzinfo.utcoffset(dt).total_seconds() != 0
    ):
        raise ValueError("datetime must be timezone-aware and in UTC")


def normalize(intervals: list[Interval]) -> list[Interval]:
    """
    Drop zero-length and inverted intervals, sort by start, then merge overlapping
    AND exactly-adjacent intervals into single spans.
    """
    valid_intervals = []
    for start, end in intervals:
        _assert_utc(start)
        _assert_utc(end)
        if start < end:
            valid_intervals.append((start, end))

    if not valid_intervals:
        return []

    valid_intervals.sort(key=lambda x: x[0])

    merged = [valid_intervals[0]]
    for current in valid_intervals[1:]:
        prev_start, prev_end = merged[-1]
        curr_start, curr_end = current

        if curr_start <= prev_end:
            # Overlapping or adjacent, merge them
            merged[-1] = (prev_start, max(prev_end, curr_end))
        else:
            merged.append(current)

    return merged


# Alias for normalize
merge = normalize


def subtract(base: list[Interval], blocks: list[Interval]) -> list[Interval]:
    """
    Remove all block time from the base intervals.
    """
    base = normalize(base)
    blocks = normalize(blocks)

    if not blocks or not base:
        return base

    result = []
    block_idx = 0
    n_blocks = len(blocks)

    for b_start, b_end in base:
        curr_start = b_start

        # Advance block_idx to the first block that doesn't end before this base starts
        while block_idx < n_blocks and blocks[block_idx][1] <= curr_start:
            block_idx += 1

        temp_idx = block_idx
        while temp_idx < n_blocks and curr_start < b_end:
            blk_start, blk_end = blocks[temp_idx]

            if blk_start >= b_end:
                # This block and all subsequent blocks are entirely after this base interval
                break

            if blk_start > curr_start:
                # Add the non-blocked prefix
                result.append((curr_start, blk_start))

            # Move the start past this block
            curr_start = max(curr_start, blk_end)
            temp_idx += 1

        if curr_start < b_end:
            result.append((curr_start, b_end))

    return normalize(result)


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """
    The time present in both sets. Implement efficiently with a two-pointer sweep.
    """
    a = normalize(a)
    b = normalize(b)

    result = []
    i, j = 0, 0

    while i < len(a) and j < len(b):
        a_start, a_end = a[i]
        b_start, b_end = b[j]

        # Calculate intersection
        start = max(a_start, b_start)
        end = min(a_end, b_end)

        if start < end:
            result.append((start, end))

        # Move the pointer of the interval that ends first
        if a_end < b_end:
            i += 1
        else:
            j += 1

    return normalize(result)


def clamp(intervals: list[Interval], start: datetime, end: datetime) -> list[Interval]:
    """
    Trim the set to the window [start, end). Drop anything falling entirely outside.
    """
    _assert_utc(start)
    _assert_utc(end)
    return intersect(intervals, [(start, end)])


def total_duration(intervals: list[Interval]) -> timedelta:
    """
    Sum of the normalized intervals.
    """
    normalized = normalize(intervals)
    return sum((end - start for start, end in normalized), timedelta())


def contains(intervals: list[Interval], start: datetime, end: datetime) -> bool:
    """
    True if [start, end) fits entirely within a single normalized interval.
    """
    _assert_utc(start)
    _assert_utc(end)

    if start >= end:
        return False

    normalized = normalize(intervals)

    for int_start, int_end in normalized:
        if int_start <= start and int_end >= end:
            return True
        elif int_start > start:
            # Since it's sorted, we can stop early
            break

    return False
