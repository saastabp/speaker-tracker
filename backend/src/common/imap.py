"""IMAP access to the WorkMail mailbox — Sent-folder continuity and the app's drop folders.

Slice 6a uses one operation, :func:`append_to_sent`, so a message sent through the app also shows
up in Donna's Outlook Sent folder (acceptance #1). The folder plumbing here — SPECIAL-USE
discovery, delimiter detection, idempotent create + subscribe — is shared infrastructure that 6b's
poller reuses for ``Speaker Tracker/Import`` and ``/Processed`` (acceptance #4 and #5).

**Nothing here is discovered by guessing a folder name.** The Sent folder is located by the
``\\Sent`` SPECIAL-USE flag in the LIST response, because its display name is localized and
renameable; the hierarchy delimiter is read from the same response rather than assumed to be
``/``. Only if SPECIAL-USE yields nothing does the code fall back to conventional names, and that
fallback logs at WARNING — a silent fallback here would mean sent mail quietly stops appearing in
Outlook with nothing to alert on.

**Authentication failures are their own exception type.** :class:`ImapAuthError` lets a caller
retry once with ``get_imap_credentials(refresh=True)`` when a rotation is the likely cause, and
lets 6b satisfy acceptance #11 — a wrong password must alarm, never look like "no new mail".

**The `APPEND` is best-effort and time-boxed** (decision #2). It runs *after* SES has accepted the
message, so it can never un-send anything; the worst case must be a logged WARNING, not a failed
request or a stalled Lambda. Hence :data:`IMAP_TIMEOUT_S`, comfortably inside the API's 15s budget
even after a send has already consumed part of it.

*Known limitation, accepted for this scale:* ``imaplib`` is synchronous, so this is a blocking
call inside a request. At any real volume the Sent-folder copy belongs on a queue with retries and
a DLQ, leaving the request path to finish as soon as the send is recorded. For one user sending a
handful of emails a day, a bounded blocking call is the simpler correct thing.
"""

from __future__ import annotations

import imaplib
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager

from common.logger import logger
from common.secrets import get_imap_credentials

#: Env var holding the IMAP endpoint, set by the Messaging stack.
IMAP_HOST_ENV = "IMAP_HOST"

#: Implicit-TLS IMAP port. WorkMail offers no plaintext alternative, and none is wanted.
IMAP_PORT = 993

#: Socket timeout. Must stay well under the API Lambda's 15s so a hung mailbox degrades to a
#: WARNING instead of timing out the whole request after SES has already accepted the message.
IMAP_TIMEOUT_S = 10

#: Folders the app owns, as path segments joined with the server's own delimiter (never a
#: hardcoded ``/``). 6b's poller watches Import and moves processed mail to Processed.
IMPORT_FOLDER_PATH = ("Speaker Tracker", "Import")
PROCESSED_FOLDER_PATH = ("Speaker Tracker", "Processed")

#: Conventional Sent-folder names, tried only when SPECIAL-USE discovery finds nothing.
_SENT_FALLBACK_NAMES = ("Sent Items", "Sent", "INBOX.Sent")

#: LIST response line: (flags) "delimiter" "name"
_LIST_LINE_RE = re.compile(rb'\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.+)')


class ImapError(Exception):
    """An IMAP operation failed."""


class ImapAuthError(ImapError):
    """Authentication was rejected — wrong or rotated credentials.

    Distinct from :class:`ImapError` so a caller can retry once with refreshed credentials, and so
    monitoring can alarm on it specifically rather than on generic mailbox noise.
    """


def imap_host() -> str:
    """Return the IMAP endpoint from the environment.

    Returns
    -------
    str
        Hostname set in ``IMAP_HOST``.

    Raises
    ------
    RuntimeError
        When the variable is unset — a deployment fault.
    """
    host = os.environ.get(IMAP_HOST_ENV)
    if not host:
        raise RuntimeError(f"Required environment variable {IMAP_HOST_ENV} is not set")
    return host


def _connect(host: str) -> imaplib.IMAP4_SSL:
    """Open a TLS IMAP connection. The seam tests monkeypatch instead of reaching the network."""
    return imaplib.IMAP4_SSL(host=host, port=IMAP_PORT, timeout=IMAP_TIMEOUT_S)


