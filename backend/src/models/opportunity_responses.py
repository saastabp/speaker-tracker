"""Pydantic contracts for opportunity responses — what a delivered gig generated.

A response is an audience member acting on a talk's call to action: booking a Legacy Spark Chat or
a Discovery call, or requesting the Booklet (``0015_opportunity_responses.sql``). It is the last
thing a gig produces.

**These are counters, not events, and not a target.** One row per (opportunity, type) carrying how
many of that response the gig produced — no dates, no per-response rows. When each response arrived
and who it was live in legacy-tracker and GHL; this side tracks audience growth in aggregate.

Named for the table, with the ``opportunity_`` prefix carried through every layer — see the
migration header for why (in short: it matches ``opportunity_notes`` / ``opportunity_contacts``, and
keeps the entity clear of ``handlers/responses.py``, which composes detail responses and is
unrelated).

The wire contract follows the project's Option-A rule — the parent by id (in the path, since a
response counter has exactly one parent and is never re-pointed) and the vocabulary by
``opportunity_response_types`` short_name.

**The write is a set, not a delta.** :class:`OpportunityResponseCountInput` carries the resulting
count rather than ``+1`` / ``-1``, so a retried or double-fired click lands on the same number
instead of counting twice. It is also why there is no delete route: correcting a mistake is setting
the count lower, and zero is the empty state.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OpportunityResponseCountInput(BaseModel):
    """The new count for one response type on one gig.

    Parameters
    ----------
    count : int
        The resulting number of responses of this type, not a delta. Zero or greater — the
        database's ``ck_opportunity_responses_count`` CHECK is the real guarantee and this rejects
        a negative as a 400 rather than letting it surface as a 500 from the constraint.
    """

    count: int = Field(ge=0)


class OpportunityResponseCount(BaseModel):
    """One counter as returned to clients, embedded in the opportunity detail.

    Only types with a stored row appear. The SPA renders the full grid from the
    ``opportunity_response_types`` catalog it already holds and treats a missing type as zero, so
    the server never invents rows for counts nobody has set.

    Carries no opportunity id: it is only ever read as part of the opportunity that owns it.
    """

    response_type: str
    count: int
