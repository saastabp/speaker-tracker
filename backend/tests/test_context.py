"""Tests for ``handlers.context.authenticate`` wiring — no database required.

Verifies the shared authentication step resolves the principal, opens a timezone-scoped
connection, upserts the caller's ``users`` row, and returns all three. The DB and repository
calls are stubbed, so this runs everywhere.
"""

from __future__ import annotations

from handlers import context


def test_authenticate_resolves_principal_connection_and_user(monkeypatch) -> None:
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "abc", "email": "d@e.com"}}}},
        "headers": {"x-user-timezone": "Pacific/Honolulu"},
    }
    fake_conn = object()
    seen: dict = {}

    def fake_get_connection(tz):
        seen["tz"] = tz
        return fake_conn

    def fake_upsert(conn, sub, email):
        seen.update(conn=conn, sub=sub, email=email)
        return 42

    monkeypatch.setattr(context, "get_connection", fake_get_connection)
    monkeypatch.setattr(context, "upsert_user_id", fake_upsert)

    request = context.authenticate(event)

    assert request.principal.sub == "abc"
    assert request.principal.email == "d@e.com"
    assert request.connection is fake_conn
    assert request.user_id == 42
    # The validated caller timezone reaches get_connection, and the upsert gets the principal.
    assert seen["tz"] == "Pacific/Honolulu"
    assert seen["conn"] is fake_conn
    assert seen["sub"] == "abc"
    assert seen["email"] == "d@e.com"
