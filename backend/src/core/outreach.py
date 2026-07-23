"""Outreach kind inference — pure domain logic, no I/O.

The Log-outreach composer shows the outreach *kind* as an editable chip whose default is inferred
from the contact's touch history (DEV-PLAN slice 4 acceptance #1, DATABASE.md §5):

- the **first** outbound touch to a contact infers ``initial``;
- any **later** outbound touch infers ``correspondence`` (logistics / admin on an existing
  conversation) — deliberately *not* counted toward the outreaches target, so a long thread with an
  already-booked venue never inflates the metric (``outreach_kinds.counts_toward_target``).

``follow_up`` is never inferred. It is a *prospecting* re-touch — a genuine second pitch to a cold
contact — and is only reachable when Donna overrides the chip. The override then persists as the
row's kind (acceptance #1). This module owns only the defaulting rule; whether a prior touch exists
(non-deleted, outbound) is a repository query, and validating an override against the
``outreach_kinds`` catalog is the repository's job — core hardcodes no catalog membership beyond the
two canonical short_names it must name to express the rule.
"""

from __future__ import annotations

#: Kind for the first outbound touch to a contact — counts toward the outreaches target.
INITIAL_KIND = "initial"

#: Kind for a subsequent outbound touch inferred by default — admin/logistics, does NOT count
#: toward the target (``outreach_kinds.counts_toward_target = FALSE``, acceptance #2).
CORRESPONDENCE_KIND = "correspondence"


def infer_outreach_kind(has_prior_outbound_touch: bool) -> str:
    """Return the default outreach kind short_name for a new touch to a contact.

    Parameters
    ----------
    has_prior_outbound_touch : bool
        Whether a non-deleted, outbound outreach to this contact already exists. The repository
        determines this (``WHERE contact_id = ... AND deleted_at IS NULL``); this function is pure.

    Returns
    -------
    str
        ``initial`` when there is no prior outbound touch, else ``correspondence``.

    Examples
    --------
    >>> infer_outreach_kind(False)
    'initial'
    >>> infer_outreach_kind(True)
    'correspondence'
    """
    return CORRESPONDENCE_KIND if has_prior_outbound_touch else INITIAL_KIND


def resolve_outreach_kind(
    has_prior_outbound_touch: bool, override_short_name: str | None = None
) -> str:
    """Return the kind to persist for a new touch: the override if given, else the inferred default.

    An explicit ``override_short_name`` always wins — this is how the editable chip persists a
    correction (e.g. a re-pitch to a cold contact corrected to ``follow_up``, acceptance #1). The
    caller is responsible for having validated the override against the ``outreach_kinds`` catalog;
    this function does not, so it stays free of catalog knowledge beyond the two names it infers.

    Parameters
    ----------
    has_prior_outbound_touch : bool
        Whether a non-deleted, outbound outreach to this contact already exists.
    override_short_name : str or None, optional
        A user-chosen ``outreach_kinds`` short_name from the editable chip, or ``None`` to accept
        the inferred default.

    Returns
    -------
    str
        ``override_short_name`` when it is not ``None``, otherwise the inferred default from
        :func:`infer_outreach_kind`.

    Examples
    --------
    >>> resolve_outreach_kind(False)
    'initial'
    >>> resolve_outreach_kind(True, "follow_up")
    'follow_up'
    """
    if override_short_name is not None:
        return override_short_name
    return infer_outreach_kind(has_prior_outbound_touch)
