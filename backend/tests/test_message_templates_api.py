"""End-to-end message-template handler tests through the Powertools resolver (slice-4 #4).

Requests are resolved by the real ``app`` with the principal and connection seams patched (as in
``test_pipeline_api``). Exercises the full HTTP path for the template library: listing shared
seeds, creating a personal template, editing a shared row in place, and Duplicate. Skips without
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
        resp = app_module.app.resolve(event, None)
        parsed = json.loads(resp["body"]) if resp.get("body") else None
        return resp["statusCode"], parsed

    return call


def _find(templates: list[dict], name: str) -> dict | None:
    return next((t for t in templates if t["name"] == name), None)


def test_list_returns_seeded_shared_templates(api) -> None:
    status, body = api("GET", "/templates")
    assert status == 200
    names = {t["name"] for t in body["templates"]}
    assert {"Cold DM", "Cold Email", "Power-Partner DM"} <= names
    assert _find(body["templates"], "Cold DM")["is_shared"] is True


def test_create_personal_template(api) -> None:
    status, created = api(
        "POST",
        "/templates",
        {
            "kind": "cold_pitch",
            "channel": "email",
            "name": "My Pitch",
            "subject": "Hi",
            "body": "x",
        },
    )
    assert status == 200
    assert created["is_shared"] is False
    got_status, got = api("GET", f"/templates/{created['id']}")
    assert got_status == 200 and got["name"] == "My Pitch"


def test_edit_shared_in_place(api) -> None:
    _, body = api("GET", "/templates")
    cold_dm = _find(body["templates"], "Cold DM")
    status, updated = api(
        "PUT",
        f"/templates/{cold_dm['id']}",
        {"kind": "cold_pitch", "channel": "dm", "name": "Cold DM", "body": "Revised"},
    )
    assert status == 200
    assert updated["body"] == "Revised"
    assert updated["is_shared"] is True  # still shared, not forked


def test_duplicate_shared_creates_personal_copy(api) -> None:
    _, body = api("GET", "/templates")
    cold_dm = _find(body["templates"], "Cold DM")
    status, copy = api("POST", f"/templates/{cold_dm['id']}/duplicate")
    assert status == 200
    assert copy["is_shared"] is False
    assert copy["name"] == "Cold DM (copy)"


def test_delete_shared_is_404(api) -> None:
    _, body = api("GET", "/templates")
    cold_dm = _find(body["templates"], "Cold DM")
    # Shared reference templates cannot be deleted → the repo reports no row → 404.
    status, _ = api("DELETE", f"/templates/{cold_dm['id']}")
    assert status == 404


def test_delete_own_template(api) -> None:
    _, created = api(
        "POST", "/templates", {"kind": "cold_pitch", "channel": "dm", "name": "Mine", "body": "b"}
    )
    status, _ = api("DELETE", f"/templates/{created['id']}")
    assert status == 200
    gone_status, _ = api("GET", f"/templates/{created['id']}")
    assert gone_status == 404


def test_create_unknown_kind_is_400(api) -> None:
    status, _ = api(
        "POST", "/templates", {"kind": "nope", "channel": "dm", "name": "X", "body": "b"}
    )
    assert status == 400
