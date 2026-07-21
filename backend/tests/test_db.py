"""Tests for ``common.db`` connection reuse and single-reconnect — plan verification #2.

No real database or AWS: ``pymysql.connect`` and the RDS client are stubbed so the module-scope
connection lifecycle can be driven deterministically. These pin the properties that make reuse
safe on a warm Lambda container:

- a live reused connection is returned without reconnecting;
- a dead socket (a lost-connection ``OperationalError`` or an ``InterfaceError`` on the
  ``SET time_zone`` probe) triggers **exactly one** reconnect, with a **fresh** IAM token;
- ``ping()`` is never called — ``ping(reconnect=True)`` would reconnect with the *expired* token
  (see ``db.py``), so its absence is a correctness property, not an implementation detail;
- a non-connection error propagates **without** reconnecting;
- a second consecutive failure propagates as a real outage rather than looping.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pymysql.err import InterfaceError, OperationalError

from common import db


class _FakeCursor:
    def __init__(self, on_execute):
        self._on_execute = on_execute

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return self._on_execute(*args, **kwargs)


class _FakeConn:
    def __init__(self, on_execute, password):
        self._on_execute = on_execute
        self.password = password  # the IAM token this connection was opened with
        self.closed = False
        self.ping = MagicMock(name="ping")  # asserted never called

    def cursor(self):
        return _FakeCursor(self._on_execute)

    def close(self):
        self.closed = True


def _ok(*args, **kwargs):
    """A tz-probe that succeeds."""
    return None


def _raises(exc):
    """Build a tz-probe behaviour that raises ``exc``."""

    def _behaviour(*args, **kwargs):
        raise exc

    return _behaviour


@pytest.fixture(autouse=True)
def _db_env(monkeypatch):
    """Provide connection env and reset the module-scope connection state between tests."""
    monkeypatch.setenv("DB_HOST", "db.example.com")
    monkeypatch.setenv("DB_USER", "speakertracker_app")
    monkeypatch.setenv("DB_NAME", "speakertracker")
    monkeypatch.setenv("DB_REGION", "us-west-2")
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.setattr(db, "_rds_client", None)


def _install(monkeypatch, *behaviours):
    """Stub the RDS client and ``pymysql.connect``; return ``(fake_rds, created_conns)``.

    Each positional ``behaviour`` is the ``SET time_zone`` execute behaviour for the Nth
    connection opened. A fresh token string is minted per ``generate_db_auth_token`` call.
    """
    fake_rds = MagicMock()
    counter = {"n": 0}

    def _token(**kwargs):
        counter["n"] += 1
        return f"token-{counter['n']}"

    fake_rds.generate_db_auth_token.side_effect = _token
    monkeypatch.setattr(db, "_rds_client", fake_rds)

    conns: list[_FakeConn] = []
    behaviour_iter = iter(behaviours)

    def _connect(**kwargs):
        conn = _FakeConn(next(behaviour_iter), kwargs.get("password"))
        conns.append(conn)
        return conn

    monkeypatch.setattr(db.pymysql, "connect", _connect)
    return fake_rds, conns


def test_warm_connection_is_reused(monkeypatch) -> None:
    fake_rds, conns = _install(monkeypatch, _ok, _ok)
    first = db.get_connection("Pacific/Honolulu")
    second = db.get_connection("Pacific/Honolulu")
    assert first is second
    assert len(conns) == 1  # only one connect ever — the second call reuses it
    assert fake_rds.generate_db_auth_token.call_count == 1
    first.ping.assert_not_called()


def test_dead_socket_reconnects_once_with_fresh_token(monkeypatch) -> None:
    fake_rds, conns = _install(
        monkeypatch,
        _raises(OperationalError(2013, "Lost connection during query")),
        _ok,
    )
    result = db.get_connection("Pacific/Honolulu")
    assert result is conns[1]  # the reconnected connection is returned
    assert len(conns) == 2  # exactly one reconnect
    assert fake_rds.generate_db_auth_token.call_count == 2  # a fresh token was minted
    assert conns[0].password != conns[1].password  # the NEW token flowed into the reconnect
    assert conns[0].closed is True  # the dead connection was closed
    conns[0].ping.assert_not_called()  # never ping(reconnect=True)
    conns[1].ping.assert_not_called()


def test_interface_error_also_reconnects(monkeypatch) -> None:
    _, conns = _install(monkeypatch, _raises(InterfaceError("socket closed")), _ok)
    result = db.get_connection("Pacific/Honolulu")
    assert result is conns[1]
    assert len(conns) == 2


def test_non_connection_error_propagates_without_reconnect(monkeypatch) -> None:
    boom = OperationalError(1146, "Table 'speakertracker.x' doesn't exist")
    fake_rds, conns = _install(monkeypatch, _raises(boom))
    with pytest.raises(OperationalError) as excinfo:
        db.get_connection("Pacific/Honolulu")
    assert excinfo.value is boom
    assert len(conns) == 1  # no reconnect attempted for a non-connection error
    assert fake_rds.generate_db_auth_token.call_count == 1
    conns[0].ping.assert_not_called()


def test_second_failure_propagates_as_outage(monkeypatch) -> None:
    second_error = OperationalError(2006, "MySQL server has gone away")
    fake_rds, conns = _install(
        monkeypatch,
        _raises(OperationalError(2006, "MySQL server has gone away")),
        _raises(second_error),
    )
    with pytest.raises(OperationalError) as excinfo:
        db.get_connection("Pacific/Honolulu")
    assert excinfo.value is second_error  # the SECOND failure surfaces, not the first
    assert len(conns) == 2  # one reconnect, then give up — no loop
    assert fake_rds.generate_db_auth_token.call_count == 2
    conns[0].ping.assert_not_called()
    conns[1].ping.assert_not_called()
