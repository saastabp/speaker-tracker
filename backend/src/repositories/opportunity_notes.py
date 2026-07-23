"""Raw-SQL persistence for opportunity notes — the dated free-text journal on a gig.

Notes are soft-deleted (`deleted_at`), scoped to the owning user and opportunity. `occurred_at` is
user-settable (a note can record something that happened earlier) and defaults to now when omitted.
Reads go through :func:`repositories.opportunities.get_opportunity_notes`; this module owns writes.
"""

from __future__ import annotations

from pymysql.connections import Connection

from common import errors
from models.opportunities import OpportunityNoteInput


def add_note(conn: Connection, user_id: int, opp_id: int, data: OpportunityNoteInput) -> int:
    """Insert a note on an opportunity and return its new id; the opportunity must be the caller's.

    The insert is guarded by an `EXISTS` check on the owning opportunity, so a missing/foreign id
    inserts nothing. `occurred_at` uses the supplied value, or `CURRENT_TIMESTAMP` when omitted.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    opp_id : int
        The opportunity id.
    data : models.opportunities.OpportunityNoteInput
        The note body and optional `occurred_at`.

    Returns
    -------
    int
        The new note's id.

    Raises
    ------
    common.errors.NotFound
        When the opportunity does not exist for this user.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO opportunity_notes (user_id, opportunity_id, body, occurred_at) "
            "SELECT %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP) FROM DUAL "
            "WHERE EXISTS (SELECT 1 FROM opportunities "
            "              WHERE id = %s AND user_id = %s AND deleted_at IS NULL)",
            (user_id, opp_id, data.body, data.occurred_at, opp_id, user_id),
        )
        if cur.rowcount == 0:
            raise errors.NotFound("opportunity not found")
        return cur.lastrowid


def soft_delete_note(conn: Connection, user_id: int, opp_id: int, note_id: int) -> bool:
    """Soft-delete a note; return whether a live note was deleted.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    opp_id : int
        The opportunity the note belongs to.
    note_id : int
        The note id.

    Returns
    -------
    bool
        True if a non-deleted note owned by this user on this opportunity was marked deleted;
        False otherwise.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE opportunity_notes SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND opportunity_id = %s AND user_id = %s AND deleted_at IS NULL",
            (note_id, opp_id, user_id),
        )
        return cur.rowcount > 0
