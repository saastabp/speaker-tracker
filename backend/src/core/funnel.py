"""Pipeline stage taxonomy and the server-owned funnel — pure domain logic, no I/O.

The board's columns, their order, and their labels come from the server (DEV-PLAN slice 3
acceptance #9): the SPA hardcodes no stage name. This module derives that column list from the
``opportunity_statuses`` catalog and classifies each status as a board column vs a Close-flow
outcome.

The one status that needs naming is ``delivered``: it is *terminal* yet still shows as an active
board column, because a delivered-but-unpaid gig stays on the board until Donna collects
(``closed_at`` predicate, DATABASE.md §4). Every other terminal status (cancelled, lost) closes on
entry and is reached through the Close flow, never a board drag.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

#: The sole terminal status that remains an active board column until payment settles. Naming it
#: here is deliberate: the §4 close predicate's payment gate applies to exactly this status, and it
#: is what separates a board column from a Close-flow outcome among the terminal statuses.
DELIVERED_STATUS = "delivered"


@dataclass(frozen=True)
class Stage:
    """One pipeline stage as the funnel sees it, projected from an ``opportunity_statuses`` row."""

    short_name: str
    label: str
    sort_order: int
    is_terminal: bool


def is_board_stage(status_short_name: str, status_is_terminal: bool) -> bool:
    """Return whether a status is a board column.

    A status is a column exactly when an *open* opportunity can occupy it: every non-terminal
    status, plus ``delivered`` (terminal but payment-gated, so a delivered-but-unpaid gig stays on
    the board). ``cancelled`` and ``lost`` close on entry, so they are never columns.

    Parameters
    ----------
    status_short_name : str
        The status ``short_name``.
    status_is_terminal : bool
        The status's ``is_terminal`` flag.

    Returns
    -------
    bool
        True iff the status should appear as a board column / is reachable by a board drag.
    """
    return (not status_is_terminal) or status_short_name == DELIVERED_STATUS


def is_close_status(status_short_name: str, status_is_terminal: bool) -> bool:
    """Return whether a status is reached through the Close flow (cancelled / lost).

    These are the terminal statuses other than ``delivered``: they close the opportunity
    immediately, are never board columns, and require a reason note (acceptance #8), so they are set
    via ``POST /{id}/close`` rather than a board drag.

    Parameters
    ----------
    status_short_name : str
        The status ``short_name``.
    status_is_terminal : bool
        The status's ``is_terminal`` flag.

    Returns
    -------
    bool
        True iff the status is a Close-flow outcome.
    """
    return status_is_terminal and status_short_name != DELIVERED_STATUS


def build_funnel(statuses: Iterable[Stage]) -> list[Stage]:
    """Return the board columns in display order.

    The columns are the board stages (:func:`is_board_stage`) sorted ascending by ``sort_order``.
    This is the server-owned stage order and labels the SPA renders (acceptance #9).

    Parameters
    ----------
    statuses : Iterable[Stage]
        The ``opportunity_statuses`` catalog projected to :class:`Stage` values.

    Returns
    -------
    list[Stage]
        Board-column stages, ascending ``sort_order``.
    """
    columns = [s for s in statuses if is_board_stage(s.short_name, s.is_terminal)]
    return sorted(columns, key=lambda s: s.sort_order)


def reached_or_beyond(max_reached_sort_order: int, stage_sort_order: int) -> bool:
    """Return whether an opportunity counts toward a funnel stage, reached-or-beyond.

    A gig counts toward a stage if the furthest status it has ever reached is at or past that
    stage's ``sort_order`` (DATABASE.md §3). This is why a cancelled gig (sort 80) still counts as
    Booked (sort 50) — it was booked, then fell through — and why a booked-only gig (50) does not
    yet count as Delivered (60), which is the visible Booked→Delivered leak (acceptance #6).

    Parameters
    ----------
    max_reached_sort_order : int
        The highest ``opportunity_statuses.sort_order`` the opportunity has ever reached.
    stage_sort_order : int
        The ``sort_order`` of the funnel stage being counted.

    Returns
    -------
    bool
        True iff ``max_reached_sort_order >= stage_sort_order``.
    """
    return max_reached_sort_order >= stage_sort_order
