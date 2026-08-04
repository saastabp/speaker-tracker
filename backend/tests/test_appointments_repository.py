"""Appointment repository tests against a seeded MySQL (slice 11).

Skip without ``TEST_DATABASE_URL`` (see conftest). These cover the persistence half of the feature:
the scope split that is this table's entire lifecycle, the patch's two different meanings of
``None``, owner scoping on every read and write, and the wall-clock property the DATETIME column
was chosen for — the last one being the only test here that would fail against a TIMESTAMP.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from common import errors
from models.appointments import AppointmentInput, AppointmentPatch
from repositories import appointments as appts

NOW = datetime(2026, 8, 3, 12, 0)


@pytest.fixture
def appt_db(seeded_db):
    """A migrated DB with two contacts and a second tenant's contact, for scoping checks."""
    conn, user_id, _, _ = seeded_db
    with conn.cursor() as cur:
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Kalei')", (user_id,))
        kalei = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Iris')", (user_id,))
        iris = cur.lastrowid
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('user2', 'user2@example.com')")
        other_user = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Foreign')", (other_user,))
        foreign_contact = cur.lastrowid
    return (
        conn,
        user_id,
        {
            "kalei": kalei,
            "iris": iris,
            "other_user": other_user,
            "foreign_contact": foreign_contact,
        },
    )


def _make(conn, user_id, contact_id, when, title="Coffee", details=None) -> int:
    return appts.create_appointment(
        conn,
        user_id,
        AppointmentInput(contact_id=contact_id, title=title, scheduled_at=when, details=details),
    )


def test_create_and_read_round_trips_every_field(appt_db) -> None:
    conn, user_id, ids = appt_db
    appt_id = _make(conn, user_id, ids["kalei"], NOW + timedelta(days=1), "Coffee", "At Java Kai")
    row = appts.get_appointment(conn, user_id, appt_id)
    assert row["contact_id"] == ids["kalei"]
    assert row["contact_name"] == "Kalei"
    assert row["title"] == "Coffee"
    assert row["scheduled_at"] == NOW + timedelta(days=1)
    assert row["details"] == "At Java Kai"


def test_scope_splits_the_list_on_the_supplied_instant(appt_db) -> None:
    conn, user_id, ids = appt_db
    _make(conn, user_id, ids["kalei"], NOW + timedelta(days=1), "Future")
    _make(conn, user_id, ids["kalei"], NOW - timedelta(days=1), "Yesterday")

    upcoming = appts.list_appointments(conn, user_id, scope="upcoming", as_of=NOW)
    past = appts.list_appointments(conn, user_id, scope="past", as_of=NOW)
    every = appts.list_appointments(conn, user_id, as_of=NOW)

    assert [r["title"] for r in upcoming] == ["Future"]
    assert [r["title"] for r in past] == ["Yesterday"]
    assert {r["title"] for r in every} == {"Future", "Yesterday"}


def test_upcoming_honours_the_hour_not_just_the_day(appt_db) -> None:
    """A 9am meeting is over by noon — the reason the hour is stored at all."""
    conn, user_id, ids = appt_db
    _make(conn, user_id, ids["kalei"], NOW.replace(hour=9), "This morning")
    _make(conn, user_id, ids["kalei"], NOW.replace(hour=15), "This afternoon")
    rows = appts.list_appointments(conn, user_id, scope="upcoming", as_of=NOW)
    assert [r["title"] for r in rows] == ["This afternoon"]


def test_past_reads_backwards_from_now(appt_db) -> None:
    conn, user_id, ids = appt_db
    _make(conn, user_id, ids["kalei"], NOW - timedelta(days=30), "Last month")
    _make(conn, user_id, ids["kalei"], NOW - timedelta(days=1), "Yesterday")
    rows = appts.list_appointments(conn, user_id, scope="past", as_of=NOW)
    assert [r["title"] for r in rows] == ["Yesterday", "Last month"]


def test_limit_caps_the_list(appt_db) -> None:
    conn, user_id, ids = appt_db
    for day in range(1, 5):
        _make(conn, user_id, ids["kalei"], NOW + timedelta(days=day), f"Day {day}")
    rows = appts.list_appointments(conn, user_id, scope="upcoming", as_of=NOW, limit=2)
    assert [r["title"] for r in rows] == ["Day 1", "Day 2"]


def test_contact_filter_narrows_to_one_person(appt_db) -> None:
    conn, user_id, ids = appt_db
    _make(conn, user_id, ids["kalei"], NOW + timedelta(days=1), "With Kalei")
    _make(conn, user_id, ids["iris"], NOW + timedelta(days=2), "With Iris")
    rows = appts.list_appointments(conn, user_id, contact_id=ids["iris"])
    assert [r["title"] for r in rows] == ["With Iris"]


def test_reads_are_owner_scoped(appt_db) -> None:
    conn, user_id, ids = appt_db
    appt_id = _make(conn, user_id, ids["kalei"], NOW + timedelta(days=1))
    assert appts.get_appointment(conn, ids["other_user"], appt_id) is None
    assert appts.list_appointments(conn, ids["other_user"]) == []


