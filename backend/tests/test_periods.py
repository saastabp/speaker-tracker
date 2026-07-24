"""Unit tests for target-cadence period math — pure, no database, no wall clock.

Pin the current-period boundary computation behind the dashboard's actual-vs-target buckets
(DEV-PLAN slice 5 acceptance #1). Weeks start Monday; month/quarter boundaries roll the year over
correctly. The window is left-closed / right-open.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.periods import (
    STALE_AFTER_DAYS,
    period_bounds,
    stale_cutoff,
)


@pytest.mark.parametrize(
    ("now", "start", "end"),
    [
        # Wednesday → Sunday-of-week .. next Sunday.
        (datetime(2026, 7, 22, 15, 30), datetime(2026, 7, 19), datetime(2026, 7, 26)),
        # Sunday itself is the start of its own week.
        (datetime(2026, 7, 19, 9, 0), datetime(2026, 7, 19), datetime(2026, 7, 26)),
        # Saturday is the last day of the Sunday-started week.
        (datetime(2026, 7, 25, 23, 59), datetime(2026, 7, 19), datetime(2026, 7, 26)),
        # Week spanning a month/year boundary (2026-12-31 is a Thursday).
        (datetime(2026, 12, 31, 12, 0), datetime(2026, 12, 27), datetime(2027, 1, 3)),
    ],
)
def test_weekly_bounds(now: datetime, start: datetime, end: datetime) -> None:
    assert period_bounds("weekly", now) == (start, end)


@pytest.mark.parametrize(
    ("now", "start", "end"),
    [
        (datetime(2026, 7, 22, 15, 30), datetime(2026, 7, 1), datetime(2026, 8, 1)),
        # December rolls the year over.
        (datetime(2026, 12, 15, 8, 0), datetime(2026, 12, 1), datetime(2027, 1, 1)),
        # February end (non-leap year) → next month is March 1.
        (datetime(2026, 2, 28, 23, 0), datetime(2026, 2, 1), datetime(2026, 3, 1)),
    ],
)
def test_monthly_bounds(now: datetime, start: datetime, end: datetime) -> None:
    assert period_bounds("monthly", now) == (start, end)


@pytest.mark.parametrize(
    ("now", "start", "end"),
    [
        (datetime(2026, 2, 10), datetime(2026, 1, 1), datetime(2026, 4, 1)),  # Q1
        (datetime(2026, 5, 10), datetime(2026, 4, 1), datetime(2026, 7, 1)),  # Q2
        (datetime(2026, 7, 22), datetime(2026, 7, 1), datetime(2026, 10, 1)),  # Q3
        (
            datetime(2026, 11, 10, 9, 0),
            datetime(2026, 10, 1),
            datetime(2027, 1, 1),
        ),  # Q4 → next year
    ],
)
def test_quarterly_bounds(now: datetime, start: datetime, end: datetime) -> None:
    assert period_bounds("quarterly", now) == (start, end)


def test_bounds_start_at_midnight_regardless_of_time_of_day() -> None:
    _, _ = period_bounds("weekly", datetime(2026, 7, 22, 23, 59, 59))
    start, end = period_bounds("weekly", datetime(2026, 7, 22, 23, 59, 59))
    assert start.hour == start.minute == start.second == start.microsecond == 0
    assert end.hour == end.minute == 0


def test_window_is_left_closed_right_open() -> None:
    start, end = period_bounds("monthly", datetime(2026, 7, 22))
    # A touch exactly at `start` is in-window; one exactly at `end` belongs to the next period.
    assert start <= datetime(2026, 7, 1, 0, 0)
    assert end == datetime(2026, 8, 1, 0, 0)


def test_unknown_cadence_raises() -> None:
    with pytest.raises(ValueError):
        period_bounds("daily", datetime(2026, 7, 22))


def test_stale_cutoff_is_two_weeks_back() -> None:
    assert STALE_AFTER_DAYS == 14
    now = datetime(2026, 7, 22, 15, 30)
    assert stale_cutoff(now) == now - timedelta(days=14)
