"""Unit tests for outreach kind inference — pure, no database.

Pin the defaulting rule behind the editable kind chip (DEV-PLAN slice 4 acceptance #1/#2): the
first outbound touch to a contact defaults to ``initial``, later touches to ``correspondence``, and
an explicit override always wins so a correction persists.
"""

from __future__ import annotations

import pytest

from core.outreach import (
    CORRESPONDENCE_KIND,
    INITIAL_KIND,
    infer_outreach_kind,
    resolve_outreach_kind,
)


def test_first_touch_infers_initial() -> None:
    # No prior outbound touch → the opening touch to a contact is 'initial' (acceptance #1).
    assert infer_outreach_kind(False) == INITIAL_KIND


def test_later_touch_infers_correspondence() -> None:
    # A prior outbound touch exists → the default is 'correspondence', which does not count toward
    # the outreaches target (acceptance #2).
    assert infer_outreach_kind(True) == CORRESPONDENCE_KIND


def test_resolve_uses_inference_when_no_override() -> None:
    assert resolve_outreach_kind(False) is not None
    assert resolve_outreach_kind(False) == INITIAL_KIND
    assert resolve_outreach_kind(True) == CORRESPONDENCE_KIND


@pytest.mark.parametrize("override", ["follow_up", "initial", "correspondence"])
def test_override_always_wins(override: str) -> None:
    # The editable chip's override persists regardless of what would have been inferred (acceptance
    # #1) — including follow_up, which is never inferred.
    assert resolve_outreach_kind(False, override) == override
    assert resolve_outreach_kind(True, override) == override


def test_follow_up_is_never_inferred() -> None:
    # follow_up is a prospecting re-touch reachable only by override, never by the default rule.
    assert infer_outreach_kind(False) != "follow_up"
    assert infer_outreach_kind(True) != "follow_up"


def test_empty_string_override_is_still_an_override() -> None:
    # Only None means "accept the default"; a passed value (even falsy) is treated as an explicit
    # choice, so the None-sentinel contract is unambiguous for callers.
    assert resolve_outreach_kind(False, "") == ""
