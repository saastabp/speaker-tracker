"""Opportunity lifecycle rules — the ``closed_at`` predicate and status-move validity.

Pure domain logic, no I/O. These rules are the risk centre of slice 3: they decide when an
opportunity leaves the active board for History and when a status drag is a real move. They are
written and unit-tested with no database or HTTP so the invariants are pinned before any repo or
handler wires them up (DEV-PLAN slice 3 acceptance #1/#3/#5/#7).

The API keeps ``opportunities.current_status_id`` and ``opportunities.closed_at`` denormalized and
in sync using these rules; ``closed_at`` is never recomputed on read (DATABASE.md §4).
"""

from __future__ import annotations

from core.funnel import DELIVERED_STATUS


def is_closed(status_short_name: str, status_is_terminal: bool, payment_is_settled: bool) -> bool:
    """Return whether an opportunity is closed (``closed_at`` should be set).

    The §4 predicate::

        closed  ⇔  (delivered AND payment settled) ∨ cancelled ∨ lost

    The payment gate applies **only to** ``delivered``: a delivered-but-unpaid gig stays on the
    active board so it is not lost before Donna collects (acceptance #4), and correcting a payment
    back off ``paid`` clears ``closed_at`` and returns the card (acceptance #5). Cancelled and lost
    close immediately — there is nothing to collect. Non-terminal statuses (including ``nurture``)
    never close (acceptance #7).

    Parameters
    ----------
    status_short_name : str
        The opportunity's current status ``short_name``.
    status_is_terminal : bool
        That status's ``is_terminal`` flag.
    payment_is_settled : bool
        The opportunity's payment status ``is_settled`` flag.

    Returns
    -------
    bool
        True iff the opportunity is closed and belongs in History.
    """
    if not status_is_terminal:
        return False
    if status_short_name == DELIVERED_STATUS:
        return payment_is_settled
    return True


def is_real_move(current_status_short_name: str, target_status_short_name: str) -> bool:
    """Return whether a status change is a real move that should journal a ``status_events`` row.

    A drag onto the column the card already sits in is a no-op: no event is written and nothing
    changes (acceptance #1). Any change to a different status is a real move.

    Parameters
    ----------
    current_status_short_name : str
        The opportunity's current status ``short_name``.
    target_status_short_name : str
        The requested target status ``short_name``.

    Returns
    -------
    bool
        True iff ``target`` differs from ``current``.
    """
    return current_status_short_name != target_status_short_name


#: The payment status a new opportunity starts in, by comp type (DATABASE.md §"is_settled"). Paid
#: gigs begin ``unbilled`` — money is owed once delivered; ``pro_bono`` and ``trade`` have nothing
#: to collect, so they begin settled at ``n_a`` (and a delivered pro-bono gig closes immediately).
_INITIAL_PAYMENT_STATUS = {
    "paid": "unbilled",
    "pro_bono": "n_a",
    "trade": "n_a",
}


def initial_payment_status(comp_type_short_name: str) -> str:
    """Return the payment-status short_name a new opportunity starts in for a comp type.

    Parameters
    ----------
    comp_type_short_name : str
        A ``comp_types`` short_name (paid, pro_bono, trade).

    Returns
    -------
    str
        The initial ``payment_statuses`` short_name: ``unbilled`` for paid, ``n_a`` for pro bono /
        trade. Falls back to ``unbilled`` (unsettled — the card stays on the board) for an
        unrecognized comp type, so a mis-mapped type can never silently close a gig.
    """
    return _INITIAL_PAYMENT_STATUS.get(comp_type_short_name, "unbilled")
