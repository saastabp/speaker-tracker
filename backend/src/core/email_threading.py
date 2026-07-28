"""Which thread does an inbound message belong to — pure domain logic, no I/O.

Two strategies, tried in order, for the question the IMAP poller asks about every message it
ingests (DEV-PLAN slice 6b acceptance #1 and #10):

- **The RFC 5322 header chain** — ``In-Reply-To`` and ``References`` resolved against Message-IDs
  we have stored. This is the only strategy DESIGN.md §3 relies on for correctness; Microsoft's
  proprietary ``Thread-Index`` is deliberately not used, since external senders do not set it.
- **A guarded ``From`` + subject + time-window fallback**, for clients that drop or mangle
  ``References``. It is guarded because the failure modes are asymmetric: starting a redundant
  second thread is a cosmetic annoyance, while merging two venues' conversations into one puts a
  stranger's mail on a contact's timeline.

Thread identity is assigned **once, at ingest** (DATABASE.md §"email_threads") rather than
re-derived per read, which is why this module runs in the poller and not behind the thread view.

The scope decision — whether a message should be ingested at all — is
:mod:`core.email_scope`; poll watermarks are :mod:`core.imap_cursor`. Address parsing lives in
:mod:`core.email_headers` alongside the other header helpers.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection, Mapping, Sequence
from typing import NamedTuple

from core.email_headers import normalize_subject, parse_message_ids

#: How far apart two messages may be and still be joined by the broken-``References`` fallback.
#: Long enough to cover a venue that goes quiet for a few weeks mid-negotiation, short enough that
#: next year's "Speaking inquiry" starts a new thread instead of resurrecting a dead one.
FALLBACK_WINDOW_DAYS = 30

# Reason codes. Constants rather than inline literals so the poller's log lines, the tests, and
# these functions cannot drift apart.
MATCH_HEADER_CHAIN = "header_chain"
MATCH_SUBJECT_FALLBACK = "subject_fallback"
MATCH_NEW_THREAD = "new_thread"


class ThreadCandidate(NamedTuple):
    """An existing thread the fallback matcher may join a message to.

    Attributes
    ----------
    thread_id : int
        ``email_threads.id``.
    subject_normalized : str
        The thread's stored grouping key, already ``Re:``/``Fwd:``-stripped.
    counterpart_addresses : tuple of str
        Addresses identifying the *other* party on the thread — the linked contact's email, and/or
        the addresses seen on its messages. The repository decides what to supply; this module only
        intersects sets, so a thread with no contact yet (an unimported one) can still match on the
        addresses its messages carry.
    last_message_at : datetime or None
        When the thread last moved. ``None`` — a thread whose only message is an unsent pending
        send — never matches, because there is no anchor for the time window.
    """

    thread_id: int
    subject_normalized: str
    counterpart_addresses: tuple[str, ...]
    last_message_at: dt.datetime | None


class ThreadMatch(NamedTuple):
    """Which thread a message joins, and on what evidence.

    Attributes
    ----------
    thread_id : int or None
        The thread to attach to, or ``None`` when the message starts a new one.
    reason : str
        :data:`MATCH_HEADER_CHAIN`, :data:`MATCH_SUBJECT_FALLBACK`, or :data:`MATCH_NEW_THREAD`.
        Carried into the poller's log line so a mis-threaded message can be diagnosed after the
        fact without re-fetching it from the mailbox.
    """

    thread_id: int | None
    reason: str


def candidate_ancestors(in_reply_to: str | None, references: str | None) -> list[str]:
    """List the msg-ids to try when threading a reply, nearest ancestor first.

    ``In-Reply-To`` names the immediate parent, so it is tried first. ``References`` is then walked
    **backwards**: it accumulates root-first, so its tail is the closest ancestor and its head is
    the thread root. Walking it forwards would still find a thread, but the wrong one once a chain
    has been forwarded between conversations — the root would win over the actual parent.

    Parameters
    ----------
    in_reply_to : str or None
        The message's ``In-Reply-To`` header value.
    references : str or None
        The message's ``References`` header value.

    Returns
    -------
    list of str
        Bracketed msg-ids, nearest first, deduplicated.

    Examples
    --------
    >>> candidate_ancestors('<c@x.com>', '<a@x.com> <b@x.com> <c@x.com>')
    ['<c@x.com>', '<b@x.com>', '<a@x.com>']
    >>> candidate_ancestors(None, None)
    []
    """
    ordered = parse_message_ids(in_reply_to) + list(reversed(parse_message_ids(references)))
    seen: set[str] = set()
    result: list[str] = []
    for message_id in ordered:
        if message_id not in seen:
            seen.add(message_id)
            result.append(message_id)
    return result


def match_by_headers(
    in_reply_to: str | None,
    references: str | None,
    thread_by_message_id: Mapping[str, int],
) -> int | None:
    """Find the thread a reply belongs to from its header chain (acceptance #1).

    Parameters
    ----------
    in_reply_to : str or None
        The message's ``In-Reply-To`` header value.
    references : str or None
        The message's ``References`` header value.
    thread_by_message_id : mapping of str to int
        Bracketed ``email_messages.message_id`` to ``thread_id``. The repository resolves this in
        one query over the ids in the chain; ids we never stored are simply absent.

    Returns
    -------
    int or None
        The nearest matching ancestor's thread, or ``None`` when no id in the chain is ours.
    """
    for message_id in candidate_ancestors(in_reply_to, references):
        thread_id = thread_by_message_id.get(message_id)
        if thread_id is not None:
            return thread_id
    return None


def as_naive_utc(value: dt.datetime) -> dt.datetime:
    """Drop tzinfo, converting to UTC first, so DB and header timestamps are comparable.

    MySQL timestamps come back naive; a parsed ``Date`` header is aware. Subtracting one from the
    other raises ``TypeError`` — a crash the poller would only ever hit in production, on a real
    reply from a real venue.

    The repository layer needs this too, and for a quieter reason: pymysql formats a ``datetime``
    without consulting ``tzinfo``, so binding an aware value stores its *local* wall time with the
    offset discarded. That is not a crash but a silent hours-off timestamp, so every aware value is
    converted here before it can reach a query.

    Parameters
    ----------
    value : datetime
        Naive or aware.

    Returns
    -------
    datetime
        Naive, in UTC. A naive input is returned unchanged — it is already assumed to be UTC.

    Examples
    --------
    >>> as_naive_utc(dt.datetime(2026, 7, 27, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=-4))))
    datetime.datetime(2026, 7, 27, 16, 0)
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(dt.UTC).replace(tzinfo=None)


