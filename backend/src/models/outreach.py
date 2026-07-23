"""Pydantic contracts for the outbound outreach journal.

An outreach is a single **outbound** touch, logged against a contact and decoupled from pipeline
stage (DATABASE.md §"outreaches", DEV-PLAN slice 4 acceptance #6). The wire contract follows the
project's Option-A rule: entities by id (``contact_id``, ``opportunity_id``, ``message_template_id``)
and catalog vocabularies by ``short_name`` (``channel``, ``kind``). An outreach is a first-class
resource with two symmetric links — the required ``contact_id`` and the optional ``opportunity_id``
— both carried in the body of a flat ``POST /outreaches`` (not nested under either parent), so a gig
and a venue are equal *filter* axes over one journal rather than separate parents.

``kind`` is optional on input: omit it and the server infers the default from the contact's touch
history — **contact-scoped**: ``initial`` for the first-ever outbound touch to that contact,
``correspondence`` after (``core/outreach.py``, acceptance #1). Send ``kind`` to persist an override
(e.g. a fresh pitch to a known contact marked as prospecting). The only distinction any metric
consumes is ``outreach_kinds.counts_toward_target`` (prospecting vs. admin), so the ``opportunity``
and venue links are for display filtering, never a separate target. The poller-only
``email_message_id`` is deliberately absent from the input — it is set when an email touch is
ingested (slice 7), never by a client.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OutreachInput(BaseModel):
    """A logged outbound touch, for create.

    Parameters
    ----------
    contact_id : int
        The contact this touch went to (entity FK — an outreach always has one).
    channel : str
        ``outreach_channels`` catalog short_name (email, dm, call, in_person, text).
    kind : str or None
        ``outreach_kinds`` catalog short_name. Omit to accept the server-inferred default
        (initial / correspondence); set it to persist an override such as ``follow_up``.
    opportunity_id : int or None
        Optional attribution to a gig; a touch need not belong to one.
    message_template_id : int or None
        The template used to compose this touch, if any (entity FK).
    note : str or None
        Free-text note about the touch.
    occurred_at : datetime or None
        When the touch happened; defaults to now server-side when omitted. A touch may be backdated.
    """

    contact_id: int
    channel: str = Field(min_length=1)
    kind: str | None = None
    opportunity_id: int | None = None
    message_template_id: int | None = None
    note: str | None = None
    occurred_at: datetime | None = None


class OutreachSummary(BaseModel):
    """A logged outbound touch, for responses (list and create result).

    Carries the *resolved* ``kind`` — the override if one was sent, otherwise the inferred default —
    so a client never re-derives inference. ``contact_name`` is denormalized for display in lists
    and the contact timeline.
    """

    id: int
    contact_id: int
    contact_name: str
    opportunity_id: int | None
    channel: str
    kind: str
    message_template_id: int | None
    note: str | None
    occurred_at: datetime
    created_at: datetime
