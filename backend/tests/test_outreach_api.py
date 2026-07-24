"""End-to-end outreach + timeline handler tests through the Powertools resolver (slice-4 #1/#5/#6).

Requests are resolved by the real ``app`` with two seams patched (fixed dev principal, test
connection), mirroring ``test_pipeline_api``. Exercises the full HTTP path: routing,
``authenticate``, request validation, the JSON envelope, kind inference over HTTP, the contact
timeline union, and the decoupling of outreach from pipeline stage. Skips without
``TEST_DATABASE_URL``.
"""

from __future__ import annotations

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
def seeded(api):
    """Create a venue, a contact, and an opportunity through the API; return their ids."""
    _, org = api("POST", "/organizations", {"organization_type": "expo", "name": "Kauai Expo"})
    _, contact = api("POST", "/contacts", {"name": "Jane Coordinator"})
    _, opp = api(
        "POST",
        "/opportunities",
        {
            "title": "Wellness Workshop",
            "organization_id": org["id"],
            "opportunity_format": "workshop",
            "comp_type": "paid",
        },
    )
    return {"org": org["id"], "contact": contact["id"], "opp": opp["id"]}


def test_create_infers_initial_then_correspondence(api, seeded) -> None:
    status1, first = api("POST", "/outreaches", {"contact_id": seeded["contact"], "channel": "dm"})
    assert status1 == 200
    assert first["kind"] == "initial"  # first touch to the contact (#1)
    _, second = api("POST", "/outreaches", {"contact_id": seeded["contact"], "channel": "email"})
    assert second["kind"] == "correspondence"  # later touch defaults to correspondence (#1)


def test_kind_override_persists(api, seeded) -> None:
    _, row = api(
        "POST",
        "/outreaches",
        {"contact_id": seeded["contact"], "channel": "dm", "kind": "follow_up"},
    )
    assert row["kind"] == "follow_up"


def test_create_unknown_channel_is_400(api, seeded) -> None:
    status, _ = api("POST", "/outreaches", {"contact_id": seeded["contact"], "channel": "nope"})
    assert status == 400  # domain InvalidInput → 400


def test_list_contact_outreaches(api, seeded) -> None:
    api("POST", "/outreaches", {"contact_id": seeded["contact"], "channel": "dm"})
    status, body = api("GET", f"/contacts/{seeded['contact']}/outreaches")
    assert status == 200
    assert len(body["outreaches"]) == 1
    assert body["outreaches"][0]["contact_name"] == "Jane Coordinator"


def test_logging_outreach_does_not_change_stage(api, seeded) -> None:
    # A gig-attributed touch must not move the opportunity's pipeline stage (#6).
    api(
        "POST",
        "/outreaches",
        {"contact_id": seeded["contact"], "channel": "dm", "opportunity_id": seeded["opp"]},
    )
    _, opp = api("GET", f"/opportunities/{seeded['opp']}")
    assert opp["current_status"] == "researching"


def test_timeline_interleaves_and_deletes(api, seeded) -> None:
    # Link the contact to the gig so the opportunity's status event surfaces on the timeline (#5).
    api(
        "POST",
        f"/opportunities/{seeded['opp']}/contacts",
        {"contact_id": seeded["contact"], "is_primary": True},
    )
    _, created = api("POST", "/outreaches", {"contact_id": seeded["contact"], "channel": "dm"})
    status, body = api("GET", f"/contacts/{seeded['contact']}/timeline")
    assert status == 200
    types = {item["item_type"] for item in body["timeline"]}
    assert "outreach" in types and "status_event" in types

    # Deleting the outreach drops it from the timeline.
    del_status, _ = api("DELETE", f"/outreaches/{created['id']}")
    assert del_status == 200
    _, body2 = api("GET", f"/contacts/{seeded['contact']}/timeline")
    assert all(item["item_type"] != "outreach" for item in body2["timeline"])


def test_delete_missing_outreach_is_404(api, seeded) -> None:
    status, _ = api("DELETE", "/outreaches/999999")
    assert status == 404
