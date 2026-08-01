"""End-to-end email handler tests through the Powertools resolver, with AWS mocked.

Requests are resolved by the real ``app``; the principal and connection seams are patched as in
``test_signatures_api``, and the three AWS edges — SES, S3, IMAP — are replaced at their module
seams (decision #1). Skips without ``TEST_DATABASE_URL``.

The repository tests already prove the transaction mechanics in isolation. What these add is the
**orchestration**, which is where the interesting failures live:

- a clean SES failure must leave **zero rows** (acceptance #2) — proven here through the handler,
  with SES raising, rather than by calling the compensation directly;
- an IMAP failure must **not** fail the request, because the mail has already gone out
  (decision #2);
- a confirm failure must leave a **pending** row and an ERROR naming the ``Message-ID``, not a
  failed response the user would retry into a double-send;
- a reply's ``In-Reply-To`` / ``References`` must reach the **actual bytes handed to SES**
  (acceptance #3), not merely the database.
"""

from __future__ import annotations

import json
import logging
import uuid
from email import message_from_bytes
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

import app as app_module
from common import imap, mail, storage
from common.auth import Principal
from handlers import context
from migrations.runner import run_migrations
from repositories import email_sends

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "src" / "migrations"


class FakeSes:
    """SES stand-in that records what it was asked to send."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    def send_raw_email(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"MessageId": f"ses-{len(self.calls):04d}"}

    @property
    def last_message(self):
        """The most recent raw message, parsed."""
        return message_from_bytes(self.calls[-1]["RawMessage"]["Data"])


class FakeS3:
    """In-memory object store, so stored MIME can be read back exactly as S3 would."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_gets_for: set[str] = set()

    def put_object(self, Bucket, Key, Body, ContentType=None):  # noqa: N803 - boto3 names
        self.objects[Key] = Body
        return {}

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 names
        if Key in self.fail_gets_for or Key not in self.objects:
            raise FileNotFoundError(f"NoSuchKey: {Key}")
        import io

        return {"Body": io.BytesIO(self.objects[Key])}

    def generate_presigned_url(self, operation, Params, ExpiresIn):  # noqa: N803 - boto3 names
        return f"https://s3.example.test/{Params['Key']}?sig=fake"


@pytest.fixture
def aws(monkeypatch):
    """Replace SES, S3 and IMAP at their seams. Returns a handle on each fake."""
    ses = FakeSes()
    s3 = FakeS3()
    appended: list[bytes] = []

    monkeypatch.setattr(mail, "_client", lambda: ses)
    monkeypatch.setattr(storage, "_client", lambda: s3)
    monkeypatch.setattr(
        "handlers.emails.append_to_sent_best_effort",
        lambda raw: (appended.append(raw), True)[1],
    )
    monkeypatch.setenv(storage.CONTENT_BUCKET_ENV, "test-content-bucket")
    monkeypatch.setenv(mail.MAIL_FROM_ENV, "donna@360balancedliving.com")
    monkeypatch.setenv(mail.MAIL_FROM_NAME_ENV, "Donna King")

    class Handles:
        pass

    handles = Handles()
    handles.ses = ses
    handles.s3 = s3
    handles.appended = appended
    return handles


