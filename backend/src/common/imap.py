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
retry once with ``get_imap_credentials(refresh=True)``, and lets 6b satisfy acceptance #11 — a
wrong password must alarm, never look like "no new mail". The underlying library reports it as a
distinct :class:`~imapclient.exceptions.LoginError`, so the distinction rests on a type rather than
on which call happened to raise.

**But a rejected login is not always about the credentials.** WorkMail answers
``[UNAVAILABLE] Temporary authentication failure`` when it is busy or the per-user connection quota
is reached, and ``imapclient`` raises the same ``LoginError`` it raises for a bad password. Treating
the two alike alarmed on a healthy mailbox that recovered a minute later, and told the operator to
check for a rotated password — for a secret that does not rotate. The response code now decides
(:data:`TRANSIENT_LOGIN_CODES`): a transient one raises plain :class:`ImapError`, which the poller
already handles by skipping the cycle and letting the next minute retry, while anything else stays
:class:`ImapAuthError` and still alarms. **An unrecognised rejection counts as an auth failure** —
a false alarm is recoverable, a silenced one means inbound mail stops with nobody told.

**The `APPEND` is best-effort and time-boxed** (decision #2). It runs *after* SES has accepted the
message, so it can never un-send anything; the worst case must be a logged WARNING, not a failed
request or a stalled Lambda. Hence :data:`IMAP_TIMEOUT_S`, comfortably inside the API's 15s budget
even after a send has already consumed part of it.

**Why `imapclient` and not stdlib `imaplib`** (CODING-GUIDELINES §4 names it as this project's IMAP
package). 6a needed only ``APPEND``, for which stdlib is a one-liner, and this module was
originally written against ``imaplib`` with no justification recorded — a slip, not a trade-off.
6b's poller needs ``SELECT``'s ``UIDVALIDITY``/``UIDNEXT``, UID ``SEARCH``, UID ``FETCH`` and
``MOVE``, which is where the difference stops being cosmetic: ``imaplib.IMAP4.search`` returns
*sequence numbers* while ``uid('SEARCH', ...)`` returns UIDs, and confusing the two silently reads
the wrong message once anything is deleted. ``IMAPClient`` defaults to ``use_uid=True`` across
search/fetch/move, parses FETCH responses into typed values, and returns LIST as the
``(flags, delimiter, name)`` tuples this module used to reconstruct with a regex.

*Known limitation, accepted for this scale:* IMAP here is synchronous, so this is a blocking call
inside a request. That is a property of doing mailbox I/O on the request path, not of the library.
At any real volume the Sent-folder copy belongs on a queue with retries and a DLQ, leaving the
request path to finish as soon as the send is recorded. For one user sending a handful of emails a
day, a bounded blocking call is the simpler correct thing.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager

from imapclient import SEEN, IMAPClient
from imapclient.exceptions import IMAPClientError, LoginError

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

#: The SPECIAL-USE flag identifying the Sent folder, whatever it is called (RFC 6154).
SENT_FLAG = "\\Sent"

#: Conventional Sent-folder names, tried only when SPECIAL-USE discovery finds nothing.
_SENT_FALLBACK_NAMES = ("Sent Items", "Sent", "INBOX.Sent")


class ImapError(Exception):
    """An IMAP operation failed."""


class ImapAuthError(ImapError):
    """Authentication was rejected and the credentials are the likely cause.

    Distinct from :class:`ImapError` so a caller can retry once with refreshed credentials, and so
    monitoring can alarm on it specifically rather than on generic mailbox noise.

    **Not every rejected login lands here.** A rejection carrying a transient response code is
    raised as a plain :class:`ImapError` instead — see :data:`TRANSIENT_LOGIN_CODES`.
    """


#: IMAP response codes on a rejected login that mean "try again later", not "your credentials are
#: wrong" (RFC 5530). WorkMail returns ``[UNAVAILABLE] Temporary authentication failure`` under load
#: and when the per-user connection quota is reached — the same exception a bad password raises, and
#: until this existed, indistinguishable from one. That cost a false alarm on 2026-08-02 whose
#: advice was to check a rotated password, for a secret that does not rotate.
TRANSIENT_LOGIN_CODES = frozenset({"UNAVAILABLE", "SERVERBUG", "CONTACTADMIN", "INUSE", "LIMIT"})

#: The response code leading an IMAP status line: ``[UNAVAILABLE] Temporary authentication failure.
#: [2026-08-03 06:00:06]``. Letters only, so the trailing timestamp cannot be read as a code.
_RESPONSE_CODE = re.compile(r"\[([A-Za-z]+)\]")


def _is_transient_login_failure(exc: BaseException) -> bool:
    """Return whether a rejected login was the server's fault rather than the credentials'.

    **Defaults to False**, including when no response code is present. The two mistakes are not
    symmetric: calling a genuine auth failure "transient" silences the alarm and lets inbound mail
    stop with nobody told, while calling a transient an auth failure costs only a false alarm. When
    in doubt, alarm.
    """
    match = _RESPONSE_CODE.search(str(exc))
    return bool(match) and match.group(1).upper() in TRANSIENT_LOGIN_CODES


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


def _as_text(value: bytes | str) -> str:
    """Decode a LIST flag or delimiter, which the library returns as bytes while names are str.

    Comparing a ``bytes`` flag against ``"\\\\Sent"`` is always False, so SPECIAL-USE discovery
    would fall through to guessing a folder name — degraded silently apart from one WARNING.
    Normalizing here keeps that comparison honest.
    """
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _connect(host: str) -> IMAPClient:
    """Open a TLS IMAP connection. The seam tests monkeypatch instead of reaching the network."""
    return IMAPClient(host, port=IMAP_PORT, ssl=True, timeout=IMAP_TIMEOUT_S)


@contextmanager
def connection(*, refresh_credentials: bool = False) -> Iterator[IMAPClient]:
    """Yield a logged-in IMAP connection, always logging out afterwards.

    Parameters
    ----------
    refresh_credentials : bool, optional
        Bypass the cached secret when fetching credentials. Pass ``True`` on a retry after
        :class:`ImapAuthError`, so a rotated password recovers without waiting for the Lambda
        container to recycle.

    Yields
    ------
    IMAPClient
        An authenticated connection, with ``use_uid=True`` (the library default) so every
        subsequent search/fetch/move speaks UIDs rather than shifting sequence numbers.

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
    except (OSError, IMAPClientError) as exc:
        # Network-level failure: unreachable, TLS refused, or the socket timed out.
        logger.exception("IMAP connect failed host=%s", host)
        raise ImapError(f"could not connect to {host}") from exc

    try:
        conn.login(credentials.username, credentials.password)
    except LoginError as exc:
        # The password is never logged, and never included in the message.
        _logout_quietly(conn)
        if _is_transient_login_failure(exc):
            # Raised as a plain ImapError so the poller's transient branch takes it: log, skip this
            # cycle, let the next minute retry. Refreshing the secret cannot help a server-side
            # condition, and reconnecting immediately is worse if the cause is the connection quota.
            logger.warning(
                "IMAP login temporarily unavailable host=%s user=%s — server-side, not credentials",
                host,
                credentials.username,
            )
            raise ImapError(f"IMAP temporarily unavailable at {host}") from exc
        logger.error("IMAP login rejected host=%s user=%s", host, credentials.username)
        raise ImapAuthError(f"IMAP login rejected for {credentials.username}") from exc
    except (IMAPClientError, OSError) as exc:
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


def _logout_quietly(conn: IMAPClient) -> None:
    """Log out, tolerating an already-broken connection."""
    try:
        conn.logout()
    except Exception:  # noqa: BLE001 - teardown; the socket is already suspect
        logger.debug("Ignoring error during IMAP logout", exc_info=True)


def _list_folders(conn: IMAPClient) -> list[tuple[set[str], str, str]]:
    """Return ``(flags, delimiter, name)`` for every folder the server lists.

    Thin normalization over ``IMAPClient.list_folders``: flags and the delimiter arrive as bytes
    while names are already decoded from modified UTF-7, so this decodes the first two and keeps
    the rest of the module comparing like with like.
    """
    try:
        listing = conn.list_folders()
    except IMAPClientError as exc:
        raise ImapError(f"IMAP LIST failed: {exc}") from exc

    folders: list[tuple[set[str], str, str]] = []
    for flags, delimiter, name in listing:
        folders.append(
            (
                {_as_text(flag) for flag in flags or ()},
                _as_text(delimiter) if delimiter else "",
                _as_text(name).strip(),
            )
        )
    return folders


def folder_delimiter(conn: IMAPClient) -> str:
    """Return the server's hierarchy delimiter, defaulting to ``/`` when it reports none.

    WorkMail uses ``/``, but the delimiter is a server property — hardcoding it is how folder
    creation silently produces one folder literally named ``Speaker Tracker/Import`` instead of a
    nested pair.
    """
    for _flags, delimiter, _name in _list_folders(conn):
        if delimiter:
            return delimiter
    return "/"


def find_sent_folder(conn: IMAPClient) -> str:
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
    Verified against the live mailbox on 2026-07-26, where the folder is ``Sent Items`` and carries
    the flag — no folder named ``Sent`` exists, so the flag is doing real work here.
    """
    folders = _list_folders(conn)
    for flags, _delimiter, name in folders:
        if SENT_FLAG in flags:
            return name

    known = {name for _flags, _delim, name in folders}
    for candidate in _SENT_FALLBACK_NAMES:
        if candidate in known:
            logger.warning(
                "IMAP server advertised no \\Sent SPECIAL-USE flag; falling back to %r", candidate
            )
            return candidate

    raise ImapError("could not identify the Sent folder (no \\Sent flag and no known name)")


def append_to_sent(conn: IMAPClient, raw_message: bytes) -> str:
    """Append an already-sent message to the Sent folder and return the folder name.

    The message is flagged ``\\Seen`` — Donna sent it, so it is not unread mail.

    Parameters
    ----------
    conn : IMAPClient
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
    try:
        # The folder name is passed raw: IMAPClient applies quoting and modified-UTF-7 itself, so
        # pre-quoting it here would create a folder whose name contains literal quote characters.
        conn.append(folder, raw_message, flags=[SEEN])
    except IMAPClientError as exc:
        raise ImapError(f"APPEND to {folder!r} failed: {exc}") from exc

    logger.info(
        "IMAP APPEND ok folder=%s bytes=%d duration_ms=%d",
        folder,
        len(raw_message),
        int((time.monotonic() - started) * 1000),
    )
    return folder


def ensure_folder(conn: IMAPClient, path: tuple[str, ...]) -> str:
    """Create and subscribe a folder if absent; return its server-delimited name.

    Idempotent in both directions: an existing folder is left alone, and a *deleted* one is
    recreated on the next call (acceptance #5). SUBSCRIBE is what makes it appear in Outlook
    without Donna manually subscribing (acceptance #4) — creation alone is not enough.

    Parameters
    ----------
    conn : IMAPClient
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
        try:
            conn.create_folder(name)
        except IMAPClientError as exc:
            # Some servers answer NO for "already exists"; only a still-absent folder is an error.
            recheck = {n for _f, _d, n in _list_folders(conn)}
            if name not in recheck:
                raise ImapError(f"CREATE {name!r} failed: {exc}") from exc
            logger.warning("IMAP CREATE %r reported an error but the folder exists: %s", name, exc)
        else:
            logger.info("IMAP created folder=%s", name)

    try:
        conn.subscribe_folder(name)
    except IMAPClientError as exc:
        raise ImapError(f"SUBSCRIBE {name!r} failed: {exc}") from exc
    return name


def ensure_app_folders(conn: IMAPClient) -> tuple[str, str]:
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
