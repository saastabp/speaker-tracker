"""Pydantic contracts for logged appointments.

An appointment is a *scheduled meeting with a person*, recorded so it appears on the Dashboard's
"Coming up" card (``0014_appointments.sql``). It is not a calendar entry: nothing syncs, invites or
emails, and there is no completion state — an appointment stops being upcoming when its time
passes, which is the only lifecycle it has.

The wire contract follows the project's Option-A rule — entities by id, catalog vocabularies by
short_name. There are no catalogs here, so ``contact_id`` is the only reference.

**``scheduled_at`` is a wall-clock instant, not a zone-aware one.** The column is a DATETIME
(decision 1 in the migration) and the value crosses the wire naive — ``2026-08-07T14:00:00`` means
2pm where Donna is, and no layer converts it. Sending an offset would imply a precision this
feature does not have.

**Patch semantics are split, deliberately.** ``title``, ``scheduled_at`` and ``contact_id`` are NOT
NULL in the database, so ``None`` can only mean "unchanged" for them and there is no ambiguity to
resolve — the same reasoning :class:`models.follow_ups.FollowUpPatch` relies on. ``details`` is
nullable, so it needs the other rule: the repository reads ``model_fields_set`` and an explicitly
sent ``null`` **clears** it, while omitting the key leaves it alone. Without that split there would
be no way to remove details once written.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: What slice of the journal a list read wants. ``upcoming`` is ``scheduled_at >= now``, ``past`` is
#: everything before it, and ``all`` is the unfiltered default — the Appointments page's toggle
#: sends the first two, the Dashboard sends ``upcoming``, and an unfiltered call means everything.
AppointmentScope = Literal["upcoming", "past", "all"]


class AppointmentInput(BaseModel):
    """An appointment, for create.

    Parameters
    ----------
    contact_id : int
        The person the appointment is with. Required — an appointment with nobody is not a thing
        this feature models.
    title : str
        The display label every surface renders (the "Coming up" row, the list, the contact panel),
        so it is required and non-empty.
    scheduled_at : datetime
        When it happens, as a naive wall-clock value. Not validated as future: an appointment may
        legitimately be backdated (logged after the fact) and the past/upcoming split is a read
        concern, never a write constraint.
    details : str or None
        The free-text block. Optional — a person, a time and a title already make a useful row.
    """

    contact_id: int
    title: str = Field(min_length=1, max_length=255)
    scheduled_at: datetime
    details: str | None = None


class AppointmentPatch(BaseModel):
    """A partial edit to an existing appointment.

    Every field is optional. For ``contact_id``, ``title`` and ``scheduled_at``, ``None`` means
    unchanged — none of them is nullable in the database, so there is nothing an explicit ``null``
    could mean. ``details`` is different: the repository inspects ``model_fields_set``, so sending
    ``{"details": null}`` clears it and omitting the key leaves it as it was.

    Unlike a follow-up, whose links are fixed at create, ``contact_id`` **is** patchable here. There
    is one link and it is required, so re-pointing it is a single validated FK swap rather than a
    re-run of a multi-column CHECK — and picking the wrong person from a long list is an ordinary
    mistake that should not require deleting the row to fix.

    Parameters
    ----------
    contact_id : int or None
        Move the appointment to a different person.
    title : str or None
        New display label; non-empty when provided.
    scheduled_at : datetime or None
        New wall-clock date and time.
    details : str or None
        New free text. Explicit ``null`` clears it (see above).
    """

    contact_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    scheduled_at: datetime | None = None
    details: str | None = None


class AppointmentSummary(BaseModel):
    """An appointment as returned to clients, for list and single-item responses.

    ``contact_name`` is denormalized for display so a list render needs no per-row lookup. The join
    behind it does not filter ``deleted_at``, so an appointment still names its person after that
    contact is soft-deleted — the same choice ``outreaches`` and ``follow_ups`` make.

    Whether the appointment is *past* is not sent: it depends on the viewer's now, and both the SPA
    and the list endpoint already know it.
    """

    id: int
    contact_id: int
    contact_name: str
    title: str
    scheduled_at: datetime
    details: str | None
    created_at: datetime
