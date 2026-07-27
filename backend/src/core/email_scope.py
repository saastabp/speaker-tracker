"""Whether a polled message enters the app at all — pure domain logic, no I/O.

This module is the enforcement point for the guarantee in DESIGN.md §3 that Speaker Tracker
**never ingests the whole mailbox**. Donna reads a real WorkMail account through Outlook: it holds
years of personal and unrelated mail, and the app is a peer IMAP client on that mailbox, not an
owner of it. Only three things are in scope (DEV-PLAN slice 6b acceptance #2):

- a message that arrived in the **import folder**, because dragging it there is Donna's explicit,
  per-message authorization;
- a message whose header chain **already matched one of our threads**, because the conversation is
  ours regardless of who else joined it;
- correspondence with a **tracked contact** — the sender inbound, or a recipient outbound.

Anything else is skipped: no row, no S3 object, nothing on any timeline. The acceptance criterion
is verified by emailing the mailbox from a personal address and confirming the app stays empty.

Two things this module never does. It never creates an ``outreaches`` row — receiving email must
not move a target (acceptance #8) — and it never infers an ``opportunity_id``. A contact having
exactly one open gig is not evidence that a given email concerns it, and quietly filing
side-channel mail against the wrong gig is worse than leaving a thread unlinked; threads reach an
opportunity by inheriting one through a header match, or by Donna linking them by hand.

Thread matching is :mod:`core.email_threading`; poll watermarks are :mod:`core.imap_cursor`.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import Literal, NamedTuple

from core.email_headers import addresses_in, normalize_address

#: Which polled folder a message came out of. ``processed`` is deliberately absent: it is the
#: destination of the post-import move, never a source (DESIGN.md §3), so polling it would re-read
#: every message the app has already handled.
FolderKind = Literal["inbox", "sent", "import"]

FOLDER_INBOX: FolderKind = "inbox"
FOLDER_SENT: FolderKind = "sent"
FOLDER_IMPORT: FolderKind = "import"

#: Matches the ``email_messages.direction`` ENUM.
Direction = Literal["in", "out"]

DIRECTION_IN: Direction = "in"
DIRECTION_OUT: Direction = "out"

# Reason codes, for the poller's log line and the tests.
INGEST_IMPORT_FOLDER = "import_folder"
INGEST_TRACKED_CONTACT = "tracked_contact"
INGEST_THREAD_MATCH = "thread_match"
SKIP_UNTRACKED_SENDER = "untracked_sender"
SKIP_UNTRACKED_RECIPIENTS = "untracked_recipients"


class IngestDecision(NamedTuple):
    """Whether a polled message enters the app, and how it is attributed.

    Attributes
    ----------
    ingest : bool
        ``False`` means skip it entirely. The poll cursor still advances past it, so a skipped
        message is examined once and never again.
    direction : {"in", "out"}
        Value for ``email_messages.direction``. Meaningful even when ``ingest`` is ``False``, since
        the poller logs it.
    contact_id : int or None
        The tracked contact this message is attributed to, or ``None``. ``None`` alongside
        ``ingest=True`` is a legitimate state rather than a failure: it is either an unknown sender
        awaiting import, or a thread match whose contact the caller inherits from the thread — this
        module is never told what that is.
    reason : str
        One of the ``INGEST_*`` / ``SKIP_*`` constants.
    """

    ingest: bool
    direction: Direction
    contact_id: int | None
    reason: str


def classify_message(
    *,
    folder_kind: FolderKind,
    from_addr: str | None,
    to_addrs: str | None = None,
    cc_addrs: str | None = None,
    matched_thread_id: int | None = None,
    contact_by_address: Mapping[str, int],
    own_addresses: Collection[str],
) -> IngestDecision:
    """Decide whether a polled message is ingested, and to which contact it is attributed.

    Parameters
    ----------
    folder_kind : {"inbox", "sent", "import"}
        Which polled folder the message came from.
    from_addr : str or None
        The raw ``From`` header value.
    to_addrs, cc_addrs : str or None, optional
        The raw ``To`` and ``Cc`` header values.
    matched_thread_id : int or None, optional
        Result of :func:`core.email_threading.resolve_thread`, so an ongoing conversation stays in
        scope even after a contact writes from a different address than the one on file.
    contact_by_address : mapping of str to int
        Normalized address to ``contacts.id``, resolved by the repository against the
        ``(user_id, email)`` index for the addresses this message carries.
    own_addresses : collection of str
        Donna's own addresses, so she is never counted as the counterpart of her own mail.

    Returns
    -------
    IngestDecision
        When ``ingest`` is ``False``, only ``direction`` and ``reason`` are meaningful.

    Examples
    --------
    A stranger writing to the mailbox is not ingested:

    >>> classify_message(
    ...     folder_kind=FOLDER_INBOX, from_addr="stranger@example.com",
    ...     contact_by_address={}, own_addresses={"donna@x.com"},
    ... )
    IngestDecision(ingest=False, direction='in', contact_id=None, reason='untracked_sender')

    The same message, dragged into the import folder, is — with no contact yet:

    >>> classify_message(
    ...     folder_kind=FOLDER_IMPORT, from_addr="stranger@example.com",
    ...     contact_by_address={}, own_addresses={"donna@x.com"},
    ... )
    IngestDecision(ingest=True, direction='in', contact_id=None, reason='import_folder')
    """
    sender = normalize_address(from_addr)
    mine = {address.strip().lower() for address in own_addresses if address}
    recipients = [address for address in addresses_in(to_addrs, cc_addrs) if address not in mine]

    # Direction comes from the sender, not the folder, so a message Donna Cc'd to herself — and so
    # also has sitting in INBOX — is still recorded as outbound rather than as mail she received.
    # The Sent folder forces outbound regardless: a send from an alias missing from `own_addresses`
    # is still hers, and misfiling it as inbound would invent a reply that never happened.
    direction: Direction = (
        DIRECTION_OUT if folder_kind == FOLDER_SENT or sender in mine else DIRECTION_IN
    )

    if direction == DIRECTION_OUT:
        contact_id = _first_tracked(recipients, contact_by_address)
    else:
        contact_id = contact_by_address.get(sender)

    if folder_kind == FOLDER_IMPORT:
        # contact_id is often None here, and that is exactly the pending-import state (#3): the row
        # is created with contact_id NULL and badged for triage.
        return IngestDecision(True, direction, contact_id, INGEST_IMPORT_FOLDER)

    if matched_thread_id is not None:
        return IngestDecision(True, direction, contact_id, INGEST_THREAD_MATCH)

    if contact_id is not None:
        return IngestDecision(True, direction, contact_id, INGEST_TRACKED_CONTACT)

    skip_reason = SKIP_UNTRACKED_RECIPIENTS if direction == DIRECTION_OUT else SKIP_UNTRACKED_SENDER
    return IngestDecision(False, direction, None, skip_reason)


def _first_tracked(addresses: Iterable[str], contact_by_address: Mapping[str, int]) -> int | None:
    """Return the contact id of the first tracked address, preserving header order."""
    for address in addresses:
        contact_id = contact_by_address.get(address)
        if contact_id is not None:
            return contact_id
    return None
