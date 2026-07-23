"""Pydantic contracts for organizations (venues, podcasts, expos) and their affiliated contacts.

The three **Kindling** research fields (`what_it_is`, `why_it_fits`, `how_to_approach`) are
structured columns; research-readiness is computed from them by :mod:`core.research`, surfaced on
responses as `research_ready`. Writes use a single input shape for both create and full-replace
update (PUT); `id`, timestamps, and computed fields appear only on responses.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OrganizationInput(BaseModel):
    """Writable organization fields, for create and full-replace update.

    Parameters
    ----------
    organization_type_id : int
        FK into the `organization_types` catalog. Required — an org always has a type.
    name : str
        Display name; 1-255 characters.
    location, website_url, email_domain, notes : str or None
        Optional detail. `email_domain` seeds the drop-folder import's sender match (slice 6b).
    what_it_is, why_it_fits, how_to_approach : str or None
        The three Kindling research fields; drive research-readiness.
    """

    organization_type_id: int
    name: str = Field(min_length=1, max_length=255)
    location: str | None = None
    website_url: str | None = None
    email_domain: str | None = None
    what_it_is: str | None = None
    why_it_fits: str | None = None
    how_to_approach: str | None = None
    notes: str | None = None


class AffiliatedContact(BaseModel):
    """A contact affiliated with an organization, with their role at that org."""

    contact_id: int
    name: str
    title: str | None
    is_primary: bool
    is_power_partner: bool


class OrganizationSummary(BaseModel):
    """One row in the venues list — enough to scan and to show the research-ready state."""

    id: int
    organization_type_id: int
    name: str
    location: str | None
    why_it_fits: str | None
    contact_count: int
    research_ready: bool
    created_at: datetime
    updated_at: datetime


class Organization(OrganizationInput):
    """Full organization detail, including affiliated contacts and computed readiness."""

    id: int
    contact_count: int
    research_ready: bool
    created_at: datetime
    updated_at: datetime
    contacts: list[AffiliatedContact] = Field(default_factory=list)