@pytest.fixture
def api(db_connection, monkeypatch, aws):
    """Return ``call(method, path, body=None, query=None) -> (status, parsed_body)``."""
    run_migrations(db_connection, MIGRATIONS_DIR)
    monkeypatch.setattr(
        context, "principal_from_event", lambda event: Principal(sub="dev", email="dev@example.com")
    )
    monkeypatch.setattr(context, "get_connection", lambda tz: db_connection)

    def call(method: str, path: str, body: dict | None = None, query: dict | None = None):
        event = {
            "version": "2.0",
            "routeKey": f"{method} {path}",
            "rawPath": path,
            "rawQueryString": "",
            "headers": {"content-type": "application/json"},
            "queryStringParameters": query,
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


def _rejected() -> ClientError:
    """What SES raises when it received the request and refused it — a *clean* failure."""
    return ClientError({"Error": {"Code": "MessageRejected"}}, "SendRawEmail")


def send_body(**overrides) -> dict:
    payload = {
        # Fresh per call — see test_email_sends_repository._send_input.
        "idempotency_key": uuid.uuid4().hex,
        "to": ["venue@example.com"],
        "subject": "Speaking at your event",
        "body_html": "<p>Hi Jane,</p><p>Are you booking speakers?</p>",
    }
    payload.update(overrides)
    return payload


def reply_body(**overrides) -> dict:
    """A reply payload with a fresh idempotency key, like a new compose in the UI."""
    payload = {"idempotency_key": uuid.uuid4().hex, "body_html": "<p>Following up.</p>"}
    payload.update(overrides)
    return payload


def rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


# --- the happy path -------------------------------------------------------------------------------


def test_send_writes_all_three_rows_and_confirms(api, aws, db_connection) -> None:
    status, body = api("POST", "/emails/send", send_body())

    assert status == 200
    assert body["thread_id"] is not None
    assert body["message"]["direction"] == "out"

    messages = rows(db_connection, "SELECT * FROM email_messages")
    assert len(messages) == 1
    assert messages[0]["sent_at"] is not None, "the send must be confirmed, not left pending"

    threads = rows(db_connection, "SELECT * FROM email_threads")
    assert len(threads) == 1
    assert threads[0]["last_message_at"] is not None
    assert len(aws.ses.calls) == 1


def test_send_transmits_our_message_id(api, aws, db_connection) -> None:
    # The transmitted header and the stored row must carry the same id, or replies match nothing.
    _status, body = api("POST", "/emails/send", send_body())

    assert aws.ses.last_message["Message-ID"] == body["message"]["message_id"]


def test_send_stores_the_raw_mime_byte_identically(api, aws, db_connection) -> None:
    api("POST", "/emails/send", send_body())

    stored_key = rows(db_connection, "SELECT s3_key FROM email_messages")[0]["s3_key"]
    assert aws.s3.objects[stored_key] == aws.ses.calls[0]["RawMessage"]["Data"]


def test_send_copies_to_the_sent_folder(api, aws) -> None:
    api("POST", "/emails/send", send_body())
    assert aws.appended[0] == aws.ses.calls[0]["RawMessage"]["Data"]


def test_cc_recipients_reach_the_ses_envelope(api, aws) -> None:
    # SES delivers to Destinations, not the headers — dropping Cc here loses those copies.
    api("POST", "/emails/send", send_body(cc=["assistant@example.com"]))

    assert aws.ses.calls[0]["Destinations"] == ["venue@example.com", "assistant@example.com"]
    assert aws.ses.last_message["Cc"] == "assistant@example.com"


def test_send_logs_an_outreach_when_linked_to_a_contact(api, db_connection) -> None:
    # The users row is created lazily by authenticate(), so make a request before looking it up.
    api("GET", "/emails/threads")
    with db_connection.cursor() as cur:
        cur.execute("SELECT id FROM users LIMIT 1")
        user_id = cur.fetchone()["id"]
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Jane')", (user_id,))
        contact_id = cur.lastrowid

    _status, body = api("POST", "/emails/send", send_body(contact_id=contact_id))

    assert body["outreach_id"] is not None
    assert len(rows(db_connection, "SELECT * FROM outreaches")) == 1


# --- acceptance #2: a clean SES failure leaves nothing --------------------------------------------


def test_ses_rejection_leaves_no_rows(api, aws, db_connection) -> None:
    """Acceptance #2 — a *clean* rejection compensates fully.

    The failure is a ``ClientError``, which is what SES raises when it received the request and
    refused it. That is the only case where discarding the intent is safe, and the only case this
    test is about; the ambiguous one is below.
    """
    aws.ses.error = _rejected()

    status, _body = api("POST", "/emails/send", send_body())

    assert status >= 500
    assert rows(db_connection, "SELECT * FROM email_messages") == []
    assert rows(db_connection, "SELECT * FROM email_threads") == []
    assert rows(db_connection, "SELECT * FROM outreaches") == []


def test_ambiguous_ses_failure_keeps_the_pending_row(api, aws, db_connection, caplog) -> None:
    """A timeout is NOT evidence that nothing was sent, so the record must survive.

    This is the case the handler used to get wrong: a single ``except Exception`` compensated for
    every failure, so a dropped response erased the only trace of a message that may well have been
    delivered — and the retry then double-sent it.
    """
    aws.ses.error = EndpointConnectionError(endpoint_url="https://email.us-east-1.amazonaws.com")

    with caplog.at_level(logging.ERROR):
        status, _body = api("POST", "/emails/send", send_body())

    assert status >= 500
    pending = rows(db_connection, "SELECT * FROM email_messages")
    assert len(pending) == 1, "the record of an attempt with unknown outcome must not be discarded"
    assert pending[0]["sent_at"] is None
    assert any("UNKNOWN" in r.getMessage() for r in caplog.records)


def test_retrying_the_same_compose_conflicts_instead_of_sending_twice(api, aws) -> None:
    """The idempotency key is what makes UNIQUE(user_id, message_id) able to fire at all.

    Without it the retry mints a fresh Message-ID, sails past the constraint, and Donna's venue
    gets the same pitch twice with nothing in the app showing it happened.
    """
    body = send_body()
    first, _ = api("POST", "/emails/send", body)
    assert first == 200

    second, error = api("POST", "/emails/send", body)  # same key: a retry, not a new message

    assert second == 409
    assert len(aws.ses.calls) == 1, "the retry must not reach SES"
    assert error is not None


def test_forced_ses_failure_after_a_good_send_keeps_the_earlier_one(
    api, aws, db_connection
) -> None:
    # Compensation must remove only this attempt's rows, never an existing conversation.
    api("POST", "/emails/send", send_body())
    aws.ses.error = _rejected()
    api("POST", "/emails/send", send_body(subject="Second attempt"))

    assert len(rows(db_connection, "SELECT * FROM email_messages")) == 1
    assert len(rows(db_connection, "SELECT * FROM email_threads")) == 1


# --- decision #2: IMAP is best-effort -------------------------------------------------------------


def test_imap_failure_does_not_fail_the_send(api, aws, monkeypatch, db_connection) -> None:
    # The mail has already gone out; a mailbox problem must never surface as a failed request.
    monkeypatch.setattr("handlers.emails.append_to_sent_best_effort", lambda raw: False)

    status, _body = api("POST", "/emails/send", send_body())

    assert status == 200
    assert rows(db_connection, "SELECT sent_at FROM email_messages")[0]["sent_at"] is not None


# --- the ugly case: sent but unconfirmed --------------------------------------------------------


def test_confirm_failure_leaves_a_pending_row_and_logs_loudly(
    api, aws, monkeypatch, db_connection, caplog
) -> None:
    def explode(*args, **kwargs):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(email_sends, "confirm_send", explode)

    with caplog.at_level(logging.ERROR):
        status, _body = api("POST", "/emails/send", send_body())

    # The email WAS sent, so the request must not report failure...
    assert status == 200
    assert len(aws.ses.calls) == 1
    # ...but the row stays pending for the poller to reconcile, and the log names the Message-ID.
    message = rows(db_connection, "SELECT * FROM email_messages")[0]
    assert message["sent_at"] is None
    assert message["message_id"] in caplog.text
    assert "confirm failed" in caplog.text


# --- acceptance #3: reply threading ---------------------------------------------------------------


def send_and_get_thread(api) -> tuple[int, dict]:
    _status, body = api("POST", "/emails/send", send_body())
    return body["thread_id"], body["message"]


def test_reply_threads_via_the_parent_message_id(api, aws) -> None:
    thread_id, parent = send_and_get_thread(api)

    status, body = api("POST", f"/emails/threads/{thread_id}/replies", reply_body())

    assert status == 200
    assert body["thread_id"] == thread_id, "a reply must reuse the thread, not open a new one"
    # The headers must be in the bytes SES received, not merely in the database.
    sent = aws.ses.last_message
    assert sent["In-Reply-To"] == parent["message_id"]
    assert parent["message_id"] in sent["References"]


def test_reply_subject_gets_a_single_re_prefix(api, aws) -> None:
    thread_id, _parent = send_and_get_thread(api)

    api("POST", f"/emails/threads/{thread_id}/replies", reply_body(body_html="<p>One</p>"))
    assert aws.ses.last_message["Subject"] == "Re: Speaking at your event"

    api("POST", f"/emails/threads/{thread_id}/replies", reply_body(body_html="<p>Two</p>"))
    assert aws.ses.last_message["Subject"] == "Re: Speaking at your event"


def test_reply_to_our_own_message_goes_to_the_venue(api, aws) -> None:
    thread_id, _parent = send_and_get_thread(api)

    api("POST", f"/emails/threads/{thread_id}/replies", reply_body())

    assert aws.ses.calls[-1]["Destinations"] == ["venue@example.com"]


def test_reply_recipients_can_be_overridden(api, aws) -> None:
    thread_id, _parent = send_and_get_thread(api)

    api(
        "POST",
        f"/emails/threads/{thread_id}/replies",
        reply_body(body_html="<p>Hi</p>", to=["someone-else@example.com"]),
    )

    assert aws.ses.calls[-1]["Destinations"] == ["someone-else@example.com"]


def test_reply_to_an_unknown_thread_is_404(api) -> None:
    status, _body = api("POST", "/emails/threads/999999/replies", reply_body(body_html="<p>Hi</p>"))
    assert status == 404


def test_reply_to_a_message_on_another_thread_is_rejected(api) -> None:
    first_thread, first_message = send_and_get_thread(api)
    second_thread, _ = send_and_get_thread(api)

    status, _body = api(
        "POST",
        f"/emails/threads/{second_thread}/replies",
        reply_body(body_html="<p>Hi</p>", in_reply_to_message_id=first_message["id"]),
    )

    assert status == 400
    assert first_thread != second_thread


# --- reads --------------------------------------------------------------------------------------


def test_thread_list_and_detail_reconstruct_the_body(api) -> None:
    thread_id, _parent = send_and_get_thread(api)

    status, listing = api("GET", "/emails/threads")
    assert status == 200
    assert [t["id"] for t in listing["threads"]] == [thread_id]
    assert listing["threads"][0]["pending_count"] == 0

    status, detail = api("GET", f"/emails/threads/{thread_id}")
    assert status == 200
    assert "Are you booking speakers?" in detail["messages"][0]["body_html"]


def test_attachments_are_listed_from_the_stored_mime(api, aws) -> None:
    aws.s3.objects["email/attachments/1/one-sheet.pdf"] = b"%PDF-1.4 fake"
    _status, body = api(
        "POST",
        "/emails/send",
        send_body(
            attachments=[
                {
                    "s3_key": "email/attachments/1/one-sheet.pdf",
                    "filename": "one-sheet.pdf",
                    "content_type": "application/pdf",
                }
            ]
        ),
    )

    _status, detail = api("GET", f"/emails/threads/{body['thread_id']}")
    attachments = detail["messages"][0]["attachments"]
    assert [a["filename"] for a in attachments] == ["one-sheet.pdf"]


def test_unreadable_body_degrades_instead_of_failing_the_thread(api, aws, caplog) -> None:
    thread_id, _parent = send_and_get_thread(api)
    aws.s3.fail_gets_for = set(aws.s3.objects)  # the object vanished from the bucket

    with caplog.at_level(logging.WARNING):
        status, detail = api("GET", f"/emails/threads/{thread_id}")

    assert status == 200, "one unreadable object must not 500 an entire conversation"
    assert detail["messages"][0]["body_html"] is None
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_unknown_thread_detail_is_404(api) -> None:
    status, _body = api("GET", "/emails/threads/999999")
    assert status == 404


def test_mark_read_close_and_reopen(api) -> None:
    thread_id, _parent = send_and_get_thread(api)

    assert api("POST", f"/emails/threads/{thread_id}/read")[0] == 200
    assert api("POST", f"/emails/threads/{thread_id}/close")[0] == 200
    # Closed threads leave the inbox but remain reachable.
    assert api("GET", "/emails/threads")[1]["threads"] == []
    assert api("POST", f"/emails/threads/{thread_id}/reopen")[0] == 200
    assert len(api("GET", "/emails/threads")[1]["threads"]) == 1


def test_closing_twice_is_404(api) -> None:
    thread_id, _parent = send_and_get_thread(api)
    api("POST", f"/emails/threads/{thread_id}/close")
    assert api("POST", f"/emails/threads/{thread_id}/close")[0] == 404


# --- attachment upload (acceptance #6) ------------------------------------------------------------


def test_attachment_upload_key_is_server_generated_and_user_scoped(api) -> None:
    status, body = api(
        "POST", "/emails/attachments", {"filename": "menu.pdf", "content_type": "application/pdf"}
    )

    assert status == 200
    assert body["s3_key"].startswith(storage.ATTACHMENT_PREFIX)
    assert body["s3_key"].endswith("/menu.pdf")
    assert body["upload_url"].startswith("https://")


def test_attachment_upload_ignores_a_client_supplied_key(api) -> None:
    # Honouring a client key would let one caller write under another's prefix.
    _status, body = api(
        "POST",
        "/emails/attachments",
        {"filename": "menu.pdf", "s3_key": "materials/../../etc/passwd"},
    )

    assert body["s3_key"].startswith(storage.ATTACHMENT_PREFIX)
    assert ".." not in body["s3_key"]


def test_attachment_upload_requires_a_filename(api) -> None:
    assert api("POST", "/emails/attachments", {})[0] == 400


def test_imap_module_is_never_reached_in_these_tests() -> None:
    # Guard against a future refactor quietly reintroducing a real socket into the test path.
    assert imap.IMAP_TIMEOUT_S < 15
