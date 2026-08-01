"""Unit tests for the dead-letter consumer (slice 7).

No database and no AWS: the connection and the repository write are replaced at their seams, which
is enough because the interesting behaviour here is *routing* — which failures are retryable, which
are not, and which are not failures at all.

The distinction that matters: a message this cannot parse must be **dropped**, because redelivering
it just repeats the same parse failure forever; a message it could not *write* must be **retried**,
because the database being briefly unreachable says nothing about the message. Getting those two
backwards either loses failures silently or wedges the queue.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from handlers import followup_failed
from handlers.followup_failed import extract_follow_up_id


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        function_name="followup-failed",
        memory_limit_in_mb=512,
        invoked_function_arn="arn:aws:lambda:us-west-2:1:function:followup-failed",
        aws_request_id="req-test-123",
        get_remaining_time_in_millis=lambda: 30_000,
    )


def _sqs_event(*bodies: str) -> dict:
    return {
        "Records": [
            {"messageId": f"msg-{i}", "body": body} for i, body in enumerate(bodies, start=1)
        ]
    }


def _payload(follow_up_id: int) -> str:
    """A dead-lettered body in the shape core.follow_ups.ReminderSchedule.payload produces."""
    return json.dumps(
        {
            "follow_up_id": follow_up_id,
            "to_address": "donna@example.com",
            "note": "Chase the contract",
            "due_date": "2026-08-01",
            "contact_name": "Kalei",
            "opportunity_title": None,
        }
    )


@pytest.fixture
def marked(monkeypatch):
    """Capture which follow-up ids were flagged; returns the list."""
    calls: list[int] = []

    @contextmanager
    def fake_transaction(conn):
        yield conn

    monkeypatch.setattr(followup_failed, "get_connection", lambda tz: object())
    monkeypatch.setattr(followup_failed, "transaction", fake_transaction)
    monkeypatch.setattr(
        followup_failed.follow_ups_repo,
        "mark_reminder_failed",
        lambda conn, follow_up_id: (calls.append(follow_up_id), True)[1],
    )
    return calls


# --- parsing --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('{"follow_up_id": 42}', 42),
        ('{"follow_up_id": "42"}', 42),  # JSON-numeric-as-string still resolves
        ('{"detail": {"follow_up_id": 7}}', 7),  # one level of nesting, shape not yet observed
        ("not json", None),
        ("[1, 2, 3]", None),
        ('{"note": "no id here"}', None),
        ('{"follow_up_id": null}', None),
    ],
)
def test_extract_follow_up_id(body: str, expected: int | None) -> None:
    assert extract_follow_up_id(body) == expected


# --- routing --------------------------------------------------------------------------------------


def test_marks_each_dead_lettered_reminder(marked) -> None:
    result = followup_failed.lambda_handler(_sqs_event(_payload(11), _payload(12)), _context())

    assert marked == [11, 12]
    assert result == {"batchItemFailures": []}


def test_an_unparseable_body_is_dropped_not_retried(marked, caplog) -> None:
    """Redelivering it would just repeat the same parse failure until the queue gave up."""
    with caplog.at_level("ERROR"):
        result = followup_failed.lambda_handler(_sqs_event("}{ not json"), _context())

    assert result == {"batchItemFailures": []}, "must not be sent back for redelivery"
    assert marked == []
    # The raw body is logged because it is the only description we have of the payload shape.
    assert any("no follow_up_id" in r.getMessage() for r in caplog.records)


def test_a_database_failure_is_retried(monkeypatch, caplog) -> None:
    """The message is fine; the database was not. That one belongs back on the queue."""

    def boom(tz):
        raise RuntimeError("RDS unreachable")

    monkeypatch.setattr(followup_failed, "get_connection", boom)
    with caplog.at_level("ERROR"):
        result = followup_failed.lambda_handler(_sqs_event(_payload(5)), _context())

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-1"}]}


def test_one_bad_record_does_not_sink_the_batch(monkeypatch, marked) -> None:
    result = followup_failed.lambda_handler(
        _sqs_event(_payload(1), "garbage", _payload(3)), _context()
    )

    assert marked == [1, 3], "the parseable records are still handled"
    assert result == {"batchItemFailures": []}


def test_a_deleted_follow_up_is_not_an_error(monkeypatch, caplog) -> None:
    """Deleted between the reminder failing and this running — nothing left to flag, and fine."""

    @contextmanager
    def fake_transaction(conn):
        yield conn

    monkeypatch.setattr(followup_failed, "get_connection", lambda tz: object())
    monkeypatch.setattr(followup_failed, "transaction", fake_transaction)
    monkeypatch.setattr(
        followup_failed.follow_ups_repo, "mark_reminder_failed", lambda conn, follow_up_id: False
    )

    with caplog.at_level("WARNING"):
        result = followup_failed.lambda_handler(_sqs_event(_payload(9)), _context())

    assert result == {"batchItemFailures": []}
    assert any("no longer exists" in r.getMessage() for r in caplog.records)


def test_a_flagged_reminder_logs_at_error(marked, caplog) -> None:
    """A nudge Donna was relying on did not reach her — that is not an INFO-level event."""
    with caplog.at_level("ERROR"):
        followup_failed.lambda_handler(_sqs_event(_payload(4)), _context())

    assert any(r.levelname == "ERROR" and "NOT delivered" in r.getMessage() for r in caplog.records)


def test_an_empty_batch_is_harmless(marked) -> None:
    assert followup_failed.lambda_handler({}, _context()) == {"batchItemFailures": []}
