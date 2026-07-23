"""Unit tests for the pipeline stage taxonomy and server-owned funnel — pure, no database.

Cover the board-column classification, the ordered funnel the SPA renders (acceptance #9), and the
reached-or-beyond rule behind the funnel ratios (#6).
"""

from __future__ import annotations

import pytest

from core.funnel import (
    Stage,
    build_funnel,
    is_board_stage,
    is_close_status,
    reached_or_beyond,
)

# The full opportunity_statuses catalog as seeded in 0001 (short_name, label, sort_order, terminal).
ALL_STATUSES = [
    Stage("researching", "Researching", 10, False),
    Stage("outreach_sent", "Outreach Sent", 20, False),
    Stage("in_conversation", "In Conversation", 30, False),
    Stage("pitched", "Pitched", 40, False),
    Stage("booked", "Booked", 50, False),
    Stage("delivered", "Delivered", 60, True),
    Stage("nurture", "Nurture", 70, False),
    Stage("cancelled", "Cancelled", 80, True),
    Stage("lost", "Lost / Passed", 90, True),
]


@pytest.mark.parametrize(
    ("short_name", "is_terminal"),
    [
        ("researching", False),
        ("booked", False),
        ("nurture", False),
        ("delivered", True),  # terminal but payment-gated: still a board column
    ],
)
def test_board_stages(short_name: str, is_terminal: bool) -> None:
    assert is_board_stage(short_name, is_terminal) is True
    assert is_close_status(short_name, is_terminal) is False


@pytest.mark.parametrize("short_name", ["cancelled", "lost"])
def test_close_flow_statuses(short_name: str) -> None:
    assert is_board_stage(short_name, status_is_terminal=True) is False
    assert is_close_status(short_name, status_is_terminal=True) is True


def test_build_funnel_returns_board_columns_in_order() -> None:
    columns = [s.short_name for s in build_funnel(ALL_STATUSES)]
    # cancelled/lost excluded; delivered and nurture kept; ascending sort_order.
    assert columns == [
        "researching",
        "outreach_sent",
        "in_conversation",
        "pitched",
        "booked",
        "delivered",
        "nurture",
    ]


def test_build_funnel_sorts_unordered_input() -> None:
    shuffled = [ALL_STATUSES[4], ALL_STATUSES[0], ALL_STATUSES[6], ALL_STATUSES[2]]
    assert [s.sort_order for s in build_funnel(shuffled)] == [10, 30, 50, 70]


def test_cancelled_counts_as_booked() -> None:
    # #6: a cancelled gig (80) still counts toward Booked (50) in the funnel.
    assert reached_or_beyond(max_reached_sort_order=80, stage_sort_order=50) is True


def test_booked_only_has_not_reached_delivered() -> None:
    # #6: the visible Booked(50)→Delivered(60) leak.
    assert reached_or_beyond(max_reached_sort_order=50, stage_sort_order=60) is False


def test_reached_or_beyond_is_inclusive_at_the_boundary() -> None:
    assert reached_or_beyond(max_reached_sort_order=50, stage_sort_order=50) is True
