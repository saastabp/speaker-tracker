"""End-to-end signature handler tests through the Powertools resolver.

Requests resolved by the real ``app`` with the principal + connection seams patched (as in
``test_message_templates_api``). Skips without ``TEST_DATABASE_URL``.
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


def test_create_list_and_default(api) -> None:
    status, created = api(
        "POST",
        "/signatures",
        {"name": "Main", "body_html": "<p>Best, Donna</p>", "is_default": True},
    )
    assert status == 200
    assert created["name"] == "Main" and created["is_default"] is True
    status, body = api("GET", "/signatures")
    assert status == 200 and any(s["id"] == created["id"] for s in body["signatures"])
    status, dflt = api("GET", "/signatures/default")
    assert status == 200 and dflt["signature"]["id"] == created["id"]


def test_default_null_when_none(api) -> None:
    status, dflt = api("GET", "/signatures/default")
    assert status == 200 and dflt["signature"] is None


def test_second_default_wins(api) -> None:
    _, a = api("POST", "/signatures", {"name": "A", "body_html": "<p>a</p>", "is_default": True})
    _, b = api("POST", "/signatures", {"name": "B", "body_html": "<p>b</p>", "is_default": True})
    _, dflt = api("GET", "/signatures/default")
    assert dflt["signature"]["id"] == b["id"]


def test_update(api) -> None:
    _, created = api("POST", "/signatures", {"name": "X", "body_html": "<p>x</p>"})
    status, updated = api(
        "PUT",
        f"/signatures/{created['id']}",
        {"name": "X2", "body_html": "<p>y</p>", "is_default": True},
    )
    assert status == 200 and updated["name"] == "X2" and updated["is_default"] is True


def test_update_missing_is_404(api) -> None:
    status, _ = api("PUT", "/signatures/999999", {"name": "N", "body_html": "<p>n</p>"})
    assert status == 404


def test_delete_then_missing(api) -> None:
    _, created = api("POST", "/signatures", {"name": "D", "body_html": "<p>d</p>"})
    status, _ = api("DELETE", f"/signatures/{created['id']}")
    assert status == 200
    status, _ = api("DELETE", f"/signatures/{created['id']}")
    assert status == 404