@contextmanager
def connection(*, refresh_credentials: bool = False) -> Iterator[imaplib.IMAP4_SSL]:
    """Yield a logged-in IMAP connection, always logging out afterwards.

    Parameters
    ----------
    refresh_credentials : bool, optional
        Bypass the cached secret when fetching credentials. Pass ``True`` on a retry after
        :class:`ImapAuthError`, so a rotated password recovers without waiting for the Lambda
        container to recycle.

    Yields
    ------
    imaplib.IMAP4_SSL
        An authenticated connection.

    Raises
    ------
    ImapAuthError
        When the server rejects the credentials.
    ImapError
        When the connection itself cannot be established.
    """
    host = imap_host()
    credentials = get_imap_credentials(refresh=refresh_credentials)
    started = time.monotonic()

    try:
        conn = _connect(host)
    except OSError as exc:
        # Network-level failure: unreachable, TLS refused, or the socket timed out.
        logger.exception("IMAP connect failed host=%s", host)
        raise ImapError(f"could not connect to {host}") from exc

    try:
        conn.login(credentials.username, credentials.password)
    except imaplib.IMAP4.error as exc:
        # The password is never logged, and never included in the message.
        logger.error("IMAP login rejected host=%s user=%s", host, credentials.username)
        _logout_quietly(conn)
        raise ImapAuthError(f"IMAP login rejected for {credentials.username}") from exc
    except OSError as exc:
        logger.exception("IMAP login failed at the socket host=%s", host)
        _logout_quietly(conn)
        raise ImapError(f"IMAP login failed against {host}") from exc

    logger.info(
        "IMAP connected host=%s user=%s duration_ms=%d",
        host,
        credentials.username,
        int((time.monotonic() - started) * 1000),
    )
    try:
        yield conn
    finally:
        _logout_quietly(conn)


def _logout_quietly(conn: imaplib.IMAP4_SSL) -> None:
    """Log out, tolerating an already-broken connection."""
    try:
        conn.logout()
    except Exception:  # noqa: BLE001 - teardown; the socket is already suspect
        logger.debug("Ignoring error during IMAP logout", exc_info=True)


def _list_folders(conn: imaplib.IMAP4_SSL) -> list[tuple[set[str], str, str]]:
    """Return ``(flags, delimiter, name)`` for every folder the server lists."""
    status, lines = conn.list()
    if status != "OK":
        raise ImapError(f"IMAP LIST failed: {status}")

    folders: list[tuple[set[str], str, str]] = []
    for line in lines or []:
        raw = line if isinstance(line, bytes) else bytes(str(line), "utf-8")
        match = _LIST_LINE_RE.match(raw)
        if match is None:
            continue
        flags = {f.decode(errors="replace") for f in match.group("flags").split()}
        delimiter = match.group("delim").decode(errors="replace")
        name = match.group("name").decode(errors="replace").strip().strip('"')
        folders.append((flags, delimiter, name))
    return folders


def folder_delimiter(conn: imaplib.IMAP4_SSL) -> str:
    """Return the server's hierarchy delimiter, defaulting to ``/`` when it reports none.

    WorkMail uses ``/``, but the delimiter is a server property — hardcoding it is how folder
    creation silently produces one folder literally named ``Speaker Tracker/Import`` instead of a
    nested pair.
    """
    for _flags, delimiter, _name in _list_folders(conn):
        if delimiter:
            return delimiter
    return "/"


def find_sent_folder(conn: imaplib.IMAP4_SSL) -> str:
    """Locate the Sent folder by its ``\\Sent`` SPECIAL-USE flag.

    Returns
    -------
    str
        The folder name to ``APPEND`` into.

    Raises
    ------
    ImapError
        When neither SPECIAL-USE nor any conventional name matches, since appending to a guessed
        folder would scatter sent mail somewhere Donna will not look.

    Notes
    -----
    The fallback to conventional names logs at WARNING: sent mail quietly ceasing to appear in
    Outlook is exactly the kind of silent degradation that needs to be visible in monitoring.
    """
    folders = _list_folders(conn)
    for flags, _delimiter, name in folders:
        if "\\Sent" in flags:
            return name

    known = {name for _flags, _delim, name in folders}
    for candidate in _SENT_FALLBACK_NAMES:
        if candidate in known:
            logger.warning(
                "IMAP server advertised no \\Sent SPECIAL-USE flag; falling back to %r", candidate
            )
            return candidate

    raise ImapError("could not identify the Sent folder (no \\Sent flag and no known name)")


