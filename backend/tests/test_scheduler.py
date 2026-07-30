"""Unit tests for the EventBridge Scheduler edge (slice 7 checkpoint E).

No AWS: the boto3 client is replaced at ``common.scheduler._client_instance``, the same seam
``common.storage`` and ``common.secrets`` use. What matters here is not that boto3 is called but
*how the module reacts to what boto3 raises* — the conflict path that makes a replace idempotent,
the not-found path that makes a cancel harmless, and the two independent botocore exception roots
that must never escape into the API's catch-all.
"""

from __future__ import annotations

import json

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from common import scheduler

GROUP = "st-followups"
NOTIFY_ARN = "arn:aws:lambda:us-west-2:1:function:notify"
ROLE_ARN = "arn:aws:iam::1:role/sched"

PUT_ARGS = {
    "follow_up_id": 42,
    "expression": "at(2026-08-01T07:00:00)",
    "timezone": "Pacific/Honolulu",
    "payload": {"note": "Chase the contract", "to_address": "donna@example.com"},
}


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "CreateSchedule")


class FakeClient:
    """Records calls; raises whatever the test asked it to."""

    def __init__(self, create_raises=None, update_raises=None, delete_raises=None):
        self.calls: list[tuple[str, dict]] = []
        self._raises = {
            "create": create_raises,
            "update": update_raises,
            "delete": delete_raises,
        }

    def _record(self, kind: str, kw: dict):
        self.calls.append((kind, kw))
        exc = self._raises[kind]
        if exc is not None:
            raise exc

    def create_schedule(self, **kw):
        self._record("create", kw)

    def update_schedule(self, **kw):
        self._record("update", kw)

    def delete_schedule(self, **kw):
        self._record("delete", kw)


@pytest.fixture
def configured(monkeypatch):
    """Env set as the Api stack sets it; returns a factory that installs a fake client."""
    monkeypatch.setenv(scheduler.SCHEDULER_GROUP_ENV, GROUP)
    monkeypatch.setenv(scheduler.SCHEDULER_NOTIFY_ARN_ENV, NOTIFY_ARN)
    monkeypatch.setenv(scheduler.SCHEDULER_ROLE_ARN_ENV, ROLE_ARN)

    def install(**kwargs) -> FakeClient:
        client = FakeClient(**kwargs)
        monkeypatch.setattr(scheduler, "_client_instance", client)
        return client

    yield install
    scheduler.reset_client()


def test_schedule_name_is_derived_from_the_id_alone() -> None:
    assert scheduler.schedule_name(42) == "followup-42"


def test_put_sends_the_one_time_schedule_shape(configured) -> None:
    client = configured()
    assert scheduler.put_schedule(**PUT_ARGS) is True

    kind, kw = client.calls[0]
    assert kind == "create"
    assert kw["Name"] == "followup-42"
    assert kw["GroupName"] == GROUP
    assert kw["ScheduleExpression"] == "at(2026-08-01T07:00:00)"
    # The user's zone, never UTC — this is the whole point of decision 2.
    assert kw["ScheduleExpressionTimezone"] == "Pacific/Honolulu"
    # OFF or the fire time drifts; DELETE or fired schedules pile up in the group forever.
    assert kw["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert kw["ActionAfterCompletion"] == "DELETE"
    assert kw["Target"]["Arn"] == NOTIFY_ARN
    assert kw["Target"]["RoleArn"] == ROLE_ARN


def test_put_merges_the_id_so_name_and_payload_cannot_disagree(configured) -> None:
    """followup_notify correlates on the payload id and never reads the DB; a mismatch is
    undiagnosable, so the id comes from the argument the name is built from."""
    client = configured()
    scheduler.put_schedule(**{**PUT_ARGS, "payload": {"follow_up_id": 999, "note": "x"}})
    payload = json.loads(client.calls[0][1]["Target"]["Input"])
    assert payload["follow_up_id"] == 42


def test_put_falls_back_to_update_on_conflict(configured) -> None:
    """A deterministic name means a replace is one idempotent call, with no missing window."""
    client = configured(create_raises=_client_error("ConflictException"))
    assert scheduler.put_schedule(**PUT_ARGS) is True

    assert [kind for kind, _ in client.calls] == ["create", "update"]
    assert client.calls[1][1]["ScheduleExpression"] == "at(2026-08-01T07:00:00)"


def test_delete_treats_already_gone_as_success(configured) -> None:
    """A one-time schedule self-deletes when it fires and we are never told (acceptance #3)."""
    configured(delete_raises=_client_error("ResourceNotFoundException"))
    assert scheduler.delete_schedule(follow_up_id=42) is True


def test_delete_addresses_the_derived_name(configured) -> None:
    client = configured()
    assert scheduler.delete_schedule(follow_up_id=42) is True
    assert client.calls[0][1] == {"Name": "followup-42", "GroupName": GROUP}


@pytest.mark.parametrize(
    "failure",
    [
        _client_error("ThrottlingException"),
        _client_error("ValidationException"),
        EndpointConnectionError(endpoint_url="https://scheduler.us-west-2.amazonaws.com"),
    ],
    ids=["throttling", "validation", "connect-failure"],
)
def test_no_scheduler_failure_ever_escapes(configured, failure) -> None:
    """Botocore has two independent exception roots and neither subclasses the other.

    Catching only ``ClientError`` let ``EndpointConnectionError`` — and every other client-side
    failure: connect/read timeouts, missing credentials — propagate into the API's catch-all 500,
    failing the user's request *after* the follow-up row had already been committed. The whole
    commit-first ordering depends on a scheduler problem being survivable, so this returns ``False``
    for every failure rather than raising.
    """
    configured(create_raises=failure, update_raises=failure, delete_raises=failure)
    assert scheduler.put_schedule(**PUT_ARGS) is False
    assert scheduler.delete_schedule(follow_up_id=42) is False


def test_update_failure_after_a_conflict_also_returns_false(configured) -> None:
    configured(
        create_raises=_client_error("ConflictException"),
        update_raises=EndpointConnectionError(endpoint_url="https://scheduler"),
    )
    assert scheduler.put_schedule(**PUT_ARGS) is False


@pytest.mark.parametrize(
    "missing",
    [
        scheduler.SCHEDULER_GROUP_ENV,
        scheduler.SCHEDULER_NOTIFY_ARN_ENV,
        scheduler.SCHEDULER_ROLE_ARN_ENV,
    ],
)
def test_unconfigured_is_a_logged_no_op(configured, monkeypatch, caplog, missing) -> None:
    """Tearing down (or not yet deploying) the scheduler infrastructure must not break the API.

    The follow-up row still persists and still shows on the Dashboard; only the email is lost — and
    that loss is logged at WARNING, because a silently swallowed failure is invisible to monitoring.
    """
    client = configured()
    monkeypatch.delenv(missing)
    with caplog.at_level("WARNING"):
        assert scheduler.put_schedule(**PUT_ARGS) is False
        assert scheduler.delete_schedule(follow_up_id=42) is False

    assert client.calls == []  # never reached AWS at all
    assert any(r.levelname == "WARNING" and missing in r.getMessage() for r in caplog.records)
