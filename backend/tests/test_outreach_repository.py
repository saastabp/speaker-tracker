"""Outreach repository tests against a seeded MySQL — kind inference, reads, and tenancy.

Skip without ``TEST_DATABASE_URL`` (see conftest). These mechanize the slice-4 acceptance criteria
at the repository level: first-touch infers ``initial`` and a later touch ``correspondence`` with
an override persisting (#1), and logging a touch never writes a ``status_events`` row (#6).
Inference is contact-scoped — a new opportunity does not reset it — and every read is owner-scoped.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from common import errors
from models.outreach import OutreachInput, OutreachPatch
from repositories import outreaches as out


@pytest.fixture
def outreach_db(seeded_db):
    """A migrated DB with one user, a venue, two contacts, an opportunity, and a 2nd user's contact.

    Returns ``(conn, user_id, ids)`` where ``ids`` has ``jane`` / ``ann`` (contacts), ``opp`` (an
    opportunity), ``template`` (a seeded shared template id), and ``other_contact`` (a contact owned
    by a second user, for tenancy checks).
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
        cur.execute(
            "INSERT INTO opportunities "
            "(user_id, organization_id, opportunity_format_id, current_status_id, comp_type_id, "
            " payment_status_id, title) "
            "SELECT %s, %s, fmt.id, st.id, ct.id, pay.id, 'Gig' "
            "FROM opportunity_formats fmt, opportunity_statuses st, comp_types ct, "
            "     payment_statuses pay "
            "WHERE fmt.short_name = 'workshop' AND st.short_name = 'researching' "
            "  AND ct.short_name = 'paid' AND pay.short_name = 'unbilled'",
            (user_id, org),
        )
        opp = cur.lastrowid
        cur.execute("SELECT id FROM message_templates WHERE user_id IS NULL AND name = 'Cold DM'")
        template = cur.fetchone()["id"]
        # A second tenant with their own contact, to prove cross-user references are rejected.
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('user2', 'user2@example.com')")
        other_user = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Foreign')", (other_user,))
        other_contact = cur.lastrowid
    return (
        conn,
        user_id,
        {
            "jane": jane,
            "ann": ann,
            "opp": opp,
            "template": template,
            "other_contact": other_contact,
        },
    )


def _count_status_events(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM status_events")
        return cur.fetchone()["n"]


# --- kind inference (acceptance #1) --------------------------------------------------------------


def test_first_touch_infers_initial(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    oid = out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["jane"], channel="dm"))
    assert out.get_outreach(conn, user_id, oid)["kind"] == "initial"


def test_second_touch_infers_correspondence(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["jane"], channel="dm"))
    oid2 = out.create_outreach(
        conn, user_id, OutreachInput(contact_id=ids["jane"], channel="email")
    )
    assert out.get_outreach(conn, user_id, oid2)["kind"] == "correspondence"


def test_override_persists_over_inference(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    # follow_up is never inferred — only an explicit override lands it, even on a first touch.
    oid = out.create_outreach(
        conn, user_id, OutreachInput(contact_id=ids["jane"], channel="dm", kind="follow_up")
    )
    assert out.get_outreach(conn, user_id, oid)["kind"] == "follow_up"


def test_inference_is_contact_scoped(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["jane"], channel="dm"))
    # A first touch to a *different* contact is still initial, despite prior touches to Jane.
    oid = out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["ann"], channel="dm"))
    assert out.get_outreach(conn, user_id, oid)["kind"] == "initial"


def test_new_opportunity_does_not_reset_inference(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["jane"], channel="dm"))
    # A later touch to the same contact tied to a specific gig still defaults to correspondence:
    # the opportunity is a filter axis, not part of inference.
    oid = out.create_outreach(
        conn,
        user_id,
        OutreachInput(contact_id=ids["jane"], channel="email", opportunity_id=ids["opp"]),
    )
    row = out.get_outreach(conn, user_id, oid)
    assert row["kind"] == "correspondence"
    assert row["opportunity_id"] == ids["opp"]


