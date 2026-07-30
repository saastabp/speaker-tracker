"""Unit tests for the follow-up reminder notifier (slice 7 checkpoint G).

No AWS and no database — ``mail.send_raw`` is replaced and the module has nothing to connect to.
The most important test here is structural: :func:`test_module_cannot_reach_the_database` asserts
the handler imports nothing that could open a connection. That is not stylistic. It is what lets
this function run outside the VPC with no RDS handshake, and it is why every edit to a rendered
field has to cancel and recreate the schedule — one convenience import would quietly undo the
reasoning the whole slice is built on.
"""

from __future__ import annotations

import inspect
import re
from types import SimpleNamespace

import pytest

from common import mail
from handlers import followup_notify as notify

PAYLOAD = {
    "follow_up_id": 42,
    "to_address": "donna@example.com",
    "note": "Check whether the committee met about the October slot.",
    "due_date": "2026-08-01",
    "contact_name": "Kalei",
    "opportunity_title": "Wellness Wheel for Women",
}


def _context() -> SimpleNamespace:
    """A minimal stand-in for the Lambda context Powertools reads."""
    return SimpleNamespace(
        function_name="followup-notify",
        memory_limit_in_mb=256,
        invoked_function_arn="arn:aws:lambda:us-west-2:1:function:followup-notify",
        aws_request_id="req-test-123",
        get_remaining_time_in_millis=lambda: 30_000,
    )


@pytest.fixture
def sent(monkeypatch):
    """Capture sends instead of making them; returns the list of captured messages."""
    monkeypatch.setenv(mail.MAIL_FROM_ENV, "donna@360balancedliving.com")
    monkeypatch.setenv(mail.MAIL_FROM_NAME_ENV, "Donna King")
    captured: list[dict] = []

    def fake_send(raw, *, sender, destinations):
        captured.append({"raw": raw.decode(), "sender": sender, "destinations": destinations})
        return "0100018f-ses-id"

    monkeypatch.setattr(mail, "send_raw", fake_send)
    return captured


def _html_part(raw: str) -> str:
    """Return just the text/html section.

    The plain alternative is derived by ``html_to_text``, which *unescapes* — so markup appears
    there as literal text. That is inert (a client renders text/plain as text), but it means an
    escaping assertion has to look at the HTML part specifically or it tests the wrong thing.
    """
    return raw.split("Content-Type: text/html", 1)[1]


def test_module_cannot_reach_the_database() -> None:
    """The no-DB guarantee, enforced rather than documented."""
    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", inspect.getsource(notify), re.M)
    assert [i for i in imports if i.startswith("common.db")] == []
    assert [i for i in imports if i.startswith("repositories")] == []
    assert [i for i in imports if "pymysql" in i] == []


def test_sends_the_reminder_and_reports_the_ses_id(sent) -> None:
    result = notify.lambda_handler(dict(PAYLOAD), _context())

    assert result == {
        "status": "sent",
        "follow_up_id": 42,
        "ses_message_id": "0100018f-ses-id",
    }
    assert len(sent) == 1
    assert sent[0]["destinations"] == ["donna@example.com"]
    assert sent[0]["sender"] == "Donna King <donna@360balancedliving.com>"


def test_message_shape(sent) -> None:
    notify.lambda_handler(dict(PAYLOAD), _context())
    raw = sent[0]["raw"]

    # A bare text/html body with no plain alternative is a well-known spam signal.
    assert "multipart/alternative" in raw
    assert "Content-Type: text/plain" in raw
    # The Message-ID must be on the sending domain, not the recipient's.
    assert "@360balancedliving.com>" in raw
    assert "committee met about the October slot" in raw
    assert "August 1, 2026" in raw


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "Kalei"),
        ({"contact_name": None}, "Wellness Wheel for Women"),
        ({"opportunity_title": None}, "Kalei"),
    ],
    ids=["both", "gig-only", "contact-only"],
)
def test_subject_label_uses_whichever_links_exist(sent, overrides, expected) -> None:
    """``ck_follow_ups_target`` guarantees at least one, never necessarily both."""
    notify.lambda_handler({**PAYLOAD, **overrides}, _context())
    assert expected in sent[0]["raw"]


def test_note_is_escaped_before_interpolation(sent) -> None:
    notify.lambda_handler({**PAYLOAD, "note": '<script>alert(1)</script> & "quotes"'}, _context())
    html_part = _html_part(sent[0]["raw"])
    assert "<script>" not in html_part
    assert "&lt;script&gt;" in html_part


def test_multiline_note_survives(sent) -> None:
    notify.lambda_handler({**PAYLOAD, "note": "first line\nsecond line"}, _context())
    assert "<br>" in _html_part(sent[0]["raw"])


def test_malformed_due_date_still_sends(sent, caplog) -> None:
    """The note is the substance; losing the email over a cosmetic date would be worse."""
    with caplog.at_level("WARNING"):
        notify.lambda_handler({**PAYLOAD, "due_date": "not-a-date"}, _context())

    assert len(sent) == 1
    assert "not-a-date" in sent[0]["raw"]
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_missing_recipient_raises_rather_than_reporting_success(sent) -> None:
    """Nothing is written back to the DB, so a silent success is an undiagnosable lost reminder."""
    with pytest.raises(ValueError, match="to_address"):
        notify.lambda_handler({**PAYLOAD, "to_address": None}, _context())
    assert sent == []


def test_send_failure_propagates_so_eventbridge_retries(sent, monkeypatch, caplog) -> None:
    def boom(raw, *, sender, destinations):
        raise RuntimeError("SES said no")

    monkeypatch.setattr(mail, "send_raw", boom)
    with caplog.at_level("ERROR"), pytest.raises(RuntimeError, match="SES said no"):
        notify.lambda_handler(dict(PAYLOAD), _context())

    failures = [r for r in caplog.records if r.levelname == "ERROR"]
    assert failures, "a failed reminder must leave an ERROR line — it is the only record"
    assert failures[0].exc_info is not None, "and it must carry the stack trace"
