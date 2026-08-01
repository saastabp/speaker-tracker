"""The opt-in follow-up rider on logging a touch and on sending an email (slice 7, acceptance #6).

The rider is the one part of this slice that changes what *existing* actions do, so what matters is
as much what it does **not** do: a send or a touch without one must create nothing. That is
asserted directly rather than inferred from the default, because "off by default" is exactly the
property a later refactor flips without anyone noticing.

Skips without ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

import app as app_module
from common import imap, mail, scheduler, storage
from common.auth import Principal
from common.db import db_now_local
from handlers import context
from migrations.runner import run_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "src" / "migrations"


class FakeSes:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_raw_email(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"MessageId": f"ses-{len(self.calls):04d}"}


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):  # noqa: N803 - boto3 names
        self.objects[Key] = Body
        return {}

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 names
        import io

        return {"Body": io.BytesIO(self.objects[Key])}


class FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def create_schedule(self, **kw):
        self.calls.append(("create", kw["Name"]))

    def update_schedule(self, **kw):
        self.calls.append(("update", kw["Name"]))

    def delete_schedule(self, **kw):
        self.calls.append(("delete", kw["Name"]))


@pytest.fixture
def api(db_connection, monkeypatch):
    """Return ``(call, sched, ids)`` with SES, S3, IMAP and EventBridge all faked."""
    run_migrations(db_connection, MIGRATIONS_DIR)
    monkeypatch.setattr(
        context, "principal_from_event", lambda event: Principal(sub="dev", email="dev@example.com")
    )
    monkeypatch.setattr(context, "get_connection", lambda tz: db_connection)
    monkeypatch.setattr(mail, "_client", lambda: FakeSes())
    monkeypatch.setattr(storage, "_client", lambda: FakeS3())
    monkeypatch.setattr(imap, "append_to_sent_best_effort", lambda raw: True)
    monkeypatch.setattr("handlers.emails.append_to_sent_best_effort", lambda raw: True)
    monkeypatch.setenv(storage.CONTENT_BUCKET_ENV, "test-content-bucket")
    monkeypatch.setenv(mail.MAIL_FROM_ENV, "donna@360balancedliving.com")

    monkeypatch.setenv(scheduler.SCHEDULER_GROUP_ENV, "st-followups")
    monkeypatch.setenv(scheduler.SCHEDULER_NOTIFY_ARN_ENV, "arn:aws:lambda:us-west-2:1:function:n")
    monkeypatch.setenv(scheduler.SCHEDULER_ROLE_ARN_ENV, "arn:aws:iam::1:role/sched")
    sched = FakeScheduler()
    monkeypatch.setattr(scheduler, "_client_instance", sched)

    with db_connection.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('dev', 'dev@example.com')")
        user_id = cur.lastrowid
        cur.execute(
            "INSERT INTO contacts (user_id, name, email) VALUES (%s, 'Kalei', 'kalei@venue.test')",
            (user_id,),
        )
        contact_id = cur.lastrowid

    def call(method: str, path: str, body: dict | None = None):
        event = {
            "version": "2.0",
            "routeKey": f"{method} {path}",
            "rawPath": path,
            "rawQueryString": "",
            "headers": {"content-type": "application/json", "x-user-timezone": "Pacific/Honolulu"},
            "queryStringParameters": None,
            "requestContext": {
                "stage": "$default",
                "http": {"method": method, "path": path, "sourceIp": "1.2.3.4", "userAgent": "t"},
            },
            "body": json.dumps(body) if body is not None else None,
            "isBase64Encoded": False,
        }
        resp = app_module.app.resolve(event, None)
        return resp["statusCode"], (json.loads(resp["body"]) if resp.get("body") else None)

    due = (db_now_local(db_connection).date() + timedelta(days=5)).isoformat()
    return call, sched, {"contact": contact_id, "due": due}


def _send_body(**overrides) -> dict:
    payload = {
        "idempotency_key": uuid.uuid4().hex,
        "to": ["kalei@venue.test"],
        "subject": "Speaking at your event",
        "body_html": "<p>Hello</p>",
    }
    payload.update(overrides)
    return payload


# --- acceptance #6: off is off --------------------------------------------------------------------


def test_sending_without_a_rider_creates_no_follow_up(api) -> None:
    call, sched, ids = api
    status, _ = call("POST", "/emails/send", _send_body(contact_id=ids["contact"]))

    assert status == 200
    _, listed = call("GET", "/follow-ups")
    assert listed["follow_ups"] == []
    assert sched.calls == [], "no schedule either — nothing was asked for"


def test_logging_a_touch_without_a_rider_creates_no_follow_up(api) -> None:
    call, sched, ids = api
    status, _ = call("POST", "/outreaches", {"contact_id": ids["contact"], "channel": "dm"})

    assert status == 200
    _, listed = call("GET", "/follow-ups")
    assert listed["follow_ups"] == []
    assert sched.calls == []


# --- on is on -------------------------------------------------------------------------------------


def test_sending_with_a_rider_creates_and_schedules_one(api) -> None:
    call, sched, ids = api
    status, _ = call(
        "POST",
        "/emails/send",
        _send_body(
            contact_id=ids["contact"],
            follow_up={"due_date": ids["due"], "note": "Chase the committee decision."},
        ),
    )

    assert status == 200
    _, listed = call("GET", "/follow-ups")
    assert len(listed["follow_ups"]) == 1
    created = listed["follow_ups"][0]
    assert created["note"] == "Chase the committee decision."
    assert created["due_date"] == ids["due"]
    # Inherited from the parent action rather than supplied by the rider.
    assert created["contact_id"] == ids["contact"]
    assert sched.calls == [("create", f"followup-{created['id']}")]


def test_a_rider_without_a_note_falls_back_to_the_subject(api) -> None:
    """Donna should not retype context the app already has."""
    call, _sched, ids = api
    call(
        "POST",
        "/emails/send",
        _send_body(contact_id=ids["contact"], follow_up={"due_date": ids["due"]}),
    )

    _, listed = call("GET", "/follow-ups")
    assert listed["follow_ups"][0]["note"] == "Follow up on: Speaking at your event"


def test_touch_rider_inherits_both_links(api) -> None:
    call, sched, ids = api
    status, _ = call(
        "POST",
        "/outreaches",
        {
            "contact_id": ids["contact"],
            "channel": "dm",
            "follow_up": {"due_date": ids["due"], "note": "Did she reply?"},
        },
    )

    assert status == 200
    _, listed = call("GET", "/follow-ups")
    assert len(listed["follow_ups"]) == 1
    assert listed["follow_ups"][0]["contact_id"] == ids["contact"]
    assert len(sched.calls) == 1


def test_a_bad_rider_date_rejects_the_whole_request(api) -> None:
    """The touch and its reminder share a transaction, so neither should land."""
    call, _sched, ids = api
    status, _ = call(
        "POST",
        "/outreaches",
        {
            "contact_id": ids["contact"],
            "channel": "dm",
            "follow_up": {"due_date": "not-a-date", "note": "x"},
        },
    )

    assert status == 400
    _, listed = call("GET", "/follow-ups")
    assert listed["follow_ups"] == []
    _, touches = call("GET", f"/contacts/{ids['contact']}/outreaches")
    assert touches["outreaches"] == [], "the touch must not survive a rejected rider"