# --- decoupled from pipeline (acceptance #6) -----------------------------------------------------


def test_logging_outreach_writes_no_status_event(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    before = _count_status_events(conn)
    out.create_outreach(
        conn,
        user_id,
        OutreachInput(contact_id=ids["jane"], channel="dm", opportunity_id=ids["opp"]),
    )
    assert _count_status_events(conn) == before


# --- reads / fields ------------------------------------------------------------------------------


def test_summary_fields_and_template_link(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    when = datetime(2026, 7, 1, 9, 30, 0)
    oid = out.create_outreach(
        conn,
        user_id,
        OutreachInput(
            contact_id=ids["jane"],
            channel="dm",
            message_template_id=ids["template"],
            note="sent the intro",
            occurred_at=when,
        ),
    )
    row = out.get_outreach(conn, user_id, oid)
    assert row["contact_name"] == "Jane"
    assert row["channel"] == "dm"
    assert row["message_template_id"] == ids["template"]
    assert row["note"] == "sent the intro"
    assert row["occurred_at"] == when


def test_list_for_contact_newest_first_and_scoped(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    old = out.create_outreach(
        conn,
        user_id,
        OutreachInput(contact_id=ids["jane"], channel="dm", occurred_at=datetime(2026, 1, 1, 8)),
    )
    new = out.create_outreach(
        conn,
        user_id,
        OutreachInput(contact_id=ids["jane"], channel="email", occurred_at=datetime(2026, 6, 1, 8)),
    )
    out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["ann"], channel="dm"))
    rows = out.list_outreaches_for_contact(conn, user_id, ids["jane"])
    assert [r["id"] for r in rows] == [new, old]  # newest first, Ann's touch excluded


# --- tenancy / bad references (InvalidInput) -----------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("channel", "carrier_pigeon"),
        ("kind", "nonsense"),
    ],
)
def test_unknown_catalog_short_name_rejected(outreach_db, field, value) -> None:
    conn, user_id, ids = outreach_db
    kwargs = {"contact_id": ids["jane"], "channel": "dm"}
    kwargs[field] = value
    with pytest.raises(errors.InvalidInput):
        out.create_outreach(conn, user_id, OutreachInput(**kwargs))


