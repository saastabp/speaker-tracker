"""End-to-end tests for the poll loop — the file that ties slice 6b together.

Skip without ``TEST_DATABASE_URL`` (see conftest).

Everything below the handler is real: the pure core, all four repositories, and a genuine MySQL
schema built by the migration runner. Only the two outside edges are faked — IMAP by
``fake_imap.FakeIMAP``, which reproduces the protocol quirks rather than the convenient version of
them, and S3 by a small in-memory store. That is the same seam 6a used for SES and S3.

These are the tests that would actually catch the slice regressing. The unit files check each part
in isolation; this one checks that a stranger's mail stays out, a venue's reply lands on the right
thread, and a dragged message becomes a triage row and then leaves the mailbox — the behaviours
DEV-PLAN's acceptance criteria are written in terms of.
"""

from __future__ import annotations

import datetime as dt
import io
import types

import pytest
from conftest import MIGRATIONS_DIR
from fake_imap import IMPORT_FOLDER, PROCESSED_FOLDER, SENT_FOLDER, FakeIMAP, build_message

from common import imap, storage
from common.imap import ImapAuthError, ImapError
from handlers import imap_poll as poll_handler
from migrations.runner import run_migrations
from repositories import email_imports

OUR_ADDRESS = "donna@360balancedliving.com"


class FakeS3:
    """In-memory object store. Kept local rather than shared with ``test_emails_api`` so neither
    file's fixtures can drift under the other."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_puts = False

    def put_object(self, Bucket, Key, Body, ContentType=None):  # noqa: N803 - boto3 names
        if self.fail_puts:
            raise RuntimeError("S3 is down")
        self.objects[Key] = Body
        return {}

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 names
        if Key not in self.objects:
            raise FileNotFoundError(f"NoSuchKey: {Key}")
        return {"Body": io.BytesIO(self.objects[Key])}


class Poller:
    """Handle on one wired-up poller: the fake mailbox, the fake bucket, and a way to run it."""

    def __init__(self, conn, user_id: int, server: FakeIMAP, s3: FakeS3) -> None:
        self.conn = conn
        self.user_id = user_id
        self.server = server
        self.s3 = s3
        self._context = types.SimpleNamespace(
            aws_request_id="req-1",
            function_name="imap-poll",
            memory_limit_in_mb=512,
            invoked_function_arn="arn:aws:lambda:us-west-2:1:function:imap-poll",
            function_version="$LATEST",
        )

    def run(self) -> dict:
        return poll_handler.lambda_handler({}, self._context)

    def folder(self, result: dict, name: str) -> dict:
        """Return one folder's summary from a poll result."""
        return next(entry for entry in result["folders"] if entry["folder"] == name)

    def rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


@pytest.fixture
def poller(db_connection, monkeypatch) -> Poller:
    run_migrations(db_connection, MIGRATIONS_DIR)
    with db_connection.cursor() as cur:
        # The Cognito address is deliberately NOT the mailbox address: own_addresses must come from
        # MAIL_FROM_ADDRESS and the IMAP username, never from users.email, or mail arriving from
        # the Cognito address would be misclassified as outbound.
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('sub', 'donna@login.example')")
        user_id = cur.lastrowid

    server = FakeIMAP()
    s3 = FakeS3()
    credentials = types.SimpleNamespace(username=OUR_ADDRESS, password="secret")

    monkeypatch.setattr(imap, "_connect", lambda host: server)
    monkeypatch.setattr(imap, "get_imap_credentials", lambda refresh=False: credentials)
    monkeypatch.setattr(poll_handler, "get_imap_credentials", lambda refresh=False: credentials)
    monkeypatch.setattr(poll_handler, "get_connection", lambda tz: db_connection)
    monkeypatch.setattr(storage, "_client", lambda: s3)
    monkeypatch.setenv(storage.CONTENT_BUCKET_ENV, "test-content-bucket")
    monkeypatch.setenv(imap.IMAP_HOST_ENV, "imap.test")
    monkeypatch.setenv(poll_handler.MAIL_FROM_ENV, OUR_ADDRESS)

    return Poller(db_connection, user_id, server, s3)


