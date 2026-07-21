"""Forward-only SQL migration runner for the Speaker Tracker schema.

Applies ``migrations/NNNN_*.sql`` files in lexical (== numeric, zero-padded) order,
tracking each in ``schema_migrations``. The design constraints are all safety properties
against the shared ``jobtracker-db`` instance (see ``ARCHITECTURE.md`` and ``DATABASE.md``
§6):

- **Advisory lock on the caller's dedicated connection.** ``GET_LOCK`` is released when the
  session ends, so two concurrent deploys cannot both apply the same file — one wins, the
  other blocks then aborts. The caller MUST pass a connection that is *not* the API's cached
  module-scope one (``common/db.py``): the lock's whole value is that it dies with the
  session, and the API connection outlives the request.
- **Checksum integrity gate.** ``schema_migrations`` records a sha256 (CRLF-normalised) of
  every applied file. A file edited after it was applied, a missing applied file, or a new
  file that sorts *below* an already-applied one (the merged-branches case) all abort before
  anything runs — otherwise schema drift is completely silent.
- **One statement at a time via ``sqlparse.split()``.** Never ``CLIENT.MULTI_STATEMENTS``
  (which misattributes which statement failed), and never a naive ``split(';')`` (which
  breaks on the semicolons and escaped quotes that catalog ``description`` seeds contain).
- **A ``failed`` or ``running`` row is a deliberate hard stop.** MySQL 8 commits each DDL
  statement implicitly, so a *file* can be half-applied with no rollback. Recovery is
  forward-only and human-driven — fix the SQL, ``DELETE FROM schema_migrations WHERE
  version='NNNN'``, redeploy. Auto-retrying from statement 1 against a partially-mutated
  schema is how databases get corrupted, so the runner refuses.

The runner takes ``(connection, migrations_dir)`` rather than reading env at import so tests
drive it against a real MySQL and the highest-risk code in slice 1 is exercised on every push.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path

import sqlparse
from pymysql.connections import Connection

from common.logger import logger

#: Namespaced advisory-lock key; scoped to this schema so sibling apps never contend.
_LOCK_NAME = "speakertracker_schema_migration"

#: Seconds ``GET_LOCK`` waits before returning 0. Covers a slow concurrent apply without
#: hanging a deploy indefinitely.
_LOCK_TIMEOUT_S = 30

#: A migration filename: a 4-digit zero-padded version, an underscore, a name, ``.sql``.
_FILENAME_RE = re.compile(r"^(\d{4})_.+\.sql$")

#: Constructs that need a client-side ``DELIMITER`` or ship server-side code. They defeat
#: single-statement splitting and are out of scope for this schema — reject them loudly
#: rather than mis-apply them (checked on comment-stripped SQL so a comment can mention them).
_BANNED_RE = re.compile(
    r"^\s*(?:DELIMITER\b"
    r"|CREATE\s+(?:DEFINER\s*=\s*\S+\s+)?(?:TRIGGER|PROCEDURE|FUNCTION|EVENT)\b)",
    re.IGNORECASE,
)

#: DDL for the migration ledger. Bootstrapped by the runner, never by a migration file —
#: the runner must query it to decide whether ``0001`` has run, so ``0001`` creating it would
#: be circular (DATABASE.md §6). ``IF NOT EXISTS`` makes concurrent bootstrap race-safe.
_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version          VARCHAR(20)  NOT NULL,
  name             VARCHAR(255) NOT NULL,
  checksum         CHAR(64)     NOT NULL,
  status           ENUM('running','applied','failed') NOT NULL,
  statements_total INT UNSIGNED NOT NULL,
  statements_ok    INT UNSIGNED NOT NULL DEFAULT 0,
  started_at       TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  finished_at      TIMESTAMP(3) NULL,
  execution_ms     INT UNSIGNED NULL,
  error            TEXT NULL,
  PRIMARY KEY (version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""".strip()


class MigrationError(Exception):
    """Base class for every migration failure raised by this module."""


class MigrationLockError(MigrationError):
    """The advisory lock could not be acquired — another runner holds it."""


class MigrationIntegrityError(MigrationError):
    """The migration set on disk is inconsistent with the recorded history.

    Raised *before* any statement runs (drifted checksum, missing applied file, a new file
    that sorts below an applied one, a leftover ``running``/``failed`` row, or a banned
    construct). Signals a state a human must reconcile, never an automatic retry.
    """


class MigrationExecutionError(MigrationError):
    """A statement within a migration file failed while applying it.

    The file's row is left ``failed`` as a deliberate hard stop; recovery is forward-only.
    """


