"""Target cadence period math and dashboard staleness thresholds — pure domain logic, no I/O.

The dashboard buckets actuals into the *current* period for each cadence, **in the user's local
timezone** (DEV-PLAN slice 5 acceptance #1): a touch logged at 22:00 Kauaʻi counts toward that
Kauaʻi day, not the next UTC day. Getting this wrong silently mis-buckets every metric.

This module owns the boundary computation as a pure function over a caller-supplied *local* "now",
so it is unit-tested with no database and no wall clock. The repository obtains the local now
(``datetime.now(ZoneInfo(user_tz))`` stripped to naive) and passes the returned bounds as query
parameters. The window is left-closed/right-open ``[start, end)``; both bounds are **naive local**
datetimes — the MySQL session ``time_zone`` (already set to the user's zone in ``common/db.py``)
interprets them in local time and converts the stored UTC ``TIMESTAMP`` columns to match, so the
comparison lands on the correct local day. Weeks start **Sunday** (US convention).
"""

from __future__ import annotations

from datetime import datetime, timedelta

WEEKLY = "weekly"
MONTHLY = "monthly"
QUARTERLY = "quarterly"

#: The three target cadences (matches the ``targets.cadence`` ENUM).
CADENCES = (WEEKLY, MONTHLY, QUARTERLY)

#: An opportunity with no status change or outreach in this many days is "stale" (DEV-PLAN slice 5).
#: The repository turns this into a cutoff (``now_local - STALE_AFTER_DAYS``) it compares against
#: each opportunity's last activity.
STALE_AFTER_DAYS = 14


def _add_months(first_of_month: datetime, n: int) -> datetime:
    """Return the first-of-month ``n`` months after ``first_of_month`` (which must have day=1)."""
    month_index = first_of_month.month - 1 + n
    year = first_of_month.year + month_index // 12
    month = month_index % 12 + 1
    return first_of_month.replace(year=year, month=month, day=1)


def period_bounds(cadence: str, now_local: datetime) -> tuple[datetime, datetime]:
    """Return the ``[start, end)`` bounds of the current period containing ``now_local``.

    Parameters
    ----------
    cadence : str
        One of :data:`CADENCES` (``weekly`` / ``monthly`` / ``quarterly``).
    now_local : datetime
        The current time in the user's local timezone, as a naive datetime (tzinfo is ignored — the
        caller strips it; see the module docstring).

    Returns
    -------
    tuple of datetime
        ``(start, end)`` naive-local datetimes: ``start`` is inclusive at 00:00, ``end`` is the
        exclusive start of the next period. Weeks start Sunday.

    Raises
    ------
    ValueError
        If ``cadence`` is not a known cadence.

    Examples
    --------
    >>> from datetime import datetime
    >>> period_bounds("weekly", datetime(2026, 7, 22, 15, 30))  # a Wednesday
    (datetime.datetime(2026, 7, 19, 0, 0), datetime.datetime(2026, 7, 26, 0, 0))
    >>> period_bounds("quarterly", datetime(2026, 11, 10, 9, 0))  # Q4
    (datetime.datetime(2026, 10, 1, 0, 0), datetime.datetime(2027, 1, 1, 0, 0))
    """
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if cadence == WEEKLY:
        # weekday() is Mon=0..Sun=6; (weekday()+1) % 7 is days since the most recent Sunday.
        start = day_start - timedelta(days=(now_local.weekday() + 1) % 7)
        return start, start + timedelta(days=7)
    if cadence == MONTHLY:
        start = day_start.replace(day=1)
        return start, _add_months(start, 1)
    if cadence == QUARTERLY:
        first_month_of_quarter = ((now_local.month - 1) // 3) * 3 + 1
        start = day_start.replace(month=first_month_of_quarter, day=1)
        return start, _add_months(start, 3)
    raise ValueError(f"unknown cadence: {cadence!r}")


def stale_cutoff(now_local: datetime) -> datetime:
    """Return the local-naive instant before which an opportunity's last activity makes it stale.

    An opportunity whose most recent status event or outreach is older than this cutoff (and which
    is not closed) is stale.

    Parameters
    ----------
    now_local : datetime
        The current time in the user's local timezone (naive).

    Returns
    -------
    datetime
        ``now_local - STALE_AFTER_DAYS`` days.
    """
    return now_local - timedelta(days=STALE_AFTER_DAYS)
