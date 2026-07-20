"""HTTP response envelope and domain-exception → status mapping.

Presentation is the only layer that knows HTTP. Every success is **bare JSON** — each
handler names its own top-level keys, no ``{"data": ...}`` wrapper. Every failure is
``{"error": "<message>"}`` with the status decided centrally here. Two legacy bugs
(``ARCHITECTURE.md`` §1.1) are fixed by construction:

- **One error shape, always.** An unhandled exception becomes ``{"error": "internal error"}``
  + 500 after ``logger.exception`` — never API Gateway's default ``{"message": "..."}``.
- **Domain errors map explicitly.** ``NotFound`` (including the user lookup) is 404, never 500.

The status is resolved by an ordered ``isinstance`` walk in :func:`status_for`, **not** by
Powertools' exception-handler MRO precedence. ``app.py`` registers a single catch-all
``@app.exception_handler(Exception)`` that delegates to :func:`response_for_exception`, so
correctness does not depend on handler registration order.
"""

from __future__ import annotations

import json
from http import HTTPStatus

from aws_lambda_powertools.event_handler import Response, content_types
from aws_lambda_powertools.event_handler.exceptions import ServiceError
from pydantic import ValidationError as PydanticValidationError

from common import errors
from common.logger import logger

#: Ordered most-specific-first; the first ``isinstance`` match wins. Unmatched → 500.
_STATUS_RULES: list[tuple[type[Exception], int]] = [
    (errors.Unauthorized, 401),
    (errors.NotFound, 404),  # includes UserNotFoundError
    (errors.Conflict, 409),
    (errors.InvalidInput, 400),
    (PydanticValidationError, 400),
    (errors.DomainError, 400),  # any other domain error
]

#: Fixed body for 5xx — never leaks internal detail to the client.
INTERNAL_ERROR_MESSAGE = "internal error"

#: Generic 400 for request-model validation — avoids leaking pydantic model structure.
INVALID_REQUEST_MESSAGE = "invalid request"


def status_for(exc: Exception) -> int:
    """Return the HTTP status an exception maps to; 500 if it maps to nothing.

    Parameters
    ----------
    exc : Exception
        The exception raised by a route handler or the request-parsing layer.

    Returns
    -------
    int
        The mapped status code (404/409/400), or 500 for any unrecognized exception.
    """
    # Powertools' own framework exceptions (incl. the NotFoundError raised for an unmatched
    # route) subclass ServiceError and carry the correct status; honor it rather than letting
    # a route-not-found masquerade as 500.
    if isinstance(exc, ServiceError):
        return exc.status_code
    for cls, status in _STATUS_RULES:
        if isinstance(exc, cls):
            return status
    return 500


def error_envelope(message: str) -> dict:
    """Return the failure body ``{"error": message}``."""
    return {"error": message}


def json_response(status: int, body: dict) -> Response:
    """Build a Powertools JSON ``Response`` with ``status`` and a JSON-encoded ``body``."""
    return Response(
        status_code=status,
        content_type=content_types.APPLICATION_JSON,
        body=json.dumps(body),
    )


def response_for_exception(exc: Exception) -> Response:
    """Map any exception to a JSON error ``Response``, logging appropriately.

    5xx responses log a full traceback via ``logger.exception`` and return the fixed
    :data:`INTERNAL_ERROR_MESSAGE`. 4xx responses log at ``warning`` and return a client-safe
    message: a domain exception's own message, :data:`INVALID_REQUEST_MESSAGE` for a pydantic
    ``ValidationError``, or the standard reason phrase for a Powertools framework error (so an
    internal class name like ``NotFoundError`` never reaches the client).

    Parameters
    ----------
    exc : Exception
        The exception to translate into an HTTP error response.

    Returns
    -------
    aws_lambda_powertools.event_handler.Response
        The JSON error response with the mapped status and ``{"error": ...}`` body.
    """
    status = status_for(exc)
    if status >= 500:
        logger.exception("Unhandled exception -> 500")
        return json_response(500, error_envelope(INTERNAL_ERROR_MESSAGE))

    if isinstance(exc, PydanticValidationError):
        message = INVALID_REQUEST_MESSAGE
    elif isinstance(exc, errors.DomainError):
        # Our domain exceptions carry crafted, client-safe messages.
        message = str(exc) or type(exc).__name__
    else:
        # Powertools framework errors (e.g. NotFoundError) — use the standard reason phrase
        # for the status, never the internal class name.
        message = HTTPStatus(status).phrase.lower()
    logger.warning("Request failed: %s -> %d", type(exc).__name__, status)
    return json_response(status, error_envelope(message))
