"""Pending-import handler tests through the Powertools resolver.

Requests resolved by the real ``app`` with the principal and connection seams patched, as in
``test_signatures_api``. Skips without ``TEST_DATABASE_URL``.

What this file adds over ``test_email_imports_repository`` is the wire contract: that the routes
are actually registered (a route the CDK table knows about but the resolver does not is the
slice-2 gateway gap, from the other side), that the repository's ``False`` becomes a 404 and its
``InvalidInput`` a 400, and that ``PendingImportSummary`` serializes the rows the repository
returns without a field going missing between them.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import app as app_module
from common.auth import Principal
from handlers import context
from migrations.runner import run_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "src" / "migrations"


@pytest.fixture
def api(db_connection, monkeypatch):
    """Return ``call(method, path, body=None) -> (status, parsed_body)``."""
    run_migrations(db_connection, MIGRATIONS_DIR)
    monkeypatch.setattr(
        context, "principal_from_event", lambda event: Principal(sub="dev", email="dev@example.com")
    )
    monkeypatch.setattr(context, "get_connection", lambda tz: db_connection)

    def call(method: str, path: str, body: dict | None = None):
        event = {
            "version": "2.0",
            "routeKey": f"{method} {path}",
            "rawPath": path,
            "rawQueryString": "",
            "headers": {"content-type": "application/json"},
            "queryStringParameters": None,
            "requestContext": {
                "stage": "$default",
                "http": {"method": method, "path": path, "sourceIp": "1.2.3.4", "userAgent": "t"},
            },
            "body": json.dumps(body) if body is not None else None,
            "isBase64Encoded": False,
        }
        response = app_module.app.resolve(event, None)
        parsed = json.loads(response["body"]) if response.get("body") else None
        return response["statusCode"], parsed

    # One warm-up request so ``upsert_user_id`` creates the caller's ``users`` row. That row is
    # written lazily on the first authenticated request, exactly as it is in production, so tests
    # seeding data against ``_user_id`` need it to exist first.
    call("GET", "/emails/imports")
    return call


def _user_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE cognito_sub = 'dev'")
        return cur.fetchone()["id"]


def _other_user(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('other', 'other@x.com')")
        return cur.lastrowid


def _contact(conn, user_id: int, name: str = "Pat Host", email: str = "pat@riverbend.org") -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO contacts (user_id, name, email) VALUES (%s, %s, %s)",
            (user_id, name, email),
        )
        return cur.lastrowid


def _organization(conn, user_id: int, name: str, email_domain: str | None) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM organization_types LIMIT 1")
        type_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO organizations (user_id, organization_type_id, name, email_domain) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, type_id, name, email_domain),
        )
        return cur.lastrowid


def _opportunity(conn, user_id: int, organization_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM opportunity_statuses ORDER BY sort_order LIMIT 1")
        status_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM payment_statuses LIMIT 1")
        payment_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM opportunity_formats LIMIT 1")
        format_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM comp_types LIMIT 1")
        comp_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO opportunities (user_id, organization_id, opportunity_format_id, "
            " current_status_id, comp_type_id, payment_status_id, title, currency) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'Fall keynote', 'USD')",
            (user_id, organization_id, format_id, status_id, comp_id, payment_id),
        )
        return cur.lastrowid


def _pending_thread(
    conn,
    user_id: int,
    *,
    from_addr: str = "Pat Host <pat@riverbend.org>",
    contact_id: int | None = None,
) -> int:
    """Create a thread with one inbound message — the shape the poller leaves behind."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_threads "
            "(user_id, contact_id, subject_normalized, last_direction, last_message_at) "
            "VALUES (%s, %s, 'Speaking inquiry', 'in', %s)",
            (user_id, contact_id, dt.datetime(2026, 7, 27, 10, 0)),
        )
        thread_id = cur.lastrowid
        cur.execute(
            "INSERT INTO email_messages "
            "(user_id, thread_id, message_id, direction, from_addr, subject, received_at) "
            "VALUES (%s, %s, %s, 'in', %s, 'Speaking inquiry', %s)",
            (
                user_id,
                thread_id,
                f"<t{thread_id}@riverbend.org>",
                from_addr,
                dt.datetime(2026, 7, 27, 10, 0),
            ),
        )
        return thread_id


# --- GET /emails/imports ---------------------------------------------------------------------


def test_an_empty_queue_returns_an_empty_list(api) -> None:
    status, body = api("GET", "/emails/imports")
    assert status == 200
    assert body == {"imports": []}