def tracked_contact(
    poller: Poller, name: str = "Pat Host", email: str = "pat@riverbend.org"
) -> int:
    with poller.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO contacts (user_id, name, email) VALUES (%s, %s, %s)",
            (poller.user_id, name, email),
        )
        return cur.lastrowid


# --- the first poll, and the quiet ones that follow ----------------------------------------------


def test_the_first_poll_baselines_and_imports_nothing(poller) -> None:
    """A mailbox holding years of unrelated personal mail must not arrive in the app on deploy.

    The cost is a reply that landed in the seconds before the first poll; the alternative is
    importing Donna's entire correspondence history.
    """
    poller.server.add(
        "INBOX", 900, build_message(message_id="<old@personal.com>", from_addr="may@personal.com")
    )

    result = poller.run()

    assert poller.folder(result, "INBOX")["reason"] == "first_poll_baseline"
    assert poller.folder(result, "INBOX")["ingested"] == 0
    assert poller.rows("SELECT id FROM email_messages") == []


def test_a_quiet_poll_examines_nothing(poller) -> None:
    """The ``UID n:*`` phantom, seen from the top.

    A ``*``-terminated range would return the newest message on every poll of a quiet folder, so
    this would read ``examined=1, duplicates=1`` forever — and a real rescan would then be
    invisible in the noise.
    """
    poller.server.add("INBOX", 900, build_message(message_id="<a@x.com>", from_addr="a@x.com"))
    poller.run()

    inbox = poller.folder(poller.run(), "INBOX")
    assert (inbox["examined"], inbox["duplicates"]) == (0, 0)


def test_the_poller_never_issues_a_star_terminated_search(poller) -> None:
    poller.run()
    poller.run()
    assert poller.server.searches, "expected the poller to have searched at all"
    assert all(not str(criteria[1]).endswith(":*") for criteria in poller.server.searches)


# --- scoping: acceptance #2 ----------------------------------------------------------------------


def test_mail_from_an_untracked_sender_is_never_ingested(poller) -> None:
    """Verified in production with a personal email; verified here with a stranger."""
    poller.run()
    poller.server.add(
        "INBOX", 901, build_message(message_id="<spam@nowhere.com>", from_addr="nobody@nowhere.com")
    )

    inbox = poller.folder(poller.run(), "INBOX")
    assert (inbox["examined"], inbox["ingested"], inbox["skipped"]) == (1, 0, 1)
    assert poller.rows("SELECT id FROM email_messages") == []


def test_skipped_mail_is_not_written_to_s3_either(poller) -> None:
    """The never-ingest-the-whole-mailbox guarantee covers the object store, not just the rows."""
    poller.run()
    poller.server.add(
        "INBOX", 901, build_message(message_id="<spam@nowhere.com>", from_addr="nobody@nowhere.com")
    )
    poller.run()

    assert poller.s3.objects == {}


def test_mail_from_a_tracked_contact_is_ingested_and_attributed(poller) -> None:
    contact_id = tracked_contact(poller)
    poller.run()
    poller.server.add(
        "INBOX",
        901,
        build_message(message_id="<pat-1@riverbend.org>", from_addr="Pat Host <pat@riverbend.org>"),
    )

    inbox = poller.folder(poller.run(), "INBOX")
    assert (inbox["ingested"], inbox["skipped"]) == (1, 0)

    thread = poller.rows(
        "SELECT contact_id, opportunity_id, subject_normalized FROM email_threads"
    )[0]
    assert thread["contact_id"] == contact_id
    assert thread["opportunity_id"] is None, "an inbound-first thread never infers a gig"
    assert thread["subject_normalized"] == "Speaking inquiry"


def test_the_date_header_is_stored_as_naive_utc(poller) -> None:
    tracked_contact(poller)
    poller.run()
    poller.server.add(
        "INBOX",
        901,
        build_message(
            message_id="<pat-1@riverbend.org>",
            from_addr="pat@riverbend.org",
            date="Mon, 27 Jul 2026 10:00:00 -0400",
        ),
    )
    poller.run()

    stored = poller.rows("SELECT received_at FROM email_messages")[0]
    assert stored["received_at"] == dt.datetime(2026, 7, 27, 14, 0)


