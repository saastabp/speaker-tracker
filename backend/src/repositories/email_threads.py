"""Raw-SQL reads for email threads and their messages.

The read side of slice 6a: the Emails inbox list, the thread view, and the single-message lookup
the reply path needs to recover a parent's ``Message-ID`` / ``References``. Writes live in
:mod:`repositories.email_sends`; the only mutations here are the explicit thread-state changes
(read marker, close, reopen), which are user actions rather than part of a send.

Two derived aggregates ride on every thread row. ``message_count`` is the obvious one;
``pending_count`` counts outbound messages still awaiting confirmation (``direction='out' AND
sent_at IS NULL``) and drives the inbox's "pending" badge. Neither is a column — both recompute
per read, so a pending count falls to zero the moment ``confirm_send`` lands.

Ordering is deliberate in both directions:

- **Threads** sort by ``last_message_at`` descending with **NULLs last**. MySQL sorts NULL first
  by default, which would float a thread whose only message is an unconfirmed send to the top of
  the inbox — the least useful thing on the page. The explicit ``IS NULL`` sort key sinks them.
- **Messages** sort by ``COALESCE(sent_at, received_at)``, matching the functional index
  ``ix_email_messages_thread`` from ``0008`` so the thread view is an index scan. A pending
  message has neither timestamp, so ``id`` breaks the tie and it lands at the end — where a
  message that has not gone out belongs.

Addresses are stored as comma-separated TEXT (``to_addr`` / ``cc_addr``) and split into lists
here, so no layer above the repository parses header text.
"""

from __future__ import annotations

from pymysql.connections import Connection

#: Thread columns plus the two derived aggregates. ``contacts`` is LEFT-joined without a
#: ``deleted_at`` filter: a thread keeps showing its contact's name after that contact is
#: soft-deleted, and threads with no contact at all (unknown sender) are valid.
_THREAD_SELECT = (
    "SELECT t.id, t.subject_normalized, t.contact_id, c.name AS contact_name, "
    "       t.opportunity_id, t.last_direction, t.last_message_at, t.last_read_at, t.closed_at, "
    "       COUNT(m.id) AS message_count, "
    "       COALESCE(SUM(m.direction = 'out' AND m.sent_at IS NULL), 0) AS pending_count "
    "FROM email_threads t "
    "LEFT JOIN contacts c ON c.id = t.contact_id "
    "LEFT JOIN email_messages m ON m.thread_id = t.id "
)

#: Message columns as stored. Body and attachments are not columns — they are reconstructed from
#: the raw MIME at ``s3_key`` by the handler.
_MESSAGE_SELECT = (
    "SELECT m.id, m.thread_id, m.direction, m.message_id, m.in_reply_to, m.message_references, "
    "       m.subject, m.from_addr, m.to_addr, m.cc_addr, m.s3_key, m.sent_at, m.received_at "
    "FROM email_messages m "
)


def _split_addresses(value: str | None) -> list[str]:
    """Split a stored comma-separated address column into a list.

    Parameters
    ----------
    value : str or None
        ``to_addr`` / ``cc_addr`` as stored, or ``None`` for an empty Cc.

    Returns
    -------
    list of str
        Trimmed addresses, empty when `value` is ``None`` or blank.
    """
    if not value:
        return []
    return [address.strip() for address in value.split(",") if address.strip()]


def _thread_row(row: dict) -> dict:
    """Coerce a raw thread row into the shape ``EmailThreadSummary`` expects.

    MySQL returns ``SUM(...)`` as ``Decimal``; Pydantic would accept it but every consumer would
    then be handling a numeric type that is not ``int``. Cast once, here.
    """
    row["message_count"] = int(row["message_count"] or 0)
    row["pending_count"] = int(row["pending_count"] or 0)
    return row


def _message_row(row: dict) -> dict:
    """Coerce a raw message row into the shape the message models expect."""
    row["to_addr"] = _split_addresses(row.get("to_addr"))
    row["cc_addr"] = _split_addresses(row.get("cc_addr"))
    return row


def list_threads(conn: Connection, user_id: int, *, include_closed: bool = False) -> list[dict]:
    """Return the caller's threads for the Emails inbox, most recent activity first.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user; threads are owner-scoped, so another account's are never visible.
    include_closed : bool, optional
        Whether to include explicitly closed threads. Default ``False`` — the inbox shows live
        conversations, and closing is how a thread leaves it.

    Returns
    -------
    list of dict
        Thread summary rows with ``message_count`` / ``pending_count``, ordered newest-activity
        first with unconfirmed-only threads last.
    """
    sql = _THREAD_SELECT + "WHERE t.user_id = %s AND t.deleted_at IS NULL "
    if not include_closed:
        sql += "AND t.closed_at IS NULL "
    sql += "GROUP BY t.id ORDER BY t.last_message_at IS NULL, t.last_message_at DESC, t.id DESC"
    with conn.cursor() as cur:
        cur.execute(sql, (user_id,))
        return [_thread_row(row) for row in cur.fetchall()]


