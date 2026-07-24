"""Message-template repository tests against a seeded MySQL — visibility, edit-in-place, Duplicate.

Skip without ``TEST_DATABASE_URL`` (see conftest). These mechanize slice-4 acceptance #4: a shared
template (``user_id IS NULL``) edits in place, and Duplicate forks it into a personal copy.
Migration ``0005`` seeds three shared templates, which the visibility and duplicate cases build on.
"""

from __future__ import annotations

import pytest

from common import errors
from models.message_templates import MessageTemplateInput
from repositories import message_templates as tpl


@pytest.fixture
def template_db(seeded_db):
    """A migrated DB (three shared templates seeded) with a second user owning a personal template.

    Returns ``(conn, user_id, ids)`` where ``ids`` has ``cold_dm`` (a seeded shared template) and
    ``foreign`` (a personal template owned by a second user, for visibility checks).
    """
    conn, user_id, _, _ = seeded_db
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM message_templates WHERE user_id IS NULL AND name = 'Cold DM'")
        cold_dm = cur.fetchone()["id"]
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('user2', 'user2@example.com')")
        other_user = cur.lastrowid
        cur.execute(
            "INSERT INTO message_templates "
            "(user_id, message_template_kind_id, channel_id, name, body) "
            "SELECT %s, k.id, ch.id, 'Foreign Personal', 'secret' "
            "FROM message_template_kinds k, outreach_channels ch "
            "WHERE k.short_name = 'cold_pitch' AND ch.short_name = 'dm'",
            (other_user,),
        )
        foreign = cur.lastrowid
    return conn, user_id, {"cold_dm": cold_dm, "foreign": foreign}


def _by_name(rows: list[dict], name: str) -> dict | None:
    return next((r for r in rows if r["name"] == name), None)


# --- visibility ----------------------------------------------------------------------------------


def test_list_shows_shared_but_not_foreign_personal(template_db) -> None:
    conn, user_id, ids = template_db
    rows = tpl.list_message_templates(conn, user_id)
    names = {r["name"] for r in rows}
    assert {"Cold DM", "Cold Email", "Power-Partner DM"} <= names  # three seeded shared
    assert "Foreign Personal" not in names  # another user's personal is invisible
    assert _by_name(rows, "Cold DM")["is_shared"] == 1


def test_get_shared_template_fields(template_db) -> None:
    conn, user_id, ids = template_db
    row = tpl.get_message_template(conn, user_id, ids["cold_dm"])
    assert row["channel"] == "dm"
    assert row["kind"] == "cold_pitch"
    assert row["subject"] is None  # DM templates carry no subject
    assert row["is_shared"] == 1


def test_get_foreign_personal_is_invisible(template_db) -> None:
    conn, user_id, ids = template_db
    assert tpl.get_message_template(conn, user_id, ids["foreign"]) is None


# --- create (personal) ---------------------------------------------------------------------------


def test_create_makes_personal_template(template_db) -> None:
    conn, user_id, ids = template_db
    new_id = tpl.create_message_template(
        conn,
        user_id,
        MessageTemplateInput(
            kind="cold_pitch", channel="email", name="My Pitch", subject="Hi", body="Hello [Name]"
        ),
    )
    row = tpl.get_message_template(conn, user_id, new_id)
    assert row["is_shared"] == 0
    assert row["subject"] == "Hi"
    assert _by_name(tpl.list_message_templates(conn, user_id), "My Pitch") is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [("kind", "nope"), ("channel", "smoke_signal")],
)
def test_create_unknown_catalog_rejected(template_db, field, value) -> None:
    conn, user_id, ids = template_db
    kwargs = {"kind": "cold_pitch", "channel": "dm", "name": "X", "body": "b"}
    kwargs[field] = value
    with pytest.raises(errors.InvalidInput):
        tpl.create_message_template(conn, user_id, MessageTemplateInput(**kwargs))


# --- edit in place (acceptance #4) ---------------------------------------------------------------


def test_update_edits_shared_in_place(template_db) -> None:
    conn, user_id, ids = template_db
    ok = tpl.update_message_template(
        conn,
        user_id,
        ids["cold_dm"],
        MessageTemplateInput(kind="cold_pitch", channel="dm", name="Cold DM", body="Revised body"),
    )
    assert ok is True
    row = tpl.get_message_template(conn, user_id, ids["cold_dm"])
    assert row["body"] == "Revised body"
    assert row["is_shared"] == 1  # editing in place does NOT fork it to personal


def test_update_foreign_personal_returns_false(template_db) -> None:
    conn, user_id, ids = template_db
    ok = tpl.update_message_template(
        conn,
        user_id,
        ids["foreign"],
        MessageTemplateInput(kind="cold_pitch", channel="dm", name="Hijacked", body="x"),
    )
    assert ok is False
    # The foreign row is untouched.
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM message_templates WHERE id = %s", (ids["foreign"],))
        assert cur.fetchone()["name"] == "Foreign Personal"


# --- duplicate (acceptance #4) -------------------------------------------------------------------


def test_duplicate_shared_creates_personal_copy(template_db) -> None:
    conn, user_id, ids = template_db
    new_id = tpl.duplicate_message_template(conn, user_id, ids["cold_dm"])
    copy = tpl.get_message_template(conn, user_id, new_id)
    src = tpl.get_message_template(conn, user_id, ids["cold_dm"])
    assert copy["is_shared"] == 0  # the copy is personal
    assert copy["name"] == "Cold DM (copy)"
    assert copy["kind"] == src["kind"] and copy["channel"] == src["channel"]
    assert copy["body"] == src["body"]
    assert src["is_shared"] == 1  # the shared original is unchanged


def test_duplicate_foreign_personal_raises_not_found(template_db) -> None:
    conn, user_id, ids = template_db
    with pytest.raises(errors.NotFound):
        tpl.duplicate_message_template(conn, user_id, ids["foreign"])


# --- delete (own only) ---------------------------------------------------------------------------


def test_soft_delete_own_but_not_shared(template_db) -> None:
    conn, user_id, ids = template_db
    mine = tpl.create_message_template(
        conn, user_id, MessageTemplateInput(kind="cold_pitch", channel="dm", name="Mine", body="b")
    )
    assert tpl.soft_delete_message_template(conn, user_id, mine) is True
    assert tpl.get_message_template(conn, user_id, mine) is None
    # A shared reference template cannot be deleted by a caller.
    assert tpl.soft_delete_message_template(conn, user_id, ids["cold_dm"]) is False
    assert tpl.get_message_template(conn, user_id, ids["cold_dm"]) is not None
