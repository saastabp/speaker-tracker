"""Raw-SQL persistence for opportunity response counters.

One row per (opportunity, response type) carrying a count — not a journal of individual responses
(``0015_opportunity_responses.sql``). There is no soft delete and no date: zero is the empty state,
and the per-response detail lives in legacy-tracker and GHL.

Writes resolve the ``response_type`` short_name to its FK id and reads join it back (Option A), the
same shape ``repositories.outreaches`` uses for channel and kind.

**The write is an upsert on the natural key**, so the ``+``/``-`` control can fire freely: setting a
count twice lands on the same number instead of creating a second counter for the same type.
"""

from __future__ import annotations

from pymysql.connections import Connection

from common import errors
from repositories import catalogs as catalogs_repo


def _opportunity_exists(conn: Connection, user_id: int, opp_id: int) -> bool:
    """Return whether the opportunity is a live row owned by ``user_id``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM opportunities WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (opp_id, user_id),
        )
        return cur.fetchone() is not None


def set_response_count(
    conn: Connection, user_id: int, opp_id: int, response_type: str, count: int
) -> None:
    """Set one response counter on an opportunity, creating the row if it does not exist yet.

    The parent is checked before the write rather than folded into it: ``ON DUPLICATE KEY UPDATE``
    reports ``rowcount`` 0 both for "no such opportunity" and for "the value was already that", so a
    guarded ``INSERT ... SELECT`` could not tell a missing gig from an unchanged count.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    opp_id : int
        The opportunity whose counter is being set.
    response_type : str
        ``opportunity_response_types`` catalog short_name.
    count : int
        The resulting count, not a delta. Non-negative — the model rejects the rest, and the
        table's CHECK is the backstop.

    Raises
    ------
    common.errors.NotFound
        When the opportunity is not a live row owned by the caller.
    common.errors.InvalidInput
        When ``response_type`` is not a known catalog short_name.
    """
    if not _opportunity_exists(conn, user_id, opp_id):
        raise errors.NotFound("opportunity not found")
    type_id = catalogs_repo.resolve_catalog_id(
        conn, "opportunity_response_types", response_type, "response type"
    )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO opportunity_responses "
            "(user_id, opportunity_id, opportunity_response_type_id, response_count) "
            "VALUES (%s, %s, %s, %s) "
            # The count is passed twice rather than using VALUES(), which MySQL 8.0.20 deprecated.
            "ON DUPLICATE KEY UPDATE response_count = %s",
            (user_id, opp_id, type_id, count, count),
        )


def get_response_counts(conn: Connection, user_id: int, opp_id: int) -> list[dict]:
    """Return an opportunity's response counters, in catalog order, owner-scoped.

    Only types with a stored row come back — including ones sitting at zero, which is a counter that
    was raised and then lowered again. The SPA renders the full grid from the catalog and treats an
    absent type as zero, so this never invents rows.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    opp_id : int
        The opportunity whose counters to read.

    Returns
    -------
    list of dict
        Rows shaped for :class:`models.opportunity_responses.OpportunityResponseCount`, ordered by
        the catalog's ``sort_order`` so the grid reads the same way everywhere.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.short_name AS response_type, r.response_count AS count "
            "FROM opportunity_responses r "
            "JOIN opportunity_response_types t ON t.id = r.opportunity_response_type_id "
            "WHERE r.user_id = %s AND r.opportunity_id = %s "
            "ORDER BY t.sort_order, t.short_name",
            (user_id, opp_id),
        )
        return list(cur.fetchall())
