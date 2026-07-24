"""Pydantic contract for the contact timeline — a read-time union, no backing table.

The contact page interleaves three journals into one time-ordered list (DEV-PLAN slice 4 acceptance
#5, DATABASE.md §4 "Computed on the fly"): outbound ``outreaches``, dated ``opportunity_notes``, and
pipeline ``status_events``. The repository assembles it with a ``UNION ALL`` and orders by
``occurred_at`` descending; ``email_messages`` joins the union in the email slice (0008).

The union emits a common column set, so this is one flat model with an ``item_type`` discriminator
rather than a polymorphic hierarchy: each row populates only the fields its type carries and leaves
the rest null. This keeps the projection a straight map from the union query with no per-type
assembly.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TimelineItem(BaseModel):
    """One entry in a contact's unified timeline, projected from the union query.

    Parameters
    ----------
    item_type : str
        Which journal the row came from: ``outreach``, ``note``, or ``status_event``.
    source_id : int
        The row id in its source table (``outreaches`` / ``opportunity_notes`` / ``status_events``),
        for building a link back. Not unique across the timeline — pair it with ``item_type``.
    occurred_at : datetime
        The event time; the descending order key for the whole list.
    text : str or None
        The row's free text: an outreach ``note``, an opportunity_note ``body``, or a status event's
        close-reason ``note``. Null when the source row has none.
    opportunity_id : int or None
        The gig this entry relates to, when any (an outreach may be unattributed; a note or status
        event always has one).
    opportunity_title : str or None
        The related gig's title, denormalized for display.
    channel : str or None
        ``outreach`` items only — the ``outreach_channels`` short_name.
    kind : str or None
        ``outreach`` items only — the resolved ``outreach_kinds`` short_name.
    status : str or None
        ``status_event`` items only — the ``opportunity_statuses`` short_name moved to.
    """

    item_type: str
    source_id: int
    occurred_at: datetime
    text: str | None = None
    opportunity_id: int | None = None
    opportunity_title: str | None = None
    channel: str | None = None
    kind: str | None = None
    status: str | None = None
