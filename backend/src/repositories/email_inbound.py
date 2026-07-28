"""Raw-SQL write path for inbound email — the idempotent ingest the poller performs per message.

The read half is :mod:`repositories.email_matching`; this is where a polled message becomes rows.
One call writes at most two: the ``email_messages`` row, and an ``email_threads`` row when the
message starts a conversation. It mirrors how 6a split writes (:mod:`repositories.email_sends`)
from reads (:mod:`repositories.email_threads`).

Three rules this module exists to enforce, each of which is an acceptance criterion:

- **It never writes an ``outreaches`` row.** Not for inbound mail (#8), and not for an outbound
  message discovered in ``Sent`` because Donna composed it in Outlook. All outreach counting
  originates inside the app: a touch appearing in the journal that she never logged or sent from
  here is unexplainable from her side, and an unexplainable number costs more trust than a
  conservative one. There is deliberately no code path from here to that table.
- **Re-ingesting a message writes nothing** (#5). ``UNIQUE(user_id, message_id)`` is the key, and
  :func:`~core.email_headers.bracketed` canonicalizes the id so the same message cannot present as
  two different strings. A duplicate is reported, not raised — re-reading a message after a
  ``UIDVALIDITY`` reset is normal operation, not an error.
- **A new thread's ``opportunity_id`` is unconditionally NULL.** Even when the contact has exactly
  one open gig, a message is not evidence it concerns that gig, and filing side-channel mail
  against the wrong opportunity is worse than leaving it unattached. Threads reach an opportunity
  by inheriting one through a header match, or by Donna linking them by hand
  (``repositories.email_imports``).

And one it deliberately does *not* do: **ingest never sets an existing thread's ``contact_id``.**
A thread's contact is fixed when the thread is created or changed by an explicit human link, full
stop. Backfilling it here would let a pending-import row leave the triage queue without Donna
acting, and the queue is only worth trusting if it changes when she says so.
"""

from __future__ import annotations

import datetime as dt
from typing import NamedTuple

from pymysql.connections import Connection
from pymysql.err import IntegrityError

from core.email_headers import bracketed, normalize_subject
from core.email_scope import DIRECTION_OUT, Direction
from core.email_threading import as_naive_utc


class InboundMessage(NamedTuple):
    """One polled message, parsed from its MIME and ready to store.

    Header values are carried **raw**, as received, and normalized at the point each is used —
    ``subject`` by :func:`~core.email_headers.normalize_subject` for the thread key,
    ``message_id`` by :func:`~core.email_headers.bracketed` for the idempotency key. Storing the
    raw form keeps ``email_messages`` a faithful record of what arrived.

    Attributes
    ----------
    message_id : str
        The message's RFC 5322 ``Message-ID``. Canonicalized to bracketed form before storage.
    from_addr : str
        The raw ``From`` header, display name included, as ``email_messages.from_addr`` holds it
        for outbound mail too.
    to_addr, cc_addr : str or None
        Raw ``To`` / ``Cc`` header values. Stored verbatim in the comma-separated TEXT columns
        that ``repositories.email_threads`` splits on read.
    subject : str or None
        Raw ``Subject``; ``None`` for a subjectless message, which is legal mail.
    in_reply_to, message_references : str or None
        Raw threading headers. ``message_references`` is spelled out because ``references`` is a
        reserved word in MySQL.
    occurred_at : datetime or None
        When the message was sent or received — the ``Date`` header, or the IMAP ``INTERNALDATE``
        when that is missing or unparseable. Converted to naive UTC before it reaches a query, so
        an aware value is safe to pass. ``None`` leaves the timestamp NULL rather than inventing
        one, which keeps the thread's ``last_message_at`` honest.
    imap_folder : str
        Folder the message was read from, as named on the server.
    imap_uid : int
        Its UID within that folder's current ``UIDVALIDITY`` generation.
    s3_key : str or None
        Key of the stored raw MIME, when the poller has written it. Reads reconstruct the body and
        attachments from it; ``None`` yields a message that lists without a body rather than one
        that fails to list.
    """

    message_id: str
    from_addr: str
    to_addr: str | None = None
    cc_addr: str | None = None
    subject: str | None = None
    in_reply_to: str | None = None
    message_references: str | None = None
    occurred_at: dt.datetime | None = None
    imap_folder: str = ""
    imap_uid: int = 0
    s3_key: str | None = None


