"""Contact-timeline repository tests against a seeded MySQL — the UNION ALL (acceptance #5).

Skip without ``TEST_DATABASE_URL`` (see conftest). The timeline interleaves a contact's outreaches
with the notes and status events of the opportunities the contact is linked to, newest first, and
never leaks another opportunity's or another contact's history.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from models.opportunities import (
    OpportunityContactInput,
    OpportunityCreateInput,
    OpportunityNoteInput,
)
from models.outreach import OutreachInput
from repositories import opportunities as opp
from repositories import opportunity_contacts as oc
from repositories import opportunity_notes as notes
from repositories import outreaches as out
from repositories import timeline as tl


@pytest.fixture
def timeline_db(seeded_db):
    """One user; Jane linked to opp1 (a note + a status move + two outreaches); Ann and opp2 apart.

    ``opp2`` and Ann exist to prove isolation — their note/status/outreach must NOT appear on Jane's
    timeline.
    """
    conn, user_id, _, _ = seeded_db
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (user_id, organization_type_id, name) "
            "SELECT %s, id, 'Expo' FROM organization_types WHERE short_name = 'expo'",
            (user_id,),
        )
        org = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Jane')", (user_id,))
        jane = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Ann')", (user_id,))
        ann = cur.lastrowid

    opp1 = opp.create_opportunity(
        conn,
        user_id,
        OpportunityCreateInput(
            title="Gig One", organization_id=org, opportunity_format="workshop", comp_type="paid"
        ),
    )
    oc.add_contact(conn, user_id, opp1, OpportunityContactInput(contact_id=jane, is_primary=True))
    notes.add_note(
        conn,
        user_id,
        opp1,
        OpportunityNoteInput(body="called the coordinator", occurred_at=datetime(2026, 3, 1, 10)),
    )
    opp.patch_status(conn, user_id, opp1, "outreach_sent")  # a second status_event
    out.create_outreach(
        conn,
        user_id,
        OutreachInput(
            contact_id=jane, channel="dm", opportunity_id=opp1, occurred_at=datetime(2026, 3, 2, 9)
        ),
    )
    out.create_outreach(
        conn,
        user_id,
        OutreachInput(
            contact_id=jane,
            channel="email",
            note="general check-in",
            occurred_at=datetime(2026, 1, 5, 9),
        ),
    )

    # Isolation: opp2 (Jane NOT linked) and Ann's outreach must never surface on Jane's timeline.
    opp2 = opp.create_opportunity(
        conn,
        user_id,
        OpportunityCreateInput(
            title="Gig Two", organization_id=org, opportunity_format="keynote", comp_type="paid"
        ),
    )
    notes.add_note(conn, user_id, opp2, OpportunityNoteInput(body="unrelated note"))
    out.create_outreach(conn, user_id, OutreachInput(contact_id=ann, channel="dm"))

    return conn, user_id, {"jane": jane, "ann": ann, "opp1": opp1, "opp2": opp2}


def test_interleaves_all_three_journals(timeline_db) -> None:
    conn, user_id, ids = timeline_db
    rows = tl.contact_timeline(conn, user_id, ids["jane"])
    assert {r["item_type"] for r in rows} == {"outreach", "note", "status_event"}


def test_ordered_newest_first(timeline_db) -> None:
    conn, user_id, ids = timeline_db
    rows = tl.contact_timeline(conn, user_id, ids["jane"])
    times = [r["occurred_at"] for r in rows]
    assert times == sorted(times, reverse=True)


def test_outreach_rows_carry_channel_and_optional_gig(timeline_db) -> None:
    conn, user_id, ids = timeline_db
    rows = tl.contact_timeline(conn, user_id, ids["jane"])
    outs = {r["text"]: r for r in rows if r["item_type"] == "outreach"}
    # The gig-attributed DM resolves its opportunity title; the general check-in has none.
    dm = next(r for r in rows if r["item_type"] == "outreach" and r["channel"] == "dm")
    assert dm["opportunity_id"] == ids["opp1"] and dm["opportunity_title"] == "Gig One"
    general = outs["general check-in"]
    assert general["opportunity_id"] is None and general["opportunity_title"] is None
    assert general["status"] is None


def test_note_and_status_rows_shape(timeline_db) -> None:
    conn, user_id, ids = timeline_db
    rows = tl.contact_timeline(conn, user_id, ids["jane"])
    note = next(r for r in rows if r["item_type"] == "note")
    assert note["text"] == "called the coordinator"
    assert note["opportunity_title"] == "Gig One"
    assert note["channel"] is None and note["status"] is None
    statuses = {r["status"] for r in rows if r["item_type"] == "status_event"}
    assert statuses == {"researching", "outreach_sent"}  # both journal entries for opp1


def test_isolation_from_other_opps_and_contacts(timeline_db) -> None:
    conn, user_id, ids = timeline_db
    rows = tl.contact_timeline(conn, user_id, ids["jane"])
    texts = {r["text"] for r in rows}
    assert "unrelated note" not in texts  # opp2's note (Jane not linked)
    assert all(r["opportunity_id"] != ids["opp2"] for r in rows)
    # Only Jane's two outreaches surface — Ann's dm (a different contact) is absent.
    assert sum(1 for r in rows if r["item_type"] == "outreach") == 2


def test_unknown_contact_is_empty(timeline_db) -> None:
    conn, user_id, ids = timeline_db
    assert tl.contact_timeline(conn, user_id, 999999) == []
