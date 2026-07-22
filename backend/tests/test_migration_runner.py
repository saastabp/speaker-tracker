"""Tests for the forward-only migration runner — the highest-value code in slice 1.

The pure cases (checksum normalisation, statement splitting, banned constructs) need no
database and run everywhere. The DB-backed cases apply the real ``0001_initial.sql`` against a
clean MySQL and assert the ledger, the re-run no-op, and every integrity/execution guard;
they skip when ``TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from migrations import runner
from migrations.runner import (
    MigrationExecutionError,
    MigrationIntegrityError,
    MigrationLockError,
    run_migrations,
)

#: The real migrations directory shipped with the backend (backend/src/migrations).
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "src" / "migrations"

#: Version string of the initial migration and its known statement count.
FIRST = "0001"
INITIAL_STATEMENTS = 21


# --------------------------------------------------------------------------- pure (no DB)


def test_checksum_normalises_crlf() -> None:
    assert runner._checksum(b"line1\r\nline2\r\n") == runner._checksum(b"line1\nline2\n")


def test_real_migration_splits_into_expected_statements() -> None:
    sql = (MIGRATIONS_DIR / "0001_initial.sql").read_text()
    statements = runner._split_statements(sql)
    assert len(statements) == INITIAL_STATEMENTS
    assert all(stmt.strip() for stmt in statements)


def test_comment_only_fragments_are_dropped() -> None:
    sql = "-- leading comment\nCREATE TABLE t (id INT);\n-- trailing comment\n"
    statements = runner._split_statements(sql)
    assert len(statements) == 1
    assert "CREATE TABLE" in statements[0].upper()


@pytest.mark.parametrize(
    "sql",
    [
        "DELIMITER //",
        "CREATE TRIGGER trg BEFORE INSERT ON t FOR EACH ROW SET @x = 1;",
        "CREATE PROCEDURE p() BEGIN SELECT 1; END;",
        "CREATE FUNCTION f() RETURNS INT DETERMINISTIC RETURN 1;",
    ],
)
def test_banned_constructs_are_rejected(sql: str) -> None:
    with pytest.raises(MigrationIntegrityError):
        runner._split_statements(sql)


def test_banned_keyword_inside_a_comment_is_allowed() -> None:
    # The banned check runs on comment-stripped SQL, so a comment may mention CREATE TRIGGER.
    sql = "-- this mentions CREATE TRIGGER but is only a comment\nCREATE TABLE t (id INT);"
    assert len(runner._split_statements(sql)) == 1


# --------------------------------------------------------------------------- DB-backed


def _rows(conn, table: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {table} ORDER BY sort_order")
        return cur.fetchall()


def test_applies_initial_migration(db_connection) -> None:
    result = run_migrations(db_connection, MIGRATIONS_DIR)
    assert result.applied[0] == FIRST  # 0001 applies first; later slices add more migrations
    assert result.skipped == []

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT status, statements_total, statements_ok "
            "FROM schema_migrations WHERE version = %s",
            (FIRST,),
        )
        ledger = cur.fetchone()
    assert ledger["status"] == "applied"
    assert ledger["statements_total"] == INITIAL_STATEMENTS
    assert ledger["statements_ok"] == INITIAL_STATEMENTS

    statuses = _rows(db_connection, "opportunity_statuses")
    assert [row["short_name"] for row in statuses] == [
        "researching",
        "outreach_sent",
        "in_conversation",
        "pitched",
        "booked",
        "delivered",
        "nurture",
        "cancelled",
        "lost",
    ]
    terminal = {row["short_name"]: row["is_terminal"] for row in statuses}
    assert terminal["delivered"] == 1
    assert terminal["nurture"] == 0

    settled = {
        row["short_name"]: row["is_settled"] for row in _rows(db_connection, "payment_statuses")
    }
    assert settled["paid"] == 1
    assert settled["n_a"] == 1
    assert settled["unbilled"] == 0

    counts = {
        row["short_name"]: row["counts_toward_target"]
        for row in _rows(db_connection, "outreach_kinds")
    }
    assert counts["initial"] == 1
    assert counts["correspondence"] == 0


def test_rerun_is_a_noop(db_connection) -> None:
    run_migrations(db_connection, MIGRATIONS_DIR)
    result = run_migrations(db_connection, MIGRATIONS_DIR)
    assert result.applied == []
    assert FIRST in result.skipped

    with db_connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM schema_migrations WHERE version = %s", (FIRST,))
        assert cur.fetchone()["n"] == 1  # 0001 recorded once, not duplicated on re-run
        cur.execute("SELECT COUNT(*) AS n FROM opportunity_statuses")
        assert cur.fetchone()["n"] == 9  # seeds not duplicated on re-run


def test_failed_history_row_blocks(db_connection) -> None:
    run_migrations(db_connection, MIGRATIONS_DIR)
    with db_connection.cursor() as cur:
        cur.execute("UPDATE schema_migrations SET status = 'failed' WHERE version = %s", (FIRST,))
    with pytest.raises(MigrationIntegrityError):
        run_migrations(db_connection, MIGRATIONS_DIR)


def test_edited_applied_file_aborts(db_connection, tmp_path) -> None:
    staged = tmp_path / "0001_initial.sql"
    shutil.copy(MIGRATIONS_DIR / "0001_initial.sql", staged)
    run_migrations(db_connection, tmp_path)
    # Editing an already-applied file changes its bytes and therefore its checksum.
    staged.write_text(staged.read_text() + "\n-- tampered after apply\n")
    with pytest.raises(MigrationIntegrityError):
        run_migrations(db_connection, tmp_path)


def test_failing_statement_records_failed_row(db_connection, tmp_path) -> None:
    shutil.copy(MIGRATIONS_DIR / "0001_initial.sql", tmp_path / "0001_initial.sql")
    # First statement succeeds, second references a missing table → fails mid-file.
    (tmp_path / "0002_broken.sql").write_text(
        "CREATE TABLE IF NOT EXISTS t_ok (id INT PRIMARY KEY);\n"
        "INSERT INTO t_absent (id) VALUES (1);\n"
    )
    with pytest.raises(MigrationExecutionError):
        run_migrations(db_connection, tmp_path)

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT status, statements_ok, statements_total, error "
            "FROM schema_migrations WHERE version = '0002'"
        )
        row = cur.fetchone()
    assert row["status"] == "failed"
    assert row["statements_ok"] == 1
    assert row["statements_total"] == 2
    assert row["error"]


def test_new_file_below_max_aborts(db_connection, tmp_path) -> None:
    shutil.copy(MIGRATIONS_DIR / "0001_initial.sql", tmp_path / "0001_initial.sql")
    (tmp_path / "0003_later.sql").write_text(
        "CREATE TABLE IF NOT EXISTS t0003 (id INT PRIMARY KEY);\n"
    )
    result = run_migrations(db_connection, tmp_path)
    assert result.applied == ["0001", "0003"]

    # A migration sorting below the applied tip appears afterwards (a rebase accident).
    (tmp_path / "0002_sneaked_in.sql").write_text(
        "CREATE TABLE IF NOT EXISTS t0002 (id INT PRIMARY KEY);\n"
    )
    with pytest.raises(MigrationIntegrityError):
        run_migrations(db_connection, tmp_path)


def test_concurrent_runner_times_out(db_connect, monkeypatch) -> None:
    # Keep the timeout short so the test does not wait the full 30s for the lock.
    monkeypatch.setattr(runner, "_LOCK_TIMEOUT_S", 1)
    holder = db_connect()
    other = db_connect()

    with holder.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, 5)", (runner._LOCK_NAME,))
        assert next(iter(cur.fetchone().values())) == 1
    try:
        with pytest.raises(MigrationLockError):
            run_migrations(other, MIGRATIONS_DIR)
    finally:
        with holder.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK(%s)", (runner._LOCK_NAME,))
