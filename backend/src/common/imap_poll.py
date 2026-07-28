"""Message-level IMAP operations the poller needs — select, search, fetch, move.

Split from :mod:`common.imap`, which holds what the *send* path and the poller share: connection
and authentication, folder topology, and the Sent-folder ``APPEND``. Everything here reads or
relocates individual messages, which only the poller does. Keeping them apart also keeps
``common/imap.py`` inside the size guideline it would otherwise have blown through.

**What the search is for, and what it deliberately is not.** ``UID SEARCH`` here asks the server
exactly one question: *which messages arrived after the cursor*. It never filters by sender, even
though IMAP would allow it. The mail server has no idea who Donna's contacts are — that lives in
MySQL — and two of the three ways a message qualifies cannot be expressed server-side at all: a
reply qualifies because its ``In-Reply-To`` names a ``Message-ID`` *we* stored, and a dragged
message qualifies because of the folder it is in. So the server answers "what is new" and
``core.email_scope.classify_message`` answers "is it ours". Most fetched messages are discarded
without a row ever being written, which is how the never-ingest-the-whole-mailbox guarantee is
kept: by what gets stored, not by what gets looked at.

Four protocol details this module exists to get right, each of which fails quietly rather than
loudly if it is missed:

- **The search range is bounded by a number, never by ``*``.** ``*`` means "the highest UID in
  use", and IMAP normalizes a backwards range, so ``UID 901:*`` on a folder topping out at 900
  becomes ``901:900`` — the same set as ``900:901`` — and returns UID 900, the message we just
  processed. On a quiet folder, which is most folders most minutes, that would happen on every
  poll: a wasted fetch a minute forever, and ``PollSummary.duplicates`` permanently non-zero,
  destroying the one signal that is supposed to mean "a rescan is under way". Bounding with
  :data:`MAX_UID` removes the quirk instead of compensating for it — the range is then well-formed
  and genuinely empty.
- **Fetching uses ``BODY.PEEK[]``, never ``BODY[]`` or ``RFC822``.** The latter two set ``\\Seen``,
  so a background poll would mark Donna's unread mail as read in Outlook — the app reaching into a
  mailbox it is a guest in. The response key comes back as ``b'BODY[]'`` regardless of which was
  requested, which is the part that misleads.
- **``MOVE`` is not universal, and the library does not paper over it.** ``IMAPClient.move`` is
  decorated ``@require_capability("MOVE")`` and raises rather than degrading, so
  :func:`move_uids` implements the RFC 3501 fallback — ``COPY``, flag ``\\Deleted``, expunge.
- **Expunging by UID needs UIDPLUS.** A plain ``EXPUNGE`` removes *every* ``\\Deleted`` message in
  the folder, including ones Outlook flagged and has not yet purged. The fallback checks the
  capability and warns rather than reaching for the blunt instrument silently.

.. warning::
   **The test double for this module must reproduce the ``*`` behaviour**, not the convenient
   behaviour. A fake whose ``search`` simply returns the UIDs above the floor passes whether or not
   the range is bounded correctly, so it would keep passing if someone "simplified"
   :data:`MAX_UID` back to ``*``. The fake must answer a ``*``-terminated range with the boundary
   UID included, and a numeric range with a true empty set. That test is the thing that enforces
   this going forward; the paragraph above is not.

Times arrive already normalized: ``IMAPClient`` defaults to ``normalise_times=True``, so
``INTERNALDATE`` is a **naive UTC** ``datetime``. The ``Date`` header parsed in :mod:`common.mail`
is timezone-aware instead; both are reconciled in ``repositories.email_inbound``, which is the
layer allowed to know about ``core``.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import NamedTuple

from imapclient import DELETED, IMAPClient
from imapclient.exceptions import CapabilityError, IMAPClientError

from common.imap import ImapError
from common.logger import logger

#: Largest UID RFC 3501 permits — UIDs are unsigned 32-bit. Used as the search range's upper bound
#: instead of ``*`` so no real message can fall outside it and no range normalization can occur.
MAX_UID = 4294967295

#: Response keys, as bytes, from ``SELECT``/``EXAMINE`` and ``FETCH``.
_UIDVALIDITY_KEY = b"UIDVALIDITY"
_UIDNEXT_KEY = b"UIDNEXT"
_EXISTS_KEY = b"EXISTS"
_BODY_KEY = b"BODY[]"
_INTERNALDATE_KEY = b"INTERNALDATE"

#: What to ask for per message. ``BODY.PEEK[]`` is the whole RFC 5322 message *without* setting
#: ``\Seen``; the server answers under the key ``BODY[]`` either way.
_FETCH_ITEMS = ["BODY.PEEK[]", "INTERNALDATE"]


class FolderStatus(NamedTuple):
    """What ``SELECT``/``EXAMINE`` reports about a folder.

    Attributes
    ----------
    uid_validity : int
        The folder's UID generation. A change means every stored UID now names a different message
        or none at all, which is what ``core.imap_cursor.plan_cursor`` resets on.
    uid_next : int or None
        The UID the *next* arriving message will get, so ``uid_next - 1`` is where a first poll
        plants its baseline. ``None`` when the server omits it — rare, and handled by
        ``plan_cursor`` rather than guessed at here.
    message_count : int
        ``EXISTS``. Logged only; the poller works in UIDs, never in message counts.
    """

    uid_validity: int
    uid_next: int | None
    message_count: int


class FetchedMessage(NamedTuple):
    """One message as read from the server.

    Attributes
    ----------
    uid : int
        Its UID within the folder's current ``UIDVALIDITY`` generation.
    raw : bytes
        The complete RFC 5322 message, exactly as stored on the server — the same bytes that go to
        S3 and get parsed for headers and body.
    internaldate : datetime or None
        The server's arrival timestamp, **naive UTC** (``IMAPClient`` normalizes it). Used when a
        message carries no parseable ``Date`` header, so it still gets an honest timestamp rather
        than the moment the poller happened to run.
    """

    uid: int
    raw: bytes
    internaldate: dt.datetime | None


def select_folder(conn: IMAPClient, folder: str, *, readonly: bool = True) -> FolderStatus:
    """Open a folder and report its UID generation.

    ``readonly`` defaults to ``True`` — an ``EXAMINE`` rather than a ``SELECT``. INBOX and Sent are
    only ever read, and opening them writable invites a stray flag change on a mailbox the app does
    not own. The Import folder is the exception: messages are moved out of it, so its caller passes
    ``readonly=False``.

    Parameters
    ----------
    conn : IMAPClient
        A logged-in connection.
    folder : str
        Folder name as the server reports it, **unquoted** — ``IMAPClient`` applies quoting and
        modified UTF-7 itself, so pre-quoting selects a folder whose name contains literal quotes.
    readonly : bool, optional
        ``True`` (default) issues ``EXAMINE``; ``False`` issues ``SELECT``.

    Returns
    -------
    FolderStatus
        ``UIDVALIDITY``, ``UIDNEXT`` (possibly ``None``), and the message count.

    Raises
    ------
    ImapError
        When the folder cannot be selected, or when the server reports no ``UIDVALIDITY`` — without
        it no cursor can be trusted, and continuing would risk skipping mail rather than merely
        failing this poll.
    """
    try:
        response = conn.select_folder(folder, readonly=readonly)
    except IMAPClientError as exc:
        raise ImapError(f"could not select folder {folder!r}: {exc}") from exc

    uid_validity = response.get(_UIDVALIDITY_KEY)
    if uid_validity is None:
        raise ImapError(f"folder {folder!r} reported no UIDVALIDITY; refusing to poll it blind")

    status = FolderStatus(
        uid_validity=int(uid_validity),
        uid_next=int(response[_UIDNEXT_KEY]) if response.get(_UIDNEXT_KEY) else None,
        message_count=int(response.get(_EXISTS_KEY) or 0),
    )
    logger.info(
        "IMAP selected folder=%s readonly=%s uidvalidity=%s uidnext=%s exists=%d",
        folder,
        readonly,
        status.uid_validity,
        status.uid_next,
        status.message_count,
    )
    return status


def search_uids_above(conn: IMAPClient, floor_uid: int) -> list[int]:
    """Return UIDs strictly greater than `floor_uid` in the selected folder.

    Asks the server one question — what arrived after the cursor — with a **numeric** upper bound
    rather than ``*``. See the module docstring for why that distinction is the difference between
    an empty answer and a permanent phantom result on every quiet poll.

    The ``uid > floor_uid`` comparison below is kept as an invariant guard, not as the fix: with
    :data:`MAX_UID` bounding the range the server cannot return a boundary UID, and if that ever
    changes this turns a silent behaviour change into a visibly empty list.

    Parameters
    ----------
    conn : IMAPClient
        A logged-in connection with a folder already selected.
    floor_uid : int
        From ``core.imap_cursor.plan_cursor``. UIDs at or below it are already handled.

    Returns
    -------
    list of int
        Ascending UIDs above `floor_uid`; empty on a folder with nothing new, which is the
        overwhelmingly common answer.

    Raises
    ------
    ImapError
        When the search fails.
    """
    criteria = ["UID", f"{floor_uid + 1}:{MAX_UID}"]
    try:
        found = conn.search(criteria)
    except IMAPClientError as exc:
        raise ImapError(f"UID SEARCH above {floor_uid} failed: {exc}") from exc

    uids = sorted(int(uid) for uid in found if int(uid) > floor_uid)
    if len(uids) != len(found):
        # Unreachable with a numeric bound. If it fires, the range syntax has regressed to `*`.
        logger.warning(
            "UID SEARCH %s returned %d UID(s) at or below the floor %d; range bounding has "
            "regressed and duplicates would be re-fetched every poll",
            criteria,
            len(found) - len(uids),
            floor_uid,
        )
    return uids


def fetch_messages(conn: IMAPClient, uids: list[int]) -> list[FetchedMessage]:
    """Fetch whole messages by UID, without marking any of them read.

    Parameters
    ----------
    conn : IMAPClient
        A logged-in connection with a folder already selected.
    uids : list of int
        UIDs to fetch, already capped by ``core.imap_cursor.cap_uids``. Empty returns ``[]``
        without a round trip.

    Returns
    -------
    list of FetchedMessage
        In ascending UID order — the order the poller must process them in, so a mid-batch failure
        leaves a watermark that has not jumped past unprocessed mail. A UID the server no longer
        has (deleted between the search and the fetch) is skipped with a WARNING rather than
        failing the batch, since one vanished message must not stop the rest being ingested.

    Raises
    ------
    ImapError
        When the fetch itself fails.
    """
    if not uids:
        return []

    started = time.monotonic()
    try:
        response = conn.fetch(uids, _FETCH_ITEMS)
    except IMAPClientError as exc:
        raise ImapError(f"UID FETCH of {len(uids)} message(s) failed: {exc}") from exc

    messages: list[FetchedMessage] = []
    for uid in sorted(uids):
        data = response.get(uid)
        if data is None:
            logger.warning("UID %d disappeared between SEARCH and FETCH; skipping it", uid)
            continue
        raw = data.get(_BODY_KEY)
        if raw is None:
            # A response missing the body is not something to guess around: storing an empty
            # message would create a row whose MIME can never be parsed.
            logger.warning("UID %d returned no body part; skipping it (keys=%s)", uid, list(data))
            continue
        messages.append(FetchedMessage(uid=uid, raw=raw, internaldate=data.get(_INTERNALDATE_KEY)))

    logger.info(
        "IMAP fetched requested=%d returned=%d bytes=%d duration_ms=%d",
        len(uids),
        len(messages),
        sum(len(m.raw) for m in messages),
        int((time.monotonic() - started) * 1000),
    )
    return messages


def move_uids(conn: IMAPClient, uids: list[int], destination: str) -> int:
    """Move messages to another folder, falling back when the server lacks ``MOVE``.

    RFC 6851 ``MOVE`` is not universal, and ``IMAPClient.move`` is decorated
    ``@require_capability("MOVE")`` — it raises :class:`~imapclient.exceptions.CapabilityError`
    rather than degrading, so the fallback has to exist here. The fallback is the RFC 3501
    sequence: ``COPY`` to the destination, flag the originals ``\\Deleted``, then expunge.

    Parameters
    ----------
    conn : IMAPClient
        A logged-in connection with the **source** folder selected writable
        (``select_folder(..., readonly=False)``); a read-only selection cannot flag or expunge.
    uids : list of int
        UIDs to move. Empty returns 0 without a round trip.
    destination : str
        Target folder, unquoted.

    Returns
    -------
    int
        How many UIDs were handed to the server. This counts what was requested, not what the
        server confirms — IMAP offers no per-message acknowledgement for either path.

    Raises
    ------
    ImapError
        When both the move and the copy fail. A failed move leaves the message where it was, so the
        next poll sees it again; that is preferable to a message in neither folder.
    """
    if not uids:
        return 0

    started = time.monotonic()
    try:
        conn.move(uids, destination)
    except CapabilityError:
        logger.warning(
            "IMAP server does not advertise MOVE; falling back to COPY + \\Deleted + EXPUNGE "
            "for %d message(s) to %s",
            len(uids),
            destination,
        )
        _copy_then_delete(conn, uids, destination)
    except IMAPClientError as exc:
        raise ImapError(f"MOVE of {len(uids)} message(s) to {destination!r} failed: {exc}") from exc

    logger.info(
        "IMAP moved count=%d destination=%s duration_ms=%d",
        len(uids),
        destination,
        int((time.monotonic() - started) * 1000),
    )
    return len(uids)


def _copy_then_delete(conn: IMAPClient, uids: list[int], destination: str) -> None:
    """The RFC 3501 stand-in for ``MOVE``: copy, flag ``\\Deleted``, then expunge by UID.

    Ordering matters and is not interchangeable. The ``COPY`` happens first so a failure at any
    later step leaves the message in *both* folders rather than in neither — a duplicate the next
    poll drops on ``Message-ID``, instead of mail that no longer exists anywhere.

    Expunging is the delicate half. ``expunge(uids)`` is a UID ``EXPUNGE`` and needs UIDPLUS; a
    plain ``EXPUNGE`` removes **every** ``\\Deleted`` message in the folder, which may include mail
    Outlook flagged and has not yet purged. Without UIDPLUS the originals are left flagged and a
    WARNING is logged: the copy has landed, so the move has effectively happened, and the next
    client to expunge will finish the job.
    """
    try:
        conn.copy(uids, destination)
    except IMAPClientError as exc:
        raise ImapError(f"COPY of {len(uids)} message(s) to {destination!r} failed: {exc}") from exc

    try:
        conn.add_flags(uids, [DELETED])
    except IMAPClientError as exc:
        # The copy landed, so the message has effectively moved; failing to flag the original only
        # means the next poll sees it again and dedupes it.
        logger.warning(
            "Copied %d message(s) to %s but could not flag the originals \\Deleted: %s",
            len(uids),
            destination,
            exc,
        )
        return

    try:
        conn.expunge(uids)
    except CapabilityError:
        logger.warning(
            "Server lacks UIDPLUS, so %d message(s) are flagged \\Deleted but not expunged. "
            "A plain EXPUNGE would purge every \\Deleted message in the folder, including mail "
            "this app did not flag, so it is deliberately not attempted.",
            len(uids),
        )
    except IMAPClientError as exc:
        logger.warning("UID EXPUNGE of %d message(s) failed: %s", len(uids), exc)
