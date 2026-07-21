"""Request-context assembly for authenticated routes.

Composes the pieces an authenticated data route needs — the principal, a timezone-scoped DB
connection, and the caller's ``users`` row — into one :class:`AuthenticatedRequest`. This is the
handler-edge wiring layer: it reaches *down* into ``common`` (auth, db, tz) and ``repositories``,
so those lower layers never depend on each other. Keeping it here (not in ``common/auth.py``)
leaves ``common`` a set of leaves that never import ``repositories``.
"""

from __future__ import annotations

from dataclasses import dataclass

from pymysql.connections import Connection

from common.auth import Principal, principal_from_event
from common.db import get_connection
from common.logger import logger
from common.tz import timezone_from_event
from repositories.users import upsert_user_id


@dataclass(frozen=True)
class AuthenticatedRequest:
    """The fully resolved context for one authenticated request.

    Parameters
    ----------
    principal : common.auth.Principal
        The authenticated caller.
    connection : pymysql.connections.Connection
        The reused module-scope connection with the caller's timezone applied.
    user_id : int
        The caller's ``users.id``, created on first sign-in.
    """

    principal: Principal
    connection: Connection
    user_id: int


def authenticate(event: dict) -> AuthenticatedRequest:
    """Authenticate a request end to end: principal, connection, and ``users`` row.

    This is the single entry step every authenticated route calls first. It resolves the
    principal, opens the timezone-scoped connection, and **upserts the caller's ``users``
    row** — the source of truth for that row on the first authenticated request. The Cognito
    ``post_confirmation`` trigger is only best-effort (an ``AdminCreateUser`` user is
    pre-confirmed so it may never fire, and its 5s cap collides with the 2-6s cold RDS
    handshake), so the row cannot be relied upon to exist yet. ``/health`` deliberately does
    **not** call this, which is what keeps it free of the database.

    Parameters
    ----------
    event : dict
        An API Gateway HTTP API v2 event.

    Returns
    -------
    AuthenticatedRequest
        The principal, the live connection, and the caller's ``users.id``.

    Raises
    ------
    common.errors.Unauthorized
        When no authenticated principal is present (maps to HTTP 401).
    """
    principal = principal_from_event(event)
    tz = timezone_from_event(event)
    connection = get_connection(tz)
    user_id = upsert_user_id(connection, principal.sub, principal.email)
    logger.debug("Authenticated request user_id=%s sub=%s", user_id, principal.sub)
    return AuthenticatedRequest(principal=principal, connection=connection, user_id=user_id)
