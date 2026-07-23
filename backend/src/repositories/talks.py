"""Raw-SQL persistence for talks — the reusable offers an opportunity can reference via talk_id.

Talks carry no catalog FKs and no unique-name constraint, so this is the simplest entity repo: a
straight insert/read of the writable columns, scoped to `user_id` and hiding soft-deleted rows.
Soft-delete (`deleted_at`) rather than hard-delete keeps `opportunities.talk_id` valid — an
opportunity that referenced a since-retired talk still resolves its title, because opportunity reads
do not filter the talk's `deleted_at`.
"""

from __future__ import annotations

from pymysql.connections import Connection

from models.talks import TalkInput

#: Writable columns that map 1:1 from the input.
_PLAIN_COLUMNS = ("title", "length_minutes", "one_liner", "sort_order")

#: Columns returned by the list/get reads (shared so summary and detail stay in step).
_READ_COLUMNS = "id, title, length_minutes, one_liner, sort_order, created_at, updated_at"


def _plain_values(data: TalkInput) -> tuple:
    """Return the writable values in `_PLAIN_COLUMNS` order."""
    return tuple(getattr(data, column) for column in _PLAIN_COLUMNS)


def list_talks(conn: Connection, user_id: int) -> list[dict]:
    """Return the caller's talks, ordered for the picker.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.

    Returns
    -------
    list of dict
        One row per non-deleted talk, ascending `sort_order` then `title`.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_READ_COLUMNS} FROM talks "
            "WHERE user_id = %s AND deleted_at IS NULL "
            "ORDER BY sort_order, title",
            (user_id,),
        )
        return list(cur.fetchall())


def get_talk(conn: Connection, user_id: int, talk_id: int) -> dict | None:
    """Return one talk, or None if absent for this user.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    talk_id : int
        The talk id.

    Returns
    -------
    dict or None
        The talk row (writable fields, id, timestamps), or None when it does not exist for
        this user.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_READ_COLUMNS} FROM talks "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (talk_id, user_id),
        )
        return cur.fetchone()


def create_talk(conn: Connection, user_id: int, data: TalkInput) -> int:
    """Insert a talk and return its new id.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    data : models.talks.TalkInput
        The validated writable fields.

    Returns
    -------
    int
        The new talk's id.
    """
    columns = ("user_id", *_PLAIN_COLUMNS)
    placeholders = ", ".join(["%s"] * len(columns))
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO talks ({', '.join(columns)}) VALUES ({placeholders})",
            (user_id, *_plain_values(data)),
        )
        return cur.lastrowid


def update_talk(conn: Connection, user_id: int, talk_id: int, data: TalkInput) -> bool:
    """Full-replace a talk's writable fields; return whether it existed.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    talk_id : int
        The talk id.
    data : models.talks.TalkInput
        The validated replacement fields.

    Returns
    -------
    bool
        True if the talk existed and was updated; False if absent (caller maps to 404). The
        existence check is explicit because an update to identical values reports zero affected
        rows, which must not be mistaken for a missing talk.
    """
    assignments = ", ".join(f"{column} = %s" for column in _PLAIN_COLUMNS)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM talks WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (talk_id, user_id),
        )
        if cur.fetchone() is None:
            return False
        cur.execute(
            f"UPDATE talks SET {assignments} WHERE id = %s AND user_id = %s",
            (*_plain_values(data), talk_id, user_id),
        )
    return True


def soft_delete_talk(conn: Connection, user_id: int, talk_id: int) -> bool:
    """Soft-delete a talk; return whether a live row was deleted.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    talk_id : int
        The talk id.

    Returns
    -------
    bool
        True if a non-deleted talk was marked deleted; False otherwise. Opportunities that
        reference the talk keep resolving its title (reads do not filter the talk's `deleted_at`).
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE talks SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (talk_id, user_id),
        )
        return cur.rowcount > 0
