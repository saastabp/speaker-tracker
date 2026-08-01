"""Thread/message read tests against a seeded MySQL — ordering, derived counts, and tenancy.

Skip without ``TEST_DATABASE_URL`` (see conftest). The read side has two properties worth pinning
because both are easy to regress silently:

- **NULLs sort last.** MySQL orders NULL first by default, so without the explicit sort key a
  thread whose only message is an unconfirmed send would head the inbox. These tests assert the
  pending thread is last, not merely present.
- **``message_count`` / ``pending_count`` are derived**, recomputed per read. ``pending_count``
  drops to zero on confirm with no write of its own, which is what lets the badge be honest.

Inbound messages are inserted with raw SQL: the poller that creates them is slice 6b, and the
read side must already handle a two-directional conversation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from models.emails import EmailSendInput, EmailThreadSummary
from repositories import email_sends as sends
from repositories import email_threads as threads

#: Fixed timestamps so ordering assertions never depend on wall-clock ties (TIMESTAMP is
#: second-resolution, and two confirms in one second would otherwise sort by id alone).
_T1 = datetime(2026, 7, 20, 9, 0, 0)
_T2 = datetime(2026, 7, 21, 9, 0, 0)
_T3 = datetime(2026, 7, 22, 9, 0, 0)


@pytest.fixture
def thread_db(seeded_db):
    """A migrated DB with one user, a contact, and a second tenant.

    Returns ``(conn, user_id, ids)`` where ``ids`` has ``jane`` and ``other_user``.
    """
    conn, user_id, _, _ = seeded_db
    with conn.cursor() as cur:
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Jane')", (user_id,))
        jane = cur.lastrowid
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('user2', 'user2@example.com')")
        other_user = cur.lastrowid
    return conn, user_id, {"jane": jane, "other_user": other_user}


def _send(conn, user_id, message_id, *, thread_id=None, contact_id=None, cc=None, **kwargs):
    """Create a pending send with sensible defaults."""
    data = EmailSendInput(
        # Fresh per call — these helpers stand in for separate composes, not retries of one.
        idempotency_key=uuid.uuid4().hex,
        to=["venue@example.com"],
        cc=cc or [],
        subject=kwargs.pop("subject", "Speaking at your event"),
        body_html="<p>Hello</p>",
        contact_id=contact_id,
    )
    return sends.create_pending_send(
        conn, user_id, data, message_id=message_id, from_addr="donna@x.com", thread_id=thread_id
    )


def _receive(conn, user_id, thread_id, message_id, received_at) -> int:
    """Insert an inbound message directly — the poller that would create it is slice 6b."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_messages "
            "(user_id, thread_id, message_id, direction, subject, from_addr, to_addr, received_at) "
            "VALUES (%s, %s, %s, 'in', 'Re: Speaking at your event', 'venue@example.com', "
            "        'donna@x.com', %s)",
            (user_id, thread_id, message_id, received_at),
        )
        # Capture before the UPDATE — lastrowid is reset by the next statement on this cursor.
        message_row_id = cur.lastrowid
        cur.execute(
            "UPDATE email_threads SET last_direction = 'in', last_message_at = %s WHERE id = %s",
            (received_at, thread_id),
        )
        return message_row_id


# --- inbox list ------------------------------------------------------------------------------


def test_threads_sort_newest_activity_first(thread_db) -> None:
    conn, user_id, _ = thread_db
    older = _send(conn, user_id, "<a@x.com>")
    sends.confirm_send(conn, user_id, older.message_row_id, _T1)
    newer = _send(conn, user_id, "<b@x.com>")
    sends.confirm_send(conn, user_id, newer.message_row_id, _T3)

    listed = threads.list_threads(conn, user_id)
    assert [t["id"] for t in listed] == [newer.thread_id, older.thread_id]


