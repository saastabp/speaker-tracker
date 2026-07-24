"""Raw-SQL persistence for activity targets.

A target is a ``goal_count`` per (``target_type``, ``cadence``), owner-scoped. Writes resolve the
``target_type`` catalog short_name to its FK id and upsert on the ``UNIQUE(user_id, target_type_id,
cadence)`` key (DATABASE.md §"targets"); reads join the id back to the short_name (Option A).
Targets are config rows with no soft-delete — removing a target is a hard DELETE.
"""

from __future__ import annotations

from pymysql.connections import Connection

from common import errors
from models.targets import TargetInput

#: Response columns, target_type joined back to its short_name.
_SUMMARY_SELECT = (
    "SELECT tt.short_name AS target_type, t.cadence, t.goal_count "
    "FROM targets t "
    "JOIN target_types tt ON tt.id = t.target_type_id "
)


def _resolve_target_type_id(conn: Connection, short_name: str) -> int:
    """Resolve a ``target_types`` short_name to its id, or raise InvalidInput."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM target_types WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.InvalidInput("unknown target_type")
    return row["id"]


def list_targets(conn: Connection, user_id: int) -> list[dict]:
    """Return the caller's targets, ordered for display (by target_type then cadence)."""
    with conn.cursor() as cur:
        cur.execute(
            _SUMMARY_SELECT + "WHERE t.user_id = %s ORDER BY tt.sort_order, t.cadence",
            (user_id,),
        )
        return list(cur.fetchall())


def upsert_target(conn: Connection, user_id: int, data: TargetInput) -> None:
    """Insert or update the goal for a (target_type, cadence); keyed on the unique index.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    data : models.targets.TargetInput
        The target_type (short_name), cadence, and goal_count.

    Raises
    ------
    common.errors.InvalidInput
        When ``target_type`` is not a known catalog short_name.
    """
    target_type_id = _resolve_target_type_id(conn, data.target_type)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO targets (user_id, target_type_id, cadence, goal_count) "
            "VALUES (%s, %s, %s, %s) AS v ON DUPLICATE KEY UPDATE goal_count = v.goal_count",
            (user_id, target_type_id, data.cadence, data.goal_count),
        )


def delete_target(conn: Connection, user_id: int, target_type: str, cadence: str) -> bool:
    """Remove a target; return whether a row was deleted.

    Deletes by (user, target_type short_name, cadence) via a join, so an unknown short_name or an
    unset target simply deletes nothing and returns False (the caller maps to 404).
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE t FROM targets t JOIN target_types tt ON tt.id = t.target_type_id "
            "WHERE t.user_id = %s AND tt.short_name = %s AND t.cadence = %s",
            (user_id, target_type, cadence),
        )
        return cur.rowcount > 0
