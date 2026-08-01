"""Follow-up repository tests against a seeded MySQL (slice 7 checkpoint D).

Skip without ``TEST_DATABASE_URL`` (see conftest). These cover the persistence half of the slice:
the three legal link shapes, the ``ck_follow_ups_target`` CHECK that rejects a fourth, owner
scoping on every read and write, and the ``completed_at IS NULL`` predicate that is the *only*
pending-state in the schema — mechanizing acceptance #4 (marking done drops it from the Dashboard
query) and #5 (a follow-up attached to neither parent is rejected).
"""

from __future__ import annotations

from datetime import date, timedelta

import pymysql
import pytest

from common import errors
from models.follow_ups import FollowUpInput, FollowUpPatch
from repositories import follow_ups as fu

TODAY = date(2026, 7, 30)
YESTERDAY = TODAY - timedelta(days=1)
LAST_WEEK = TODAY - timedelta(days=7)
TOMORROW = TODAY + timedelta(days=1)


@pytest.fixture
def followup_db(seeded_db):
    """A migrated DB with a venue, two contacts, a gig, and a second tenant's contact.

    Returns ``(conn, user_id, ids)`` where ``ids`` has ``kalei`` / ``iris`` (contacts), ``opp`` (an
    opportunity), ``other_user`` and ``foreign_contact`` (a second tenant, for scoping checks).
    """
    conn, user_id, _, _ = seeded_db
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (user_id, organization_type_id, name) "
            "SELECT %s, id, 'Hanalei Bay Resort' FROM organization_types WHERE short_name='resort'",
            (user_id,),
        )
        org = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Kalei')", (user_id,))
        kalei = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Iris')", (user_id,))
        iris = cur.lastrowid
        cur.execute(
            "INSERT INTO opportunities "
            "(user_id, organization_id, opportunity_format_id, current_status_id, comp_type_id, "
            " payment_status_id, title) "
            "SELECT %s, %s, fmt.id, st.id, ct.id, pay.id, 'Wellness Wheel for Women' "
            "FROM opportunity_formats fmt, opportunity_statuses st, comp_types ct, "
            "     payment_statuses pay "
            "WHERE fmt.short_name = 'workshop' AND st.short_name = 'researching' "
            "  AND ct.short_name = 'paid' AND pay.short_name = 'unbilled'",
            (user_id, org),
        )
        opp = cur.lastrowid
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
            "opp": opp,
            "other_user": other_user,
            "foreign_contact": foreign_contact,
        },
    )


def test_all_three_link_shapes_persist(followup_db) -> None:
    """Contact-only, gig-only and both — the LEFT joins must not drop any of them."""
    conn, user_id, ids = followup_db
    contact_only = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TOMORROW, note="Check in", contact_id=ids["kalei"])
    )
    gig_only = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TOMORROW, note="Chase", opportunity_id=ids["opp"])
    )
    both = fu.create_follow_up(
        conn,
        user_id,
        FollowUpInput(
            due_date=TOMORROW, note="Confirm", contact_id=ids["iris"], opportunity_id=ids["opp"]
        ),
    )

    row = fu.get_follow_up(conn, user_id, contact_only)
    assert (row["contact_name"], row["opportunity_title"]) == ("Kalei", None)
    row = fu.get_follow_up(conn, user_id, gig_only)
    assert (row["contact_name"], row["opportunity_title"]) == (None, "Wellness Wheel for Women")
    row = fu.get_follow_up(conn, user_id, both)
    assert (row["contact_name"], row["opportunity_title"]) == ("Iris", "Wellness Wheel for Women")


def test_new_row_defaults_are_pending_and_emailing(followup_db) -> None:
    conn, user_id, ids = followup_db
    fid = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TOMORROW, note="x", contact_id=ids["kalei"])
    )
    row = fu.get_follow_up(conn, user_id, fid)
    assert row["completed_at"] is None  # completed_at IS NULL *is* the pending state
    assert bool(row["remind_by_email"]) is True


def test_check_constraint_rejects_a_row_with_neither_link(followup_db) -> None:
    """Acceptance #5 at the database level, below the model validator that normally catches it.

    MySQL 8.4 raises a CHECK violation as ``OperationalError`` errno **3819** — *not* the
    ``IntegrityError`` an FK or unique violation raises. Anything mapping DB errors by exception
    class has to know that, which is why the API relies on the Pydantic validator rather than on
    catching this.
    """
    conn, user_id, _ = followup_db
    with pytest.raises(pymysql.err.OperationalError) as excinfo, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO follow_ups (user_id, due_date, note) VALUES (%s, %s, 'orphan')",
            (user_id, TOMORROW),
        )
    assert excinfo.value.args[0] == 3819
    assert "ck_follow_ups_target" in str(excinfo.value)


