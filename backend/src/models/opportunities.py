"""Pydantic contracts for opportunities (gigs / podcast spots) and their journal.

The wire contract follows the project's Option-A rule: entities are referenced by id
(``organization_id``, ``talk_id``), catalog vocabularies by ``short_name``
(``opportunity_format``, ``comp_type``, ``current_status``, ``payment_status``, ``contact_role``).

``OpportunityInput`` — the shape for create and full-replace update (PUT) — is deliberately limited
to descriptive and money-*setup* fields. The lifecycle fields (``status``, ``payment_status``,
``paid_on``, and the derived ``closed_at``) never travel on it: they move only through the journaled
endpoints (``PATCH /{id}/status``, ``PATCH /{id}/payment``, ``POST /{id}/close``), which is what
keeps ``status_events`` a faithful one-row-per-move log and ``closed_at`` recomputed in exactly the
places that own it (DATABASE.md §4, DEV-PLAN slice 3 acceptance #1/#3/#5/#8).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class OpportunityInput(BaseModel):
    """Writable descriptive/money-setup fields, for create and full-replace update.

    Parameters
    ----------
    title : str
        Display title for the gig; 1-255 characters.
    organization_id : int
        The host venue / podcast (entity FK — an opportunity always has one).
    opportunity_format : str
        ``opportunity_formats`` catalog short_name (workshop, podcast_spot, …).
    comp_type : str
        ``comp_types`` catalog short_name (paid, pro_bono, trade). On create it seeds the initial
        payment status (paid → unbilled; pro_bono/trade → n_a) server-side.
    talk_id : int or None
        Which talk was offered (entity FK, optional).
    event_date : date or None
        Scheduled date, if known.
    fee_amount : Decimal or None
        Agreed fee; non-negative, up to 10 digits with 2 decimals. None for pro bono / unknown.
    currency : str
        ISO 4217 code; 3 letters, defaults to USD.
    angle : str or None
        The pitch angle; seeded from the venue's ``how_to_approach`` at create, then editable.
    outcome : str or None
        Free-text outcome / result notes.
    """

    title: str = Field(min_length=1, max_length=255)
    organization_id: int
    opportunity_format: str = Field(min_length=1)
    comp_type: str = Field(min_length=1)
    talk_id: int | None = None
    event_date: date | None = None
    fee_amount: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    angle: str | None = None
    outcome: str | None = None


class OpportunityCreateInput(OpportunityInput):
    """Create-only extension of :class:`OpportunityInput` carrying optional lifecycle *seeds*.

    ``OpportunityInput`` (reused by PUT) is deliberately lifecycle-free; create alone may seed where
    a gig starts, so a back-filled opportunity lands in the stage it is actually in — with its first
    ``status_events`` row recorded there (not a phantom ``researching`` → stage move) — and can name
    its lead contact in the same call. All three are optional; omitting them reproduces the previous
    behaviour (start in ``researching``, payment status derived from the comp type, no lead).

    Parameters
    ----------
    starting_status : str or None
        ``opportunity_statuses`` short_name to start in; must be a non-terminal board stage. None
        starts in ``researching``.
    payment_status : str or None
        ``payment_statuses`` short_name to start in. None derives it from ``comp_type``.
    lead_contact_id : int or None
        A contact to link as the lead on this gig (``is_primary``). None links no one.
    """

    starting_status: str | None = None
    payment_status: str | None = None
    lead_contact_id: int | None = None


class OpportunityStatusPatch(BaseModel):
    """Move an opportunity to a new board stage (``PATCH /{id}/status``).

    Journals exactly one ``status_events`` row per real move (a move to the current status is a
    no-op) and recomputes ``closed_at``. The target must be a board stage; cancelled / lost are
    reached through the Close flow, not here.

    Parameters
    ----------
    status : str
        Target ``opportunity_statuses`` short_name.
    """

    status: str = Field(min_length=1)


class OpportunityPaymentPatch(BaseModel):
    """Update payment state (``PATCH /{id}/payment``); recomputes ``closed_at``.

    Marking a delivered gig paid moves it to History (#4); correcting the payment back off a settled
    state clears ``closed_at`` and returns the card to the board (#5).

    Parameters
    ----------
    payment_status : str
        ``payment_statuses`` catalog short_name.
    paid_on : date or None
        Date the payment was received, if applicable.
    """

    payment_status: str = Field(min_length=1)
    paid_on: date | None = None


class OpportunityCloseInput(BaseModel):
    """Close an opportunity through the Close flow (``POST /{id}/close``).

    Writes a terminal ``status_events`` row **and** captures the reason as a note (#8).

    Parameters
    ----------
    status : str
        Terminal ``opportunity_statuses`` short_name — cancelled or lost.
    reason : str
        Why it closed; required, recorded on the status event and as a note.
    """

    status: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class OpportunityContactInput(BaseModel):
    """Link a contact to an opportunity, with their role on this gig.

    ``is_primary`` here means "lead on this gig" — unrelated to
    ``contact_organizations.is_primary`` (the default contact for a venue).

    Parameters
    ----------
    contact_id : int
        The contact to link.
    contact_role : str or None
        ``contact_roles`` catalog short_name for this gig.
    is_primary : bool
        Whether this contact is the lead on this gig.
    """

    contact_id: int
    contact_role: str | None = None
    is_primary: bool = False


class OpportunityContactUpdate(BaseModel):
    """Update a linked contact's role on a gig (the contact is fixed by the path)."""

    contact_role: str | None = None
    is_primary: bool = False


class OpportunityNoteInput(BaseModel):
    """Add a dated note to an opportunity.

    Parameters
    ----------
    body : str
        The note text; required.
    occurred_at : datetime or None
        When it happened; defaults to now server-side when omitted.
    """

    body: str = Field(min_length=1)
    occurred_at: datetime | None = None


class OpportunityContact(BaseModel):
    """A contact linked to an opportunity, with their per-gig role, for detail responses."""

    contact_id: int
    name: str
    contact_role: str | None
    is_primary: bool


class OpportunityNote(BaseModel):
    """A dated note on an opportunity."""

    id: int
    body: str
    occurred_at: datetime
    created_at: datetime


class StatusEvent(BaseModel):
    """One entry in an opportunity's status journal (the lifecycle history)."""

    id: int
    status: str
    note: str | None
    occurred_at: datetime


class OpportunitySummary(BaseModel):
    """One card in the flat board payload (also a History row).

    The SPA buckets cards into columns by ``current_status`` (the flat-list board decision), and
    filters board vs History by ``closed_at``. Carries the money / payment chip fields.
    """

    id: int
    title: str
    organization_id: int
    organization_name: str
    organization_type: str
    talk_title: str | None
    opportunity_format: str
    current_status: str
    comp_type: str
    fee_amount: Decimal | None
    currency: str
    payment_status: str
    event_date: date | None
    paid_on: date | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class Opportunity(OpportunityInput):
    """Full opportunity detail: descriptive fields plus lifecycle state and the journals."""

    id: int
    organization_name: str
    talk_title: str | None
    current_status: str
    payment_status: str
    paid_on: date | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    contacts: list[OpportunityContact] = Field(default_factory=list)
    notes: list[OpportunityNote] = Field(default_factory=list)
    status_events: list[StatusEvent] = Field(default_factory=list)
