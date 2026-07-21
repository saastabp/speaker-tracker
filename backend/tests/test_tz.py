"""Tests for ``common.tz`` timezone validation and header extraction.

No database required. These pin the properties date-bucketed metrics depend on:

- a valid IANA zone passes through unchanged;
- a missing value (``None``/``""``) falls back to the default **without** a warning — that is
  the normal no-header case, not an error;
- an invalid or malformed value (including a path-traversal attempt) falls back **with** a
  WARNING, so monitoring sees a genuine bad value (the silent-fallbacks-log-WARNING rule);
- ``timezone_from_event`` matches the ``X-User-Timezone`` header case-insensitively.

The Powertools logger is swapped for a mock (``tz.logger``) so warnings can be asserted without
depending on log-propagation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from common import tz


@pytest.fixture
def spy_logger(monkeypatch):
    """Replace ``tz.logger`` with a mock and return it for warning assertions."""
    logger = MagicMock(name="logger")
    monkeypatch.setattr(tz, "logger", logger)
    return logger


@pytest.mark.parametrize(
    "name",
    ["Pacific/Honolulu", "America/New_York", "Europe/London", "UTC", "Asia/Tokyo"],
)
def test_valid_timezone_passes_through(name: str, spy_logger) -> None:
    assert tz.validate_timezone(name) == name
    spy_logger.warning.assert_not_called()


@pytest.mark.parametrize("name", [None, ""])
def test_missing_timezone_defaults_without_warning(name, spy_logger) -> None:
    assert tz.validate_timezone(name) == tz.DEFAULT_TIMEZONE
    spy_logger.warning.assert_not_called()  # no header is normal, not a bad value


@pytest.mark.parametrize(
    "name",
    ["Not/AZone", "Mars/Phobos", "garbage", "../../etc/passwd", "Pacific/Honolulu/nope"],
)
def test_invalid_timezone_defaults_with_warning(name: str, spy_logger) -> None:
    assert tz.validate_timezone(name) == tz.DEFAULT_TIMEZONE
    spy_logger.warning.assert_called_once()


@pytest.mark.parametrize(
    ("header_key", "value"),
    [
        ("X-User-Timezone", "America/New_York"),  # mixed case
        ("x-user-timezone", "Europe/London"),  # lower case
        ("X-USER-TIMEZONE", "Asia/Tokyo"),  # upper case
    ],
)
def test_timezone_from_event_extracts_header_case_insensitively(
    header_key: str, value: str
) -> None:
    assert tz.timezone_from_event({"headers": {header_key: value}}) == value


@pytest.mark.parametrize(
    "event",
    [{}, {"headers": None}, {"headers": {}}, {"headers": {"other": "v"}}],
)
def test_timezone_from_event_missing_header_defaults(event: dict) -> None:
    assert tz.timezone_from_event(event) == tz.DEFAULT_TIMEZONE


def test_timezone_from_event_invalid_header_defaults_with_warning(spy_logger) -> None:
    event = {"headers": {"X-User-Timezone": "Nope/Nowhere"}}
    assert tz.timezone_from_event(event) == tz.DEFAULT_TIMEZONE
    spy_logger.warning.assert_called_once()