def test_pending_only_thread_sorts_last(thread_db) -> None:
    # The whole point of the explicit NULLs-last sort key: an unconfirmed send must not head the
    # inbox just because MySQL sorts NULL first.
    conn, user_id, _ = thread_db
    pending = _send(conn, user_id, "<pending@x.com>")
    confirmed = _send(conn, user_id, "<sent@x.com>")
    sends.confirm_send(conn, user_id, confirmed.message_row_id, _T1)

    listed = threads.list_threads(conn, user_id)
    assert listed[-1]["id"] == pending.thread_id
    assert listed[0]["id"] == confirmed.thread_id


def test_pending_count_falls_to_zero_on_confirm(thread_db) -> None:
    conn, user_id, _ = thread_db
    pending = _send(conn, user_id, "<a@x.com>")

    before = threads.get_thread(conn, user_id, pending.thread_id)
    assert before["pending_count"] == 1
    assert before["message_count"] == 1

    sends.confirm_send(conn, user_id, pending.message_row_id, _T1)

    after = threads.get_thread(conn, user_id, pending.thread_id)
    assert after["pending_count"] == 0
    assert after["message_count"] == 1


def test_counts_are_plain_ints_for_the_model(thread_db) -> None:
    # MySQL returns SUM(...) as Decimal; the model must receive int.
    conn, user_id, _ = thread_db
    pending = _send(conn, user_id, "<a@x.com>")
    row = threads.get_thread(conn, user_id, pending.thread_id)

    assert type(row["pending_count"]) is int
    assert type(row["message_count"]) is int
    summary = EmailThreadSummary.model_validate(row)
    assert summary.pending_count == 1


def test_message_count_covers_both_directions(thread_db) -> None:
    conn, user_id, _ = thread_db
    sent = _send(conn, user_id, "<a@x.com>")
    sends.confirm_send(conn, user_id, sent.message_row_id, _T1)
    _receive(conn, user_id, sent.thread_id, "<in@x.com>", _T2)

    row = threads.get_thread(conn, user_id, sent.thread_id)
    assert row["message_count"] == 2
    assert row["pending_count"] == 0
    assert row["last_direction"] == "in"


def test_closed_threads_are_excluded_by_default(thread_db) -> None:
    conn, user_id, _ = thread_db
    open_thread = _send(conn, user_id, "<a@x.com>")
    sends.confirm_send(conn, user_id, open_thread.message_row_id, _T1)
    closed = _send(conn, user_id, "<b@x.com>")
    sends.confirm_send(conn, user_id, closed.message_row_id, _T2)
    threads.close_thread(conn, user_id, closed.thread_id)

    assert [t["id"] for t in threads.list_threads(conn, user_id)] == [open_thread.thread_id]
    assert closed.thread_id in {
        t["id"] for t in threads.list_threads(conn, user_id, include_closed=True)
    }


def test_contact_name_is_denormalized_and_optional(thread_db) -> None:
    conn, user_id, ids = thread_db
    linked = _send(conn, user_id, "<a@x.com>", contact_id=ids["jane"])
    unlinked = _send(conn, user_id, "<b@x.com>")

    assert threads.get_thread(conn, user_id, linked.thread_id)["contact_name"] == "Jane"
    assert threads.get_thread(conn, user_id, unlinked.thread_id)["contact_name"] is None


def test_list_is_owner_scoped(thread_db) -> None:
    conn, user_id, ids = thread_db
    _send(conn, user_id, "<a@x.com>")
    assert threads.list_threads(conn, ids["other_user"]) == []


# --- thread view -----------------------------------------------------------------------------


def test_messages_are_oldest_first_with_pending_last(thread_db) -> None:
    conn, user_id, _ = thread_db
    first = _send(conn, user_id, "<a@x.com>")
    sends.confirm_send(conn, user_id, first.message_row_id, _T1)
    inbound = _receive(conn, user_id, first.thread_id, "<in@x.com>", _T2)
    pending = _send(conn, user_id, "<c@x.com>", thread_id=first.thread_id)

    detail = threads.get_thread_with_messages(conn, user_id, first.thread_id)
    assert [m["id"] for m in detail["messages"]] == [
        first.message_row_id,
        inbound,
        pending.message_row_id,
    ]


