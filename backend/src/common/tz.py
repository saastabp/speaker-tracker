"""Timezone resolution for date-bucketed queries.

Every data request carries the caller's IANA timezone in the ``X-User-Timezone`` header so
that ``CURDATE()`` and other date functions evaluate in the caller's local time. Kauaʻi is
**UTC-10**, so "today" rolls over ten hours after UTC; getting this wrong silently
mis-buckets every metric (see ``DEV-PLAN.md`` slice 5 acceptance #1).

Validation here is defense in depth: the name is validated against the IANA database before
it reaches ``db.get_connection``, which also passes it to ``SET time_zone`` as a bound
parameter. MySQL's own ``time_zone_name`` table (confirmed populated) is what actually
resolves the zone; Python's :mod:`zoneinfo` (system tzdata, present on the Lambda AL2023
runtime, CI, and dev) is the proxy used to reject unknown or malformed input here.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from common.logger import logger

#: Fallback when the client sends no or an invalid timezone — Kauaʻi, the end user's zone.
DEFAULT_TIMEZONE = "Pacific/Honolulu"

_HEADER = "x-user-timezone"


def validate_timezone(name: str | None) -> str:
    """Return ``name`` if it is a known IANA timezone, else :data:`DEFAULT_TIMEZONE`.

    Parameters
    ----------
    name : str or None
        A candidate IANA timezone name from an untrusted client header.

    Returns
    -------
    str
        ``name`` when it resolves against the IANA database, otherwise the default.
        A malformed value (including path-traversal attempts) can never propagate.
    """
    if not name:
        return DEFAULT_TIMEZONE
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # A non-empty but unrecognized zone is an actual bad value (not the normal
        # "no header" case), so warn — monitoring keys off WARNING, not INFO.
        logger.warning("Unknown timezone %r from client; defaulting to %s", name, DEFAULT_TIMEZONE)
        return DEFAULT_TIMEZONE
    return name


def timezone_from_event(event: dict) -> str:
    """Extract and validate the caller's timezone from an API Gateway HTTP event.

    Parameters
    ----------
    event : dict
        An API Gateway HTTP API v2 event.

    Returns
    -------
    str
        A valid IANA timezone name safe to pass to ``db.get_connection``.
    """
    headers = event.get("headers") or {}
    # HTTP API v2 lowercases header keys; iterate case-insensitively to be defensive.
    raw = next((v for k, v in headers.items() if k.lower() == _HEADER), None)
    return validate_timezone(raw)
