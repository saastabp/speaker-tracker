"""Unit tests for the opportunity lifecycle rules — pure, no database.

These pin the slice-3 risk centre before any repo/handler wires them up: the ``closed_at``
predicate (acceptance #3/#4/#5/#7) and the one-event-per-real-move rule (#1).
"""

from __future__ import annotations

import pytest

from core.opportunities import initial_payment_status, is_closed, is_real_move

# Non-terminal statuses never close, regardless of payment settlement.
NON_TERMINAL = ["researching", "outreach_sent", "in_conversation", "pitched", "booked"]


@pytest.mark.parametrize("status", NON_TERMINAL)
@pytest.mark.parametrize("settled", [True, False])
def test_non_terminal_never_closes(status: str, settled: bool) -> None:
    # A non-terminal status stays on the board even if payment happens to be settled.
    assert is_closed(status, status_is_terminal=False, payment_is_settled=settled) is False


def test_delivered_and_settled_closes() -> None:
    # #3: (delivered AND settled) closes.
    assert is_closed("delivered", status_is_terminal=True, payment_is_settled=True) is True


def test_delivered_but_unpaid_stays_open() -> None:
    # #4/#5: delivered-but-unpaid stays on the board; clearing payment re-opens it.
    assert is_closed("delivered", status_is_terminal=True, payment_is_settled=False) is False


@pytest.mark.parametrize("status", ["cancelled", "lost"])
@pytest.mark.parametrize("settled", [True, False])
def test_cancelled_and_lost_close_unconditionally(status: str, settled: bool) -> None:
    # #3: cancelled/lost close immediately — the payment gate applies only to delivered.
    assert is_closed(status, status_is_terminal=True, payment_is_settled=settled) is True


def test_same_status_is_not_a_real_move() -> None:
    # #1: a drag onto the current column writes no status_events row.
    assert is_real_move("booked", "booked") is False


def test_different_status_is_a_real_move() -> None:
    assert is_real_move("pitched", "booked") is True


@pytest.mark.parametrize(
    ("comp_type", "expected"),
    [("paid", "unbilled"), ("pro_bono", "n_a"), ("trade", "n_a")],
)
def test_initial_payment_status(comp_type: str, expected: str) -> None:
    # Paid gigs start billable; pro bono / trade have nothing to collect, so they start settled.
    assert initial_payment_status(comp_type) == expected