class IngestResult(NamedTuple):
    """What one ingest attempt produced.

    Attributes
    ----------
    message_row_id : int
        ``email_messages.id`` — the stored row, whether this call wrote it or found it already
        there. Named as in ``repositories.email_sends.PendingSend``, where a bare ``message_id``
        would mean the RFC 5322 header instead.
    thread_id : int
        The thread the message belongs to.
    thread_created : bool
        Whether this call created the thread.
    duplicate : bool
        ``True`` when the message was already stored and nothing was written. Normal after a
        ``UIDVALIDITY`` reset re-reads a folder, and the mechanism behind acceptance #5.
    pending : bool
        ``True`` only alongside ``duplicate``: the stored row is one of *our* sends that never got
        confirmed (``direction='out' AND sent_at IS NULL``), because the process died between SES
        accepting it and phase 3. Seeing it come back in ``Sent`` is the reconciliation signal
        ``repositories.email_sends`` was designed around — the **caller** then calls
        ``confirm_send``, since repositories in this codebase do not call one another.
    """

    message_row_id: int
    thread_id: int
    thread_created: bool
    duplicate: bool
    pending: bool


def _existing_message(conn: Connection, user_id: int, message_id: str) -> dict | None:
    """Return the stored row for a canonical ``Message-ID``, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, thread_id, direction, sent_at FROM email_messages "
            "WHERE user_id = %s AND message_id = %s",
            (user_id, message_id),
        )
        return cur.fetchone()


def _duplicate_result(row: dict) -> IngestResult:
    """Build the no-write result for a message we already hold."""
    pending = row["direction"] == DIRECTION_OUT and row["sent_at"] is None
    return IngestResult(
        message_row_id=row["id"],
        thread_id=row["thread_id"],
        thread_created=False,
        duplicate=True,
        pending=pending,
    )


def _create_thread(
    conn: Connection,
    user_id: int,
    message: InboundMessage,
    *,
    direction: Direction,
    contact_id: int | None,
    occurred_at: dt.datetime | None,
) -> int:
    """Insert a thread for a conversation we have not seen before and return its id.

    ``opportunity_id`` is omitted from the INSERT rather than passed as ``None``, so the column's
    NULL default is what makes it NULL. There is no parameter to pass a gig through: the rule that
    an inbound-first thread starts unattached is expressed as an absence, which is harder to defeat
    by accident than a default argument.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_threads "
            "(user_id, contact_id, subject_normalized, last_direction, last_message_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                user_id,
                contact_id,
                normalize_subject(message.subject),
                direction,
                occurred_at,
            ),
        )
        return cur.lastrowid


