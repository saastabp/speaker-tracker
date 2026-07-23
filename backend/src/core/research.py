"""Research-readiness rule for organizations — pure domain logic, no I/O.

An organization is *outreach-ready* only when all three Kindling research fields are filled AND it
has at least one affiliated contact (DESIGN §5 / DEV-PLAN slice 2 acceptance #4). This is the
quality bar for the "new venues researched" target, so it must be computed identically wherever it
is shown; keeping it here — pure, unit-tested with no database — is what guarantees that.
"""

from __future__ import annotations


def _is_filled(value: str | None) -> bool:
    """Return True if a research field has non-whitespace content."""
    return value is not None and value.strip() != ""


def is_research_ready(
    what_it_is: str | None,
    why_it_fits: str | None,
    how_to_approach: str | None,
    contact_count: int,
) -> bool:
    """Return whether an organization is outreach-ready.

    Parameters
    ----------
    what_it_is, why_it_fits, how_to_approach : str or None
        The three Kindling research fields.
    contact_count : int
        Number of non-deleted affiliated contacts.

    Returns
    -------
    bool
        True iff all three research fields are non-empty and ``contact_count >= 1``.
    """
    kindling_complete = all(_is_filled(v) for v in (what_it_is, why_it_fits, how_to_approach))
    return kindling_complete and contact_count >= 1