@pytest.mark.parametrize("link", ["contact_id", "opportunity_id"])
def test_references_must_belong_to_the_caller(followup_db, link) -> None:
    conn, user_id, ids = followup_db
    target = ids["foreign_contact"] if link == "contact_id" else 999_999
    with pytest.raises(errors.InvalidInput):
        fu.create_follow_up(
            conn, user_id, FollowUpInput(due_date=TOMORROW, note="x", **{link: target})
        )


def test_reads_are_owner_scoped(followup_db) -> None:
    conn, user_id, ids = followup_db
    fid = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TOMORROW, note="x", contact_id=ids["kalei"])
    )
    assert fu.get_follow_up(conn, ids["other_user"], fid) is None
    assert fu.list_follow_ups(conn, ids["other_user"]) == []
    assert fu.patch_follow_up(conn, ids["other_user"], fid, FollowUpPatch(note="hijack")) is False
    assert fu.soft_delete_follow_up(conn, ids["other_user"], fid) is False


def test_list_due_is_overdue_plus_today_only(followup_db) -> None:
    """The Dashboard card: an unactioned reminder must get louder, not scroll off a future list."""
    conn, user_id, ids = followup_db
    old = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=LAST_WEEK, note="old", opportunity_id=ids["opp"])
    )
    yesterday = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=YESTERDAY, note="y", opportunity_id=ids["opp"])
    )
    today = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="t", opportunity_id=ids["opp"])
    )
    fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TOMORROW, note="not yet", opportunity_id=ids["opp"])
    )

    due = fu.list_due(conn, user_id, due_through=TODAY)
    assert [r["id"] for r in due] == [old, yesterday, today]  # most overdue first


def test_completing_and_reopening_moves_it_in_and_out_of_the_due_list(followup_db) -> None:
    """Acceptance #4, both directions."""
    conn, user_id, ids = followup_db
    fid = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="t", opportunity_id=ids["opp"])
    )
    assert [r["id"] for r in fu.list_due(conn, user_id, due_through=TODAY)] == [fid]

    fu.patch_follow_up(conn, user_id, fid, FollowUpPatch(completed=True))
    assert fu.get_follow_up(conn, user_id, fid)["completed_at"] is not None
    assert fu.list_due(conn, user_id, due_through=TODAY) == []

    fu.patch_follow_up(conn, user_id, fid, FollowUpPatch(completed=False))
    assert fu.get_follow_up(conn, user_id, fid)["completed_at"] is None
    assert [r["id"] for r in fu.list_due(conn, user_id, due_through=TODAY)] == [fid]


def test_patch_touches_only_the_fields_that_were_set(followup_db) -> None:
    conn, user_id, ids = followup_db
    fid = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="original", contact_id=ids["kalei"])
    )
    fu.patch_follow_up(conn, user_id, fid, FollowUpPatch(remind_by_email=False))

    row = fu.get_follow_up(conn, user_id, fid)
    assert bool(row["remind_by_email"]) is False
    assert row["note"] == "original"  # untouched by a flag-only patch
    assert row["due_date"] == TODAY


def test_empty_patch_matches_without_changing_anything(followup_db) -> None:
    """A redundant request must not look like a missing row (the handler maps False to 404)."""
    conn, user_id, ids = followup_db
    fid = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="x", contact_id=ids["kalei"])
    )
    assert fu.patch_follow_up(conn, user_id, fid, FollowUpPatch()) is True
    assert fu.patch_follow_up(conn, user_id, 999_999, FollowUpPatch()) is False


def test_soft_delete_is_idempotent_and_hides_the_row(followup_db) -> None:
    conn, user_id, ids = followup_db
    fid = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="x", contact_id=ids["kalei"])
    )
    assert fu.soft_delete_follow_up(conn, user_id, fid) is True
    assert fu.get_follow_up(conn, user_id, fid) is None
    assert fu.soft_delete_follow_up(conn, user_id, fid) is False
    assert fu.list_follow_ups(conn, user_id) == []


