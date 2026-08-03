"""End-to-end targets + dashboard handler tests through the Powertools resolver (slice 5).

Requests are resolved by the real ``app`` with the principal and connection seams patched (as in
``test_pipeline_api``). Exercises the full HTTP path: routing, ``authenticate``, request validation,
the JSON envelope, the targets upsert/delete, and the composite dashboard shape. Skips without
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


def test_targets_upsert_list_and_delete(api) -> None:
    status, target = api(
        "PUT", "/targets", {"target_type": "outreaches", "cadence": "weekly", "goal_count": 5}
    )
    assert status == 200
    assert target == {"target_type": "outreaches", "cadence": "weekly", "goal_count": 5}
    # PUT again updates in place.
    api("PUT", "/targets", {"target_type": "outreaches", "cadence": "weekly", "goal_count": 9})
    _, listed = api("GET", "/targets")
    assert listed["targets"] == [
        {"target_type": "outreaches", "cadence": "weekly", "goal_count": 9}
    ]
    # Delete unsets it.
    del_status, _ = api("DELETE", "/targets/outreaches/weekly")
    assert del_status == 200
    _, after = api("GET", "/targets")
    assert after["targets"] == []


def test_put_bad_cadence_is_400(api) -> None:
    status, _ = api(
        "PUT", "/targets", {"target_type": "outreaches", "cadence": "daily", "goal_count": 5}
    )
    assert status == 400  # pydantic Literal validation → ValidationError → 400


def test_put_unknown_target_type_is_400(api) -> None:
    status, _ = api(
        "PUT", "/targets", {"target_type": "nope", "cadence": "weekly", "goal_count": 5}
    )
    assert status == 400  # domain InvalidInput


def test_delete_missing_target_is_404(api) -> None:
    status, _ = api("DELETE", "/targets/outreaches/weekly")
    assert status == 404


def test_dashboard_returns_all_sections(api) -> None:
    status, body = api("GET", "/dashboard")
    assert status == 200
    assert set(body) == {
        "week",
        "targets",
        "funnel",
        "money",
        "needs_attention",
        "coming_up",
        "follow_ups",
    }
    assert len(body["funnel"]) == 5  # all five funnel stages always present
    assert body["money"]["currency"] == "USD"


def test_the_dashboard_can_be_asked_for_a_past_week(api) -> None:
    status, body = api("GET", "/dashboard", params={"week_of": "2026-07-15"})
    assert status == 200
    # Any day in the week resolves to its Sunday-start bounds, and the response says which week it
    # answered for — the SPA labels the navigator from this rather than recomputing week maths.
    assert body["week"] == {"start": "2026-07-12", "end": "2026-07-19"}
    for tile in body["targets"]:
        if tile["cadence"] == "weekly":
            assert tile["period_start"] == "2026-07-12"


def test_an_unparseable_week_is_refused(api) -> None:
    """400 rather than falling back to this week under a label the user cannot see is wrong."""
    status, body = api("GET", "/dashboard", params={"week_of": "last-tuesday"})
    assert status == 400
    assert "week_of" in json.dumps(body)