def test_a_pending_thread_serializes_every_field_the_model_declares(api, db_connection) -> None:
    """Guards the seam between the repository's dict and ``PendingImportSummary``: a key renamed on
    one side and not the other would only show up here."""
    user_id = _user_id(db_connection)
    org_id = _organization(db_connection, user_id, "Riverbend Center", "riverbend.org")
    thread_id = _pending_thread(db_connection, user_id)

    status, body = api("GET", "/emails/imports")

    assert status == 200
    assert body["imports"] == [
        {
            "thread_id": thread_id,
            "email_message_id": body["imports"][0]["email_message_id"],
            "from_addr": "pat@riverbend.org",
            "from_name": "Pat Host",
            "subject": "Speaking inquiry",
            "received_at": "2026-07-27T10:00:00",
            "suggested_organization_id": org_id,
            "suggested_organization_name": "Riverbend Center",
        }
    ]


def test_the_queue_is_owner_scoped(api, db_connection) -> None:
    other_id = _other_user(db_connection)
    _pending_thread(db_connection, other_id)

    status, body = api("GET", "/emails/imports")
    assert (status, body) == (200, {"imports": []})


def test_the_badge_count_is_the_length_of_this_list(api, db_connection) -> None:
    """There is deliberately no count endpoint: a second query could disagree with the first."""
    user_id = _user_id(db_connection)
    _pending_thread(db_connection, user_id)
    _pending_thread(db_connection, user_id)

    _status, body = api("GET", "/emails/imports")
    assert len(body["imports"]) == 2


# --- PUT /emails/threads/{id}/contact ---------------------------------------------------------


def test_linking_a_contact_returns_the_link_and_empties_the_queue(api, db_connection) -> None:
    user_id = _user_id(db_connection)
    contact_id = _contact(db_connection, user_id)
    thread_id = _pending_thread(db_connection, user_id)

    status, body = api("PUT", f"/emails/threads/{thread_id}/contact", {"contact_id": contact_id})

    assert status == 200
    assert body == {"thread_id": thread_id, "contact_id": contact_id}
    assert api("GET", "/emails/imports")[1] == {"imports": []}


def test_linking_the_same_contact_twice_still_succeeds(api, db_connection) -> None:
    """Why the route is PUT: setting a property to the value it already holds is not a failure.
    The sibling /close route 404s on a second call because closing is a verb; this is not."""
    user_id = _user_id(db_connection)
    contact_id = _contact(db_connection, user_id)
    thread_id = _pending_thread(db_connection, user_id)
    path = f"/emails/threads/{thread_id}/contact"

    assert api("PUT", path, {"contact_id": contact_id})[0] == 200
    assert api("PUT", path, {"contact_id": contact_id})[0] == 200


def test_an_unknown_thread_is_a_404(api, db_connection) -> None:
    user_id = _user_id(db_connection)
    contact_id = _contact(db_connection, user_id)

    status, _body = api("PUT", "/emails/threads/99999/contact", {"contact_id": contact_id})
    assert status == 404


def test_another_users_thread_is_a_404_not_a_403(api, db_connection) -> None:
    """The response must not distinguish "does not exist" from "exists but is not yours", which
    would confirm the existence of another account's row."""
    user_id = _user_id(db_connection)
    other_id = _other_user(db_connection)
    contact_id = _contact(db_connection, user_id)
    theirs = _pending_thread(db_connection, other_id)

    status, _body = api("PUT", f"/emails/threads/{theirs}/contact", {"contact_id": contact_id})
    assert status == 404


def test_an_unknown_contact_is_a_400(api, db_connection) -> None:
    user_id = _user_id(db_connection)
    thread_id = _pending_thread(db_connection, user_id)

    status, _body = api("PUT", f"/emails/threads/{thread_id}/contact", {"contact_id": 99999})
    assert status == 400


def test_another_users_contact_is_a_400(api, db_connection) -> None:
    user_id = _user_id(db_connection)
    other_id = _other_user(db_connection)
    theirs = _contact(db_connection, other_id, "Theirs", "theirs@x.com")
    thread_id = _pending_thread(db_connection, user_id)

    status, _body = api("PUT", f"/emails/threads/{thread_id}/contact", {"contact_id": theirs})
    assert status == 400


def test_sending_a_null_contact_detaches_and_returns_the_thread_to_the_queue(
    api, db_connection
) -> None:
    """The correction for linking the wrong person.

    ``contact_id`` was non-nullable when this route was written, on the grounds that a detached
    thread would land back in a queue with no interface. Building that queue removed the objection.
    """
    user_id = _user_id(db_connection)
    contact_id = _contact(db_connection, user_id)
    thread_id = _pending_thread(db_connection, user_id)
    path = f"/emails/threads/{thread_id}/contact"

    api("PUT", path, {"contact_id": contact_id})
    assert api("GET", "/emails/imports")[1]["imports"] == []

    status, body = api("PUT", path, {"contact_id": None})
    assert status == 200
    assert body == {"thread_id": thread_id, "contact_id": None}
    assert len(api("GET", "/emails/imports")[1]["imports"]) == 1


