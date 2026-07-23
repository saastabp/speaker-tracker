"""End-to-end handler tests through the Powertools resolver — slice-2 acceptance + error mapping.

Requests are resolved by the real ``app`` with two seams patched: the principal is a fixed dev
user (no Cognito), and ``get_connection`` returns the test connection (no RDS/IAM). This exercises
the full HTTP path — routing, ``authenticate``, request validation, the bare-JSON envelope, the
computed ``research_ready``, and the domain-error → status mapping. Skips without
``TEST_DATABASE_URL`` (via the ``db_connection`` fixture).
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


def _first_short_name(conn, table: str) -> str:
    with conn.cursor() as cur:
        cur.execute(f"SELECT short_name FROM {table} LIMIT 1")
        return cur.fetchone()["short_name"]


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
            "rawQueryString": "&".join(f"{k}={v}" for k, v in (params or {}).items()),
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


def test_create_organization_requires_name(api) -> None:
    status, body = api("POST", "/organizations", {"organization_type": "womens_network"})
    assert status == 400
    assert body == {"error": "invalid request"}


def test_create_then_get_organization(api, db_connection) -> None:
    org_type = _first_short_name(db_connection, "organization_types")
    status, created = api("POST", "/organizations", {"organization_type": org_type, "name": "PWN"})
    assert status == 200
    assert created["name"] == "PWN"
    assert created["research_ready"] is False
    assert created["contact_count"] == 0

    status, fetched = api("GET", f"/organizations/{created['id']}")
    assert status == 200
    assert fetched["id"] == created["id"]


def test_duplicate_organization_name_conflicts(api, db_connection) -> None:
    org_type = _first_short_name(db_connection, "organization_types")
    api("POST", "/organizations", {"organization_type": org_type, "name": "PWN"})
    status, body = api("POST", "/organizations", {"organization_type": org_type, "name": "PWN"})
    assert status == 409
    assert "already exists" in body["error"]


def test_get_missing_organization_is_404(api) -> None:
    assert api("GET", "/organizations/999")[0] == 404


def test_malformed_id_is_404(api) -> None:
    assert api("GET", "/organizations/not-an-int")[0] == 404


def test_contact_affiliated_with_two_organizations(api, db_connection) -> None:
    org_type = _first_short_name(db_connection, "organization_types")
    a = api("POST", "/organizations", {"organization_type": org_type, "name": "Alpha"})[1]["id"]
    b = api("POST", "/organizations", {"organization_type": org_type, "name": "Bravo"})[1]["id"]
    contact = api("POST", "/contacts", {"name": "Jane"})[1]

    api(
        "POST", f"/contacts/{contact['id']}/organizations", {"organization_id": a, "title": "Chair"}
    )
    status, updated = api(
        "POST",
        f"/contacts/{contact['id']}/organizations",
        {"organization_id": b, "title": "Member"},
    )
    assert status == 200
    assert {o["organization_name"] for o in updated["organizations"]} == {"Alpha", "Bravo"}

    # appears under both orgs
    for org_id in (a, b):
        org = api("GET", f"/organizations/{org_id}")[1]
        assert [c["name"] for c in org["contacts"]] == ["Jane"]


def test_duplicate_affiliation_conflicts(api, db_connection) -> None:
    org_type = _first_short_name(db_connection, "organization_types")
    a = api("POST", "/organizations", {"organization_type": org_type, "name": "Alpha"})[1]["id"]
    contact = api("POST", "/contacts", {"name": "Jane"})[1]
    api("POST", f"/contacts/{contact['id']}/organizations", {"organization_id": a})
    status, _ = api("POST", f"/contacts/{contact['id']}/organizations", {"organization_id": a})
    assert status == 409


def test_dedupe_search_finds_existing_contact(api) -> None:
    api("POST", "/contacts", {"name": "Jane Doe", "email": "jane@venue.com"})
    api("POST", "/contacts", {"name": "Bob Smith"})
    status, body = api("GET", "/contacts", params={"q": "jane"})
    assert status == 200
    assert [c["name"] for c in body["contacts"]] == ["Jane Doe"]


def test_research_ready_requires_kindling_and_a_contact(api, db_connection) -> None:
    org_type = _first_short_name(db_connection, "organization_types")
    org = api(
        "POST",
        "/organizations",
        {
            "organization_type": org_type,
            "name": "PWN",
            "what_it_is": "a network",
            "why_it_fits": "great audience",
            "how_to_approach": "attend first",
        },
    )[1]
    assert org["research_ready"] is False  # kindling filled but no contact yet

    contact = api("POST", "/contacts", {"name": "Jane"})[1]
    api("POST", f"/contacts/{contact['id']}/organizations", {"organization_id": org["id"]})
    refreshed = api("GET", f"/organizations/{org['id']}")[1]
    assert refreshed["research_ready"] is True


def test_soft_delete_hides_organization(api, db_connection) -> None:
    org_type = _first_short_name(db_connection, "organization_types")
    org_id = api("POST", "/organizations", {"organization_type": org_type, "name": "PWN"})[1]["id"]
    assert api("DELETE", f"/organizations/{org_id}") == (200, {"deleted": True})
    assert api("GET", f"/organizations/{org_id}")[0] == 404
    assert api("GET", "/organizations")[1] == {"organizations": []}
