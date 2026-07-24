"""Pydantic contracts for activity targets.

A target is a ``goal_count`` for a (``target_type``, ``cadence``) pair — e.g. outreaches/week. The
wire contract follows Option A: the catalog vocabulary ``target_type`` travels as a ``short_name``
(``target_types``: venues_researched / outreaches / pitches / bookings), never a numeric id. The
``PUT /targets`` upsert keys on (user, target_type, cadence); ``cadence`` is a fixed ENUM.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: The three target cadences (matches the ``targets.cadence`` ENUM and ``core.periods.CADENCES``).
Cadence = Literal["weekly", "monthly", "quarterly"]


class TargetInput(BaseModel):
    """A goal to upsert for a (target_type, cadence) pair.

    Parameters
    ----------
    target_type : str
        ``target_types`` catalog short_name.
    cadence : str
        ``weekly`` / ``monthly`` / ``quarterly``.
    goal_count : int
        The goal for the period; non-negative (0 allowed — the caller deletes to remove a target).
    """

    target_type: str = Field(min_length=1)
    cadence: Cadence
    goal_count: int = Field(ge=0)


class Target(BaseModel):
    """A stored target, for responses."""

    target_type: str
    cadence: Cadence
    goal_count: int