def test_an_omitted_contact_id_also_detaches(api, db_connection) -> None:
    """Symmetric with the opportunity route: the field defaults to ``None``, so an empty body is a
    detach rather than a 400."""
    user_id = _user_id(db_connection)
    thread_id = _pending_thread(db_connection, user_id)

    status, body = api("PUT", f"/emails/threads/{thread_id}/contact", {})
    assert status == 200
    assert body["contact_id"] is None


def test_a_non_numeric_thread_id_is_a_404(api, db_connection) -> None:
    user_id = _user_id(db_connection)
    contact_id = _contact(db_connection, user_id)

    status, _body = api("PUT", "/emails/threads/abc/contact", {"contact_id": contact_id})
    assert status == 404


# --- PUT /emails/threads/{id}/opportunity ------------------------------------------------------


def test_linking_a_gig_returns_the_link(api, db_connection) -> None:
    user_id = _user_id(db_connection)
    org_id = _organization(db_connection, user_id, "Riverbend", "riverbend.org")
    opportunity_id = _opportunity(db_connection, user_id, org_id)
    thread_id = _pending_thread(db_connection, user_id)

    status, body = api(
        "PUT", f"/emails/threads/{thread_id}/opportunity", {"opportunity_id": opportunity_id}
    )

    assert status == 200
    assert body == {"thread_id": thread_id, "opportunity_id": opportunity_id}


def test_sending_a_null_opportunity_detaches(api, db_connection) -> None:
    """The correction path for a thread linked to the wrong gig — and the only way back, since
    nothing infers an opportunity for an inbound-first thread."""
    user_id = _user_id(db_connection)
    org_id = _organization(db_connection, user_id, "Riverbend", "riverbend.org")
    opportunity_id = _opportunity(db_connection, user_id, org_id)
    thread_id = _pending_thread(db_connection, user_id)
    path = f"/emails/threads/{thread_id}/opportunity"

    api("PUT", path, {"opportunity_id": opportunity_id})
    status, body = api("PUT", path, {"opportunity_id": None})

    assert status == 200
    assert body == {"thread_id": thread_id, "opportunity_id": None}
    with db_connection.cursor() as cur:
        cur.execute("SELECT opportunity_id FROM email_threads WHERE id = %s", (thread_id,))
        assert cur.fetchone()["opportunity_id"] is None


def test_an_omitted_opportunity_id_also_detaches(api, db_connection) -> None:
    """``opportunity_id`` defaults to ``None``, so an empty body is a detach rather than a 400."""
    user_id = _user_id(db_connection)
    thread_id = _pending_thread(db_connection, user_id)

    status, body = api("PUT", f"/emails/threads/{thread_id}/opportunity", {})
    assert status == 200
    assert body["opportunity_id"] is None


def test_another_users_gig_is_a_400(api, db_connection) -> None:
    user_id = _user_id(db_connection)
    other_id = _other_user(db_connection)
    their_org = _organization(db_connection, other_id, "Theirs", "theirs.org")
    theirs = _opportunity(db_connection, other_id, their_org)
    thread_id = _pending_thread(db_connection, user_id)

    status, _body = api(
        "PUT", f"/emails/threads/{thread_id}/opportunity", {"opportunity_id": theirs}
    )
    assert status == 400


def test_linking_a_gig_on_an_unknown_thread_is_a_404(api) -> None:
    status, _body = api("PUT", "/emails/threads/99999/opportunity", {"opportunity_id": None})
    assert status == 404


def test_a_gig_link_does_not_remove_the_thread_from_the_queue(api, db_connection) -> None:
    """The queue is about attribution to a person; a gig link is a separate axis."""
    user_id = _user_id(db_connection)
    org_id = _organization(db_connection, user_id, "Riverbend", "riverbend.org")
    opportunity_id = _opportunity(db_connection, user_id, org_id)
    thread_id = _pending_thread(db_connection, user_id)

    api("PUT", f"/emails/threads/{thread_id}/opportunity", {"opportunity_id": opportunity_id})
    assert len(api("GET", "/emails/imports")[1]["imports"]) == 1


# --- registration ------------------------------------------------------------------------------


def test_the_routes_are_registered_under_the_methods_the_cdk_table_declares(api) -> None:
    """A route the CDK ROUTES table knows about but the resolver does not is the slice-2 gateway
    gap seen from the other side — the request reaches the Lambda and 404s there instead."""
    assert api("GET", "/emails/imports")[0] == 200
    assert api("POST", "/emails/imports")[0] == 404
    assert api("GET", "/emails/threads/1/contact")[0] == 404
    assert api("POST", "/emails/threads/1/contact", {"contact_id": 1})[0] == 404
