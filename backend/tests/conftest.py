"""Shared pytest fixtures.

DB-backed tests connect to the MySQL named by ``TEST_DATABASE_URL`` and **skip** when it is
unset, so ``pytest`` still runs on a laptop with no database (CI provides a ``mysql:8.4``
service container — pinned to match RDS 8.4). Each test starts from a dropped-clean schema so
the migration runner is exercised from an empty database exactly as in production.

``TEST_DATABASE_URL`` is a standard URL, e.g. ``mysql://root:root@127.0.0.1:3306/speakertracker_test``.
It uses password auth, not RDS IAM, so tests build their own connections rather than going
through ``common/db.py``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from urllib.parse import urlparse

import pymysql
import pymysql.cursors
import pytest
from pymysql.connections import Connection


def _connect_params(url: str) -> dict:
    """Parse a ``mysql://user:pass@host:port/db`` URL into ``pymysql.connect`` kwargs."""
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": parsed.username or "root",
        "password": parsed.password or "",
        "database": (parsed.path or "").lstrip("/"),
    }


def _drop_all_tables(conn: Connection, database: str) -> None:
    """Drop every table in ``database`` so each test sees an empty schema."""
    with conn.cursor() as cur:
        # Alias the column: information_schema reports it as TABLE_NAME (uppercase) on
        # MySQL 8.4, so a lowercase alias keeps the dict key stable across versions.
        cur.execute(
            "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = %s",
            (database,),
        )
        tables = [row["name"] for row in cur.fetchall()]
        # Order is unknown and FKs may cross-reference; disable checks for the teardown.
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in tables:
            cur.execute(f"DROP TABLE IF EXISTS `{table}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")


@pytest.fixture
def db_connect() -> Iterator[Callable[[], Connection]]:
    """Yield a factory that opens autocommit ``DictCursor`` connections to the test database.

    The schema is dropped clean once up front. Every connection the factory hands out is
    tracked and closed at teardown, so tests needing more than one session (e.g. the
    concurrency test) simply call the factory again. Skips the test when
    ``TEST_DATABASE_URL`` is unset.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set; skipping DB-backed migration tests")
    params = _connect_params(url)
    opened: list[Connection] = []

    def make() -> Connection:
        conn = pymysql.connect(
            **params,
            autocommit=True,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        opened.append(conn)
        return conn

    _drop_all_tables(make(), params["database"])
    yield make
    for conn in opened:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - teardown; a broken socket is not worth failing on
            pass


@pytest.fixture
def db_connection(db_connect: Callable[[], Connection]) -> Connection:
    """A single connection to the clean test database (the common case)."""
    return db_connect()
