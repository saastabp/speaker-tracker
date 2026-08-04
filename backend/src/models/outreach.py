"""Pydantic contracts for the outbound outreach journal.

An outreach is a single **outbound** touch, logged against a contact and decoupled from pipeline
stage (DATABASE.md §"outreaches", DEV-PLAN slice 4 acceptance #6). The wire contract follows the
project's Option-A rule: entities by id (``contact_id``, ``opportunity_id``,
``message_template_id``) and catalog vocabularies by ``short_name`` (``channel``, ``kind``). An
outreach is a first-class
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

from models.follow_ups import FollowUpRider


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
    follow_up : models.follow_ups.FollowUpRider or None
        Opt-in request to schedule a reminder alongside this touch. **``None`` is the off state and
        the default** — logging a touch never silently schedules anything (DESIGN.md §7). The
        reminder inherits this touch's contact and opportunity.
    """

    contact_id: int
    channel: str = Field(min_length=1)
    kind: str | None = None
    opportunity_id: int | None = None
    message_template_id: int | None = None
    note: str | None = None
    occurred_at: datetime | None = None
    follow_up: FollowUpRider | None = None


class OutreachPatch(BaseModel):
    """A partial edit to a logged touch.

    Every field is optional, with the same split :class:`models.appointments.AppointmentPatch` uses:
    ``channel``, ``kind`` and ``occurred_at`` back NOT NULL columns, so ``None`` can only mean
    "unchanged"; ``opportunity_id`` and ``note`` are nullable, so the repository reads
    ``model_fields_set`` and an explicitly sent ``null`` **clears** them. Without that, a touch
    attributed to the wrong gig could never be un-attributed.

    Three fields are deliberately absent:

    - **``contact_id``.** Who a touch went to is what the row *is*; re-homing it would move an entry
      between two contacts' timelines and re-open the kind inference that ran at create. Fix a
      wrong-contact touch by deleting it and logging it again.
    - **``message_template_id``.** It records which template was used to compose, which is a fact
      about the moment of sending and not an editable property of the touch.
    - **``follow_up``.** The rider creates a *separate* reminder alongside a new touch; editing the
      touch later has nothing to schedule. Reminders are edited on their own resource.

    ``kind`` here is always an explicit value — **an edit never re-runs inference.** Re-deriving it
    would let an unrelated change (fixing a typo in the note) silently flip ``initial`` to
    ``correspondence`` and move a weekly target count, which is exactly the kind of quiet metric
    drift the resolved-kind response contract exists to prevent.

    Parameters
    ----------
    channel : str or None
        New ``outreach_channels`` short_name.
    kind : str or None
        New ``outreach_kinds`` short_name. Explicit; never inferred on an edit.
    opportunity_id : int or None
        Re-attribute the touch to a gig. Explicit ``null`` clears the attribution.
    note : str or None
        New free-text note. Explicit ``null`` clears it.
    occurred_at : datetime or None
        New timestamp for when the touch happened.
    """

    channel: str | None = Field(default=None, min_length=1)
    kind: str | None = Field(default=None, min_length=1)
    opportunity_id: int | None = None
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