def match_by_subject(
    subject: str | None,
    counterpart_addresses: Collection[str],
    occurred_at: dt.datetime,
    candidates: Sequence[ThreadCandidate],
    *,
    window_days: int = FALLBACK_WINDOW_DAYS,
) -> int | None:
    """Find a thread for a message whose ``References`` chain is missing or broken (acceptance #10).

    Requires **all three** of: an identical normalized subject, a shared counterpart address, and a
    gap within `window_days`. Subject alone is not enough — two venues both writing "Speaking
    inquiry" would be merged into one conversation. An empty normalized subject never matches,
    since every blank subject normalizes alike.

    Parameters
    ----------
    subject : str or None
        The message's raw ``Subject``; normalized here, so callers pass it through unmodified.
    counterpart_addresses : collection of str
        Addresses of the other party on *this* message.
    occurred_at : datetime
        When the message was sent or received. Naive and aware values are both accepted.
    candidates : sequence of ThreadCandidate
        Threads worth considering, supplied by the repository.
    window_days : int, optional
        Override for :data:`FALLBACK_WINDOW_DAYS`.

    Returns
    -------
    int or None
        The most recently active qualifying thread, or ``None``. Most-recent wins so a revived
        annual conversation joins the latest instance rather than the oldest.
    """
    key = normalize_subject(subject)
    if not key:
        return None
    counterparts = {address.strip().lower() for address in counterpart_addresses if address}
    if not counterparts:
        return None

    window = dt.timedelta(days=window_days)
    anchor = as_naive_utc(occurred_at)
    best_thread_id: int | None = None
    best_last: dt.datetime | None = None
    for candidate in candidates:
        if candidate.subject_normalized != key or candidate.last_message_at is None:
            continue
        others = {address.strip().lower() for address in candidate.counterpart_addresses if address}
        if counterparts.isdisjoint(others):
            continue
        last = as_naive_utc(candidate.last_message_at)
        if abs(anchor - last) > window:
            continue
        if best_last is None or last > best_last:
            best_thread_id, best_last = candidate.thread_id, last
    return best_thread_id


def resolve_thread(
    *,
    in_reply_to: str | None,
    references: str | None,
    subject: str | None,
    counterpart_addresses: Collection[str],
    occurred_at: dt.datetime,
    thread_by_message_id: Mapping[str, int],
    candidates: Sequence[ThreadCandidate] = (),
    window_days: int = FALLBACK_WINDOW_DAYS,
) -> ThreadMatch:
    """Decide which thread a polled message joins: header chain, then fallback, then a new thread.

    Parameters
    ----------
    in_reply_to, references : str or None
        The message's threading headers.
    subject : str or None
        The message's raw ``Subject``.
    counterpart_addresses : collection of str
        Addresses of the other party on this message.
    occurred_at : datetime
        When the message was sent or received.
    thread_by_message_id : mapping of str to int
        Stored ``message_id`` to ``thread_id``, covering the ids in this message's chain.
    candidates : sequence of ThreadCandidate, optional
        Threads eligible for the subject fallback. Empty — the default — disables the fallback, so
        a caller that has not looked any up gets header matching only rather than a silent miss.
    window_days : int, optional
        Override for :data:`FALLBACK_WINDOW_DAYS`.

    Returns
    -------
    ThreadMatch
        ``thread_id`` is ``None`` only when both strategies miss.
    """
    thread_id = match_by_headers(in_reply_to, references, thread_by_message_id)
    if thread_id is not None:
        return ThreadMatch(thread_id, MATCH_HEADER_CHAIN)

    thread_id = match_by_subject(
        subject, counterpart_addresses, occurred_at, candidates, window_days=window_days
    )
    if thread_id is not None:
        return ThreadMatch(thread_id, MATCH_SUBJECT_FALLBACK)

    return ThreadMatch(None, MATCH_NEW_THREAD)
