"""Where a poll should start reading an IMAP folder — pure domain logic, no I/O.

Mishandling ``UIDVALIDITY`` is the classic IMAP-poller bug, and it fails in both directions: trust
a stale cursor and mail is skipped forever; discard it and the whole folder is re-read. DEV-PLAN
slice 6b acceptance #6 tests it explicitly, and DATABASE.md §"imap_folder_cursors" spells out why
the column is not optional bookkeeping — an IMAP UID is only meaningful within the ``UIDVALIDITY``
generation it was issued under.

There is a second, quieter hazard this module handles: the **first** poll of a folder. A cursor
starting at UID 0 would ingest Donna's entire mailbox, which is years of personal mail and a direct
breach of the never-the-whole-mailbox guarantee in :mod:`core.email_scope`. First polls therefore
baseline rather than read.

Kept apart from :mod:`core.email_threading` and :mod:`core.email_scope` because it decides nothing
about mail — only about UIDs — and is the one part of the poller with no notion of a message.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple

#: Most UIDs one invocation will process. A normal poll sees zero or one message; this cap only
#: bites after a ``UIDVALIDITY`` reset, where it lets a large rescan drain over several one-minute
#: polls instead of timing out a single invocation and making no progress at all.
MAX_UIDS_PER_POLL = 200

CURSOR_FIRST_POLL = "first_poll_baseline"
CURSOR_UIDVALIDITY_CHANGED = "uidvalidity_changed"
CURSOR_RESUME = "resume"


class CursorPlan(NamedTuple):
    """Where this poll should start reading a folder.

    Attributes
    ----------
    floor_uid : int
        Process UIDs **strictly greater** than this.
    baseline : bool
        ``True`` when this is the folder's first poll and the plan is deliberately to ingest
        nothing, only to record where "new" begins. The caller still writes the cursor row.
    reason : str
        :data:`CURSOR_FIRST_POLL`, :data:`CURSOR_UIDVALIDITY_CHANGED`, or :data:`CURSOR_RESUME` —
        logged, so a folder that silently stops producing mail can be diagnosed from the log alone.
    """

    floor_uid: int
    baseline: bool
    reason: str


class PollSummary(NamedTuple):
    """What one folder's poll did — the handler's return value and its exit-log payload.

    Deliberately a ``NamedTuple`` and not a Pydantic model: this is an internal value, never a
    request or a response, and ``models/`` is where the project keeps wire contracts. It is the
    same kind of thing as :class:`CursorPlan` and :class:`core.email_scope.IngestDecision`.

    The counters are not decoration. A poller that quietly does nothing is the failure mode this
    slice is most exposed to, and these are what make "nothing happened" distinguishable from
    "nothing was there" in CloudWatch, without attaching a debugger to a Lambda that runs once a
    minute.

    Attributes
    ----------
    folder : str
        Folder polled, as named on the server (``INBOX``, ``Sent Items``, ``Import``) — the live
        Sent folder is *not* called ``Sent``, so this records what was actually opened.
    reason : str
        The :class:`CursorPlan` reason this poll ran under.
    floor_uid : int
        The cursor this poll started above.
    examined : int
        UIDs actually processed, after :func:`cap_uids`.
    remaining : int
        UIDs above the floor that the cap deferred to the next poll. Non-zero means a rescan is
        draining, which must never be inferable only from the absence of activity.
    ingested : int
        New ``email_messages`` rows written.
    duplicates : int
        Messages whose ``Message-ID`` was already stored — ``UNIQUE(user_id, message_id)`` doing
        its job. Expected to be non-zero on a rescan and near zero otherwise.
    skipped : int
        Messages classified as not ours and left alone — the never-the-whole-mailbox guarantee,
        counted so it can be seen working.
    moved : int
        Messages moved out of ``Import`` into ``Processed``.
    last_seen_uid : int
        Watermark written back to ``imap_folder_cursors``. Equals `floor_uid` when nothing was
        processed.
    """

    folder: str
    reason: str
    floor_uid: int
    examined: int
    remaining: int
    ingested: int
    duplicates: int
    skipped: int
    moved: int
    last_seen_uid: int


def plan_cursor(
    *,
    stored_uid_validity: int | None,
    stored_last_seen_uid: int | None,
    server_uid_validity: int,
    server_uid_next: int | None,
) -> CursorPlan:
    """Decide where to resume reading a folder, honouring ``UIDVALIDITY`` (acceptance #6).

    Three cases, of which the first two are the ones that go wrong in the wild:

    **No stored cursor — baseline, ingest nothing.** The cursor is planted at the current
    ``UIDNEXT`` so that "new" means "arrived after the app started watching". The cost is a reply
    that landed in the seconds before the first poll; the alternative is importing years of
    unrelated mail on deploy.

    **``UIDVALIDITY`` changed — reset to 0 and rescan.** The stored UIDs now name different
    messages, or none, so they cannot be trusted. Rescanning does not re-import anything:
    ``UNIQUE(user_id, message_id)`` makes ingest idempotent, so already-known messages collide and
    are dropped. That is how acceptance #6's "resets the cursor rather than skipping *or*
    re-importing" is satisfied without having to choose between the two failure modes.
    :data:`MAX_UIDS_PER_POLL` keeps the rescan bounded per invocation.

    **Unchanged — resume from the watermark.** The ordinary path. Most polls then find zero UIDs,
    which is what makes a one-minute interval cost about a dollar a month.

    Parameters
    ----------
    stored_uid_validity : int or None
        ``imap_folder_cursors.uid_validity``; ``None`` when the folder has never been polled.
    stored_last_seen_uid : int or None
        ``imap_folder_cursors.last_seen_uid``. ``None`` is treated as 0.
    server_uid_validity : int
        ``UIDVALIDITY`` reported by the server on ``SELECT``.
    server_uid_next : int or None
        ``UIDNEXT`` reported on ``SELECT``. Treated as 1 — an empty folder — when absent, so a
        server that omits it baselines to 0 and reads the folder from the start rather than
        crashing the poll. Only reachable on a first poll.

    Returns
    -------
    CursorPlan
        Process UIDs strictly above ``floor_uid``.

    Examples
    --------
    >>> plan_cursor(stored_uid_validity=None, stored_last_seen_uid=None,
    ...             server_uid_validity=42, server_uid_next=901)
    CursorPlan(floor_uid=900, baseline=True, reason='first_poll_baseline')
    >>> plan_cursor(stored_uid_validity=42, stored_last_seen_uid=900,
    ...             server_uid_validity=43, server_uid_next=901)
    CursorPlan(floor_uid=0, baseline=False, reason='uidvalidity_changed')
    >>> plan_cursor(stored_uid_validity=42, stored_last_seen_uid=900,
    ...             server_uid_validity=42, server_uid_next=901)
    CursorPlan(floor_uid=900, baseline=False, reason='resume')
    """
    if stored_uid_validity is None:
        uid_next = server_uid_next if server_uid_next else 1
        return CursorPlan(max(uid_next - 1, 0), True, CURSOR_FIRST_POLL)

    if stored_uid_validity != server_uid_validity:
        return CursorPlan(0, False, CURSOR_UIDVALIDITY_CHANGED)

    return CursorPlan(max(stored_last_seen_uid or 0, 0), False, CURSOR_RESUME)


def cap_uids(uids: Iterable[int], floor_uid: int, *, cap: int = MAX_UIDS_PER_POLL) -> list[int]:
    """Select the UIDs this poll will process: above the floor, ascending, at most `cap`.

    Ascending order is load-bearing. The poller advances the cursor to the last UID it
    *successfully* processed, so a mid-batch failure leaves the remainder for the next poll; out of
    order, a failure would strand messages below a watermark that had already jumped past them.

    Parameters
    ----------
    uids : iterable of int
        UIDs reported by the server's search, in any order.
    floor_uid : int
        From :func:`plan_cursor`. UIDs at or below it are already handled.
    cap : int, optional
        Override for :data:`MAX_UIDS_PER_POLL`.

    Returns
    -------
    list of int
        Ascending, strictly above `floor_uid`, truncated to `cap`.

    Examples
    --------
    >>> cap_uids([903, 901, 900, 902], 900, cap=2)
    [901, 902]
    >>> cap_uids([1, 2, 3], 3)
    []
    """
    return sorted(uid for uid in uids if uid > floor_uid)[:cap]
