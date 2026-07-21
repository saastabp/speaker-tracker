"""Tests for the migrate Lambda handler wiring — no database required.

Verifies the handler opens a *dedicated* connection, delegates to the runner with the bundled
migrations directory, closes the connection even on failure, and re-raises so a broken
migration fails the invoking deploy.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from handlers import migrate
from migrations.runner import MigrationExecutionError, MigrationRunResult


def _context() -> SimpleNamespace:
    """A minimal stand-in for the Lambda context Powertools reads."""
    return SimpleNamespace(
        function_name="migrate",
        memory_limit_in_mb=512,
        invoked_function_arn="arn:aws:lambda:us-west-2:381492047863:function:migrate",
        aws_request_id="req-test-123",
        get_remaining_time_in_millis=lambda: 300_000,
    )


def test_applies_migrations_and_returns_summary(monkeypatch) -> None:
    conn = MagicMock(name="dedicated_conn")
    monkeypatch.setattr(migrate.db, "open_dedicated_connection", lambda: conn)

    captured: dict = {}

    def fake_run(connection, migrations_dir):
        captured["connection"] = connection
        captured["migrations_dir"] = migrations_dir
        return MigrationRunResult(applied=["0001"], skipped=[])

    monkeypatch.setattr(migrate.runner, "run_migrations", fake_run)

    result = migrate.lambda_handler({}, _context())

    assert result == {"applied": ["0001"], "skipped": []}
    assert captured["connection"] is conn  # the dedicated connection, not the API's cached one
    assert captured["migrations_dir"] == migrate.MIGRATIONS_DIR
    conn.close.assert_called_once()


def test_reraises_and_closes_on_failure(monkeypatch) -> None:
    conn = MagicMock(name="dedicated_conn")
    monkeypatch.setattr(migrate.db, "open_dedicated_connection", lambda: conn)

    def boom(connection, migrations_dir):
        raise MigrationExecutionError("statement 2 failed")

    monkeypatch.setattr(migrate.runner, "run_migrations", boom)

    with pytest.raises(MigrationExecutionError):
        migrate.lambda_handler({}, _context())

    conn.close.assert_called_once()  # closed in finally despite the failure