def _thread_state(conn: Connection, user_id: int, thread_id: int) -> dict | None:
    """Return an owned thread's attribution and clock, or ``None`` when it is not the caller's."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT contact_id, opportunity_id, last_message_at FROM email_threads "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (thread_id, user_id),
        )
        return cur.fetchone()


def _advance_thread(
    conn: Connection,
    user_id: int,
    thread_id: int,
    *,
    direction: Direction,
    occurred_at: dt.datetime | None,
    current_last: dt.datetime | None,
) -> None:
    """Move a thread's clock and direction forward, but never backward.

    The comparison is done here rather than in SQL deliberately. Expressing it as a single
    ``ON DUPLICATE``-style statement would make ``last_direction`` depend on ``last_message_at``
    not having been assigned yet — MySQL evaluates such assignments left to right, and that
    ordering dependency is invisible to a reader and survives no refactor. In Python the condition
    is stated once and reads as what it is.

    A message older than the thread's newest (a backfilled import, or a rescan reaching an old
    message first) updates nothing: it belongs to the conversation but is not its latest news.
    """
    if occurred_at is None:
        return
    if current_last is not None and occurred_at <= current_last:
        return
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE email_threads SET last_direction = %s, last_message_at = %s "
            "WHERE id = %s AND user_id = %s",
            (direction, occurred_at, thread_id, user_id),
        )


def ingest_message(
    conn: Connection,
    user_id: int,
    message: InboundMessage,
    *,
    direction: Direction,
    contact_id: int | None,
    thread_id: int | None,
) -> IngestResult:
    """Store one polled message, idempotently, inside the caller's transaction.

    The decisions are all made before this call: ``core.email_scope.classify_message`` has already
    said the message is in scope and to which contact it attributes, and
    ``core.email_threading.resolve_thread`` has already found its thread or decided it starts one.
    This function's whole job is to write that down exactly once.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection, already inside ``common.db.transaction``.
    user_id : int
        The owning user, from ``repositories.users.resolve_solo_user_id``.
    message : InboundMessage
        The parsed message.
    direction : {"in", "out"}
        From the ``IngestDecision`` — derived from the sender, not the folder, so a message Donna
        Cc'd to herself is not recorded as mail she received.
    contact_id : int or None
        The tracked contact this message attributes to, or ``None``. ``None`` is legitimate: it is
        either the pending-import state, or a thread match whose contact is inherited below.
    thread_id : int or None
        Thread to append to, from ``resolve_thread``. ``None`` creates one.

    Returns
    -------
    IngestResult
        With ``duplicate`` set when the message was already stored — check ``pending`` on such a
        result to decide whether one of our own unconfirmed sends has just been reconciled.

    Raises
    ------
    common.errors.NotFound
        Never. A ``thread_id`` that is not the caller's is treated as no thread at all, and a new
        one is created — the ids come from this module's own owner-scoped lookups, so a foreign id
        means a caller bug, and inventing a thread is safer inside a poll than aborting it.
    """
    canonical_id = bracketed(message.message_id)
    occurred_at = as_naive_utc(message.occurred_at) if message.occurred_at else None

    existing = _existing_message(conn, user_id, canonical_id)
    if existing is not None:
        return _duplicate_result(existing)

    thread_state = _thread_state(conn, user_id, thread_id) if thread_id is not None else None
    thread_created = thread_state is None

    if thread_state is None:
        resolved_thread_id = _create_thread(
            conn,
            user_id,
            message,
            direction=direction,
            contact_id=contact_id,
            occurred_at=occurred_at,
        )
        # The message inherits what the thread was just created with; a brand-new thread never
        # carries an opportunity (see the module docstring).
        row_contact_id = contact_id
        row_opportunity_id = None
    else:
        resolved_thread_id = thread_id
        # Inherit from the conversation the message joined. The thread's attribution wins for the
        # opportunity — that is how a reply reaches the right gig (#1) — while the contact falls
        # back to the thread's only when this message resolved none of its own.
        row_contact_id = contact_id if contact_id is not None else thread_state["contact_id"]
        row_opportunity_id = thread_state["opportunity_id"]

    sent_at = occurred_at if direction == DIRECTION_OUT else None
    received_at = None if direction == DIRECTION_OUT else occurred_at

    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO email_messages "
                "(user_id, thread_id, contact_id, opportunity_id, message_id, in_reply_to, "
                " message_references, direction, subject, from_addr, to_addr, cc_addr, s3_key, "
                " imap_folder, imap_uid, sent_at, received_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    user_id,
                    resolved_thread_id,
                    row_contact_id,
                    row_opportunity_id,
                    canonical_id,
                    message.in_reply_to,
                    message.message_references,
                    direction,
                    message.subject,
                    message.from_addr,
                    message.to_addr,
                    message.cc_addr,
                    message.s3_key,
                    message.imap_folder or None,
                    message.imap_uid or None,
                    sent_at,
                    received_at,
                ),
            )
            message_row_id = cur.lastrowid
    except IntegrityError:
        # The pre-check above races only if two polls overlap, which reserved concurrency 1 is
        # meant to prevent (#7). Handling it anyway costs one query and means the guarantee rests
        # on the unique index rather than on a deployment setting staying correct.
        existing = _existing_message(conn, user_id, canonical_id)
        if existing is None:
            raise
        return _duplicate_result(existing)

    _advance_thread(
        conn,
        user_id,
        resolved_thread_id,
        direction=direction,
        occurred_at=occurred_at,
        current_last=None if thread_state is None else thread_state["last_message_at"],
    )

    return IngestResult(
        message_row_id=message_row_id,
        thread_id=resolved_thread_id,
        thread_created=thread_created,
        duplicate=False,
        pending=False,
    )
