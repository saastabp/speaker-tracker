"""Pydantic contracts for inbound email — the pending-import queue and its two link actions.

A "pending import" is **not a table**. It is an ``email_threads`` row whose ``contact_id`` is NULL
(DESIGN.md §3 says so explicitly): mail the poller was authorized to ingest but which is not yet
attached to anyone we track. Two paths produce one — a message dragged into the ``Import`` folder
(the drag *is* the authorization, :mod:`core.email_scope`), and an inbound message whose header
chain joined a stored thread that itself has no contact.

Shape notes, and why:

- **Linking attaches an *existing* contact; it never creates one.** The frontend opens the shipped
  ``ContactFormModal`` prefilled from the ``From`` header and saves through ``POST /contacts``,
  which already routes through slice 2's dedupe. A link endpoint that could also create would put
  that dedupe in a second place, and DEV-PLAN slice 6b acceptance #4's "offers to attach rather
  than create, for a person we already know" *is* the dedupe.
- **``suggested_organization_id`` is a hint, never an action.** The repository resolves it by
  matching the sender's domain against ``organizations.email_domain`` (present in ``0002`` for
  exactly this flow, with an index on ``(user_id, email_domain)``). It prefills the venue field on
  the Add Contact form; nothing is written from it server-side.
- **``LinkOpportunityInput`` exists because inbound-first threads get ``opportunity_id = NULL``
  unconditionally.** The auto-attach-when-exactly-one heuristic was rejected — a lone opportunity
  is no guarantee the mail is about it, and mislabeling side-channel mail is worse than leaving it
  unattached. The consequence is that such a thread can never reach a gig without a manual action,
  so this control is required, not optional.
- **No outreach shape here, and none anywhere on the inbound path.** Ingest never writes an
  ``outreaches`` row — not for inbound mail (acceptance #8), and not for a message discovered in
  the ``Sent`` folder because Donna composed it in Outlook (settled 2026-07-27). Outreach counting
  originates inside the app only: a touch the app did not send appearing in the journal is
  unexplainable from Donna's side, and an unexplainable number is worse than a low one.
- **The poller's own return value is deliberately not here.** It is
  :class:`core.imap_cursor.PollSummary`, a ``NamedTuple``: an internal value and log payload, never
  an HTTP request or response. Every model in this package backs a wire contract, and that is what
  the package means.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PendingImportSummary(BaseModel):
    """One thread awaiting import — a row in the "N emails awaiting import" queue.

    The envelope fields come from the thread's earliest ingested message, which is the one whose
    ``From`` header prefills Add Contact. ``from_addr`` and ``from_name`` are split by the
    repository out of the stored ``email_messages.from_addr``, which holds the full header form
    (``Donna King <donna@example.com>``); clients never parse header text.

    Attributes
    ----------
    thread_id : int
        ``email_threads.id`` — the target of both link actions.
    email_message_id : int
        ``email_messages.id`` of the message shown. Named as on ``outreaches.email_message_id``,
        since a bare ``message_id`` means the RFC 5322 header everywhere else in this codebase.
    from_addr : str
        Bare, lowercased sender address (``core.email_headers.normalize_address``).
    from_name : str or None
        Display name from the ``From`` header; ``None`` when the sender sent a bare address.
    subject : str or None
        The message's subject as received. ``None`` for a subjectless message, which is legal mail.
    received_at : datetime or None
        When the message arrived, from its ``Date`` header or the IMAP ``INTERNALDATE``.
    suggested_organization_id : int or None
        Existing organization whose ``email_domain`` matches the sender's domain, or ``None``.
    suggested_organization_name : str or None
        That organization's name, so the queue can name the suggestion without a second fetch.
    """

    thread_id: int
    email_message_id: int
    from_addr: str
    from_name: str | None = None
    subject: str | None = None
    received_at: datetime | None = None
    suggested_organization_id: int | None = None
    suggested_organization_name: str | None = None


class LinkContactInput(BaseModel):
    """Attach an existing contact to a pending thread, or detach it.

    The link backfills the whole thread — the ``email_threads`` row and every ``email_messages``
    row under it — so the conversation appears on the contact's timeline from its first message,
    not only from the moment it was linked.

    Attributes
    ----------
    contact_id : int or None
        An **existing** ``contacts.id``, or ``None`` to detach, which returns the thread to the
        pending-import queue. Creating a contact is ``POST /contacts``, deliberately not this
        endpoint; see the module docstring.

        This was non-nullable when the model was first written, on the grounds that detaching
        would put a thread back into a queue with no interface to show it. Building that queue
        removed the objection: a detached thread simply reappears there, which is exactly what
        should happen when Donna decides she linked the wrong person. Re-linking to a *different*
        contact always worked; only "none" was unreachable, and that is the one correction someone
        who has just made a mistake actually wants.
    """

    contact_id: int | None = None


class LinkOpportunityInput(BaseModel):
    """Attach a thread to a gig, or detach it.

    Attributes
    ----------
    opportunity_id : int or None
        ``opportunities.id`` to attribute the conversation to. ``None`` detaches, which is the
        correction path for a thread linked to the wrong gig — the only way back, since nothing
        infers an opportunity for an inbound-first thread.
    """

    opportunity_id: int | None = None
