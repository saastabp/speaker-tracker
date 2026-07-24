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
    """

    currency: str
    booked: Decimal
    received: Decimal
    outstanding: Decimal
    pro_bono_count: int


class StaleOpportunity(BaseModel):
    """An active gig with no status change or outreach in the stale window (``core.periods``)."""

    id: int
    title: str
    organization_name: str
    current_status: str
    last_activity_at: datetime | None


class NeedsAttentionItem(BaseModel):
    """A gig flagged for follow-up.

    ``reason`` is a machine token the SPA maps to display text: ``awaiting_payment`` (delivered but
    unsettled) or ``overdue_unbooked`` (past its event date, still pre-Booked).
    """

    id: int
    title: str
    organization_name: str
    reason: str
    event_date: date | None


class Dashboard(BaseModel):
    """The full dashboard payload — one composite response for the home screen."""

    targets: list[TargetTile]
    funnel: list[FunnelCount]
    money: MoneyRollup
    stale: list[StaleOpportunity]
    needs_attention: list[NeedsAttentionItem]
