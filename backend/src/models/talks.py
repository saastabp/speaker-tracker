"""Pydantic contracts for talks — the reusable offers Donna pitches.

A talk is a lightweight catalog of Donna's own material (workshop, keynote, podcast topic) that an
opportunity can reference via ``talk_id``. Writes use one input shape for create and full-replace
update; ``id`` and timestamps appear only on responses.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TalkInput(BaseModel):
    """Writable talk fields, for create and full-replace update.

    Parameters
    ----------
    title : str
        Display title; 1-255 characters.
    length_minutes : int or None
        Optional run time in minutes.
    one_liner : str or None
        Optional one-sentence description.
    sort_order : int
        Manual ordering for the picker; lower sorts first. Defaults to 0.
    """

    title: str = Field(min_length=1, max_length=255)
    length_minutes: int | None = None
    one_liner: str | None = None
    sort_order: int = 0


class TalkSummary(BaseModel):
    """One row in the talks list / picker."""

    id: int
    title: str
    length_minutes: int | None
    one_liner: str | None
    sort_order: int
    created_at: datetime
    updated_at: datetime


class Talk(TalkInput):
    """Full talk detail."""

    id: int
    created_at: datetime
    updated_at: datetime
