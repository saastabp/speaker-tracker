"""Persistence for the ``users`` aggregate — the tenant root.

``upsert_user_id`` is the **source of truth** for a user's row, called by every authenticated
handler on each request. It creates the row on first sign-in and returns its id thereafter.
This is deliberately *not* the Cognito ``post_confirmation`` trigger: that trigger has a hard
5s timeout against a 2-6s cold RDS handshake, and ``AdminCreateUser`` creates users
already-confirmed so ``PostConfirmation`` may never fire at all (see ``DEV-PLAN.md``
acceptance #4). The lazy upsert on the warm request path is what we rely on.
"""

from __future__ import annotations

from pymysql.connections import Connection

from common.logger import logger

# One atomic statement: insert on first sign-in, else re-surface the existing id.
# LAST_INSERT_ID(id) makes cursor.lastrowid return the existing row's id on conflict and the
# new id on insert — one round trip, no separate SELECT, and no duplicate rows under a
# concurrent race (e.g. the post_confirmation trigger firing at the same moment as the first
# API request). The conflict branch is a true no-op write: it does not refresh email, so an
# authed request never bumps updated_at. A later Cognito email change is therefore not tracked
# here — an accepted trade for a single admin-managed user.
_UPSERT_SQL = (
    "INSERT INTO users (cognito_sub, email) VALUES (%s, %s) "
    "ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)"
)


def upsert_user_id(conn: Connection, sub: str, email: str) -> int:
    """Return the ``users.id`` for a Cognito subject, creating the row if absent.

    Idempotent and race-safe: the insert-or-resurface is a single statement, so concurrent
    callers can neither create duplicate rows nor read a half-written one.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection from :func:`common.db.get_connection`.
    sub : str
        The Cognito subject claim — the stable unique key for the user.
    email : str
        The caller's email, stored on the initial insert.

    Returns
    -------
    int
        The primary key of the caller's ``users`` row.
    """
    with conn.cursor() as cur:
        cur.execute(_UPSERT_SQL, (sub, email))
        user_id = cur.lastrowid
        # rowcount == 1 is a fresh insert; 0 is an existing, unchanged row.
        if cur.rowcount == 1:
            logger.info("Created users row user_id=%s for sub=%s", user_id, sub)
    return user_id