@dataclass(frozen=True)
class MigrationFile:
    """One discovered migration file, parsed and checksummed but not yet applied."""

    version: str
    name: str
    path: Path
    checksum: str
    statements: list[str]


@dataclass(frozen=True)
class MigrationRunResult:
    """Outcome of a run: versions applied this call, and versions already applied (skipped)."""

    applied: list[str]
    skipped: list[str]


def _checksum(raw: bytes) -> str:
    """Return the sha256 hex digest of file bytes with CRLF normalised to LF."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def _split_statements(sql: str) -> list[str]:
    """Split a migration file into individual executable statements.

    Uses ``sqlparse.split`` so semicolons inside string literals (e.g. a catalog
    ``description``) never mis-split. Blank and comment-only fragments are dropped; a banned
    construct raises before anything is executed.
    """
    statements: list[str] = []
    for fragment in sqlparse.split(sql):
        stmt = fragment.strip()
        if not stmt:
            continue
        code = sqlparse.format(stmt, strip_comments=True).strip()
        if not code:
            continue  # a standalone comment block between statements
        if _BANNED_RE.match(code):
            raise MigrationIntegrityError(
                f"banned construct (DELIMITER/trigger/procedure/function/event): {code[:80]!r}"
            )
        statements.append(stmt)
    return statements


def _discover_migrations(migrations_dir: Path) -> list[MigrationFile]:
    """Read, checksum, and parse every ``NNNN_*.sql`` file, ordered by version."""
    files: list[MigrationFile] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if not match:
            # A stray .sql that isn't a migration is almost certainly a mistake; surface it.
            logger.warning("Ignoring non-migration .sql file %s", path.name)
            continue
        raw = path.read_bytes()
        files.append(
            MigrationFile(
                version=match.group(1),
                name=path.name,
                path=path,
                checksum=_checksum(raw),
                statements=_split_statements(raw.decode("utf-8")),
            )
        )
    files.sort(key=lambda f: f.version)
    return files


def _bootstrap_schema_migrations(conn: Connection) -> None:
    """Create the ``schema_migrations`` ledger if absent (idempotent, race-safe)."""
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_MIGRATIONS_DDL)


def _acquire_lock(conn: Connection) -> None:
    """Take the schema advisory lock or raise if another session holds it past the timeout."""
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, %s)", (_LOCK_NAME, _LOCK_TIMEOUT_S))
        row = cur.fetchone()
    result = next(iter(row.values())) if row else None
    if result != 1:
        # 0 = timed out (another runner holds it); NULL = an error acquiring it.
        raise MigrationLockError(
            f"could not acquire migration lock {_LOCK_NAME!r} within {_LOCK_TIMEOUT_S}s "
            f"(GET_LOCK returned {result!r}); another migration is likely in progress"
        )


def _release_lock(conn: Connection) -> None:
    """Release the schema advisory lock, logging but never raising on failure."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK(%s)", (_LOCK_NAME,))
            cur.fetchone()
    except Exception:
        # The lock also releases when this session closes, so a failure here is not fatal —
        # but it is unexpected, so record it (WARNING-level via exception) for monitoring.
        logger.exception("Failed to release migration advisory lock %s", _LOCK_NAME)


def _load_history(conn: Connection) -> dict[str, dict]:
    """Return recorded migrations keyed by version (each row as a dict)."""
    with conn.cursor() as cur:
        cur.execute("SELECT version, name, checksum, status FROM schema_migrations")
        return {row["version"]: row for row in cur.fetchall()}


def _check_integrity(files: list[MigrationFile], history: dict[str, dict]) -> None:
    """Abort before applying anything if disk and recorded history disagree.

    Parameters
    ----------
    files : list of MigrationFile
        Migrations discovered on disk, ordered by version.
    history : dict of str to dict
        Recorded ``schema_migrations`` rows keyed by version.

    Raises
    ------
    MigrationIntegrityError
        On a leftover ``running``/``failed`` row, a checksum drift on an applied file, an
        applied version whose file has vanished, or a new file that sorts below the highest
        applied version (a rebase/merge accident).
    """
    by_version = {f.version: f for f in files}

    # A non-terminal or failed row means a prior run stopped mid-flight; a human must reconcile.
    for version, row in history.items():
        if row["status"] in ("running", "failed"):
            raise MigrationIntegrityError(
                f"migration {version} is recorded as {row['status']!r}; "
                "manual recovery required (fix, DELETE its row, redeploy)"
            )

    for version, row in history.items():
        migration = by_version.get(version)
        if migration is None:
            raise MigrationIntegrityError(
                f"migration {version} is recorded applied but its file is missing"
            )
        if migration.checksum != row["checksum"]:
            raise MigrationIntegrityError(
                f"migration {version} ({migration.name}) was edited after being applied "
                "(checksum mismatch)"
            )

    if history:
        max_applied = max(int(v) for v in history)
        for migration in files:
            if migration.version not in history and int(migration.version) < max_applied:
                raise MigrationIntegrityError(
                    f"new migration {migration.version} ({migration.name}) sorts below the "
                    f"highest applied version {max_applied:04d}; rebase it above the tip"
                )


