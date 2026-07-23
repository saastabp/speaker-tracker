"""Unit tests for the research-readiness rule — pure, no database.

An organization is outreach-ready iff all three Kindling fields are non-empty AND it has at least
one affiliated contact (DEV-PLAN slice 2 acceptance #4).
"""

from __future__ import annotations

import pytest

from core.research import is_research_ready


def test_ready_when_all_kindling_filled_and_a_contact() -> None:
    assert is_research_ready("what it is", "why it fits", "how to approach", 1) is True


def test_not_ready_without_a_contact() -> None:
    assert is_research_ready("what it is", "why it fits", "how to approach", 0) is False


@pytest.mark.parametrize(
    ("what_it_is", "why_it_fits", "how_to_approach"),
    [
        (None, "why", "how"),
        ("what", None, "how"),
        ("what", "why", None),
        ("", "why", "how"),
        ("what", "   ", "how"),  # whitespace-only counts as unfilled
    ],
)
def test_not_ready_with_missing_or_blank_kindling(
    what_it_is: str | None, why_it_fits: str | None, how_to_approach: str | None
) -> None:
    assert is_research_ready(what_it_is, why_it_fits, how_to_approach, 3) is False