# --- the raw MIME reaches S3 ---------------------------------------------------------------------


def test_the_raw_mime_is_stored_and_the_body_reads_back(poller) -> None:
    """``0008`` stores no body, so this object is the only copy of what arrived. Without it a
    received message lists in its thread with nothing in it."""
    from common.mail_parse import parse_raw_message

    tracked_contact(poller)
    poller.run()
    poller.server.add(
        "INBOX",
        901,
        build_message(
            message_id="<pat-1@riverbend.org>",
            from_addr="pat@riverbend.org",
            body="Are you available in October?",
        ),
    )
    poller.run()

    key = poller.rows("SELECT s3_key FROM email_messages")[0]["s3_key"]
    assert key, "a NULL s3_key means the message would display with no body"
    assert key in poller.s3.objects

    parsed = parse_raw_message(storage.get_object_bytes(key))
    assert "Are you available in October?" in (parsed.body_text or "")


def test_a_storage_failure_leaves_the_message_for_the_next_poll(poller) -> None:
    """Recording it bodyless would be permanent — the dedupe key refuses a second attempt — so the
    message is not ingested at all and the next poll retries it."""
    tracked_contact(poller)
    poller.run()
    poller.server.add(
        "INBOX",
        901,
        build_message(message_id="<pat-1@riverbend.org>", from_addr="pat@riverbend.org"),
    )

    poller.s3.fail_puts = True
    assert poller.folder(poller.run(), "INBOX")["ingested"] == 0
    assert poller.rows("SELECT id FROM email_messages") == []

    poller.s3.fail_puts = False
    assert poller.folder(poller.run(), "INBOX")["ingested"] == 1


# --- threading: acceptance #1 --------------------------------------------------------------------


def test_a_reply_joins_the_thread_it_answers(poller) -> None:
    tracked_contact(poller)
    poller.run()
    poller.server.add(
        "INBOX",
        901,
        build_message(message_id="<pat-1@riverbend.org>", from_addr="pat@riverbend.org"),
    )
    poller.run()
    poller.server.add(
        "INBOX",
        902,
        build_message(
            message_id="<pat-2@riverbend.org>",
            from_addr="pat@riverbend.org",
            subject="Re: Speaking inquiry",
            in_reply_to="<pat-1@riverbend.org>",
            references="<pat-1@riverbend.org>",
        ),
    )
    poller.run()

    assert len(poller.rows("SELECT id FROM email_threads")) == 1
    assert len(poller.rows("SELECT id FROM email_messages")) == 2


# --- the Import folder: acceptance #3, #4, #5, #12, #13 ------------------------------------------


def test_the_app_folders_are_created_and_subscribed_on_every_poll(poller) -> None:
    """SUBSCRIBE is what makes them visible in Outlook; creating them is not enough (#12). Running
    every poll rather than once at deploy is what makes #13 — delete the folder, it comes back —
    hold without an operator step."""
    poller.run()
    assert IMPORT_FOLDER in poller.server.subscribed
    assert PROCESSED_FOLDER in poller.server.subscribed

    del poller.server.folders[IMPORT_FOLDER]
    poller.server.subscribed.discard(IMPORT_FOLDER)
    poller.run()
    assert IMPORT_FOLDER in poller.server.folders
    assert IMPORT_FOLDER in poller.server.subscribed


def test_a_dragged_message_is_ingested_without_a_contact_and_moved_out(poller) -> None:
    """The drag is Donna's per-message authorization; the contactless row IS the pending state."""
    poller.run()
    poller.server.add(
        IMPORT_FOLDER,
        1,
        build_message(
            message_id="<vip@newvenue.org>",
            from_addr="VIP Booker <vip@newvenue.org>",
            subject="Keynote request",
        ),
    )

    summary = poller.folder(poller.run(), IMPORT_FOLDER)
    assert (summary["ingested"], summary["moved"]) == (1, 1)
    assert poller.server.uids_in(IMPORT_FOLDER) == []
    assert len(poller.server.uids_in(PROCESSED_FOLDER)) == 1

    pending = email_imports.list_pending_imports(poller.conn, poller.user_id)
    assert len(pending) == 1
    assert pending[0]["from_addr"] == "vip@newvenue.org"
    assert pending[0]["from_name"] == "VIP Booker"


