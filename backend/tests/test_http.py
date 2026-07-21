"""Tests for ``common.http`` — exception -> (status, body) mapping (plan verification #3).

No database required. These pin three things the whole error contract depends on:

- the exception -> status table, including the **ordering** of the ``isinstance`` walk (a
  ``UserNotFoundError`` is both a ``NotFound`` and a ``DomainError``, and must resolve to 404,
  not 400 — the legacy bug where it surfaced as 500/400 is exactly what this guards);
- that internal detail never leaks: any unmapped exception is ``{"error": "internal error"}`` +
  500, a pydantic ``ValidationError`` is a generic ``{"error": "invalid request"}``, and a
  Powertools framework error uses the standard reason phrase, not its own message or class name;
- that a Powertools ``ServiceError`` (e.g. the unmatched-route ``NotFoundError``) keeps its own
  ``status_code`` rather than falling through to 500.
"""

from __future__ import annotations

import json
from http import HTTPStatus

import pytest
from aws_lambda_powertools.event_handler.exceptions import NotFoundError, ServiceError
from pydantic import BaseModel, ValidationError

from common import errors, http


class _Model(BaseModel):
    x: int


def _pydantic_error() -> ValidationError:
    """Return a real pydantic ``ValidationError`` for use as a fixture value."""
    try:
        _Model(x="not-an-int")
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")  # pragma: no cover


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (errors.Unauthorized(), 401),
        (errors.NotFound(), 404),
        (errors.UserNotFoundError(), 404),  # NotFound wins over DomainError via ordering, not 400
        (errors.Conflict(), 409),
        (errors.InvalidInput(), 400),
        (errors.DomainError(), 400),  # any other domain error
        (_pydantic_error(), 400),
        (NotFoundError(), 404),  # Powertools ServiceError -> its own status_code
        (ServiceError(HTTPStatus.IM_A_TEAPOT, "brewing"), 418),  # honours status_code
        (ValueError("x"), 500),
        (RuntimeError("x"), 500),
        (KeyError("x"), 500),
    ],
)
def test_status_for(exc: Exception, expected_status: int) -> None:
    assert http.status_for(exc) == expected_status


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_body"),
    [
        # Domain errors surface their own (crafted, client-safe) message.
        (errors.Unauthorized("no auth"), 401, {"error": "no auth"}),
        (errors.NotFound("missing"), 404, {"error": "missing"}),
        (errors.UserNotFoundError("no such user"), 404, {"error": "no such user"}),
        (errors.Conflict("dup"), 409, {"error": "dup"}),
        (errors.InvalidInput("bad field"), 400, {"error": "bad field"}),
        # Pydantic detail is never surfaced — generic message instead.
        (_pydantic_error(), 400, {"error": "invalid request"}),
        # Unmapped exceptions are 500 with a fixed body — the original message never leaks.
        (ValueError("boom"), 500, {"error": "internal error"}),
        (KeyError("k"), 500, {"error": "internal error"}),
        # Framework errors use the standard reason phrase, not the class name or their own msg.
        (NotFoundError(), 404, {"error": "not found"}),
        (ServiceError(HTTPStatus.CONFLICT, "secret detail"), 409, {"error": "conflict"}),
    ],
)
def test_response_for_exception(exc: Exception, expected_status: int, expected_body: dict) -> None:
    response = http.response_for_exception(exc)
    assert response.status_code == expected_status
    assert json.loads(response.body) == expected_body


def test_domain_error_without_message_falls_back_to_class_name() -> None:
    # A domain error is expected to carry a message; with none, the class name is the fallback
    # (never the empty string). Pinning current behaviour.
    response = http.response_for_exception(errors.NotFound())
    assert response.status_code == 404
    assert json.loads(response.body) == {"error": "NotFound"}
