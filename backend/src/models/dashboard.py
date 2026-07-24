"""Pydantic contract for the composite dashboard response (DEV-PLAN slice 5).

One ``GET /dashboard`` returns everything the home screen renders: actual-vs-target tiles, the
funnel ratio counts, the money rollup, the stale-opportunity list, and the needs-attention list. It
is a read-only projection assembled by ``repositories.dashboard`` — actuals bucket into the current
period per cadence in the user's timezone (``core.periods``), funnel counts are reached-or-beyond
(``core.funnel``), and money excludes pro bono from the currency totals (acceptance #5).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from models.targets import Cadence


class TargetTile(BaseModel):
    """One actual-vs-target tile: a (target_type, cadence) goal and its current-period actual.

    Only target types the user has actually set a goal for produce a tile.
    """

    target_type: str  # target_types short_name
    cadence: Cadence
    goal: int
    actual: int


class FunnelCount(BaseModel):
    """A reached-or-beyond count for one funnel ratio stage (outreach_sent → … → booked)."""

    status: str  # opportunity_statuses short_name
    count: int


class MoneyRollup(BaseModel):
    """Money summary. Currency totals exclude pro bono; the pro-bono count is reported apart (#5).

    ``booked`` is committed fees (booked or delivered), ``received`` is what has been paid, and
    ``outstanding`` is ``booked - received``. Amounts are Decimals serialized as precise strings.
    The ``*_count`` fields are the gig counts behind each figure, shown as the money-card sub-labels
    (e.g. "3 paid gigs", "2 collected", "2 invoiced").
    """

    currency: str
    booked: Decimal
    received: Decimal
    outstanding: Decimal
    booked_count: int
    received_count: int
    invoiced_count: int
    pro_bono_count: int


class StaleOpportunity(BaseModel):
    """An active gig with no status change or outreach in the stale window (``core.periods``)."""

    id: int
    title: str
    organization_name: str
    current_status: str
    last_activity_at: datetime | None


class NeedsAttentionItem(BaseModel):
    """A row flagged for follow-up on the dashboard.

    ``reason`` is a machine token the SPA maps to display text and a link target:
    ``awaiting_payment`` (delivered gig, unsettled) and ``overdue_unbooked`` (past-event gig still
    pre-Booked) are gig-scoped, so ``id`` is the opportunity id; ``research_incomplete`` is
    org-scoped (a venue that is not research-ready), so ``id`` is the organization id and the SPA
    links to the venue. ``event_date`` is null for research rows.
    """

    id: int
    title: str
    organization_name: str
    reason: str
    event_date: date | None


class ComingUpEvent(BaseModel):
    """An active gig with a today-or-future event date (the "Coming up" card).

    Follow-up reminders will also populate this panel once ``follow_ups`` (slice 7) exists; for now
    it is upcoming gigs by ``event_date`` only.
    """

    id: int
    title: str
    organization_name: str
    event_date: date
    current_status: str


class Dashboard(BaseModel):
    """The full dashboard payload — one composite response for the home screen."""

    targets: list[TargetTile]
    funnel: list[FunnelCount]
    money: MoneyRollup
    stale: list[StaleOpportunity]
    needs_attention: list[NeedsAttentionItem]
    coming_up: list[ComingUpEvent]