def test_a_foreign_contact_is_rejected_on_create_and_on_patch(appt_db) -> None:
    conn, user_id, ids = appt_db
    with pytest.raises(errors.InvalidInput):
        _make(conn, user_id, ids["foreign_contact"], NOW + timedelta(days=1))
    appt_id = _make(conn, user_id, ids["kalei"], NOW + timedelta(days=1))
    with pytest.raises(errors.InvalidInput):
        appts.patch_appointment(
            conn, user_id, appt_id, AppointmentPatch(contact_id=ids["foreign_contact"])
        )


def test_patch_touches_only_the_fields_that_were_set(appt_db) -> None:
    conn, user_id, ids = appt_db
    appt_id = _make(conn, user_id, ids["kalei"], NOW + timedelta(days=1), "Coffee", "At Java Kai")
    assert appts.patch_appointment(conn, user_id, appt_id, AppointmentPatch(title="Lunch"))
    row = appts.get_appointment(conn, user_id, appt_id)
    assert row["title"] == "Lunch"
    assert row["details"] == "At Java Kai"  # untouched
    assert row["scheduled_at"] == NOW + timedelta(days=1)


def test_explicit_null_clears_details_but_omitting_the_key_does_not(appt_db) -> None:
    """The whole reason ``details`` reads ``model_fields_set`` rather than testing for None."""
    conn, user_id, ids = appt_db
    appt_id = _make(conn, user_id, ids["kalei"], NOW + timedelta(days=1), details="At Java Kai")

    appts.patch_appointment(conn, user_id, appt_id, AppointmentPatch(title="Lunch"))
    assert appts.get_appointment(conn, user_id, appt_id)["details"] == "At Java Kai"

    appts.patch_appointment(
        conn, user_id, appt_id, AppointmentPatch.model_validate({"details": None})
    )
    assert appts.get_appointment(conn, user_id, appt_id)["details"] is None


def test_patch_can_move_the_appointment_to_another_person(appt_db) -> None:
    conn, user_id, ids = appt_db
    appt_id = _make(conn, user_id, ids["kalei"], NOW + timedelta(days=1))
    appts.patch_appointment(conn, user_id, appt_id, AppointmentPatch(contact_id=ids["iris"]))
    row = appts.get_appointment(conn, user_id, appt_id)
    assert (row["contact_id"], row["contact_name"]) == (ids["iris"], "Iris")


def test_empty_patch_matches_without_changing_anything(appt_db) -> None:
    conn, user_id, ids = appt_db
    appt_id = _make(conn, user_id, ids["kalei"], NOW + timedelta(days=1))
    before = appts.get_appointment(conn, user_id, appt_id)
    assert appts.patch_appointment(conn, user_id, appt_id, AppointmentPatch())
    assert appts.get_appointment(conn, user_id, appt_id) == before


def test_patching_a_missing_or_foreign_row_reports_no_match(appt_db) -> None:
    conn, user_id, ids = appt_db
    appt_id = _make(conn, user_id, ids["kalei"], NOW + timedelta(days=1))
    assert appts.patch_appointment(conn, user_id, 9999, AppointmentPatch(title="x")) is False
    assert (
        appts.patch_appointment(conn, ids["other_user"], appt_id, AppointmentPatch(title="x"))
        is False
    )


def test_soft_delete_hides_the_row_and_is_idempotent(appt_db) -> None:
    conn, user_id, ids = appt_db
    appt_id = _make(conn, user_id, ids["kalei"], NOW + timedelta(days=1))
    assert appts.soft_delete_appointment(conn, user_id, appt_id) is True
    assert appts.get_appointment(conn, user_id, appt_id) is None
    assert appts.list_appointments(conn, user_id) == []
    assert appts.soft_delete_appointment(conn, user_id, appt_id) is False


def test_contact_name_survives_that_contact_being_soft_deleted(appt_db) -> None:
    conn, user_id, ids = appt_db
    appt_id = _make(conn, user_id, ids["kalei"], NOW + timedelta(days=1))
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE contacts SET deleted_at = CURRENT_TIMESTAMP WHERE id = %s", (ids["kalei"],)
        )
    assert appts.get_appointment(conn, user_id, appt_id)["contact_name"] == "Kalei"


def test_the_wall_clock_is_not_shifted_by_a_session_timezone_change(appt_db) -> None:
    """Decision 1 in the migration, mechanized: this fails if ``scheduled_at`` is a TIMESTAMP.

    Donna's 2pm has to read back as 2pm from any session, because the value is a commitment to a
    clock face and not to an instant on a global timeline.
    """
    conn, user_id, ids = appt_db
    with conn.cursor() as cur:
        cur.execute("SET time_zone = '-10:00'")  # Pacific/Honolulu
    appt_id = _make(conn, user_id, ids["kalei"], datetime(2026, 8, 7, 14, 0))
    with conn.cursor() as cur:
        cur.execute("SET time_zone = '+00:00'")
    row = appts.get_appointment(conn, user_id, appt_id)
    assert row["scheduled_at"] == datetime(2026, 8, 7, 14, 0)
