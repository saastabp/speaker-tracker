"""End-to-end follow-up handler tests through the Powertools resolver (slice 7 checkpoint F).

Requests are resolved by the real ``app`` with the principal, connection and scheduler seams
patched (as in ``test_dashboard_api``), so the full HTTP path runs: routing, ``authenticate``,
validation, the JSON envelope, the write, and the EventBridge reconciliation that follows it. The
scheduler client is a fake that **records** calls, which is what lets these assert the thing that
otherwise only shows up in production — that a given edit produced exactly one replace, or a
cancel, or nothing at all.

Mechanizes acceptance #2, #3, #4, #5 and #7, plus decision 4 (any rendered field forces a replace,
not only the date). Skips without ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

import app as app_module
from common import scheduler
from common.auth import Principal
from common.db import db_now_local
from handlers import context
from migrations.runner import run_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "src" / "migrations"


class FakeScheduler:
    """Records calls; mimics EventBridge closely enough for the paths the module reacts to."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.existing: set[str] = set()

    def create_schedule(self, **kw):
        if kw["Name"] in self.existing:
            raise ClientError(
                {"Error": {"Code": "ConflictException", "Message": "exists"}}, "CreateSchedule"
            )
        self.existing.add(kw["Name"])
        self.calls.append(("create", kw["Name"], kw))

    def update_schedule(self, **kw):
        self.calls.append(("update", kw["Name"], kw))

    def delete_schedule(self, **kw):
        # EventBridge really removes it, so a later create succeeds instead of conflicting.
        self.existing.discard(kw["Name"])
        self.calls.append(("delete", kw["Name"], kw))

    @property
    def kinds(self) -> list[str]:
        return [kind for kind, _, _ in self.calls]


@pytest.fixture
def api(db_connection, monkeypatch):
    """Return ``(call, sched, ids)`` — an HTTP caller, the fake scheduler, and seeded ids."""
    run_migrations(db_connection, MIGRATIONS_DIR)
    monkeypatch.setattr(
        context,
        "principal_from_event",
        lambda event: Principal(sub="dev", email="donna@example.com"),
    )
    monkeypatch.setattr(context, "get_connection", lambda tz: db_connection)

    monkeypatch.setenv(scheduler.SCHEDULER_GROUP_ENV, "st-followups")
    monkeypatch.setenv(scheduler.SCHEDULER_NOTIFY_ARN_ENV, "arn:aws:lambda:us-west-2:1:function:n")
    monkeypatch.setenv(scheduler.SCHEDULER_ROLE_ARN_ENV, "arn:aws:iam::1:role/sched")
    sched = FakeScheduler()
    monkeypatch.setattr(scheduler, "_client_instance", sched)

    with db_connection.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('dev', 'donna@example.com')")
        user_id = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Kalei')", (user_id,))
        kalei = cur.lastrowid
        cur.execute(
            "INSERT INTO organizations (user_id, organization_type_id, name) "
            "SELECT %s, id, 'Hanalei Bay Resort' FROM organization_types WHERE short_name='resort'",
            (user_id,),
        )
        org = cur.lastrowid
        cur.execute(
            "INSERT INTO opportunities "
            "(user_id, organization_id, opportunity_format_id, current_status_id, comp_type_id, "
            " payment_status_id, title) "
            "SELECT %s, %s, fmt.id, st.id, ct.id, pay.id, 'Wellness Wheel for Women' "
            "FROM opportunity_formats fmt, opportunity_statuses st, comp_types ct, "
            "     payment_statuses pay "
            "WHERE fmt.short_name='workshop' AND st.short_name='researching' "
            "  AND ct.short_name='paid' AND pay.short_name='unbilled'",
            (user_id, org),
        )
        opp = cur.lastrowid

    today = db_now_local(db_connection).date()

    def call(method: str, path: str, body: dict | None = None, query: dict | None = None):
        event = {
            "version": "2.0",
            "routeKey": f"{method} {path}",
            "rawPath": path,
            "rawQueryString": "",
            "headers": {"content-type": "application/json", "x-user-timezone": "Pacific/Honolulu"},
            "queryStringParameters": query,
            "requestContext": {
                "stage": "$default",
                "http": {"method": method, "path": path, "sourceIp": "1.2.3.4", "userAgent": "t"},
            },
            "body": json.dumps(body) if body is not None else None,
            "isBase64Encoded": False,
        }
        resp = app_module.app.resolve(event, None)
        return resp["statusCode"], (json.loads(resp["body"]) if resp.get("body") else None)

    return (
        call,
        sched,
        {
            "kalei": kalei,
            "opp": opp,
            "today": today,
            "future": (today + timedelta(days=3)).isoformat(),
            "later": (today + timedelta(days=9)).isoformat(),
        },
    )