def test_re_dragging_the_same_message_creates_no_duplicate(poller) -> None:
    poller.run()
    for uid in (1, 2):
        poller.server.add(
            IMPORT_FOLDER,
            uid,
            build_message(message_id="<vip@newvenue.org>", from_addr="vip@newvenue.org"),
        )
        summary = poller.folder(poller.run(), IMPORT_FOLDER)

    assert (summary["ingested"], summary["duplicates"]) == (0, 1)
    assert len(poller.rows("SELECT id FROM email_messages")) == 1


def test_a_message_that_fails_to_ingest_stays_in_the_import_folder(poller) -> None:
    """The ordering the module calls not interchangeable, and the only case that distinguishes it.

    On the happy path, moving before or after the commit look identical. They diverge exactly when
    ingest fails: move-first files the message into ``Processed``, which is never polled, with no
    row to show for it — the message is gone for good. Move-after leaves it in ``Import``, where
    the next poll finds it again.

    Mutation-checked: hoisting the ``move_uids`` call above the ingest in ``_poll_folder`` passed
    every other test in this file until this one existed.
    """
    poller.run()
    poller.server.add(
        IMPORT_FOLDER,
        1,
        build_message(message_id="<vip@newvenue.org>", from_addr="vip@newvenue.org"),
    )

    poller.s3.fail_puts = True
    summary = poller.folder(poller.run(), IMPORT_FOLDER)

    assert summary["ingested"] == 0
    assert summary["moved"] == 0, "a message with no row must not leave the Import folder"
    assert poller.server.uids_in(IMPORT_FOLDER) == [1], "it must still be there for the next poll"
    assert poller.server.uids_in(PROCESSED_FOLDER) == []
    assert poller.rows("SELECT id FROM email_messages") == []

    # And once storage recovers, the retry completes normally.
    poller.s3.fail_puts = False
    recovered = poller.folder(poller.run(), IMPORT_FOLDER)
    assert (recovered["ingested"], recovered["moved"]) == (1, 1)
    assert poller.server.uids_in(IMPORT_FOLDER) == []


def test_the_import_folder_is_opened_writable_and_the_others_are_not(poller) -> None:
    """Messages move out of Import, so it must be selected writable. INBOX and Sent are only
    read, and opening them writable invites a stray flag change on a mailbox we are a guest on."""
    poller.run()
    poller.server.add(IMPORT_FOLDER, 1, build_message(message_id="<a@x.com>", from_addr="a@x.com"))
    poller.run()
    # The Import folder is polled last, so the final selection state reflects it.
    assert poller.server.selected == IMPORT_FOLDER
    assert poller.server.readonly is False


# --- UIDVALIDITY: acceptance #6 ------------------------------------------------------------------


def test_a_uidvalidity_change_rescans_without_re_importing(poller) -> None:
    tracked_contact(poller)
    poller.run()
    poller.server.add(
        "INBOX",
        901,
        build_message(message_id="<pat-1@riverbend.org>", from_addr="pat@riverbend.org"),
    )
    poller.run()
    before = len(poller.rows("SELECT id FROM email_messages"))

    poller.server.uid_validity["INBOX"] = 43
    inbox = poller.folder(poller.run(), "INBOX")

    assert inbox["reason"] == "uidvalidity_changed"
    assert inbox["ingested"] == 0
    assert inbox["duplicates"] >= 1
    assert len(poller.rows("SELECT id FROM email_messages")) == before


# --- the mailbox is not ours to modify -----------------------------------------------------------


def test_polling_never_marks_a_message_read(poller) -> None:
    tracked_contact(poller)
    poller.run()
    poller.server.add(
        "INBOX",
        901,
        build_message(message_id="<pat-1@riverbend.org>", from_addr="pat@riverbend.org"),
    )
    poller.run()

    assert poller.server.marked_seen == []