def get_thread(conn: Connection, user_id: int, thread_id: int) -> dict | None:
    """Return one thread summary owned by ``user_id``, or ``None`` if absent or deleted."""
    with conn.cursor() as cur:
        cur.execute(
            _THREAD_SELECT + "WHERE t.id = %s AND t.user_id = %s AND t.deleted_at IS NULL "
            "GROUP BY t.id",
            (thread_id, user_id),
        )
        row = cur.fetchone()
    return _thread_row(row) if row is not None else None


def list_messages(conn: Connection, user_id: int, thread_id: int) -> list[dict]:
    """Return a thread's messages oldest first, owner-scoped.

    Ordered on ``COALESCE(sent_at, received_at)`` to match ``ix_email_messages_thread``; pending
    outbound messages have neither timestamp and sort last by ``id``.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    thread_id : int
        Thread whose messages to return. A foreign or unknown id yields an empty list rather than
        leaking another account's conversation.

    Returns
    -------
    list of dict
        Message rows with ``to_addr`` / ``cc_addr`` split into lists.
    """
    with conn.cursor() as cur:
        cur.execute(
            _MESSAGE_SELECT + "WHERE m.thread_id = %s AND m.user_id = %s "
            "ORDER BY COALESCE(m.sent_at, m.received_at) IS NULL, "
            "         COALESCE(m.sent_at, m.received_at) ASC, m.id ASC",
            (thread_id, user_id),
        )
        return [_message_row(row) for row in cur.fetchall()]


def get_thread_with_messages(conn: Connection, user_id: int, thread_id: int) -> dict | None:
    """Return one thread with its full conversation, or ``None`` if absent or deleted.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    thread_id : int
        The thread to read.

    Returns
    -------
    dict or None
        The thread summary with a ``messages`` key holding its messages oldest first.
    """
    thread = get_thread(conn, user_id, thread_id)
    if thread is None:
        return None
    thread["messages"] = list_messages(conn, user_id, thread_id)
    return thread


def get_message(conn: Connection, user_id: int, message_row_id: int) -> dict | None:
    """Return one message by its row id, owner-scoped, or ``None``.

    The reply path uses this to recover the parent's ``message_id`` and ``message_references``
    before handing them to ``core.email_headers.build_reply_headers``.
    """
    with conn.cursor() as cur:
        cur.execute(
            _MESSAGE_SELECT + "WHERE m.id = %s AND m.user_id = %s",
            (message_row_id, user_id),
        )
        row = cur.fetchone()
    return _message_row(row) if row is not None else None


def get_latest_message(conn: Connection, user_id: int, thread_id: int) -> dict | None:
    """Return a thread's most recent message, or ``None`` when the thread has none.

    This is what an inline reply replies *to* when the client sends no explicit
    ``in_reply_to_message_id``. Pending outbound messages are excluded: replying to a message that
    may never have been sent would chain ``In-Reply-To`` onto an id no recipient has ever seen.
    """
    with conn.cursor() as cur:
        cur.execute(
            _MESSAGE_SELECT + "WHERE m.thread_id = %s AND m.user_id = %s "
            "AND COALESCE(m.sent_at, m.received_at) IS NOT NULL "
            "ORDER BY COALESCE(m.sent_at, m.received_at) DESC, m.id DESC LIMIT 1",
            (thread_id, user_id),
        )
        row = cur.fetchone()
    return _message_row(row) if row is not None else None


def mark_thread_read(conn: Connection, user_id: int, thread_id: int) -> bool:
    """Stamp ``last_read_at`` on a thread; return whether a live thread was updated.

    Unread state is derived by the client from ``last_direction`` / ``last_message_at`` /
    ``last_read_at`` rather than stored as a flag, so this single stamp is the whole write.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE email_threads SET last_read_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (thread_id, user_id),
        )
        return cur.rowcount > 0


def close_thread(conn: Connection, user_id: int, thread_id: int) -> bool:
    """Close a thread explicitly; return whether an open thread was closed.

    Threads close only by an explicit act — this call, or an opportunity closing — never inferred
    from ``last_direction`` (DESIGN.md: nothing infers an owed reply).
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE email_threads SET closed_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND closed_at IS NULL AND deleted_at IS NULL",
            (thread_id, user_id),
        )
        return cur.rowcount > 0


def reopen_thread(conn: Connection, user_id: int, thread_id: int) -> bool:
    """Reopen a closed thread; return whether a closed thread was reopened."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE email_threads SET closed_at = NULL "
            "WHERE id = %s AND user_id = %s AND closed_at IS NOT NULL AND deleted_at IS NULL",
            (thread_id, user_id),
        )
        return cur.rowcount > 0
