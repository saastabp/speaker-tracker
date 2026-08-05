"""End-to-end opportunity-response handler tests through the Powertools resolver (slice 12).

Requests are resolved by the real ``app`` with two seams patched (fixed dev principal, test
connection), mirroring ``test_outreach_api``. Exercises the full HTTP path: the PUT's idempotence,
the counters coming back embedded in the opportunity detail, and the error mapping. Skips without
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

CHAT = "legacy_spark_chat"
BOOKLET = "booklet"


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
def opp_id(api):
    """One opportunity, created through the API."""
    _, org = api("POST", "/organizations", {"organization_type": "expo", "name": "Kauai Expo"})
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
    return opp["id"]


def _counts(detail: dict) -> dict[str, int]:
    return {r["response_type"]: r["count"] for r in detail["responses"]}


def test_setting_a_count_returns_the_updated_opportunity(api, opp_id) -> None:
    status, detail = api("PUT", f"/opportunities/{opp_id}/responses/{CHAT}", {"count": 3})
    assert status == 200
    # The whole opportunity comes back, so the SPA refreshes its grid from one response.
    assert detail["id"] == opp_id
    assert _counts(detail) == {CHAT: 3}


def test_a_new_opportunity_starts_with_no_counters(api, opp_id) -> None:
    _, detail = api("GET", f"/opportunities/{opp_id}")
    assert detail["responses"] == []


def test_repeating_the_same_put_is_idempotent(api, opp_id) -> None:
    """A double-fired `+` must land on the same number, not count twice."""
    _, first = api("PUT", f"/opportunities/{opp_id}/responses/{CHAT}", {"count": 2})
    _, second = api("PUT", f"/opportunities/{opp_id}/responses/{CHAT}", {"count": 2})
    assert _counts(first) == _counts(second) == {CHAT: 2}


def test_counters_accumulate_per_type_and_survive_a_reread(api, opp_id) -> None:
    api("PUT", f"/opportunities/{opp_id}/responses/{CHAT}", {"count": 3})
    api("PUT", f"/opportunities/{opp_id}/responses/{BOOKLET}", {"count": 1})

    _, detail = api("GET", f"/opportunities/{opp_id}")
    assert _counts(detail) == {CHAT: 3, BOOKLET: 1}


def test_zeroing_a_counter_keeps_the_type_at_zero(api, opp_id) -> None:
    api("PUT", f"/opportunities/{opp_id}/responses/{CHAT}", {"count": 2})
    _, detail = api("PUT", f"/opportunities/{opp_id}/responses/{CHAT}", {"count": 0})
    assert _counts(detail) == {CHAT: 0}


def test_a_negative_count_is_rejected(api, opp_id) -> None:
    status, _ = api("PUT", f"/opportunities/{opp_id}/responses/{CHAT}", {"count": -1})
    assert status == 400


def test_an_unknown_response_type_is_rejected(api, opp_id) -> None:
    status, _ = api("PUT", f"/opportunities/{opp_id}/responses/smoke_signal", {"count": 1})
    assert status == 400


def test_setting_a_counter_on_a_missing_opportunity_is_404(api, opp_id) -> None:
    status, _ = api("PUT", f"/opportunities/999999/responses/{CHAT}", {"count": 1})
    assert status == 404


def test_the_has_responses_filter_opens_what_the_funnel_row_counts(api, opp_id) -> None:
    """The query param behind the Dashboard funnel's Responses row.

    ``closed=all`` mirrors the link, which needs it because a gig that generated responses has
    usually been delivered and closed.
    """
    _, before = api("GET", "/opportunities", params={"has_responses": "true", "closed": "all"})
    assert before["opportunities"] == []

    api("PUT", f"/opportunities/{opp_id}/responses/{CHAT}", {"count": 2})
    _, after = api("GET", "/opportunities", params={"has_responses": "true", "closed": "all"})
    assert [o["id"] for o in after["opportunities"]] == [opp_id]

    # Taken back down to zero, the gig drops out again — the list tracks the number on the row.
    api("PUT", f"/opportunities/{opp_id}/responses/{CHAT}", {"count": 0})
    _, zeroed = api("GET", "/opportunities", params={"has_responses": "true", "closed": "all"})
    assert zeroed["opportunities"] == []


def test_the_unfiltered_list_is_unaffected(api, opp_id) -> None:
    """Absent the param, nothing changes — the filter is opt-in."""
    _, body = api("GET", "/opportunities")
    assert [o["id"] for o in body["opportunities"]] == [opp_id]