def test_polling_never_writes_an_outreach_row(poller) -> None:
    """Acceptance #8 plus the Sent-folder decision: all outreach counting originates in the app."""
    contact_id = tracked_contact(poller)
    poller.run()
    poller.server.add(
        "INBOX",
        901,
        build_message(message_id="<pat-1@riverbend.org>", from_addr="pat@riverbend.org"),
    )
    poller.server.add(
        SENT_FOLDER,
        1,
        build_message(
            message_id="<outlook@360balancedliving.com>",
            from_addr=OUR_ADDRESS,
            to_addr="pat@riverbend.org",
        ),
    )
    poller.run()

    assert contact_id  # the mail really is attributable; the point is that it still logs nothing
    assert poller.rows("SELECT id FROM outreaches") == []


# --- failure handling: acceptance #11 ------------------------------------------------------------


def test_a_rejected_password_raises_after_one_refresh_rather_than_no_opping(poller) -> None:
    """The project's worst failure mode is a poller that keeps running, finds nothing, and stops
    threading mail with no error anywhere. Failing the invocation is what ticks the Lambda Errors
    metric the alarm watches."""
    attempts = []

    class Rejecting:
        def __getattr__(self, name):
            def raise_login_error(*args, **kwargs):
                raise imap.LoginError("Access Denied")

            if name == "login":
                attempts.append(1)
            return raise_login_error

    poller.server = Rejecting()
    poll_handler.imap._connect = lambda host: poller.server

    with pytest.raises(ImapAuthError):
        poller.run()
    assert len(attempts) == 2, "expected one retry with refreshed credentials, then propagation"


def test_a_transient_failure_is_swallowed_for_the_next_minute(poller, monkeypatch) -> None:
    """A minute of missed mail costs nothing; paging on network noise would train everyone to
    ignore the alarm that matters."""
    monkeypatch.setattr(
        imap, "_connect", lambda host: (_ for _ in ()).throw(ImapError("unreachable"))
    )

    result = poller.run()
    assert result["status"] == "transient_error"
    assert result["folders"] == []


def test_with_no_user_the_poll_no_ops_instead_of_failing(poller) -> None:
    """Expected on a fresh deploy, before anyone has signed in. It must not page anyone."""
    with poller.conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (poller.user_id,))

    result = poller.run()
    assert result["status"] == "no_user"
    assert result["folders"] == []


def test_a_message_with_no_message_id_is_skipped_not_stored(poller) -> None:
    """Without a Message-ID there is no idempotency key, so re-reading the folder would insert it
    again on every poll."""
    tracked_contact(poller)
    poller.run()
    raw = b"From: pat@riverbend.org\r\nSubject: No id\r\n\r\nbody"
    poller.server.add("INBOX", 901, raw)

    inbox = poller.folder(poller.run(), "INBOX")
    assert inbox["skipped"] == 1
    assert poller.rows("SELECT id FROM email_messages") == []


# --- the cursor advances -------------------------------------------------------------------------


def test_the_watermark_advances_so_the_next_poll_starts_above_it(poller) -> None:
    tracked_contact(poller)
    poller.run()
    poller.server.add(
        "INBOX",
        901,
        build_message(message_id="<pat-1@riverbend.org>", from_addr="pat@riverbend.org"),
    )
    result = poller.run()

    assert poller.folder(result, "INBOX")["last_seen_uid"] == 901
    stored = poller.rows(
        "SELECT last_seen_uid FROM imap_folder_cursors WHERE folder_name = 'INBOX'"
    )
    assert stored[0]["last_seen_uid"] == 901


def test_every_polled_folder_gets_a_cursor_even_when_empty(poller) -> None:
    """``last_polled_at`` is the liveness signal, so a folder that stops being polled is visible as
    a stale timestamp rather than only as an absence of mail."""
    poller.run()

    folders = {
        row["folder_name"] for row in poller.rows("SELECT folder_name FROM imap_folder_cursors")
    }
    assert folders == {"INBOX", SENT_FOLDER, IMPORT_FOLDER}