def test_addresses_come_back_as_lists(thread_db) -> None:
    conn, user_id, _ = thread_db
    sent = _send(conn, user_id, "<a@x.com>", cc=["cc1@example.com", "cc2@example.com"])

    message = threads.get_message(conn, user_id, sent.message_row_id)
    assert message["to_addr"] == ["venue@example.com"]
    assert message["cc_addr"] == ["cc1@example.com", "cc2@example.com"]


def test_empty_cc_reads_as_empty_list(thread_db) -> None:
    conn, user_id, _ = thread_db
    sent = _send(conn, user_id, "<a@x.com>")
    assert threads.get_message(conn, user_id, sent.message_row_id)["cc_addr"] == []


def test_thread_detail_is_owner_scoped(thread_db) -> None:
    conn, user_id, ids = thread_db
    sent = _send(conn, user_id, "<a@x.com>")
    assert threads.get_thread_with_messages(conn, ids["other_user"], sent.thread_id) is None
    assert threads.list_messages(conn, ids["other_user"], sent.thread_id) == []
    assert threads.get_message(conn, ids["other_user"], sent.message_row_id) is None


def test_unknown_thread_reads_as_none(thread_db) -> None:
    conn, user_id, _ = thread_db
    assert threads.get_thread_with_messages(conn, user_id, 999_999) is None


# --- reply target ----------------------------------------------------------------------------


def test_latest_message_skips_pending_sends(thread_db) -> None:
    # Replying to an unsent message would chain In-Reply-To onto an id no recipient has seen.
    conn, user_id, _ = thread_db
    first = _send(conn, user_id, "<a@x.com>")
    sends.confirm_send(conn, user_id, first.message_row_id, _T1)
    inbound = _receive(conn, user_id, first.thread_id, "<in@x.com>", _T2)
    _send(conn, user_id, "<pending@x.com>", thread_id=first.thread_id)

    latest = threads.get_latest_message(conn, user_id, first.thread_id)
    assert latest["id"] == inbound
    assert latest["message_id"] == "<in@x.com>"


def test_latest_message_is_none_when_nothing_is_confirmed(thread_db) -> None:
    conn, user_id, _ = thread_db
    pending = _send(conn, user_id, "<a@x.com>")
    assert threads.get_latest_message(conn, user_id, pending.thread_id) is None


# --- explicit thread state -------------------------------------------------------------------


def test_mark_read_stamps_once_per_call(thread_db) -> None:
    conn, user_id, _ = thread_db
    sent = _send(conn, user_id, "<a@x.com>")
    assert threads.get_thread(conn, user_id, sent.thread_id)["last_read_at"] is None

    assert threads.mark_thread_read(conn, user_id, sent.thread_id) is True
    assert threads.get_thread(conn, user_id, sent.thread_id)["last_read_at"] is not None


def test_mark_read_is_owner_scoped(thread_db) -> None:
    conn, user_id, ids = thread_db
    sent = _send(conn, user_id, "<a@x.com>")
    assert threads.mark_thread_read(conn, ids["other_user"], sent.thread_id) is False


def test_close_and_reopen_are_idempotent(thread_db) -> None:
    conn, user_id, _ = thread_db
    sent = _send(conn, user_id, "<a@x.com>")

    assert threads.close_thread(conn, user_id, sent.thread_id) is True
    assert threads.close_thread(conn, user_id, sent.thread_id) is False
    assert threads.get_thread(conn, user_id, sent.thread_id)["closed_at"] is not None

    assert threads.reopen_thread(conn, user_id, sent.thread_id) is True
    assert threads.reopen_thread(conn, user_id, sent.thread_id) is False
    assert threads.get_thread(conn, user_id, sent.thread_id)["closed_at"] is None
