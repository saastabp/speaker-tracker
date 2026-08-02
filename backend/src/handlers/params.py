"""Request-parameter parsing helpers shared by the route modules.

Presentation-layer utilities that turn raw path/query strings into validated values, raising the
domain errors ``common/http.py`` maps to status codes. Kept out of ``context.py`` (which is only
the auth/connection composition root) so each concern stays focused.
"""

from __future__ import annotations

from datetime import date

from common import errors


def path_int(value: str, name: str = "id") -> int:
    """Parse a path parameter as an integer, mapping a malformed value to 404.

    Parameters
    ----------
    value : str
        The raw path-parameter string from the router.
    name : str, optional
        Field name used in the error message (default ``"id"``).

    Returns
    -------
    int
        The parsed integer.

    Raises
    ------
    common.errors.NotFound
        When ``value`` is not a valid integer — a malformed id cannot name an existing row, so it
        maps to 404 rather than surfacing a distinct error shape.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        raise errors.NotFound(f"invalid {name}") from None


def query_date(value: str | None, name: str) -> date | None:
    """Parse an optional ISO ``YYYY-MM-DD`` query parameter.

    Parameters
    ----------
    value : str or None
        The raw query-string value; ``None`` or empty means the filter was not supplied.
    name : str
        Field name used in the error message.

    Returns
    -------
    datetime.date or None
        The parsed date, or None when absent.

    Raises
    ------
    common.errors.InvalidInput
        When present but unparseable — 400 rather than silently ignoring it. A dropped date widens
        the window, and the caller gets a longer list than it asked for with nothing to say why.

    Examples
    --------
    >>> query_date("2026-08-02", "entered_from")
    datetime.date(2026, 8, 2)
    >>> query_date(None, "entered_from") is None
    True
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise errors.InvalidInput(f"invalid {name}; expected YYYY-MM-DD") from None
