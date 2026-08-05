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

from datetime import date, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from models.follow_ups import FollowUpSummary
from models.targets import Cadence


class Week(BaseModel):
    """The week the target tiles were anchored to, ``[start, end)`` as local dates.

    Sent so the SPA can label the week it is showing without recomputing Sunday-start boundaries —
    the same reasoning as :attr:`TargetTile.period_start`, and the reason ``currentWeekLabel()``
    could be deleted from the Dashboard page. It is **always** present, including when no weekly
    target is set, so the navigator has a week to render even with an empty tile grid.

    ``end`` is exclusive: the week of Jul 19 is ``start=2026-07-19``, ``end=2026-07-26``.
    """

    start: date
    end: date


class TargetTile(BaseModel):
    """One actual-vs-target tile: a (target_type, cadence) goal and its current-period actual.

    Only target types the user has actually set a goal for produce a tile.
    """

    target_type: str  # target_types short_name
    cadence: Cadence
    goal: int
    actual: int
    #: The window ``actual`` was counted over, ``[period_start, period_end)`` as local dates.
    #:
    #: Sent rather than left for the client to re-derive: the period maths lives in
    #: ``core.periods`` (Sunday-start weeks, quarter boundaries), and having the SPA recompute it
    #: would be that logic written twice in two languages, drifting the first time one changed.
    #: The tile's drill-down hands these straight to the list as its date range, which is the same
    #: shape a date-range picker would produce later.
    period_start: date
    period_end: date


class FunnelCount(BaseModel):
    """Both counts for one funnel stage: how many ever reached it, and how many are there now.

    ``count`` is reached-or-beyond and drives the conversion percentages; ``current`` is where gigs
    sit today and is what the card links to, so the number clicked and the list opened agree. The
    difference between them is the stage's drop-off.
    """

    status: str  # opportunity_statuses short_name
    count: int
    current: int


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
    """One dated thing on the near horizon — an active gig, or a logged appointment.

    Two shapes in one list, discriminated by ``item_type``, because "what is next" is a single
    question and answering it from two stacked lists would make the reader merge them by eye.
    Follow-up reminders stay their own card (``follow_ups``): this panel is future-facing, and an
    overdue reminder has to get *louder* rather than scroll off the top of a chronological list.

    Only one of ``organization_name`` / ``contact_name`` is set — a gig happens at a venue, an
    appointment is with a person — and ``current_status`` belongs to a gig alone. ``event_time`` is
    null for a gig, whose ``event_date`` carries no hour; an appointment always has one, which is
    also why an appointment drops off this list the moment it passes while a gig stays up all day.

    ``id`` is unique **per ``item_type``**, not across the list: a gig and an appointment may both
    be id 3, so a client keying rows has to combine the two fields.
    """

    item_type: Literal["gig", "appointment"]
    id: int
    title: str
    organization_name: str | None
    contact_name: str | None
    event_date: date
    event_time: time | None
    current_status: str | None


class Dashboard(BaseModel):
    """The full dashboard payload — one composite response for the home screen.

    ``week`` anchors the target tiles only. Every other field reports on now regardless of the week
    being viewed (DEV-PLAN slice 10 acceptance #4).
    """

    week: Week
    targets: list[TargetTile]
    funnel: list[FunnelCount]
    #: How many gigs produced at least one response — the funnel card's final row (slice 12). A
    #: plain count rather than a sixth :class:`FunnelCount`, because that model's ``status`` is an
    #: ``opportunity_statuses`` short_name and "responses" is not a status; and because its
    #: ``current`` ("how many sit here now") has no meaning for something a gig *produces* rather
    #: than occupies, so there is nothing for the card to link to either.
    responses_reached: int
    money: MoneyRollup
    needs_attention: list[NeedsAttentionItem]
    coming_up: list[ComingUpEvent]
    follow_ups: list[FollowUpSummary]
