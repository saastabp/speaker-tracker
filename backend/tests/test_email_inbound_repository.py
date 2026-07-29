"""Inbound ingest repository tests — idempotency, attribution, and what ingest must never do.

Skip without ``TEST_DATABASE_URL`` (see conftest).

Three of these tests exist for rules rather than mechanics, and those are the ones worth keeping if
the file ever has to shrink: ingest writes no ``outreaches`` row, a new thread's ``opportunity_id``
is unconditionally NULL, and ingest never changes an existing thread's contact. Each is a decision
that would be easy to "improve" into a bug.
"""

from __future__ import annotations

import datetime as dt

from repositories import email_inbound
from repositories.email_inbound import InboundMessage


def _user(conn, sub: str = "other") -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES (%s, %s)", (sub, f"{sub}@x.com"))
        return cur.lastrowid


def _contact(conn, user_id: int, name: str = "Pat Host", email: str = "pat@riverbend.org") -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO contacts (user_id, name, email) VALUES (%s, %s, %s)",
            (user_id, name, email),
        )
        return cur.lastrowid


def _opportunity(conn, user_id: int, org_type: str) -> int:
    """Create the minimum viable gig, resolving every NOT NULL catalog FK from the seeded data."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM organization_types WHERE short_name = %s", (org_type,))
        org_type_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO organizations (user_id, organization_type_id, name) "
            "VALUES (%s, %s, 'Riverbend')",
            (user_id, org_type_id),
        )
        org_id = cur.lastrowid
        cur.execute("SELECT id FROM opportunity_statuses ORDER BY sort_order LIMIT 1")
        status_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM payment_statuses LIMIT 1")
        payment_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM opportunity_formats LIMIT 1")
        format_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM comp_types LIMIT 1")
        comp_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO opportunities (user_id, organization_id, opportunity_format_id, "
            " current_status_id, comp_type_id, payment_status_id, title, currency) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'Fall keynote', 'USD')",
            (user_id, org_id, format_id, status_id, comp_id, payment_id),
        )
        return cur.lastrowid


def _message(**overrides) -> InboundMessage:
    payload = {
        "message_id": "<a@riverbend.org>",
        "from_addr": "Pat Host <pat@riverbend.org>",
        "to_addr": "donna@360balancedliving.com",
        "subject": "Speaking inquiry",
        "occurred_at": dt.datetime(2026, 7, 27, 14, 0),
        "imap_folder": "INBOX",
        "imap_uid": 901,
        "s3_key": "email/raw/1/a@riverbend.org.eml",
    }
    payload.update(overrides)
    return InboundMessage(**payload)


def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _ingest(conn, user_id, message, *, direction="in", contact_id=None, thread_id=None):
    return email_inbound.ingest_message(
        conn, user_id, message, direction=direction, contact_id=contact_id, thread_id=thread_id
    )


# --- the rules ingest must never break ----------------------------------------------------------


def test_ingest_never_writes_an_outreach_row(seeded_db) -> None:
    """Not for inbound (#8), and not for outbound found in Sent because Donna composed it in
    Outlook. All outreach counting originates inside the app: a touch appearing in the journal
    that she never logged or sent from here is unexplainable from her side."""
    conn, user_id, _, _ = seeded_db
    contact_id = _contact(conn, user_id)

    _ingest(conn, user_id, _message(), contact_id=contact_id)
    _ingest(
        conn,
        user_id,
        _message(message_id="<b@riverbend.org>", from_addr="donna@360balancedliving.com"),
        direction="out",
        contact_id=contact_id,
    )

    assert _rows(conn, "SELECT id FROM outreaches") == []


def test_a_new_thread_never_carries_an_opportunity(seeded_db) -> None:
    """Even with exactly one open gig. A message is not evidence it concerns that gig, and filing
    side-channel mail against the wrong opportunity is worse than leaving it unattached."""
    conn, user_id, org_type, _ = seeded_db
    contact_id = _contact(conn, user_id)
    _opportunity(conn, user_id, org_type)

    result = _ingest(conn, user_id, _message(), contact_id=contact_id)

    thread = _rows(
        conn, "SELECT opportunity_id FROM email_threads WHERE id = %s", (result.thread_id,)
    )
    assert thread[0]["opportunity_id"] is None


def test_ingest_never_changes_an_existing_threads_contact(seeded_db) -> None:
    """A thread's contact is set at creation or by an explicit human link, full stop. Backfilling
    here would let a pending-import row leave the triage queue without Donna acting, and the queue
    is only worth trusting if it changes when she says so."""
    conn, user_id, _, _ = seeded_db
    contact_id = _contact(conn, user_id)

    first = _ingest(conn, user_id, _message(), contact_id=None)
    assert (
        _rows(conn, "SELECT contact_id FROM email_threads WHERE id = %s", (first.thread_id,))[0][
            "contact_id"
        ]
        is None
    )

    _ingest(
        conn,
        user_id,
        _message(message_id="<b@riverbend.org>"),
        contact_id=contact_id,
        thread_id=first.thread_id,
    )

    still = _rows(conn, "SELECT contact_id FROM email_threads WHERE id = %s", (first.thread_id,))
    assert still[0]["contact_id"] is None, "the thread must stay in the pending queue"


# --- idempotency (acceptance #5) -----------------------------------------------------------------


def test_re_ingesting_the_same_message_writes_nothing(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    first = _ingest(conn, user_id, _message())
    second = _ingest(conn, user_id, _message())

    assert second.duplicate is True
    assert second.message_row_id == first.message_row_id
    assert second.thread_created is False
    assert len(_rows(conn, "SELECT id FROM email_messages")) == 1
    assert len(_rows(conn, "SELECT id FROM email_threads")) == 1


def test_an_unbracketed_message_id_is_canonicalized_so_the_dedupe_key_holds(seeded_db) -> None:
    """A sender omitting the brackets must not be able to produce a second row for a message we
    already hold; the unique key is a string, so canonicalization is what makes it structural."""
    conn, user_id, _, _ = seeded_db
    _ingest(conn, user_id, _message(message_id="a@riverbend.org"))
    second = _ingest(conn, user_id, _message(message_id="<a@riverbend.org>"))

    assert second.duplicate is True
    stored = _rows(conn, "SELECT message_id FROM email_messages")
    assert [row["message_id"] for row in stored] == ["<a@riverbend.org>"]


def test_the_same_message_id_is_allowed_for_a_different_user(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    other_id = _user(conn)
    _ingest(conn, user_id, _message())
    result = _ingest(conn, other_id, _message())

    assert result.duplicate is False
    assert len(_rows(conn, "SELECT id FROM email_messages")) == 2


# --- attribution and storage ---------------------------------------------------------------------


def test_a_first_message_creates_its_thread_with_the_normalized_subject(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    result = _ingest(conn, user_id, _message(subject="Re: Fwd: Speaking inquiry"))

    assert result.thread_created is True
    thread = _rows(
        conn, "SELECT subject_normalized, last_direction, last_message_at FROM email_threads"
    )[0]
    assert thread["subject_normalized"] == "Speaking inquiry"
    assert thread["last_direction"] == "in"
    assert thread["last_message_at"] == dt.datetime(2026, 7, 27, 14, 0)


def test_an_aware_timestamp_is_stored_as_naive_utc(seeded_db) -> None:
    """pymysql formats a datetime without consulting tzinfo, so an aware value would store its
    local wall time with the offset discarded — a quiet hours-off corruption, not a crash."""
    conn, user_id, _, _ = seeded_db
    aware = dt.datetime(2026, 7, 27, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=-4)))
    _ingest(conn, user_id, _message(occurred_at=aware))

    stored = _rows(conn, "SELECT received_at FROM email_messages")[0]
    assert stored["received_at"] == dt.datetime(2026, 7, 27, 16, 0)


def test_direction_decides_which_timestamp_column_is_filled(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    _ingest(conn, user_id, _message(), direction="in")
    _ingest(conn, user_id, _message(message_id="<b@x.com>"), direction="out")

    rows = _rows(conn, "SELECT message_id, sent_at, received_at FROM email_messages ORDER BY id")
    assert rows[0]["received_at"] is not None and rows[0]["sent_at"] is None
    assert rows[1]["sent_at"] is not None and rows[1]["received_at"] is None


def test_a_message_with_no_timestamp_leaves_the_columns_null(seeded_db) -> None:
    """Inventing a time would be a lie about when the conversation moved."""
    conn, user_id, _, _ = seeded_db
    result = _ingest(conn, user_id, _message(occurred_at=None))

    stored = _rows(conn, "SELECT received_at FROM email_messages")[0]
    assert stored["received_at"] is None
    thread = _rows(
        conn, "SELECT last_message_at FROM email_threads WHERE id = %s", (result.thread_id,)
    )
    assert thread[0]["last_message_at"] is None


def test_the_imap_coordinates_and_s3_key_are_recorded(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    _ingest(conn, user_id, _message(imap_folder="Speaker Tracker/Import", imap_uid=7))

    stored = _rows(conn, "SELECT imap_folder, imap_uid, s3_key FROM email_messages")[0]
    assert stored["imap_folder"] == "Speaker Tracker/Import"
    assert stored["imap_uid"] == 7
    assert stored["s3_key"] == "email/raw/1/a@riverbend.org.eml"


# --- joining an existing thread ---------------------------------------------------------------


def test_a_reply_joins_its_thread_and_inherits_the_opportunity(seeded_db) -> None:
    """How a reply reaches the right gig (#1): the thread's attribution wins, because nothing may
    infer an opportunity from the message itself."""
    conn, user_id, org_type, _ = seeded_db
    contact_id = _contact(conn, user_id)
    opportunity_id = _opportunity(conn, user_id, org_type)

    first = _ingest(conn, user_id, _message(), contact_id=contact_id)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE email_threads SET opportunity_id = %s WHERE id = %s",
            (opportunity_id, first.thread_id),
        )

    reply = _ingest(
        conn,
        user_id,
        _message(message_id="<b@riverbend.org>"),
        contact_id=contact_id,
        thread_id=first.thread_id,
    )

    assert reply.thread_created is False
    stored = _rows(
        conn, "SELECT opportunity_id FROM email_messages WHERE id = %s", (reply.message_row_id,)
    )[0]
    assert stored["opportunity_id"] == opportunity_id


def test_a_later_message_moves_the_threads_clock_forward(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    first = _ingest(conn, user_id, _message())
    _ingest(
        conn,
        user_id,
        _message(message_id="<b@x.com>", occurred_at=dt.datetime(2026, 7, 28, 9, 0)),
        thread_id=first.thread_id,
    )

    thread = _rows(
        conn, "SELECT last_message_at FROM email_threads WHERE id = %s", (first.thread_id,)
    )
    assert thread[0]["last_message_at"] == dt.datetime(2026, 7, 28, 9, 0)


def test_an_older_message_does_not_rewind_the_threads_clock(seeded_db) -> None:
    """A backfilled import, or a rescan reaching an old message first, belongs to the conversation
    but is not its latest news."""
    conn, user_id, _, _ = seeded_db
    first = _ingest(conn, user_id, _message())
    _ingest(
        conn,
        user_id,
        _message(message_id="<old@x.com>", occurred_at=dt.datetime(2026, 1, 1, 9, 0)),
        thread_id=first.thread_id,
    )

    thread = _rows(
        conn, "SELECT last_message_at FROM email_threads WHERE id = %s", (first.thread_id,)
    )
    assert thread[0]["last_message_at"] == dt.datetime(2026, 7, 27, 14, 0)


def test_a_foreign_thread_id_starts_a_new_thread_rather_than_aborting_the_poll(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    other_id = _user(conn)
    theirs = _ingest(conn, other_id, _message(message_id="<theirs@x.com>"))

    mine = _ingest(conn, user_id, _message(), thread_id=theirs.thread_id)
    assert mine.thread_created is True
    assert mine.thread_id != theirs.thread_id


# --- reconciling one of our own sends ---------------------------------------------------------


def test_an_unconfirmed_send_seen_in_sent_is_reported_as_pending(seeded_db) -> None:
    """The signal repositories.email_sends was designed around: the process died between SES
    accepting the message and phase 3, so the Sent folder is where it comes back."""
    conn, user_id, _, _ = seeded_db
    thread_id = _ingest(conn, user_id, _message(message_id="<seed@x.com>")).thread_id
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_messages (user_id, thread_id, message_id, direction, from_addr) "
            "VALUES (%s, %s, '<ours@x.com>', 'out', 'donna@360balancedliving.com')",
            (user_id, thread_id),
        )

    result = _ingest(
        conn,
        user_id,
        _message(message_id="<ours@x.com>", from_addr="donna@360balancedliving.com"),
        direction="out",
        thread_id=thread_id,
    )

    assert result.duplicate is True
    assert result.pending is True


def test_a_confirmed_send_seen_again_is_a_duplicate_but_not_pending(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    thread_id = _ingest(conn, user_id, _message(message_id="<seed@x.com>")).thread_id
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_messages "
            "(user_id, thread_id, message_id, direction, from_addr, sent_at) "
            "VALUES (%s, %s, '<ours@x.com>', 'out', 'donna@x.com', CURRENT_TIMESTAMP)",
            (user_id, thread_id),
        )

    result = _ingest(
        conn, user_id, _message(message_id="<ours@x.com>"), direction="out", thread_id=thread_id
    )
    assert result.duplicate is True
    assert result.pending is False


def test_an_inbound_duplicate_is_never_reported_as_pending(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    _ingest(conn, user_id, _message())
    assert _ingest(conn, user_id, _message()).pending is False