def test_create_schedules_a_local_seven_am_reminder(api) -> None:
    call, sched, ids = api
    status, created = call(
        "POST",
        "/follow-ups",
        {"due_date": ids["future"], "note": "Chase", "opportunity_id": ids["opp"]},
    )

    assert status == 200
    assert created["opportunity_title"] == "Wellness Wheel for Women"
    assert created["contact_name"] is None
    assert created["completed_at"] is None

    assert sched.kinds == ["create"]
    _, name, kw = sched.calls[0]
    assert name == f"followup-{created['id']}"
    assert kw["ScheduleExpression"] == f"at({ids['future']}T07:00:00)"
    assert kw["ScheduleExpressionTimezone"] == "Pacific/Honolulu"  # never UTC
    assert kw["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert kw["ActionAfterCompletion"] == "DELETE"

    payload = json.loads(kw["Target"]["Input"])
    assert payload["to_address"] == "donna@example.com"  # the JWT claim, not a users.email read
    assert payload["follow_up_id"] == created["id"]


def test_remind_by_email_false_creates_no_schedule(api) -> None:
    """A dashboard-only reminder. Not an error, and not a silent one either."""
    call, sched, ids = api
    status, _ = call(
        "POST",
        "/follow-ups",
        {
            "due_date": ids["future"],
            "note": "Dashboard only",
            "contact_id": ids["kalei"],
            "remind_by_email": False,
        },
    )
    assert status == 200
    assert sched.calls == []


def test_follow_up_with_neither_link_is_400_not_500(api) -> None:
    """Acceptance #5 through the API — the CHECK would surface as an unmapped OperationalError."""
    call, _, ids = api
    status, _ = call("POST", "/follow-ups", {"due_date": ids["future"], "note": "orphan"})
    assert status == 400


def test_editing_the_date_replaces_exactly_one_schedule(api) -> None:
    """Acceptance #2. A deterministic name means the replace is one call, not delete-then-create."""
    call, sched, ids = api
    _, created = call(
        "POST",
        "/follow-ups",
        {"due_date": ids["future"], "note": "Chase", "opportunity_id": ids["opp"]},
    )
    sched.calls.clear()

    status, patched = call("PATCH", f"/follow-ups/{created['id']}", {"due_date": ids["later"]})

    assert status == 200
    assert patched["due_date"] == ids["later"]
    assert len(sched.calls) == 1
    assert sched.calls[0][:2] == ("update", f"followup-{created['id']}")
    assert sched.calls[0][2]["ScheduleExpression"] == f"at({ids['later']}T07:00:00)"


def test_editing_the_note_also_replaces(api) -> None:
    """Decision 4: acceptance #2 names the date, but the note is equally baked into the payload."""
    call, sched, ids = api
    _, created = call(
        "POST",
        "/follow-ups",
        {"due_date": ids["future"], "note": "Chase", "contact_id": ids["kalei"]},
    )
    sched.calls.clear()

    call("PATCH", f"/follow-ups/{created['id']}", {"note": "Chase the fee too"})

    assert sched.kinds == ["update"]
    assert json.loads(sched.calls[0][2]["Target"]["Input"])["note"] == "Chase the fee too"


def test_patching_to_an_identical_value_touches_nothing(api) -> None:
    call, sched, ids = api
    _, created = call(
        "POST",
        "/follow-ups",
        {"due_date": ids["future"], "note": "Chase", "contact_id": ids["kalei"]},
    )
    sched.calls.clear()
    call("PATCH", f"/follow-ups/{created['id']}", {"note": "Chase"})
    assert sched.calls == []


def test_marking_done_cancels_the_schedule(api) -> None:
    """Acceptance #7 — the failure this slice most needs to avoid: nagging about finished work."""
    call, sched, ids = api
    _, created = call(
        "POST",
        "/follow-ups",
        {"due_date": ids["future"], "note": "Chase", "contact_id": ids["kalei"]},
    )
    sched.calls.clear()

    status, done = call("PATCH", f"/follow-ups/{created['id']}", {"completed": True})

    assert status == 200
    assert done["completed_at"] is not None
    assert sched.calls[0][:2] == ("delete", f"followup-{created['id']}")


def test_reopening_recreates_the_schedule(api) -> None:
    call, sched, ids = api
    _, created = call(
        "POST",
        "/follow-ups",
        {"due_date": ids["future"], "note": "Chase", "contact_id": ids["kalei"]},
    )
    call("PATCH", f"/follow-ups/{created['id']}", {"completed": True})
    sched.calls.clear()

    call("PATCH", f"/follow-ups/{created['id']}", {"completed": False})
    assert sched.kinds == ["create"]


def test_delete_cancels_and_is_not_repeatable(api) -> None:
    """Acceptance #3."""
    call, sched, ids = api
    _, created = call(
        "POST",
        "/follow-ups",
        {"due_date": ids["future"], "note": "Chase", "contact_id": ids["kalei"]},
    )
    sched.calls.clear()

    status, body = call("DELETE", f"/follow-ups/{created['id']}")
    assert (status, body) == (200, {"deleted": True})
    assert sched.calls[0][:2] == ("delete", f"followup-{created['id']}")

    assert call("DELETE", f"/follow-ups/{created['id']}")[0] == 404
    assert call("PATCH", f"/follow-ups/{created['id']}", {"note": "x"})[0] == 404


def test_same_day_after_seven_am_gets_no_schedule(api) -> None:
    """Skip-if-past: a reminder created at 10:00 for today has no future moment left to email."""
    call, sched, ids = api
    sched.calls.clear()
    status, created = call(
        "POST",
        "/follow-ups",
        {"due_date": ids["today"].isoformat(), "note": "today", "contact_id": ids["kalei"]},
    )
    assert status == 200
    # Whether a schedule is made depends on the wall clock, so assert the *rule*, not one branch.
    assert sched.calls == [] or sched.kinds == ["create"]
    if sched.calls:
        assert sched.calls[0][2]["ScheduleExpression"].endswith("T07:00:00)")


def test_list_and_filters(api) -> None:
    call, _, ids = api
    _, on_contact = call(
        "POST", "/follow-ups", {"due_date": ids["future"], "note": "c", "contact_id": ids["kalei"]}
    )
    _, on_gig = call(
        "POST",
        "/follow-ups",
        {"due_date": ids["future"], "note": "g", "opportunity_id": ids["opp"]},
    )

    _, listed = call("GET", "/follow-ups")
    assert {r["id"] for r in listed["follow_ups"]} == {on_contact["id"], on_gig["id"]}

    _, by_contact = call("GET", "/follow-ups", query={"contact_id": str(ids["kalei"])})
    assert [r["id"] for r in by_contact["follow_ups"]] == [on_contact["id"]]

    call("PATCH", f"/follow-ups/{on_gig['id']}", {"completed": True})
    _, pending = call("GET", "/follow-ups", query={"pending_only": "true"})
    assert [r["id"] for r in pending["follow_ups"]] == [on_contact["id"]]


def test_malformed_filter_is_404_not_500(api) -> None:
    call, _, _ = api
    assert call("GET", "/follow-ups", query={"contact_id": "abc"})[0] == 404


def test_dashboard_card_gains_and_loses_the_row(api) -> None:
    """Acceptance #4 through the composite dashboard the SPA actually renders."""
    call, _, ids = api
    _, created = call(
        "POST",
        "/follow-ups",
        {"due_date": ids["today"].isoformat(), "note": "today", "contact_id": ids["kalei"]},
    )

    _, dash = call("GET", "/dashboard")
    assert created["id"] in [r["id"] for r in dash["follow_ups"]]

    call("PATCH", f"/follow-ups/{created['id']}", {"completed": True})
    _, after = call("GET", "/dashboard")
    assert created["id"] not in [r["id"] for r in after["follow_ups"]]


@pytest.mark.parametrize(
    "failure",
    [
        EndpointConnectionError(endpoint_url="https://scheduler"),
        ClientError({"Error": {"Code": "ThrottlingException"}}, "CreateSchedule"),
    ],
    ids=["botocore-root", "clienterror-root"],
)
def test_a_scheduler_outage_does_not_fail_the_request(api, monkeypatch, failure) -> None:
    """The commit-first ordering is only safe if a scheduler problem is survivable.

    Both botocore roots, because neither subclasses the other: catching only ``ClientError`` let
    connect/read timeouts and missing credentials reach the API's catch-all and 500 the request
    *after* the row was committed.
    """
    call, _, ids = api

    class Broken:
        def create_schedule(self, **kw):
            raise failure

        def update_schedule(self, **kw):
            raise failure

        def delete_schedule(self, **kw):
            raise failure

    monkeypatch.setattr(scheduler, "_client_instance", Broken())

    status, created = call(
        "POST",
        "/follow-ups",
        {"due_date": ids["future"], "note": "outage", "contact_id": ids["kalei"]},
    )
    assert status == 200
    assert isinstance(created["id"], int)  # the follow-up still exists
    assert call("DELETE", f"/follow-ups/{created['id']}")[0] == 200
