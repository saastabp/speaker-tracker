"""Module-scope MySQL connection management for the Speaker Tracker API Lambda.

A *single* Lambda serves every API route (see ``ARCHITECTURE.md`` §2), so one warm
container handles many requests. A cold TLS handshake to RDS over the public internet
costs 2-6s; paying that per request is the difference between a snappy CRM and an
unusable one. Therefore the connection is opened lazily and **reused at module scope**.

Reuse introduces one hazard a per-invocation connection never had: session state and
transactions outlive a single request. Two design choices contain it:

- **Liveness via the per-request ``SET time_zone``.** Every data request must set the
  caller's timezone anyway (Kauaʻi is UTC-10; date bucketing depends on it), so that
  statement doubles as a liveness probe at zero extra round trips. On a dead socket the
  code reconnects **once** with a *fresh* IAM token and re-probes; a second failure is a
  real outage, not a stale socket, and propagates.
- **``autocommit=True`` plus an explicit ``transaction`` context manager.** With
  autocommit off, every statement — including a plain ``SELECT`` — opens an implicit
  transaction that would leak across invocations on the reused socket, freezing InnoDB's
  read snapshot and holding locks. autocommit makes standalone statements close cleanly;
  atomic multi-statement writes opt in via ``transaction``.

🚫 ``ping(reconnect=True)`` is **never** used: it reconnects with the credentials stored
on the connection — the **expired IAM token** — failing intermittently on any container
older than 15 minutes, and it silently discards session state and open transactions.
The reconnect here always mints a fresh token. (Enforced by a CI grep, not ruff — ruff
cannot see the method call on a runtime connection instance.)

The module **never connects at import time**: a DB outage must 500 ``/catalogs``, not
take ``/health`` down with an initialization error.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import boto3
import pymysql
import pymysql.cursors
from pymysql.connections import Connection
from pymysql.err import InterfaceError, OperationalError

from common.logger import logger

#: AWS RDS **global** CA bundle, shipped in the deployment package next to this module.
#: Global (not the regional bundle) so the same trust anchor works if the DB moves region.
CA_BUNDLE = Path(__file__).parent / "rds-global-bundle.pem"

#: MySQL/pymysql error codes meaning the socket is dead and a single reconnect may help:
#: 2003 can't connect, 2006 server gone away, 2013 lost during query, 2055 lost (extended).
_LOST_CONNECTION_CODES = frozenset({2003, 2006, 2013, 2055})

#: Generous cap covering the 2-6s cold TLS handshake; stays under the API's 15s timeout.
_CONNECT_TIMEOUT_S = 10

_conn: Connection | None = None
_rds_client = None  # boto3 rds client, cached at module scope (token minted fresh per connect)


def _require_env(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def _region() -> str:
    """Resolve the RDS region for IAM token generation (DB_REGION, else AWS_REGION)."""
    region = os.environ.get("DB_REGION") or os.environ.get("AWS_REGION")
    if not region:
        raise RuntimeError("DB_REGION or AWS_REGION must be set for RDS IAM auth")
    return region


def _rds():
    """Return the module-cached boto3 RDS client (created lazily on first use)."""
    global _rds_client
    if _rds_client is None:
        _rds_client = boto3.client("rds", region_name=_region())
    return _rds_client


def _new_connection() -> Connection:
    """Open a fresh TLS-verified RDS connection authenticated by a new IAM token."""
    host = _require_env("DB_HOST")
    port = int(os.environ.get("DB_PORT", "3306"))
    user = _require_env("DB_USER")
    name = _require_env("DB_NAME")
    region = _region()
    # 15-minute token, minted per connection — never reused across reconnects.
    token = _rds().generate_db_auth_token(
        DBHostname=host, Port=port, DBUsername=user, Region=region
    )
    logger.debug("Opening RDS connection host=%s db=%s user=%s region=%s", host, name, user, region)
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=token,
        database=name,
        # TLS over a public endpoint: verify the cert AND that the hostname matches.
        # Omitting ssl_verify_identity leaves the link encrypted-but-MITM-able.
        ssl_ca=str(CA_BUNDLE),
        ssl_verify_cert=True,
        ssl_verify_identity=True,
        autocommit=True,
        charset="utf8mb4",
        connect_timeout=_CONNECT_TIMEOUT_S,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _apply_timezone(conn: Connection, tz_name: str) -> None:
    """Set the session timezone; parameterized, so it doubles as a liveness probe."""
    with conn.cursor() as cur:
        cur.execute("SET time_zone = %s", (tz_name,))


def _is_lost_connection(exc: Exception) -> bool:
    """Return True if the error indicates a dead socket that a reconnect may recover."""
    if isinstance(exc, InterfaceError):
        return True
    code = exc.args[0] if exc.args else None
    return code in _LOST_CONNECTION_CODES


def _close_quietly(conn: Connection | None) -> None:
    """Close a connection, swallowing errors from an already-broken socket."""
    if conn is None:
        return
    try:
        conn.close()
    except Exception:  # noqa: BLE001 - the socket is already suspect; nothing to recover
        logger.debug("Ignoring error while closing a dead connection", exc_info=True)


def get_connection(tz_name: str) -> Connection:
    """Return the reused module-scope connection with the session timezone applied.

    The ``SET time_zone`` is required per request and also serves as the liveness
    probe. If the reused socket is dead, reconnect **once** with a fresh IAM token and
    re-probe; a second failure is treated as a real outage and propagates.

    Parameters
    ----------
    tz_name : str
        An IANA timezone name (e.g. ``"Pacific/Honolulu"``) already validated upstream
        in ``tz.py``. Passed as a bound parameter, never string-formatted into SQL.

    Returns
    -------
    pymysql.connections.Connection
        A live connection whose session timezone is set to ``tz_name``.

    Raises
    ------
    pymysql.err.OperationalError or pymysql.err.InterfaceError
        If a second connection attempt also fails, or a non-connection error occurs.
    """
    global _conn
    if _conn is None:
        _conn = _new_connection()
    try:
        _apply_timezone(_conn, tz_name)
    except (OperationalError, InterfaceError) as exc:
        if not _is_lost_connection(exc):
            raise  # e.g. an invalid timezone value — not a socket problem
        code = exc.args[0] if exc.args else type(exc).__name__
        logger.warning("Reused DB connection dead (%s); reconnecting with fresh token", code)
        _close_quietly(_conn)
        _conn = None  # so a failed reconnect leaves a clean slate for the next request
        _conn = _new_connection()
        _apply_timezone(_conn, tz_name)  # second failure propagates — a real outage
    return _conn


@contextmanager
def transaction(conn: Connection) -> Iterator[Connection]:
    """Run a block as one atomic transaction on an ``autocommit=True`` connection.

    Because the connection is reused across invocations, an explicit
    ``BEGIN``/``COMMIT``/``ROLLBACK`` boundary is required for multi-statement writes so a
    handler raising mid-write cannot leak held InnoDB locks into the next invocation.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        The connection returned by :func:`get_connection`.

    Yields
    ------
    pymysql.connections.Connection
        The same connection, inside an open transaction.

    Raises
    ------
    Exception
        Re-raises any exception from the block after rolling back.

    Examples
    --------
    >>> with transaction(conn) as c:
    ...     with c.cursor() as cur:
    ...         cur.execute("INSERT INTO email_threads (...) VALUES (...)")
    ...         cur.execute("INSERT INTO email_messages (...) VALUES (...)")
    """
    conn.begin()
    try:
        yield conn
    except Exception:
        # The likeliest reason the block failed is a dead socket — on which rollback()
        # itself raises. Guard it so the ORIGINAL exception surfaces, not a masking
        # "rollback failed". The next request's liveness probe reconnects.
        try:
            conn.rollback()
        except Exception:
            logger.exception("rollback failed while handling a transaction error")
        raise
    else:
        conn.commit()