def test_foreign_contact_rejected(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    with pytest.raises(errors.InvalidInput):
        out.create_outreach(
            conn, user_id, OutreachInput(contact_id=ids["other_contact"], channel="dm")
        )


def test_unknown_opportunity_rejected(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    with pytest.raises(errors.InvalidInput):
        out.create_outreach(
            conn,
            user_id,
            OutreachInput(contact_id=ids["jane"], channel="dm", opportunity_id=999999),
        )


def test_unknown_template_rejected(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    with pytest.raises(errors.InvalidInput):
        out.create_outreach(
            conn,
            user_id,
            OutreachInput(contact_id=ids["jane"], channel="dm", message_template_id=999999),
        )


# --- editing a logged touch (slice 11) -----------------------------------------------------------


def test_patch_updates_only_the_fields_that_were_set(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    touch = out.create_outreach(
        conn,
        user_id,
        OutreachInput(
            contact_id=ids["jane"],
            channel="dm",
            note="Sent the one-pager",
            occurred_at=datetime(2026, 7, 20, 9, 0),
        ),
    )
    assert out.patch_outreach(
        conn,
        user_id,
        touch,
        OutreachPatch(
            channel="call", note="Actually called her", occurred_at=datetime(2026, 7, 21, 15, 30)
        ),
    )
    row = out.get_outreach(conn, user_id, touch)
    assert row["channel"] == "call"
    assert row["note"] == "Actually called her"
    assert row["occurred_at"] == datetime(2026, 7, 21, 15, 30)
    assert row["contact_id"] == ids["jane"]  # the contact is not patchable


def test_patch_can_attribute_and_then_unattribute_a_gig(outreach_db) -> None:
    """``opportunity_id`` is nullable, so an explicit null has to mean *clear* — not *unchanged*."""
    conn, user_id, ids = outreach_db
    touch = out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["jane"], channel="dm"))

    out.patch_outreach(conn, user_id, touch, OutreachPatch(opportunity_id=ids["opp"]))
    assert out.get_outreach(conn, user_id, touch)["opportunity_id"] == ids["opp"]

    # Omitting the key leaves the attribution alone...
    out.patch_outreach(conn, user_id, touch, OutreachPatch(note="unrelated edit"))
    assert out.get_outreach(conn, user_id, touch)["opportunity_id"] == ids["opp"]

    # ...while sending it explicitly null removes it.
    out.patch_outreach(conn, user_id, touch, OutreachPatch.model_validate({"opportunity_id": None}))
    assert out.get_outreach(conn, user_id, touch)["opportunity_id"] is None


def test_patch_never_reinfers_the_kind(outreach_db) -> None:
    """An unrelated edit must not silently move a touch between target-counting kinds.

    The first touch is ``initial``. By the time it is edited a second touch exists, so re-running
    inference would resolve ``correspondence`` — a different answer for the same row, produced by
    an edit that said nothing about the kind.
    """
    conn, user_id, ids = outreach_db
    first = out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["jane"], channel="dm"))
    out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["jane"], channel="dm"))
    assert out.get_outreach(conn, user_id, first)["kind"] == "initial"

    out.patch_outreach(conn, user_id, first, OutreachPatch(note="fixed a typo"))
    assert out.get_outreach(conn, user_id, first)["kind"] == "initial"

    # An explicit kind, on the other hand, is honoured.
    out.patch_outreach(conn, user_id, first, OutreachPatch(kind="follow_up"))
    assert out.get_outreach(conn, user_id, first)["kind"] == "follow_up"


@pytest.mark.parametrize(
    "patch",
    [
        OutreachPatch(channel="carrier_pigeon"),
        OutreachPatch(kind="vibes"),
        OutreachPatch(opportunity_id=999999),
    ],
)
def test_patch_validates_its_references_exactly_as_create_does(outreach_db, patch) -> None:
    conn, user_id, ids = outreach_db
    touch = out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["jane"], channel="dm"))
    with pytest.raises(errors.InvalidInput):
        out.patch_outreach(conn, user_id, touch, patch)


def test_empty_patch_matches_without_changing_anything(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    touch = out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["jane"], channel="dm"))
    before = out.get_outreach(conn, user_id, touch)
    assert out.patch_outreach(conn, user_id, touch, OutreachPatch())
    assert out.get_outreach(conn, user_id, touch) == before


def test_patching_a_missing_deleted_or_foreign_touch_reports_no_match(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    touch = out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["jane"], channel="dm"))
    assert out.patch_outreach(conn, user_id, 9999, OutreachPatch(note="x")) is False
    assert out.patch_outreach(conn, user_id + 999, touch, OutreachPatch(note="x")) is False
    out.soft_delete_outreach(conn, user_id, touch)
    assert out.patch_outreach(conn, user_id, touch, OutreachPatch(note="x")) is False


# --- soft delete ---------------------------------------------------------------------------------


def test_soft_delete_hides_and_resets_inference(outreach_db) -> None:
    conn, user_id, ids = outreach_db
    first = out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["jane"], channel="dm"))
    assert out.soft_delete_outreach(conn, user_id, first) is True
    assert out.get_outreach(conn, user_id, first) is None
    assert out.list_outreaches_for_contact(conn, user_id, ids["jane"]) == []
    # With the only prior touch retracted, the next touch infers initial again.
    second = out.create_outreach(conn, user_id, OutreachInput(contact_id=ids["jane"], channel="dm"))
    assert out.get_outreach(conn, user_id, second)["kind"] == "initial"
    # A second delete of an already-deleted row is a no-op.
    assert out.soft_delete_outreach(conn, user_id, first) is False