def append_to_sent(conn: imaplib.IMAP4_SSL, raw_message: bytes) -> str:
    """Append an already-sent message to the Sent folder and return the folder name.

    The message is flagged ``\\Seen`` — Donna sent it, so it is not unread mail.

    Parameters
    ----------
    conn : imaplib.IMAP4_SSL
        A logged-in connection.
    raw_message : bytes
        The exact bytes handed to SES, so the Sent copy matches what the recipient received.

    Returns
    -------
    str
        The folder appended to.

    Raises
    ------
    ImapError
        When the server rejects the APPEND.
    """
    folder = find_sent_folder(conn)
    started = time.monotonic()
    status, response = conn.append(f'"{folder}"', r"(\Seen)", None, raw_message)
    if status != "OK":
        raise ImapError(f"APPEND to {folder!r} failed: {status} {response!r}")

    logger.info(
        "IMAP APPEND ok folder=%s bytes=%d duration_ms=%d",
        folder,
        len(raw_message),
        int((time.monotonic() - started) * 1000),
    )
    return folder


def ensure_folder(conn: imaplib.IMAP4_SSL, path: tuple[str, ...]) -> str:
    """Create and subscribe a folder if absent; return its server-delimited name.

    Idempotent in both directions: an existing folder is left alone, and a *deleted* one is
    recreated on the next call (acceptance #5). SUBSCRIBE is what makes it appear in Outlook
    without Donna manually subscribing (acceptance #4) — creation alone is not enough.

    Parameters
    ----------
    conn : imaplib.IMAP4_SSL
        A logged-in connection.
    path : tuple of str
        Hierarchy segments, e.g. ``("Speaker Tracker", "Import")``.

    Returns
    -------
    str
        The full folder name, joined with the server's delimiter.

    Raises
    ------
    ImapError
        When SUBSCRIBE fails. A CREATE failure is tolerated only when the folder already exists —
        servers report that inconsistently, so existence is re-checked rather than trusted.
    """
    delimiter = folder_delimiter(conn)
    name = delimiter.join(path)
    existing = {folder_name for _flags, _delim, folder_name in _list_folders(conn)}

    if name not in existing:
        status, response = conn.create(f'"{name}"')
        if status != "OK":
            # Some servers answer NO for "already exists"; only a still-absent folder is an error.
            recheck = {n for _f, _d, n in _list_folders(conn)}
            if name not in recheck:
                raise ImapError(f"CREATE {name!r} failed: {status} {response!r}")
            logger.warning("IMAP CREATE %r reported %s but the folder exists", name, status)
        else:
            logger.info("IMAP created folder=%s", name)

    status, response = conn.subscribe(f'"{name}"')
    if status != "OK":
        raise ImapError(f"SUBSCRIBE {name!r} failed: {status} {response!r}")
    return name


def ensure_app_folders(conn: imaplib.IMAP4_SSL) -> tuple[str, str]:
    """Ensure both app folders exist and are subscribed; return ``(import, processed)`` names."""
    return (
        ensure_folder(conn, IMPORT_FOLDER_PATH),
        ensure_folder(conn, PROCESSED_FOLDER_PATH),
    )


def append_to_sent_best_effort(raw_message: bytes) -> bool:
    """Append to Sent, swallowing every failure into a WARNING. Returns whether it succeeded.

    This is the only entry point the send path uses (decision #2). By the time it runs, SES has
    accepted the message: it cannot be un-sent, so a mailbox problem must never fail the request
    or roll anything back. One retry with refreshed credentials covers a password rotation.

    Parameters
    ----------
    raw_message : bytes
        The exact bytes SES was given.

    Returns
    -------
    bool
        ``True`` when the message is in the Sent folder. ``False`` means the email *was still
        sent* — only the Outlook Sent copy is missing, which is the honest guarantee behind
        acceptance #1.
    """
    for attempt, refresh in enumerate((False, True), start=1):
        try:
            with connection(refresh_credentials=refresh) as conn:
                append_to_sent(conn, raw_message)
            return True
        except ImapAuthError:
            if refresh:
                logger.warning(
                    "IMAP APPEND to Sent skipped: credentials rejected after refresh "
                    "(message WAS sent; only the Sent copy is missing)"
                )
                return False
            logger.warning(
                "IMAP auth rejected on attempt %d; retrying with a fresh secret", attempt
            )
        except (ImapError, OSError, RuntimeError):
            logger.warning(
                "IMAP APPEND to Sent failed (message WAS sent; only the Sent copy is missing)",
                exc_info=True,
            )
            return False
    return False
