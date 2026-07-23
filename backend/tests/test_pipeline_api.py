"""End-to-end pipeline handler tests through the Powertools resolver — slice-3 acceptance criteria.

Requests are resolved by the real ``app`` with two seams patched: the principal is a fixed dev user
(no Cognito) and ``get_connection`` returns the test connection (no RDS/IAM). This exercises the
full HTTP path — routing, ``authenticate``, request validation, the JSON envelope, the journaled
lifecycle endpoints (status / payment / close), the server-owned funnel, and the domain-error →
status mapping. Skips without ``TEST_DATABASE_URL`` (via the ``db_connection`` fixture).

Coverage maps to the nine acceptance criteria: one status event per real move and none on a
same-column drag (#1); a rejected move leaves state unchanged, so the SPA can roll back (#2); the
``closed_at`` predicate (#3); delivered-but-unpaid stays on the board and marking it paid moves it
to History (#4); correcting payment reopens it (#5); a cancelled gig leaves the board for History
(#6, board realization — the funnel *ratio* is slice 5); a retired status (nurture, dropped in 0004)
is rejected; closing writes a terminal event *and* a reason note (#8); the funnel order/labels come
from the server (#9).
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

BOARD_STAGES = [
    "researching",
    "outreach_sent",
    "in_conversation",
    "pitched",
    "booked",
    "delivered",
]


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


@pytest.fixture
def board(api):
    """Seed a venue (with a research angle), two contacts, and a talk via the API; return ids."""
    _, org = api(
        "POST",
        "/organizations",
        {"organization_type": "expo", "name": "Kauai Expo", "how_to_approach": "warm intro"},
    )
    _, jane = api("POST", "/contacts", {"name": "Jane"})
    _, ann = api("POST", "/contacts", {"name": "Ann"})
    _, talk = api("POST", "/talks", {"title": "Boundaries 101"})
    return {"org": org["id"], "jane": jane["id"], "ann": ann["id"], "talk": talk["id"]}


def _new_opp(api, board, **kw) -> dict:
    body = {
        "title": "Gig",
        "organization_id": board["org"],
        "opportunity_format": "workshop",
        "comp_type": "paid",
    }
    body.update(kw)
    status, created = api("POST", "/opportunities", body)
    assert status == 200, created
    return created


def _detail(api, opp_id: int) -> dict:
    status, body = api("GET", f"/opportunities/{opp_id}")
    assert status == 200
    return body


def _titles(api, **params) -> list[str]:
    status, body = api("GET", "/opportunities", params=params or None)
    assert status == 200
    return [o["title"] for o in body["opportunities"]]


# --- create / detail -----------------------------------------------------------------------------


def test_create_requires_title(api, board) -> None:
    status, body = api(
        "POST",
        "/opportunities",
        {"organization_id": board["org"], "opportunity_format": "workshop"},
    )
    assert status == 400
    assert body == {"error": "invalid request"}


def test_create_defaults_and_seeds_angle(api, board) -> None:
    created = _new_opp(api, board, talk_id=board["talk"])
    assert created["current_status"] == "researching"
    assert created["payment_status"] == "unbilled"
    assert created["closed_at"] is None
    assert created["angle"] == "warm intro"  # seeded from the venue's how_to_approach
    assert created["talk_title"] == "Boundaries 101"
    assert [e["status"] for e in created["status_events"]] == ["researching"]


def test_get_missing_opportunity_is_404(api) -> None:
    assert api("GET", "/opportunities/999")[0] == 404


# --- funnel (#9) ---------------------------------------------------------------------------------


def test_funnel_is_server_owned_ordered_and_labeled(api) -> None:
    status, body = api("GET", "/funnel")
    assert status == 200
    stages = body["stages"]
    assert [
        s["short_name"] for s in stages
    ] == BOARD_STAGES  # order from server; cancelled/lost out
    assert all(s["label"] for s in stages)  # labels come from the server, not the SPA
    assert next(s for s in stages if s["short_name"] == "delivered")["is_terminal"] is True


# --- status transitions (#1, #2, #7) -------------------------------------------------------------


def test_status_move_writes_exactly_one_event(api, board) -> None:
    opp = _new_opp(api, board)
    before = len(opp["status_events"])
    status, moved = api("PATCH", f"/opportunities/{opp['id']}/status", {"status": "booked"})
    assert status == 200
    assert moved["current_status"] == "booked"
    assert len(moved["status_events"]) == before + 1  # #1: exactly one new event


def test_same_column_drag_writes_no_event(api, board) -> None:
    opp = _new_opp(api, board)
    api("PATCH", f"/opportunities/{opp['id']}/status", {"status": "booked"})
    after_first = _detail(api, opp["id"])["status_events"]
    status, again = api("PATCH", f"/opportunities/{opp['id']}/status", {"status": "booked"})
    assert status == 200  # no-op is still a success
    assert len(again["status_events"]) == len(after_first)  # #1: nothing written


def test_rejected_status_move_leaves_state_unchanged(api, board) -> None:
    # #2: a bad PATCH fails without mutating, so the SPA's optimistic move rolls back cleanly.
    opp = _new_opp(api, board)
    api("PATCH", f"/opportunities/{opp['id']}/status", {"status": "booked"})
    status, _ = api("PATCH", f"/opportunities/{opp['id']}/status", {"status": "cancelled"})
    assert status == 400  # cancelled is a close-flow status, not a board drag
    assert _detail(api, opp["id"])["current_status"] == "booked"  # unchanged


def test_retired_nurture_status_is_rejected(api, board) -> None:
    # nurture was retired in migration 0004; the resolver no longer knows it, so a move is rejected.
    opp = _new_opp(api, board)
    status, _ = api("PATCH", f"/opportunities/{opp['id']}/status", {"status": "nurture"})
    assert status == 400
    assert _detail(api, opp["id"])["current_status"] == "researching"  # unchanged


# --- closed_at predicate / payment (#3, #4, #5) --------------------------------------------------


def test_delivered_but_unpaid_stays_on_board(api, board) -> None:
    opp = _new_opp(api, board)
    api("PATCH", f"/opportunities/{opp['id']}/status", {"status": "delivered"})
    detail = _detail(api, opp["id"])
    assert detail["closed_at"] is None  # #4: not closed while unpaid
    assert opp["title"] in _titles(api, closed="false")
    assert opp["title"] not in _titles(api, closed="true")


def test_marking_delivered_paid_moves_to_history(api, board) -> None:
    opp = _new_opp(api, board)
    api("PATCH", f"/opportunities/{opp['id']}/status", {"status": "delivered"})
    status, paid = api(
        "PATCH",
        f"/opportunities/{opp['id']}/payment",
        {"payment_status": "paid", "paid_on": "2026-09-01"},
    )
    assert status == 200
    assert paid["closed_at"] is not None  # #4: (delivered AND settled) closes
    assert paid["paid_on"] == "2026-09-01"
    assert opp["title"] in _titles(api, closed="true")
    assert opp["title"] not in _titles(api, closed="false")


def test_correcting_payment_reopens_the_card(api, board) -> None:
    opp = _new_opp(api, board)
    api("PATCH", f"/opportunities/{opp['id']}/status", {"status": "delivered"})
    api(
        "PATCH",
        f"/opportunities/{opp['id']}/payment",
        {"payment_status": "paid", "paid_on": "2026-09-01"},
    )
    status, reopened = api(
        "PATCH", f"/opportunities/{opp['id']}/payment", {"payment_status": "invoiced"}
    )
    assert status == 200
    assert reopened["closed_at"] is None  # #5
    assert opp["title"] in _titles(api, closed="false")


# --- close (#3, #6, #8) --------------------------------------------------------------------------


def test_close_writes_terminal_event_and_reason_note(api, board) -> None:
    opp = _new_opp(api, board)
    api("PATCH", f"/opportunities/{opp['id']}/status", {"status": "pitched"})
    status, closed = api(
        "POST",
        f"/opportunities/{opp['id']}/close",
        {"status": "lost", "reason": "went with someone else"},
    )
    assert status == 200
    assert closed["current_status"] == "lost"
    assert closed["closed_at"] is not None  # #3
    assert closed["status_events"][0]["status"] == "lost"
    assert closed["status_events"][0]["note"] == "went with someone else"  # #8: terminal event note
    assert "went with someone else" in [n["body"] for n in closed["notes"]]  # #8: a note too


def test_close_requires_a_reason(api, board) -> None:
    opp = _new_opp(api, board)
    assert api("POST", f"/opportunities/{opp['id']}/close", {"status": "lost"})[0] == 400


def test_close_rejects_non_close_status(api, board) -> None:
    opp = _new_opp(api, board)
    status, _ = api(
        "POST", f"/opportunities/{opp['id']}/close", {"status": "delivered", "reason": "x"}
    )
    assert status == 400  # delivered is a board stage, not a close outcome


def test_cancelled_gig_leaves_board_for_history(api, board) -> None:
    # #6 (board realization): a booked gig that is cancelled counts as a booking that fell through —
    # it is gone from the board and shows in History. The reached-or-beyond funnel ratio is slice 5.
    opp = _new_opp(api, board, title="Cancelled gig")
    api("PATCH", f"/opportunities/{opp['id']}/status", {"status": "booked"})
    api(
        "POST",
        f"/opportunities/{opp['id']}/close",
        {"status": "cancelled", "reason": "venue closed"},
    )
    assert "Cancelled gig" in _titles(api, closed="true")
    assert "Cancelled gig" not in _titles(api, closed="false")


# --- linked contacts -----------------------------------------------------------------------------


def test_link_contacts_enforces_single_lead(api, board) -> None:
    opp = _new_opp(api, board)
    api(
        "POST",
        f"/opportunities/{opp['id']}/contacts",
        {"contact_id": board["jane"], "contact_role": "primary", "is_primary": True},
    )
    status, detail = api(
        "POST", f"/opportunities/{opp['id']}/contacts", {"contact_id": board["ann"]}
    )
    assert status == 200
    assert len(detail["contacts"]) == 2
    # promote Ann -> Jane demoted (one lead per gig)
    _, detail = api(
        "PUT", f"/opportunities/{opp['id']}/contacts/{board['ann']}", {"is_primary": True}
    )
    leads = sorted(c["name"] for c in detail["contacts"] if c["is_primary"])
    assert leads == ["Ann"]


def test_duplicate_contact_link_conflicts(api, board) -> None:
    opp = _new_opp(api, board)
    api("POST", f"/opportunities/{opp['id']}/contacts", {"contact_id": board["jane"]})
    status, body = api(
        "POST", f"/opportunities/{opp['id']}/contacts", {"contact_id": board["jane"]}
    )
    assert status == 409
    assert "already" in body["error"]


def test_link_unknown_contact_is_404(api, board) -> None:
    opp = _new_opp(api, board)
    assert api("POST", f"/opportunities/{opp['id']}/contacts", {"contact_id": 999999})[0] == 404


def test_unlink_contact(api, board) -> None:
    opp = _new_opp(api, board)
    api("POST", f"/opportunities/{opp['id']}/contacts", {"contact_id": board["jane"]})
    assert api("DELETE", f"/opportunities/{opp['id']}/contacts/{board['jane']}")[0] == 200
    assert _detail(api, opp["id"])["contacts"] == []
    assert api("DELETE", f"/opportunities/{opp['id']}/contacts/{board['jane']}")[0] == 404


# --- notes ---------------------------------------------------------------------------------------


def test_add_and_delete_note(api, board) -> None:
    opp = _new_opp(api, board)
    _, detail = api("POST", f"/opportunities/{opp['id']}/notes", {"body": "called them"})
    assert [n["body"] for n in detail["notes"]] == ["called them"]
    note_id = detail["notes"][0]["id"]
    assert api("DELETE", f"/opportunities/{opp['id']}/notes/{note_id}")[0] == 200
    assert _detail(api, opp["id"])["notes"] == []
    assert api("DELETE", f"/opportunities/{opp['id']}/notes/{note_id}")[0] == 404


def test_add_note_to_missing_opportunity_is_404(api) -> None:
    assert api("POST", "/opportunities/999/notes", {"body": "x"})[0] == 404


# --- update / delete -----------------------------------------------------------------------------


def test_update_replaces_descriptive_fields_only(api, board) -> None:
    opp = _new_opp(api, board)
    api("PATCH", f"/opportunities/{opp['id']}/status", {"status": "booked"})
    status, updated = api(
        "PUT",
        f"/opportunities/{opp['id']}",
        {
            "title": "Renamed",
            "organization_id": board["org"],
            "opportunity_format": "panel",
            "comp_type": "paid",
        },
    )
    assert status == 200
    assert updated["title"] == "Renamed"
    assert updated["opportunity_format"] == "panel"
    assert updated["current_status"] == "booked"  # lifecycle untouched


def test_delete_opportunity(api, board) -> None:
    opp = _new_opp(api, board)
    assert api("DELETE", f"/opportunities/{opp['id']}") == (200, {"deleted": True})
    assert api("GET", f"/opportunities/{opp['id']}")[0] == 404
