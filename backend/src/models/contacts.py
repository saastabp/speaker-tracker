"""Pydantic contracts for contacts (people) and their organization affiliations.

A contact carries no `organization_id`: affiliation lives in `contact_organizations`, because one
person is frequently the contact for several venues. Affiliation writes are separate endpoints
(`/contacts/{id}/organizations`), so they have their own input shapes.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ContactInput(BaseModel):
    """Writable contact fields, for create and full-replace update.

    Parameters
    ----------
    name : str
        Display name; 1-255 characters.
    email : str or None
        Optional; matched by the IMAP poller against inbound `From` (slice 6b). One address per
        contact (a documented limitation, `DATABASE.md`).
    phone, source, how_you_know, notes : str or None
        Optional detail.
    warmth_tier : str or None
        Optional `warmth_tiers` catalog short_name; may be unset early in a relationship.
    """

    name: str = Field(min_length=1, max_length=255)
    email: str | None = None
    phone: str | None = None
    warmth_tier: str | None = None
    source: str | None = None
    how_you_know: str | None = None
    notes: str | None = None


class AffiliationInput(BaseModel):
    """Create an affiliation: attach a contact (path) to an organization (body).

    `is_primary` and `is_power_partner` are scoped to this contact↔venue edge, not the person —
    a contact can be the primary and/or a power partner at one venue and neither at another.
    """

    organization_id: int
    title: str | None = None
    is_primary: bool = False
    is_power_partner: bool = False


class AffiliationUpdate(BaseModel):
    """Update an existing affiliation's per-venue role fields (org is fixed by the path)."""

    title: str | None = None
    is_primary: bool = False
    is_power_partner: bool = False


class OrganizationAffiliation(BaseModel):
    """An organization a contact is affiliated with, with their per-venue role there."""

    organization_id: int
    organization_name: str
    title: str | None
    is_primary: bool
    is_power_partner: bool


class ContactSummary(BaseModel):
    """One row in the contacts list and in the add-contact dedupe results.

    `is_power_partner` here is a rollup — true when the contact is a power partner at **any**
    affiliated venue — since the flag itself now lives per-affiliation, not on the person.
    """

    id: int
    name: str
    email: str | None
    warmth_tier: str | None
    is_power_partner: bool
    organization_count: int
    created_at: datetime
    updated_at: datetime


class Contact(ContactInput):
    """Full contact detail, including the organizations they are affiliated with."""

    id: int
    created_at: datetime
    updated_at: datetime
    organizations: list[OrganizationAffiliation] = Field(default_factory=list)
