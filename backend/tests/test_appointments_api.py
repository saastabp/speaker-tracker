"""End-to-end appointment handler tests through the Powertools resolver (slice 11).

Requests are resolved by the real ``app`` with two seams patched (fixed dev principal, test
connection), mirroring ``test_outreach_api``. Exercises the full HTTP path: routing, the scope
query parameter and its rejection of an unknown value, the patch's explicit-null semantics over
the wire, and the 404s. Skips without ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import app as app_module
from common.auth import Principal
from handlers import context
from migrations.runner import run_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "src" / "migrations"


@pytest.fixture
def api(db_connection, monkeypatch):
    """Return ``call(method, path, body=None, params=None) -> (status, parsed_body)``."""
    run_migrations(db_connection, MIGRATIONS_DIR)
    monkeypatch.setattr(
        context, "principal_from_event", lambda event: Principal(sub="dev", email="dev@example.com")
    )
    monkeypatch.setattr(context, "get_connection", lambda tz: db_connection)

    def call(method: str, path: str, body: dict | None = None, params: dict | None = None):
        event = {
            "version": "2.0",
            "routeKey": f"{method} {path}",
            "rawPath": path,
            "rawQueryString": "",
            "headers": {"content-type": "application/json"},
            "queryStringParameters": params or None,
            "requestContext": {
                "stage": "$default",
                "http": {"method": method, "path": path, "sourceIp": "1.2.3.4", "userAgent": "t"},
            },
            "body": json.dumps(body) if body is not None else None,
            "isBase64Encoded": False,
        }
        resp = app_module.app.resolve(event, None)
        parsed = json.loads(resp["body"]) if resp.get("body") else None
        return resp["statusCode"], parsed

    return call


@pytest.fixture
def contacts(api):
    """Two contacts created through the API; returns their ids."""
    _, kalei = api("POST", "/contacts", {"name": "Kalei Nakamura"})
    _, iris = api("POST", "/contacts", {"name": "Iris Chen"})
    return {"kalei": kalei["id"], "iris": iris["id"]}


def _iso(when: datetime) -> str:
    return when.isoformat()


#: Far enough out that the row is unambiguously upcoming against the real session clock.
NEXT_WEEK = datetime.now().replace(microsecond=0) + timedelta(days=7)
LAST_WEEK = datetime.now().replace(microsecond=0) - timedelta(days=7)


def test_create_then_list_round_trips(api, contacts) -> None:
    status, created = api(
        "POST",
        "/appointments",
        {
            "contact_id": contacts["kalei"],
            "title": "Coffee",
            "scheduled_at": _iso(NEXT_WEEK),
            "details": "At Java Kai",
        },
    )
    assert status == 200
    assert created["contact_name"] == "Kalei Nakamura"
    assert created["title"] == "Coffee"
    assert created["details"] == "At Java Kai"

    status, listed = api("GET", "/appointments")
    assert status == 200
    assert [a["id"] for a in listed["appointments"]] == [created["id"]]


def test_scope_filters_upcoming_and_past(api, contacts) -> None:
    api(
        "POST",
        "/appointments",
        {"contact_id": contacts["kalei"], "title": "Future", "scheduled_at": _iso(NEXT_WEEK)},
    )
    api(
        "POST",
        "/appointments",
        {"contact_id": contacts["kalei"], "title": "Past", "scheduled_at": _iso(LAST_WEEK)},
    )

    _, upcoming = api("GET", "/appointments", params={"scope": "upcoming"})
    _, past = api("GET", "/appointments", params={"scope": "past"})
    _, every = api("GET", "/appointments")

    assert [a["title"] for a in upcoming["appointments"]] == ["Future"]
    assert [a["title"] for a in past["appointments"]] == ["Past"]
    assert {a["title"] for a in every["appointments"]} == {"Future", "Past"}


def test_an_unknown_scope_is_rejected_rather_than_ignored(api, contacts) -> None:
    status, body = api("GET", "/appointments", params={"scope": "sideways"})
    assert status == 400
    assert "scope" in body["error"]


def test_contact_filter_narrows_the_list(api, contacts) -> None:
    api(
        "POST",
        "/appointments",
        {"contact_id": contacts["kalei"], "title": "With Kalei", "scheduled_at": _iso(NEXT_WEEK)},
    )
    api(
        "POST",
        "/appointments",
        {"contact_id": contacts["iris"], "title": "With Iris", "scheduled_at": _iso(NEXT_WEEK)},
    )
    _, body = api("GET", "/appointments", params={"contact_id": str(contacts["iris"])})
    assert [a["title"] for a in body["appointments"]] == ["With Iris"]


def test_patch_returns_the_updated_row(api, contacts) -> None:
    _, created = api(
        "POST",
        "/appointments",
        {"contact_id": contacts["kalei"], "title": "Coffee", "scheduled_at": _iso(NEXT_WEEK)},
    )
    status, patched = api(
        "PATCH",
        f"/appointments/{created['id']}",
        {"title": "Lunch", "contact_id": contacts["iris"]},
    )
    assert status == 200
    assert patched["title"] == "Lunch"
    assert patched["contact_name"] == "Iris Chen"
    assert patched["scheduled_at"] == created["scheduled_at"]  # untouched


def test_an_explicit_null_clears_details_over_the_wire(api, contacts) -> None:
    _, created = api(
        "POST",
        "/appointments",
        {
            "contact_id": contacts["kalei"],
            "title": "Coffee",
            "scheduled_at": _iso(NEXT_WEEK),
            "details": "At Java Kai",
        },
    )
    _, unchanged = api("PATCH", f"/appointments/{created['id']}", {"title": "Lunch"})
    assert unchanged["details"] == "At Java Kai"

    _, cleared = api("PATCH", f"/appointments/{created['id']}", {"details": None})
    assert cleared["details"] is None


def test_delete_removes_it_from_the_list(api, contacts) -> None:
    _, created = api(
        "POST",
        "/appointments",
        {"contact_id": contacts["kalei"], "title": "Coffee", "scheduled_at": _iso(NEXT_WEEK)},
    )
    status, body = api("DELETE", f"/appointments/{created['id']}")
    assert (status, body) == (200, {"deleted": True})
    _, listed = api("GET", "/appointments")
    assert listed["appointments"] == []


@pytest.mark.parametrize("method,body", [("PATCH", {"title": "x"}), ("DELETE", None)])
def test_touching_a_missing_appointment_is_404(api, contacts, method, body) -> None:
    status, _ = api(method, "/appointments/9999", body)
    assert status == 404


def test_a_missing_title_is_rejected(api, contacts) -> None:
    status, _ = api(
        "POST",
        "/appointments",
        {"contact_id": contacts["kalei"], "scheduled_at": _iso(NEXT_WEEK)},
    )
    assert status == 400
