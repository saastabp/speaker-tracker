"""Unit tests for IMAP poll watermarks — pure, no database and no IMAP.

DEV-PLAN slice 6b acceptance #6: changing a folder's ``UIDVALIDITY`` must reset the cursor rather
than skipping mail or re-importing it. These tests pin all three cursor cases, including the first
poll — the quieter hazard, where a floor of 0 would ingest Donna's entire mailbox on deploy.
"""

from __future__ import annotations

import pytest

from core.imap_cursor import (
    CURSOR_FIRST_POLL,
    CURSOR_RESUME,
    CURSOR_UIDVALIDITY_CHANGED,
    MAX_UIDS_PER_POLL,
    cap_uids,
    plan_cursor,
)

# --- First poll: baseline, never a full-mailbox import ---------------------------------------


def test_first_poll_baselines_at_the_current_uidnext() -> None:
    plan = plan_cursor(
        stored_uid_validity=None,
        stored_last_seen_uid=None,
        server_uid_validity=42,
        server_uid_next=901,
    )

    assert plan == (900, True, CURSOR_FIRST_POLL)


def test_first_poll_ingests_nothing_from_a_full_folder() -> None:
    # The point of baselining, end to end: a mailbox with 900 existing messages yields zero to
    # process. Starting at 0 here would import years of unrelated personal mail on first deploy.
    plan = plan_cursor(
        stored_uid_validity=None,
        stored_last_seen_uid=None,
        server_uid_validity=42,
        server_uid_next=901,
    )

    assert cap_uids(range(1, 901), plan.floor_uid) == []


def test_the_next_message_to_arrive_after_a_baseline_is_processed() -> None:
    # Baselining must not skip the *next* message too — an off-by-one here would stall the folder
    # forever with no error anywhere.
    plan = plan_cursor(
        stored_uid_validity=None,
        stored_last_seen_uid=None,
        server_uid_validity=42,
        server_uid_next=901,
    )

    assert cap_uids([901], plan.floor_uid) == [901]


def test_first_poll_of_an_empty_folder_floors_at_zero() -> None:
    # The Import folder the poller just created: UIDNEXT is 1 and there is nothing to skip.
    plan = plan_cursor(
        stored_uid_validity=None,
        stored_last_seen_uid=None,
        server_uid_validity=42,
        server_uid_next=1,
    )

    assert plan.floor_uid == 0
    assert plan.baseline is True


@pytest.mark.parametrize("uid_next", [None, 0])
def test_missing_uidnext_floors_at_zero_rather_than_crashing(uid_next: int | None) -> None:
    # A server that omits UIDNEXT should degrade to reading the folder, not take the poll down.
    plan = plan_cursor(
        stored_uid_validity=None,
        stored_last_seen_uid=None,
        server_uid_validity=42,
        server_uid_next=uid_next,
    )

    assert plan.floor_uid == 0


# --- UIDVALIDITY change: reset, and let the unique key absorb the rescan ----------------------


def test_changed_uidvalidity_resets_the_floor_to_zero() -> None:
    # The stored UIDs now name different messages, or none. Rescanning re-imports nothing, because
    # UNIQUE(user_id, message_id) makes ingest idempotent — that is how #6 avoids having to choose
    # between skipping and duplicating.
    plan = plan_cursor(
        stored_uid_validity=42,
        stored_last_seen_uid=900,
        server_uid_validity=43,
        server_uid_next=901,
    )

    assert plan == (0, False, CURSOR_UIDVALIDITY_CHANGED)


def test_a_reset_is_not_reported_as_a_baseline() -> None:
    # Distinct states: a baseline deliberately ingests nothing, a reset deliberately re-reads.
    # Conflating them would make a folder recreation silently skip every message in it.
    plan = plan_cursor(
        stored_uid_validity=42,
        stored_last_seen_uid=900,
        server_uid_validity=43,
        server_uid_next=901,
    )

    assert plan.baseline is False
    assert cap_uids([1, 2, 3], plan.floor_uid) == [1, 2, 3]


def test_uidvalidity_dropping_to_a_lower_value_also_resets() -> None:
    # UIDVALIDITY is only required to differ, not to increase; a "> stored" comparison would miss
    # a rebuilt folder that came back with a smaller value.
    plan = plan_cursor(
        stored_uid_validity=99,
        stored_last_seen_uid=900,
        server_uid_validity=7,
        server_uid_next=901,
    )

    assert plan.reason == CURSOR_UIDVALIDITY_CHANGED


# --- Steady state ------------------------------------------------------------------------------


def test_unchanged_uidvalidity_resumes_from_the_watermark() -> None:
    plan = plan_cursor(
        stored_uid_validity=42,
        stored_last_seen_uid=900,
        server_uid_validity=42,
        server_uid_next=901,
    )

    assert plan == (900, False, CURSOR_RESUME)


def test_quiet_poll_finds_nothing_above_the_watermark() -> None:
    # The ordinary case, and why a one-minute interval is cheap.
    plan = plan_cursor(
        stored_uid_validity=42,
        stored_last_seen_uid=900,
        server_uid_validity=42,
        server_uid_next=901,
    )

    assert cap_uids([], plan.floor_uid) == []


@pytest.mark.parametrize("stored_uid", [None, 0])
def test_missing_watermark_resumes_from_zero(stored_uid: int | None) -> None:
    # A cursor row written by a baseline that then crashed before recording a UID.
    plan = plan_cursor(
        stored_uid_validity=42,
        stored_last_seen_uid=stored_uid,
        server_uid_validity=42,
        server_uid_next=901,
    )

    assert plan.floor_uid == 0
    assert plan.reason == CURSOR_RESUME


def test_negative_stored_watermark_is_clamped() -> None:
    plan = plan_cursor(
        stored_uid_validity=42,
        stored_last_seen_uid=-5,
        server_uid_validity=42,
        server_uid_next=901,
    )

    assert plan.floor_uid == 0


# --- cap_uids ------------------------------------------------------------------------------


def test_uids_are_returned_in_ascending_order() -> None:
    # Load-bearing: the poller advances the cursor to the last UID it successfully processed, so a
    # mid-batch failure must leave the remainder above the watermark rather than stranded below it.
    assert cap_uids([903, 901, 902], 900) == [901, 902, 903]


def test_the_floor_itself_is_excluded() -> None:
    # Strictly greater — including it would re-process the last message on every poll.
    assert cap_uids([900, 901], 900) == [901]


def test_uids_below_the_floor_are_dropped() -> None:
    assert cap_uids([1, 500, 901], 900) == [901]


def test_a_large_backlog_is_capped_to_one_poll_s_worth() -> None:
    # After a UIDVALIDITY reset the rescan drains across several polls instead of timing out a
    # single 15s invocation and making no progress at all.
    selected = cap_uids(range(1, 1001), 0)

    assert len(selected) == MAX_UIDS_PER_POLL
    assert selected[0] == 1


def test_the_cap_takes_the_oldest_uids_first() -> None:
    # Draining from the oldest keeps the watermark contiguous; taking the newest would leave a hole
    # the cursor then advances past.
    assert cap_uids([905, 901, 903, 902], 900, cap=2) == [901, 902]


def test_empty_search_result_yields_nothing() -> None:
    assert cap_uids([], 900) == []
