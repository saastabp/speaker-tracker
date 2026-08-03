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


def research_ready_sql(alias: str = "o") -> str:
    """Return the SQL predicate equivalent to :func:`is_research_ready`, for an aliased join.

    Parameters
    ----------
    alias : str, optional
        The table alias the ``organizations`` row carries in the caller's query.

    Returns
    -------
    str
        A boolean SQL expression, safe to concatenate into a ``WHERE`` clause — it interpolates
        only ``alias``, which is caller-supplied code and never request data.

    Notes
    -----
    Deliberately adjacent to the Python rule rather than living in whichever repository needed it
    first. Two spellings of one rule cannot be avoided — the dashboard has to count in SQL, the
    handlers have to answer per-row in Python — but they can at least sit where changing one
    without the other is obvious.

    Examples
    --------
    >>> "TRIM" in research_ready_sql("org")
    True
    """
    return (
        f"TRIM(COALESCE({alias}.what_it_is, '')) <> '' "
        f"AND TRIM(COALESCE({alias}.why_it_fits, '')) <> '' "
        f"AND TRIM(COALESCE({alias}.how_to_approach, '')) <> '' "
        f"AND EXISTS (SELECT 1 FROM contact_organizations co "
        f"            JOIN contacts c ON c.id = co.contact_id AND c.deleted_at IS NULL "
        f"            WHERE co.organization_id = {alias}.id)"
    )
