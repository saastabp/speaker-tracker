"""Pure unit tests for the follow-up reminder scheduling rules (slice 7 checkpoint B).

No database, no AWS, no clock — ``core.follow_ups`` takes "now" as an argument precisely so these
run as plain function calls. The rules under test are the ones the rest of the slice depends on:
the local-wall-clock expression (decision 2), the desired-state equality that decides when a
schedule must be replaced (decision 4), and the three independent reasons a row should have no
schedule at all.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime

import pytest

from core.follow_ups import (
    DELETE,
    NOOP,
    PUT,
    REMINDER_HOUR,
    ReminderSchedule,
    desired_schedule,
    fires_at,
    fires_in_past,
    reconcile,
    schedule_expression,
    wants_reminder,
)

TZ = "Pacific/Honolulu"
DUE = date(2026, 8, 1)


def _schedule(**overrides) -> ReminderSchedule | None:
    """Build a desired schedule for a pending, undeleted, emailing follow-up due 2026-08-01."""
    kwargs = {
        "follow_up_id": 42,
        "due_date": DUE,
        "note": "Chase the Hanalei contract",
        "remind_by_email": True,
        "completed_at": None,
        "deleted_at": None,
        "to_address": "donna@example.com",
        "timezone": TZ,
        "now_local": datetime(2026, 7, 30, 9, 0),
        "contact_name": "Kalei",
        "opportunity_title": "Wellness Wheel for Women",
    }
    kwargs.update(overrides)
    return desired_schedule(**kwargs)


def test_fires_at_is_local_seven_am() -> None:
    assert fires_at(DUE) == datetime(2026, 8, 1, REMINDER_HOUR, 0)
    assert fires_at(DUE).tzinfo is None  # naive: the zone travels separately


def test_expression_carries_no_offset_or_zulu() -> None:
    """The zone lives in ScheduleExpressionTimezone, so the expression must be zone-free."""
    expression = schedule_expression(DUE)
    assert expression == "at(2026-08-01T07:00:00)"
    assert "Z" not in expression
    assert "+" not in expression


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 8, 1, 6, 59), False),  # a minute before it fires
        (datetime(2026, 8, 1, 7, 0), True),  # exactly at the fire instant
        (datetime(2026, 8, 1, 10, 0), True),  # same day, after 07:00
        (datetime(2026, 7, 31, 23, 59), False),  # the night before
    ],
)
def test_fires_in_past_boundaries(now: datetime, expected: bool) -> None:
    assert fires_in_past(DUE, now) is expected


@pytest.mark.parametrize(
    ("remind", "completed", "deleted", "expected"),
    [
        (True, None, None, True),
        (False, None, None, False),  # dashboard-only
        (True, datetime(2026, 7, 30), None, False),  # done (acceptance #7)
        (True, None, datetime(2026, 7, 30), False),  # deleted (acceptance #3)
        (False, datetime(2026, 7, 30), datetime(2026, 7, 30), False),
    ],
)
def test_wants_reminder_has_three_independent_vetoes(remind, completed, deleted, expected) -> None:
    assert wants_reminder(remind, completed, deleted) is expected


def test_desired_schedule_populates_the_frozen_payload() -> None:
    schedule = _schedule()
    assert schedule is not None
    assert schedule.expression == "at(2026-08-01T07:00:00)"
    assert schedule.timezone == TZ
    assert schedule.to_address == "donna@example.com"
    assert schedule.contact_name == "Kalei"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"remind_by_email": False}, "dashboard-only"),
        ({"completed_at": datetime(2026, 7, 30)}, "already done"),
        ({"deleted_at": datetime(2026, 7, 30)}, "deleted"),
        ({"now_local": datetime(2026, 8, 1, 10, 0)}, "07:00 already passed"),
    ],
)
def test_no_schedule_when(overrides: dict, reason: str) -> None:
    assert _schedule(**overrides) is None, reason


def test_payload_is_json_ready_and_omits_scheduling_fields() -> None:
    """The payload is the contract with followup_notify, which never reads the database."""
    payload = _schedule().payload()
    assert payload == {
        "follow_up_id": 42,
        "to_address": "donna@example.com",
        "note": "Chase the Hanalei contract",
        "due_date": "2026-08-01",  # a str, because the schedule Input is JSON
        "contact_name": "Kalei",
        "opportunity_title": "Wellness Wheel for Women",
    }
    # When and where it fires is EventBridge's business, not the email's.
    assert "expression" not in payload
    assert "timezone" not in payload


def test_reconcile_noop_only_when_nothing_rendered_changed() -> None:
    schedule = _schedule()
    assert reconcile(schedule, schedule) == NOOP
    assert reconcile(schedule, dataclasses.replace(schedule, note="different")) == PUT


@pytest.mark.parametrize(
    "changed",
    [
        {"expression": "at(2026-09-09T07:00:00)"},
        {"note": "Chase the fee too"},
        {"contact_name": "Iris"},
        {"opportunity_title": "A different gig"},
        {"due_date": date(2026, 9, 9)},
        {"to_address": "someone@else.example"},
    ],
    ids=["date", "note", "contact", "opportunity", "due_date", "recipient"],
)
def test_every_rendered_field_forces_a_replace(changed: dict) -> None:
    """Acceptance #2 names the date; decision 4 covers everything the email renders.

    This is the guarantee that comes free from comparing frozen desired states instead of keeping a
    hand-maintained list of recreate-forcing field names.
    """
    before = _schedule()
    assert reconcile(before, dataclasses.replace(before, **changed)) == PUT


def test_reconcile_deletes_without_consulting_before() -> None:
    """``after is None`` must cancel even when ``before`` is also None.

    ``before`` is computed against the current clock, so a follow-up whose fire time has passed
    evaluates to ``None`` while a schedule may still exist for it. Comparing the two would skip a
    cancel that was needed, which is precisely the acceptance-#7 failure. Cancelling a schedule
    that was never there is explicitly harmless (acceptance #3), so the asymmetry is the safe side.
    """
    assert reconcile(None, None) == DELETE
    assert reconcile(_schedule(), None) == DELETE


def test_reconcile_creates_when_there_was_nothing_before() -> None:
    assert reconcile(None, _schedule()) == PUT