def _record_failure(
    conn: Connection, version: str, statements_ok: int, elapsed_ms: int, exc: Exception
) -> None:
    """Mark a migration ``failed`` with its progress and error (best-effort)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE schema_migrations SET status='failed', statements_ok=%s, "
                "finished_at=CURRENT_TIMESTAMP(3), execution_ms=%s, error=%s WHERE version=%s",
                (statements_ok, elapsed_ms, str(exc)[:2000], version),
            )
    except Exception:
        logger.exception("Could not record failure state for migration %s", version)


def _apply_migration(conn: Connection, migration: MigrationFile) -> None:
    """Apply one migration file, statement by statement, recording its ledger row.

    On any statement error the row is marked ``failed`` and a :class:`MigrationExecutionError`
    is raised — a hard stop, since MySQL has already committed the preceding DDL and there is
    no file-level rollback.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO schema_migrations (version, name, checksum, status, statements_total) "
            "VALUES (%s, %s, %s, 'running', %s)",
            (migration.version, migration.name, migration.checksum, len(migration.statements)),
        )

    start = time.perf_counter()
    statements_ok = 0
    try:
        for stmt in migration.statements:
            with conn.cursor() as cur:
                cur.execute(stmt)
            statements_ok += 1
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.exception(
            "Migration %s failed at statement %s/%s",
            migration.name,
            statements_ok + 1,
            len(migration.statements),
        )
        _record_failure(conn, migration.version, statements_ok, elapsed_ms, exc)
        raise MigrationExecutionError(
            f"migration {migration.name} failed at statement "
            f"{statements_ok + 1}/{len(migration.statements)}: {exc}"
        ) from exc

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE schema_migrations SET status='applied', statements_ok=%s, "
            "finished_at=CURRENT_TIMESTAMP(3), execution_ms=%s WHERE version=%s",
            (statements_ok, elapsed_ms, migration.version),
        )


def run_migrations(connection: Connection, migrations_dir: str | Path) -> MigrationRunResult:
    """Apply all pending migrations under an advisory lock, idempotently.

    Discovers ``NNNN_*.sql`` files, bootstraps ``schema_migrations``, validates recorded
    history against disk, then applies each not-yet-applied file in version order. Re-running
    with no new files is a no-op.

    Parameters
    ----------
    connection : pymysql.connections.Connection
        A **dedicated** connection — never the API's cached module-scope one. The advisory
        lock is released when this session ends, which is the whole safety property. Expected
        to be ``autocommit=True`` (each DDL statement commits implicitly regardless).
    migrations_dir : str or pathlib.Path
        Directory holding the ``NNNN_*.sql`` migration files.

    Returns
    -------
    MigrationRunResult
        ``applied`` — versions applied by this call; ``skipped`` — versions already applied.

    Raises
    ------
    MigrationLockError
        If another runner holds the advisory lock past the timeout.
    MigrationIntegrityError
        If disk and recorded history disagree, or a banned construct is present.
    MigrationExecutionError
        If a statement fails while applying a file; its row is left ``failed``.

    Examples
    --------
    >>> from pathlib import Path
    >>> result = run_migrations(conn, Path(__file__).parent)
    >>> result.applied
    ['0001']
    """
    migrations_dir = Path(migrations_dir)
    # File I/O and parsing happen before the lock — no DB state touched, and a banned
    # construct should fail fast without contending for the lock.
    files = _discover_migrations(migrations_dir)

    _bootstrap_schema_migrations(connection)
    _acquire_lock(connection)
    logger.info("Acquired migration lock; %s migration file(s) on disk", len(files))
    try:
        history = _load_history(connection)
        _check_integrity(files, history)

        applied: list[str] = []
        for migration in files:
            if migration.version in history:
                continue
            logger.info(
                "Applying migration %s (%s statements)", migration.name, len(migration.statements)
            )
            _apply_migration(connection, migration)
            logger.info("Applied migration %s", migration.name)
            applied.append(migration.version)

        skipped = [f.version for f in files if f.version in history]
        logger.info("Migrations complete: applied=%s skipped=%s", applied, skipped)
        return MigrationRunResult(applied=applied, skipped=skipped)
    finally:
        _release_lock(connection)
