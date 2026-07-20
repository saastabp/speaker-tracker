"""Domain exception hierarchy, mapped to HTTP responses by ``common/http.py``.

Core and repositories raise these; the exceptions carry **no HTTP concerns**. Presentation
(``common/http.py``) is the single layer that knows the status-code mapping. This is what
keeps the legacy bug from recurring: there, ``UserNotFoundError`` subclassed ``LookupError``,
fell into an unhandled re-raise branch, and surfaced as **500 instead of 404**. Here every
domain error descends from :class:`DomainError` and maps explicitly.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for domain errors that ``common/http.py`` maps to an HTTP response."""


class Unauthorized(DomainError):
    """The caller is not authenticated (maps to HTTP 401).

    Aligns with the frontend contract: ``useApi()`` treats 401 as an auth event and
    triggers ``signinRedirect()``.
    """


class NotFound(DomainError):
    """A requested entity does not exist (maps to HTTP 404)."""


class Conflict(DomainError):
    """The request conflicts with current state, e.g. a uniqueness violation (HTTP 409)."""


class InvalidInput(DomainError):
    """The request is well-formed but semantically invalid (HTTP 400).

    Named ``InvalidInput`` rather than ``ValidationError`` to avoid colliding with
    :class:`pydantic.ValidationError`, which ``common/http.py`` maps to 400 separately.
    """


class UserNotFoundError(NotFound):
    """The authenticated principal has no ``users`` row (HTTP 404, never 500)."""
