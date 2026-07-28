"""Raw-SQL persistence for the IMAP poll watermarks — one row per user and folder.

Each row answers "where did we stop reading this folder", and the poller trusts it absolutely: a
cursor that advances past unread mail loses that mail permanently, since nothing re-scans a folder
it believes it has finished. ``core.imap_cursor`` decides *where* to resume; this module only
stores the answer.

There is no ``reset_cursor``. A ``UIDVALIDITY`` change is not a separate operation — it is an
ordinary :func:`save_cursor` whose ``uid_validity`` differs, and the statement below is what makes
the reset happen. A dedicated reset function would be a second way to write the same row, and the
two would drift.
"""

from __future__ import annotations

from pymysql.connections import Connection

# Upsert on UNIQUE(user_id, folder_name). Two things here are load-bearing:
#
# 1. `last_seen_uid` only ever moves FORWARD within a UIDVALIDITY generation (GREATEST), so a
#    retried or out-of-order write cannot rewind the watermark and re-read a folder. When the
#    generation changes the stored UIDs name different messages, so the new value is taken
#    verbatim — that IS the reset, and it is the one case where the watermark may drop.
#    This guard is insensitive to the assignment order below; only the reset is.
# 2. The assignments are evaluated LEFT TO RIGHT, so `last_seen_uid` must be computed BEFORE
#    `uid_validity` is overwritten. Reordering these two lines silently breaks the RESET: the
#    comparison would then read the new generation against itself, always match, and GREATEST
#    would keep the old watermark in a UID generation where it means nothing — skipping mail,
#    which is the exact acceptance-#6 failure. Verified on MySQL 8.4 by reordering them and
#    watching the reset stop happening.
#
# The `AS new` row alias is MySQL 8.0.19+; it replaces the deprecated VALUES() form, which emits a
# warning on the 8.4 target.
_SAVE_SQL = (
    "INSERT INTO imap_folder_cursors "
    "(user_id, folder_name, uid_validity, last_seen_uid, last_polled_at) "
    "VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP) AS new "
    "ON DUPLICATE KEY UPDATE "
    "  last_seen_uid = IF(imap_folder_cursors.uid_validity <=> new.uid_validity, "
    "                     GREATEST(imap_folder_cursors.last_seen_uid, new.last_seen_uid), "
    "                     new.last_seen_uid), "
    "  uid_validity = new.uid_validity, "
    "  last_polled_at = CURRENT_TIMESTAMP"
)


def get_cursor(conn: Connection, user_id: int, folder_name: str) -> dict | None:
    """Return a folder's stored watermark, or ``None`` when it has never been polled.

    ``None`` is the first-poll signal ``core.imap_cursor.plan_cursor`` needs to baseline instead of
    reading, so it must stay distinguishable from a stored cursor at UID 0.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    folder_name : str
        Folder as named on the server (``INBOX``, ``Sent Items``, ``Speaker Tracker/Import``).
        Stored verbatim: renaming a folder server-side is a different folder to IMAP, and it
        should get its own cursor rather than inheriting a watermark that means nothing there.

    Returns
    -------
    dict or None
        ``uid_validity`` (may be NULL) and ``last_seen_uid``, or ``None`` if no row exists.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT uid_validity, last_seen_uid, last_polled_at FROM imap_folder_cursors "
            "WHERE user_id = %s AND folder_name = %s",
            (user_id, folder_name),
        )
        return cur.fetchone()


def save_cursor(
    conn: Connection,
    user_id: int,
    folder_name: str,
    *,
    uid_validity: int,
    last_seen_uid: int,
) -> None:
    """Record how far this poll got, creating the row on a first poll.

    Called on **every** poll, including one that ingested nothing, so ``last_polled_at`` stays a
    truthful liveness signal — a folder that stops being polled is then visible as a stale
    timestamp rather than only as an absence of mail.

    The watermark cannot rewind within a ``UIDVALIDITY`` generation; see the statement's comment.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection, inside the caller's transaction.
    user_id : int
        The owning user.
    folder_name : str
        Folder as named on the server.
    uid_validity : int
        The generation the server reported on ``SELECT``. A change from the stored value resets
        the watermark to `last_seen_uid` verbatim.
    last_seen_uid : int
        Highest UID this poll successfully processed. On a baseline first poll this is the
        ``UIDNEXT - 1`` floor, which is what makes the first poll ingest nothing.
    """
    with conn.cursor() as cur:
        cur.execute(_SAVE_SQL, (user_id, folder_name, uid_validity, last_seen_uid))
