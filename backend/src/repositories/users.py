"""Persistence for the ``users`` aggregate — the tenant root.

``upsert_user_id`` is the **source of truth** for a user's row, called by every authenticated
handler on each request. It creates the row on first sign-in and returns its id thereafter.
This is deliberately *not* the Cognito ``post_confirmation`` trigger: that trigger has a hard
5s timeout against a 2-6s cold RDS handshake, and ``AdminCreateUser`` creates users
already-confirmed so ``PostConfirmation`` may never fire at all (see ``DEV-PLAN.md``
acceptance #4). The lazy upsert on the warm request path is what we rely on.

A second resolver, :func:`resolve_solo_user_id`, exists for the IMAP poller, which runs on a
schedule with no Cognito context. It is deliberately a *guard* rather than a lookup: it encodes the
single-user assumption explicitly and refuses to guess when that assumption breaks.
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


class MultipleUsersError(RuntimeError):
    """Raised when the poller finds more than one user and refuses to guess whose mail this is."""


def resolve_solo_user_id(conn: Connection) -> int | None:
    """Return the only user's id, for a caller with no authenticated identity.

    The IMAP poller runs on a schedule, not on a request, so :func:`upsert_user_id` — which needs a
    Cognito subject — is unavailable to it. The mailbox it polls is one physical WorkMail account,
    so "the one user" is a real fact about the deployment rather than a convenience, and this
    function states that assumption in one place where it can fail loudly the day it stops holding.

    The two edge cases are handled differently on purpose:

    - **No users yet** — nobody has signed in, so there is no one to attribute mail to. Returns
      ``None`` and logs a WARNING; the caller no-ops. This is expected on a fresh deploy and must
      not page anyone.
    - **More than one user** — raises. Picking either one would file Donna's mail against a
      stranger's account, and picking neither silently would be the invisible failure acceptance
      #11 exists to prevent. Failing the invocation surfaces it on the Errors alarm.

    Rejected: an env var (``POLL_USER_EMAIL``, or reusing ``MAIL_FROM_ADDRESS``) matched against
    ``users.email``. That column comes from Cognito and need not equal the mailbox address, so a
    mismatch yields zero rows and a poller that runs forever finding nothing. The multi-user path,
    when it arrives, is a mailbox-to-user config table, not this function growing a parameter.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.

    Returns
    -------
    int or None
        The single user's id, or ``None`` when no user exists yet.

    Raises
    ------
    MultipleUsersError
        When more than one user row exists — the single-user assumption has been broken and the
        poller must not guess.
    """
    with conn.cursor() as cur:
        # LIMIT 2 answers "is there more than one" in one round trip, without counting a table
        # that in the failure case could hold many rows.
        cur.execute("SELECT id FROM users ORDER BY id LIMIT 2")
        rows = cur.fetchall()
    if not rows:
        logger.warning("IMAP poll skipped: no users row exists yet (nobody has signed in)")
        return None
    if len(rows) > 1:
        raise MultipleUsersError(
            f"expected exactly one user for the polled mailbox, found at least {len(rows)}"
        )
    return rows[0]["id"]