def test_list_filters(followup_db) -> None:
    conn, user_id, ids = followup_db
    on_contact = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="c", contact_id=ids["kalei"])
    )
    on_gig = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="g", opportunity_id=ids["opp"])
    )
    on_both = fu.create_follow_up(
        conn,
        user_id,
        FollowUpInput(due_date=TODAY, note="b", contact_id=ids["iris"], opportunity_id=ids["opp"]),
    )

    assert [r["id"] for r in fu.list_follow_ups(conn, user_id, contact_id=ids["kalei"])] == [
        on_contact
    ]
    assert sorted(
        r["id"] for r in fu.list_follow_ups(conn, user_id, opportunity_id=ids["opp"])
    ) == sorted([on_gig, on_both])
    # Both filters are ANDed — a reminder about this person *on* this gig.
    assert [
        r["id"]
        for r in fu.list_follow_ups(
            conn, user_id, contact_id=ids["iris"], opportunity_id=ids["opp"]
        )
    ] == [on_both]
    # A foreign id yields nothing rather than leaking.
    assert fu.list_follow_ups(conn, user_id, contact_id=ids["foreign_contact"]) == []


def test_organization_filter_is_the_union_of_a_venues_gigs_and_its_people(followup_db) -> None:
    """A venue has no ``organization_id`` on ``follow_ups`` — a reminder is about a person or a gig,
    never a building — so the filter reaches it through both links, and must reach *both*."""
    conn, user_id, ids = followup_db
    with conn.cursor() as cur:
        cur.execute("SELECT organization_id FROM opportunities WHERE id = %s", (ids["opp"],))
        org = cur.fetchone()["organization_id"]
        # Kalei is affiliated with the venue; Iris is not.
        cur.execute(
            "INSERT INTO contact_organizations (contact_id, organization_id) VALUES (%s, %s)",
            (ids["kalei"], org),
        )

    on_gig = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TOMORROW, note="gig", opportunity_id=ids["opp"])
    )
    on_person = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="person", contact_id=ids["kalei"])
    )
    unrelated = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="not here", contact_id=ids["iris"])
    )

    found = [r["id"] for r in fu.list_follow_ups(conn, user_id, organization_id=org)]
    assert found == [on_person, on_gig]  # soonest first
    assert unrelated not in found


def test_organization_filter_composes_with_pending_only(followup_db) -> None:
    conn, user_id, ids = followup_db
    with conn.cursor() as cur:
        cur.execute("SELECT organization_id FROM opportunities WHERE id = %s", (ids["opp"],))
        org = cur.fetchone()["organization_id"]

    done = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="done", opportunity_id=ids["opp"])
    )
    open_one = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="open", opportunity_id=ids["opp"])
    )
    fu.patch_follow_up(conn, user_id, done, FollowUpPatch(completed=True))

    pending = fu.list_follow_ups(conn, user_id, organization_id=org, pending_only=True)
    assert [r["id"] for r in pending] == [open_one]
    assert len(fu.list_follow_ups(conn, user_id, organization_id=org)) == 2


def test_an_unknown_organization_matches_nothing_rather_than_everything(followup_db) -> None:
    conn, user_id, ids = followup_db
    fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="x", contact_id=ids["kalei"])
    )
    assert fu.list_follow_ups(conn, user_id, organization_id=999_999) == []


def test_pending_only_drops_completed_but_the_default_keeps_history(followup_db) -> None:
    conn, user_id, ids = followup_db
    done = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="done", contact_id=ids["kalei"])
    )
    open_one = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="open", contact_id=ids["kalei"])
    )
    fu.patch_follow_up(conn, user_id, done, FollowUpPatch(completed=True))

    assert [r["id"] for r in fu.list_follow_ups(conn, user_id, pending_only=True)] == [open_one]
    assert len(fu.list_follow_ups(conn, user_id)) == 2


def test_contact_name_survives_that_contact_being_soft_deleted(followup_db) -> None:
    """Mirrors how an outreach keeps its contact's name — the reminder must stay readable."""
    conn, user_id, ids = followup_db
    fid = fu.create_follow_up(
        conn, user_id, FollowUpInput(due_date=TODAY, note="x", contact_id=ids["kalei"])
    )
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE contacts SET deleted_at = CURRENT_TIMESTAMP WHERE id = %s", (ids["kalei"],)
        )
    assert fu.get_follow_up(conn, user_id, fid)["contact_name"] == "Kalei"
