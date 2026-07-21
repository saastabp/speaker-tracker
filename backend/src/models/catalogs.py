"""Pydantic models for the catalog vocabularies returned by ``GET /catalogs``.

Each catalog shares ``short_name``/``description``/``sort_order``; three carry one extra flag
the frontend and metric SQL consume (``DATABASE.md`` §3). ``id`` and the audit/soft-delete
columns are deliberately not exposed — callers resolve vocabularies by ``short_name``.

``message_template_kinds`` is intentionally absent: it moves to slice 4 (``0004``), where it
is modelled as a template *purpose* vocabulary with a separate ``channel_id`` on
``message_templates``, rather than the mixed channel/audience axis it once held.
"""

from __future__ import annotations

from pydantic import BaseModel


class CatalogItem(BaseModel):
    """One catalog row addressable by its stable ``short_name``."""

    short_name: str
    description: str
    sort_order: int


class OpportunityStatus(CatalogItem):
    """A pipeline status; ``is_terminal`` gates the ``closed_at`` predicate (§4)."""

    is_terminal: bool


class PaymentStatus(CatalogItem):
    """A payment status; ``is_settled`` drives the money gate on ``closed_at`` (§4)."""

    is_settled: bool


class OutreachKind(CatalogItem):
    """An outreach kind; ``counts_toward_target`` filters the outreach metric (§3)."""

    counts_toward_target: bool


class Catalogs(BaseModel):
    """The full set of catalog vocabularies, keyed by table name."""

    organization_types: list[CatalogItem]
    warmth_tiers: list[CatalogItem]
    contact_roles: list[CatalogItem]
    opportunity_formats: list[CatalogItem]
    opportunity_statuses: list[OpportunityStatus]
    comp_types: list[CatalogItem]
    payment_statuses: list[PaymentStatus]
    outreach_kinds: list[OutreachKind]
    outreach_channels: list[CatalogItem]
    target_types: list[CatalogItem]
