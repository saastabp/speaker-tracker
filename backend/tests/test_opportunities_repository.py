"""Opportunities repository tests against a seeded MySQL — board reads, CRUD, the status journal,
and the child (contact / note) repos.

Skip without ``TEST_DATABASE_URL`` (see conftest). These mechanize the slice-3 acceptance criteria
at the repository level: one-event-per-real-move (#1), the ``closed_at`` predicate (#3), a
delivered-but-unpaid gig staying on the board (#4), correcting payment re-opening it (#5), and close
writing a terminal event *and* a reason note (#8).
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from common import errors
from models.opportunities import (
    OpportunityContactInput,
    OpportunityContactUpdate,
    OpportunityInput,
    OpportunityNoteInput,
)
from repositories import opportunities as opp
from repositories import opportunity_contacts as oc
from repositories import opportunity_notes as notes
from repositories.opportunities import StatusPatchResult


@pytest.fixture
def pipeline_db(seeded_db):
    """A migrated DB with one user, a venue (with how_to_approach), two contacts, and a talk."""
    conn, user_id, _, _ = seeded_db
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (user_id, organization_type_id, name, how_to_approach) "
            "SELECT %s, id, 'Expo', 'warm intro' FROM organization_types WHERE short_name = 'expo'",
            (user_id,),
        )
        org = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Jane')", (user_id,))
        jane = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Ann')", (user_id,))
        ann = cur.lastrowid
        cur.execute("INSERT INTO talks (user_id, title) VALUES (%s, 'Talk A')", (user_id,))
        talk = cur.lastrowid
    return conn, user_id, {"org": org, "jane": jane, "ann": ann, "talk": talk}


def _opp(org: int, **kw) -> OpportunityInput:
    base = {
        "title": "Gig",
        "organization_id": org,
        "opportunity_format": "workshop",
        "comp_type": "paid",
    }
    base.update(kw)
    return OpportunityInput(**base)


# --- create / read -------------------------------------------------------------------------------


def test_create_defaults_and_initial_event(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"], talk_id=ids["talk"]))
    row = opp.get_opportunity(conn, user_id, oid)
    assert row["current_status"] == "researching"
    assert row["payment_status"] == "unbilled"  # paid gig starts billable
    assert row["closed_at"] is None
    assert row["talk_title"] == "Talk A"
    events = opp.get_status_events(conn, user_id, oid)
    assert [(e["status"], e["note"]) for e in events] == [("researching", None)]


def test_create_seeds_angle_from_venue_when_absent(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    seeded = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    assert opp.get_opportunity(conn, user_id, seeded)["angle"] == "warm intro"
    kept = opp.create_opportunity(conn, user_id, _opp(ids["org"], angle="my own angle"))
    assert opp.get_opportunity(conn, user_id, kept)["angle"] == "my own angle"


def test_create_pro_bono_starts_settled(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"], comp_type="pro_bono"))
    assert opp.get_opportunity(conn, user_id, oid)["payment_status"] == "n_a"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("organization_id", 999999, "unknown organization"),
        ("talk_id", 888888, "unknown talk"),
        ("opportunity_format", "nope", "unknown opportunity_format"),
        ("comp_type", "nope", "unknown comp_type"),
    ],
)
def test_create_rejects_bad_references(pipeline_db, field, value, message) -> None:
    conn, user_id, ids = pipeline_db
    with pytest.raises(errors.InvalidInput, match=message):
        opp.create_opportunity(conn, user_id, _opp(ids["org"], **{field: value}))


def test_list_board_history_and_status_filter(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    open_id = opp.create_opportunity(conn, user_id, _opp(ids["org"], title="Open"))
    done_id = opp.create_opportunity(conn, user_id, _opp(ids["org"], title="Done"))
    opp.close(conn, user_id, done_id, "lost", "no fit")
    assert {r["title"] for r in opp.list_opportunities(conn, user_id)} == {"Open", "Done"}
    assert [r["title"] for r in opp.list_opportunities(conn, user_id, closed=False)] == ["Open"]
    assert [r["title"] for r in opp.list_opportunities(conn, user_id, closed=True)] == ["Done"]
    assert [r["id"] for r in opp.list_opportunities(conn, user_id, status="researching")] == [
        open_id
    ]


def test_list_orders_dated_before_undated(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    opp.create_opportunity(conn, user_id, _opp(ids["org"], title="Undated"))
    opp.create_opportunity(
        conn, user_id, _opp(ids["org"], title="Soon", event_date=date(2026, 9, 1))
    )
    assert [r["title"] for r in opp.list_opportunities(conn, user_id)] == ["Soon", "Undated"]


def test_get_and_reads_are_owner_scoped(pipeline_db, db_connection) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    with db_connection.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('u2', 'u2@x')")
        other = cur.lastrowid
    assert opp.get_opportunity(conn, other, oid) is None
    assert opp.get_status_events(conn, other, oid) == []


# --- update --------------------------------------------------------------------------------------


def test_update_replaces_descriptive_but_not_lifecycle(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    opp.patch_status(conn, user_id, oid, "booked")
    assert (
        opp.update_opportunity(
            conn, user_id, oid, _opp(ids["org"], title="Renamed", opportunity_format="panel")
        )
        is True
    )
    row = opp.get_opportunity(conn, user_id, oid)
    assert row["title"] == "Renamed"
    assert row["opportunity_format"] == "panel"
    assert row["current_status"] == "booked"  # lifecycle untouched by update


def test_update_missing_returns_false(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    assert opp.update_opportunity(conn, user_id, 999, _opp(ids["org"])) is False


def test_soft_delete_hides_and_is_idempotent(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    assert opp.soft_delete_opportunity(conn, user_id, oid) is True
    assert opp.get_opportunity(conn, user_id, oid) is None
    assert opp.list_opportunities(conn, user_id) == []
    assert opp.soft_delete_opportunity(conn, user_id, oid) is False


# --- patch_status --------------------------------------------------------------------------------


def test_status_real_move_writes_one_event(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    assert opp.patch_status(conn, user_id, oid, "booked") is StatusPatchResult.MOVED
    assert [e["status"] for e in opp.get_status_events(conn, user_id, oid)] == [
        "booked",
        "researching",
    ]  # newest first; exactly one new event (#1)


def test_status_same_column_is_no_op(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    opp.patch_status(conn, user_id, oid, "booked")
    before = len(opp.get_status_events(conn, user_id, oid))
    assert opp.patch_status(conn, user_id, oid, "booked") is StatusPatchResult.NO_CHANGE
    assert len(opp.get_status_events(conn, user_id, oid)) == before  # nothing written (#1)


def test_delivered_but_unpaid_stays_on_board(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    opp.patch_status(conn, user_id, oid, "delivered")
    assert opp.get_opportunity(conn, user_id, oid)["closed_at"] is None  # #4


def test_retired_nurture_status_is_rejected(pipeline_db) -> None:
    # nurture was retired in migration 0004; resolving it now raises InvalidInput.
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    with pytest.raises(errors.InvalidInput):
        opp.patch_status(conn, user_id, oid, "nurture")


def test_status_rejects_close_flow_targets(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    with pytest.raises(errors.InvalidInput, match="board stage"):
        opp.patch_status(conn, user_id, oid, "cancelled")


def test_status_missing_returns_not_found(pipeline_db) -> None:
    conn, user_id, _ = pipeline_db
    assert opp.patch_status(conn, user_id, 999, "booked") is StatusPatchResult.NOT_FOUND


# --- patch_payment -------------------------------------------------------------------------------


def test_mark_delivered_paid_closes_into_history(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    opp.patch_status(conn, user_id, oid, "delivered")
    assert opp.patch_payment(conn, user_id, oid, "paid", date(2026, 9, 1)) is True
    row = opp.get_opportunity(conn, user_id, oid)
    assert row["closed_at"] is not None  # #4 moves to History
    assert row["paid_on"] == date(2026, 9, 1)
    # payment change writes no status event
    assert [e["status"] for e in opp.get_status_events(conn, user_id, oid)] == [
        "delivered",
        "researching",
    ]


def test_correcting_payment_reopens(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    opp.patch_status(conn, user_id, oid, "delivered")
    opp.patch_payment(conn, user_id, oid, "paid", date(2026, 9, 1))
    opp.patch_payment(conn, user_id, oid, "invoiced", None)
    assert opp.get_opportunity(conn, user_id, oid)["closed_at"] is None  # #5


def test_payment_missing_returns_false(pipeline_db) -> None:
    conn, user_id, _ = pipeline_db
    assert opp.patch_payment(conn, user_id, 999, "paid", None) is False


def test_payment_rejects_unknown_status(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    with pytest.raises(errors.InvalidInput, match="payment_status"):
        opp.patch_payment(conn, user_id, oid, "nope", None)


# --- close ---------------------------------------------------------------------------------------


def test_close_writes_terminal_event_and_note(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    opp.patch_status(conn, user_id, oid, "pitched")
    assert opp.close(conn, user_id, oid, "lost", "went with someone else") is True
    row = opp.get_opportunity(conn, user_id, oid)
    assert row["current_status"] == "lost"
    assert row["closed_at"] is not None
    events = opp.get_status_events(conn, user_id, oid)
    # #8: the terminal status event carries the reason as its note, and a note row is written too
    assert (events[0]["status"], events[0]["note"]) == ("lost", "went with someone else")
    assert [n["body"] for n in opp.get_opportunity_notes(conn, user_id, oid)] == [
        "went with someone else"
    ]


def test_close_rejects_non_close_status(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    with pytest.raises(errors.InvalidInput, match="close status"):
        opp.close(conn, user_id, oid, "delivered", "x")


def test_close_missing_returns_false(pipeline_db) -> None:
    conn, user_id, _ = pipeline_db
    assert opp.close(conn, user_id, 999, "lost", "x") is False


# --- opportunity_contacts ------------------------------------------------------------------------


def test_add_contacts_and_single_lead_invariant(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    oc.add_contact(
        conn,
        user_id,
        oid,
        OpportunityContactInput(contact_id=ids["jane"], contact_role="primary", is_primary=True),
    )
    oc.add_contact(
        conn,
        user_id,
        oid,
        OpportunityContactInput(contact_id=ids["ann"], contact_role="coordinator"),
    )
    linked = opp.get_opportunity_contacts(conn, user_id, oid)
    assert {(r["name"], r["contact_role"], bool(r["is_primary"])) for r in linked} == {
        ("Jane", "primary", True),
        ("Ann", "coordinator", False),
    }
    # promote Ann -> Jane demoted
    oc.update_contact(conn, user_id, oid, ids["ann"], OpportunityContactUpdate(is_primary=True))
    leads = {r["name"] for r in opp.get_opportunity_contacts(conn, user_id, oid) if r["is_primary"]}
    assert leads == {"Ann"}


def test_add_contact_duplicate_conflicts(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    oc.add_contact(conn, user_id, oid, OpportunityContactInput(contact_id=ids["jane"]))
    with pytest.raises(errors.Conflict):
        oc.add_contact(conn, user_id, oid, OpportunityContactInput(contact_id=ids["jane"]))


def test_add_contact_foreign_contact_not_found(pipeline_db, db_connection) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    with db_connection.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('u2', 'u2@x')")
        other = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Foreign')", (other,))
        foreign = cur.lastrowid
    with pytest.raises(errors.NotFound):
        oc.add_contact(conn, user_id, oid, OpportunityContactInput(contact_id=foreign))


def test_add_contact_unknown_role_invalid(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    with pytest.raises(errors.InvalidInput, match="contact_role"):
        oc.add_contact(
            conn, user_id, oid, OpportunityContactInput(contact_id=ids["jane"], contact_role="nope")
        )


def test_remove_contact_scope_and_idempotent(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    oc.add_contact(conn, user_id, oid, OpportunityContactInput(contact_id=ids["jane"]))
    assert oc.remove_contact(conn, 424242, oid, ids["jane"]) is False  # foreign scope
    assert oc.remove_contact(conn, user_id, oid, ids["jane"]) is True
    assert oc.remove_contact(conn, user_id, oid, ids["jane"]) is False  # already gone


# --- opportunity_notes ---------------------------------------------------------------------------


def test_add_note_defaults_now_and_backdate(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    notes.add_note(conn, user_id, oid, OpportunityNoteInput(body="called"))
    notes.add_note(
        conn,
        user_id,
        oid,
        OpportunityNoteInput(body="backdated", occurred_at=datetime(2026, 1, 1, 12, 0)),
    )
    bodies = [n["body"] for n in opp.get_opportunity_notes(conn, user_id, oid)]
    assert bodies == ["called", "backdated"]  # most recent occurred_at first


def test_add_note_foreign_opportunity_not_found(pipeline_db, db_connection) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    with db_connection.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('u2', 'u2@x')")
        other = cur.lastrowid
    with pytest.raises(errors.NotFound):
        notes.add_note(conn, other, oid, OpportunityNoteInput(body="x"))


def test_soft_delete_note_hides_and_is_idempotent(pipeline_db) -> None:
    conn, user_id, ids = pipeline_db
    oid = opp.create_opportunity(conn, user_id, _opp(ids["org"]))
    note_id = notes.add_note(conn, user_id, oid, OpportunityNoteInput(body="temp"))
    assert notes.soft_delete_note(conn, user_id, oid, note_id) is True
    assert opp.get_opportunity_notes(conn, user_id, oid) == []
    assert notes.soft_delete_note(conn, user_id, oid, note_id) is False
    assert notes.soft_delete_note(conn, 424242, oid, note_id) is False  # foreign scope
