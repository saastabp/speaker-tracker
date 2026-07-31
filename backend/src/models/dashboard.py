"""Pydantic contract for the composite dashboard response (DEV-PLAN slice 5).

One ``GET /dashboard`` returns everything the home screen renders: actual-vs-target tiles, the
funnel ratio counts, the money rollup, the needs-attention list, and the due follow-up reminders.
It is a read-only projection assembled by ``repositories.dashboard`` — actuals bucket into the
current period per cadence in the user's timezone (``core.periods``),
funnel counts are reached-or-beyond (``core.funnel``), and money excludes pro bono from the
currency totals (acceptance #5).

``follow_ups`` reuses :class:`models.follow_ups.FollowUpSummary` rather than a dashboard-specific
shape: the card renders the same fields the Follow-ups page does, and a second model would be one
more thing to keep in step for no gain.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from models.follow_ups import FollowUpSummary
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


class NeedsAttentionItem(BaseModel):
    """A row flagged for follow-up on the dashboard.

    ``reason`` is a machine token the SPA maps to display text and a link target — and it is the
    **only** thing that says which id-space ``id`` belongs to, so a new reason always means
    teaching the SPA a new link:

    - ``awaiting_payment`` (delivered gig, unsettled), ``overdue_unbooked`` (past-event gig still
      pre-Booked) and ``stale`` (no status change or outreach in the stale window) are gig-scoped,
      so ``id`` is the opportunity id;
    - ``research_incomplete`` is org-scoped (a venue that is not research-ready), so ``id`` is the
      organization id and the SPA links to the venue;
    - ``awaiting_reply`` is thread-scoped (slice 6b): an open thread whose last message went out
      and has gone unanswered past the threshold, so ``id`` is the ``email_threads`` id.

    ``event_date`` is null for every reason but the gig-scoped ones, which is also what sorts dated
    rows first. ``since`` is the date the condition began — last activity for ``stale``, last
    outbound message for ``awaiting_reply`` — and is null where urgency is not a duration, so the
    SPA can render "9 days" rather than only naming the problem.
    """

    id: int
    title: str
    organization_name: str
    reason: str
    event_date: date | None
    since: date | None


class ComingUpEvent(BaseModel):
    """An active gig with a today-or-future event date (the "Coming up" card).

    Gigs only. Follow-up reminders get their own card (``follow_ups``) rather than being merged in
    here — this panel is future-facing and an overdue reminder needs to be louder than that.
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
    needs_attention: list[NeedsAttentionItem]
    coming_up: list[ComingUpEvent]
    follow_ups: list[FollowUpSummary]
