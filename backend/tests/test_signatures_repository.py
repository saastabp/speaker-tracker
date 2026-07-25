"""Signatures repository tests against a seeded MySQL — CRUD, the single-default invariant, tenancy.

Skip without ``TEST_DATABASE_URL`` (see conftest).
"""

from __future__ import annotations

from models.signatures import SignatureInput
from repositories import signatures as sig_repo


def _sig(
    name: str = "Formal", body_html: str = "<p>hi</p>", is_default: bool = False
) -> SignatureInput:
    return SignatureInput(name=name, body_html=body_html, is_default=is_default)


def test_create_and_get(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    sid = sig_repo.create_signature(conn, user_id, _sig(name="Formal", body_html="<p>Best</p>"))
    row = sig_repo.get_signature(conn, user_id, sid)
    assert row["name"] == "Formal"
    assert row["body_html"] == "<p>Best</p>"
    assert bool(row["is_default"]) is False


def test_second_default_clears_first(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    a = sig_repo.create_signature(conn, user_id, _sig(name="A", is_default=True))
    b = sig_repo.create_signature(conn, user_id, _sig(name="B", is_default=True))
    assert bool(sig_repo.get_signature(conn, user_id, a)["is_default"]) is False
    assert bool(sig_repo.get_signature(conn, user_id, b)["is_default"]) is True
    assert sig_repo.get_default_signature(conn, user_id)["id"] == b


def test_update_to_default_clears_others(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    a = sig_repo.create_signature(conn, user_id, _sig(name="A", is_default=True))
    b = sig_repo.create_signature(conn, user_id, _sig(name="B", is_default=False))
    assert sig_repo.update_signature(conn, user_id, b, _sig(name="B", is_default=True)) is True
    assert bool(sig_repo.get_signature(conn, user_id, a)["is_default"]) is False
    assert sig_repo.get_default_signature(conn, user_id)["id"] == b


def test_list_orders_default_first(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    sig_repo.create_signature(conn, user_id, _sig(name="Zeta", is_default=False))
    sig_repo.create_signature(conn, user_id, _sig(name="Alpha", is_default=True))
    names = [r["name"] for r in sig_repo.list_signatures(conn, user_id)]
    assert names[0] == "Alpha"  # default first regardless of name


def test_soft_delete_hides_and_clears_default(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    a = sig_repo.create_signature(conn, user_id, _sig(name="A", is_default=True))
    assert sig_repo.soft_delete_signature(conn, user_id, a) is True
    assert sig_repo.get_signature(conn, user_id, a) is None
    assert sig_repo.get_default_signature(conn, user_id) is None
    assert sig_repo.soft_delete_signature(conn, user_id, a) is False  # already gone


def test_owner_scoped(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    sid = sig_repo.create_signature(conn, user_id, _sig())
    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('u2', 'u2@x')")
        other = cur.lastrowid
    assert sig_repo.get_signature(conn, other, sid) is None
    assert sig_repo.update_signature(conn, other, sid, _sig(name="hax")) is False
    assert sig_repo.soft_delete_signature(conn, other, sid) is False


def test_get_default_none_when_unset(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    sig_repo.create_signature(conn, user_id, _sig(is_default=False))
    assert sig_repo.get_default_signature(conn, user_id) is None
