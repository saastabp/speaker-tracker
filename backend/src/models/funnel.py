"""Pydantic contract for the funnel endpoint — the server-owned board columns.

``GET /funnel`` returns the ordered board stages the SPA renders as columns; no stage name is
hardcoded in the SPA (DEV-PLAN slice 3 acceptance #9). Projected from :class:`core.funnel.Stage`.
"""

from __future__ import annotations

from pydantic import BaseModel


class FunnelStage(BaseModel):
    """One board column: a pipeline stage's short_name, label, order, and terminal flag."""

    short_name: str
    label: str
    sort_order: int
    is_terminal: bool
