"""Request-parameter parsing helpers shared by the route modules.

Presentation-layer utilities that turn raw path/query strings into validated values, raising the
domain errors ``common/http.py`` maps to status codes. Kept out of ``context.py`` (which is only
the auth/connection composition root) so each concern stays focused.
"""

from __future__ import annotations

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
