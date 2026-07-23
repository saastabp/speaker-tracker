"""Talks repository tests against a seeded MySQL — CRUD, ordering, soft-delete, owner scope.

Skip without ``TEST_DATABASE_URL`` (see conftest). Talks carry no catalog FKs and no unique-name
constraint, so these cover the plain persistence path plus the soft-delete/scope invariants.
"""

from __future__ import annotations

from models.talks import TalkInput
from repositories import talks


def _talk(title: str = "Boundaries 101", **kw) -> TalkInput:
    return TalkInput(title=title, **kw)


def test_create_and_get(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    talk_id = talks.create_talk(conn, user_id, _talk(length_minutes=45, one_liner="A talk"))
    row = talks.get_talk(conn, user_id, talk_id)
    assert row["title"] == "Boundaries 101"
    assert row["length_minutes"] == 45
    assert row["one_liner"] == "A talk"
    assert row["sort_order"] == 0


def test_list_orders_by_sort_order_then_title(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    talks.create_talk(conn, user_id, _talk("Zebra", sort_order=10))
    talks.create_talk(conn, user_id, _talk("Alpha", sort_order=20))
    talks.create_talk(conn, user_id, _talk("Beta", sort_order=10))
    rows = talks.list_talks(conn, user_id)
    assert [r["title"] for r in rows] == ["Beta", "Zebra", "Alpha"]


def test_update_replaces_fields(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    talk_id = talks.create_talk(conn, user_id, _talk(one_liner="old"))
    assert talks.update_talk(conn, user_id, talk_id, _talk(one_liner="new", sort_order=5)) is True
    row = talks.get_talk(conn, user_id, talk_id)
    assert row["one_liner"] == "new"
    assert row["sort_order"] == 5


def test_update_missing_returns_false(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    assert talks.update_talk(conn, user_id, 999, _talk()) is False


def test_soft_delete_hides_and_is_idempotent(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    talk_id = talks.create_talk(conn, user_id, _talk())
    assert talks.soft_delete_talk(conn, user_id, talk_id) is True
    assert talks.get_talk(conn, user_id, talk_id) is None
    assert talks.list_talks(conn, user_id) == []
    assert talks.soft_delete_talk(conn, user_id, talk_id) is False


def test_get_is_scoped_to_owner(seeded_db, db_connection) -> None:
    conn, user_id, _, _ = seeded_db
    talk_id = talks.create_talk(conn, user_id, _talk())
    with db_connection.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('u2', 'u2@x')")
        other_user = cur.lastrowid
    assert talks.get_talk(conn, other_user, talk_id) is None
