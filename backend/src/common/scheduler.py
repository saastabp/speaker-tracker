"""EventBridge Scheduler edge for follow-up reminders — the only place boto3 ``scheduler`` is used.

A follow-up row is the record; a schedule is the reminder. This module writes the second and never
reads it: there is no ``get_schedule``, no ``list_schedules``, and no stored schedule identifier
anywhere in the app. The name is a pure function of the primary key (:func:`schedule_name`, ported
from job-tracker per DATABASE.md), so create, replace and cancel are all addressable without a
read-back, and a row can never be orphaned from its schedule by a lost column.

Two behaviours follow from that and are load-bearing rather than defensive:

- **A cancel never has to know whether the reminder already fired.** One-time schedules carry
  ``ActionAfterCompletion: DELETE``, so EventBridge removes them itself the moment they fire and we
  are never told. :func:`delete_schedule` therefore treats ``ResourceNotFoundException`` as success
  — the schedule may have fired, or may never have existed (slice 7 acceptance #3).
- **A replace is one idempotent call.** :func:`put_schedule` creates, and on ``ConflictException``
  updates the existing schedule in place. Because the name is deterministic there is no window in
  which the reminder is missing, which is a stronger reading of acceptance #2's "only one email
  fires" than a delete-then-create would give.

**The expression is local wall-clock time and the zone travels beside it** —
``ScheduleExpressionTimezone`` is the *user's* IANA zone, never ``"UTC"``. ``core.follow_ups``
builds ``at(YYYY-MM-DDTHH:MM:SS)`` at 07:00 local and this module passes both through untouched, so
no UTC conversion happens anywhere in the reminder path. (job-tracker's equivalent converts to UTC
and pins the zone to ``"UTC"``; that is the opposite of what this app decided, and porting it would
reintroduce the bug class the design exists to remove.)

**Graceful degradation when the stack is absent.** If the scheduler environment is not fully
configured, both helpers no-op, log at WARNING, and return ``False``. The follow-up row still
persists and still appears on the Dashboard — only the email is lost. Tearing down or not yet
deploying the scheduler infrastructure must never break the API's create/edit flow, and a silently
swallowed failure must still be visible to monitoring, which keys on WARNING.

Layering: this module takes **primitives** — an id, an expression, a zone, a payload dict — and
never imports :mod:`core.follow_ups`. ``common/`` is a leaf (enforced in ``common/.ruff.toml``); the
handler is the composition root that turns a ``ReminderSchedule`` into these arguments.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from common.logger import logger

#: Everything a scheduler call can raise. botocore has **two independent roots** —
#: ``ClientError`` (the service returned an error response) and ``BotoCoreError`` (client-side:
#: ``EndpointConnectionError``, ``ConnectTimeoutError``, ``ReadTimeoutError``,
#: ``NoCredentialsError``) — and neither is a subclass of the other. Catching only ``ClientError``
#: would let exactly the *likely* Lambda failures escape into the API's catch-all 500, which would
#: fail the user's request **after the follow-up row had already been committed** — the opposite of
#: the graceful degradation this module promises.
_SCHEDULER_ERRORS = (ClientError, BotoCoreError)

#: Name of the EventBridge Scheduler group the schedules live in, set by the Api stack.
SCHEDULER_GROUP_ENV = "SCHEDULER_GROUP_NAME"

#: ARN of the notify Lambda a fired schedule invokes.
SCHEDULER_NOTIFY_ARN_ENV = "SCHEDULER_NOTIFY_ARN"

#: ARN of the role EventBridge assumes to invoke that Lambda.
SCHEDULER_ROLE_ARN_ENV = "SCHEDULER_ROLE_ARN"

_client_instance: Any = None


def _client() -> Any:
    """Return the lazily created ``scheduler`` client (the seam tests monkeypatch).

    Created on first use rather than at import, matching ``common.storage._client`` and
    ``common.secrets._client``, so a client construction problem fails a request instead of the
    whole Lambda's cold start.
    """
    global _client_instance
    if _client_instance is None:
        region = os.environ.get("AWS_REGION")
        _client_instance = (
            boto3.client("scheduler", region_name=region) if region else boto3.client("scheduler")
        )
    return _client_instance


def reset_client() -> None:
    """Drop the cached client. For tests, which swap the seam between cases."""
    global _client_instance
    _client_instance = None


def schedule_name(follow_up_id: int) -> str:
    """Return the deterministic schedule name for a follow-up: ``followup-<id>``.

    Parameters
    ----------
    follow_up_id : int
        The ``follow_ups.id``.

    Returns
    -------
    str
        A name derived purely from the id, so it is recomputable at any time and never stored.

    Examples
    --------
    >>> schedule_name(42)
    'followup-42'
    """
    return f"followup-{follow_up_id}"


def _config() -> tuple[str, str, str] | None:
    """Return ``(group, notify_arn, role_arn)``, or None (logging a WARNING) if incomplete.

    Read at call time, not at import: the environment is monkeypatched by tests and set per
    function by CDK, and module-scope reads would freeze whatever happened to be present when the
    module first loaded.
    """
    group = os.environ.get(SCHEDULER_GROUP_ENV, "")
    notify_arn = os.environ.get(SCHEDULER_NOTIFY_ARN_ENV, "")
    role_arn = os.environ.get(SCHEDULER_ROLE_ARN_ENV, "")
    if group and notify_arn and role_arn:
        return group, notify_arn, role_arn
    missing = [
        name
        for name, value in (
            (SCHEDULER_GROUP_ENV, group),
            (SCHEDULER_NOTIFY_ARN_ENV, notify_arn),
            (SCHEDULER_ROLE_ARN_ENV, role_arn),
        )
        if not value
    ]
    logger.warning("scheduler: not configured, missing %s; reminder email will not fire", missing)
    return None


def put_schedule(*, follow_up_id: int, expression: str, timezone: str, payload: dict) -> bool:
    """Create or replace the one-time schedule for a follow-up.

    Parameters
    ----------
    follow_up_id : int
        The ``follow_ups.id``; the schedule name is derived from it.
    expression : str
        A one-time ``at(YYYY-MM-DDTHH:MM:SS)`` expression in **local wall-clock time**, from
        ``core.follow_ups.schedule_expression``. No ``Z`` and no offset.
    timezone : str
        The user's IANA zone, which is how EventBridge interprets ``expression``. Never ``"UTC"``
        unless the user's zone genuinely is UTC.
    payload : dict
        The reminder context the notify Lambda renders, from
        ``core.follow_ups.ReminderSchedule.payload``. ``follow_up_id`` is merged in here from the
        argument, so the payload's id and the schedule's name can never disagree.

    Returns
    -------
    bool
        ``True`` when the schedule is in place. ``False`` when the scheduler environment is
        unconfigured or the API call failed — both already logged. The caller continues either way:
        the follow-up row is the source of truth and the reminder is a side effect.

    Examples
    --------
    >>> import os
    >>> _ = os.environ.pop("SCHEDULER_GROUP_NAME", None)
    >>> put_schedule(follow_up_id=1, expression="at(2026-08-01T07:00:00)",
    ...              timezone="Pacific/Honolulu", payload={})
    False
    """
    config = _config()
    if config is None:
        return False
    group, notify_arn, role_arn = config

    name = schedule_name(follow_up_id)
    request = {
        "Name": name,
        "GroupName": group,
        "ScheduleExpression": expression,
        "ScheduleExpressionTimezone": timezone,
        # OFF is required for a one-time schedule: any flex window lets the fire time drift, and a
        # reminder that arrives at an arbitrary hour is not the 07:00 the design promises.
        "FlexibleTimeWindow": {"Mode": "OFF"},
        # Self-clean once fired, or the group accumulates dead schedules forever.
        "ActionAfterCompletion": "DELETE",
        "Target": {
            "Arn": notify_arn,
            "RoleArn": role_arn,
            "Input": json.dumps({**payload, "follow_up_id": follow_up_id}),
        },
    }

    client = _client()
    logger.info("scheduler: putting schedule name=%s at=%s tz=%s", name, expression, timezone)
    try:
        client.create_schedule(**request)
        return True
    except _SCHEDULER_ERRORS as exc:
        # Only a ClientError carries a service error code; a BotoCoreError never conflicts, it
        # simply never reached the service, so it falls straight through to the failure branch.
        code = exc.response.get("Error", {}).get("Code") if isinstance(exc, ClientError) else None
        if code != "ConflictException":
            # Swallow-and-report: the caller has no recovery path, and failing the whole request
            # would lose a follow-up the user did successfully create. WARNING-and-above is what
            # monitoring watches, so this cannot pass silently.
            logger.exception("scheduler: create_schedule failed for %s", name)
            return False
    logger.info("scheduler: schedule %s exists, replacing it", name)
    try:
        client.update_schedule(**request)
        return True
    except _SCHEDULER_ERRORS:
        logger.exception("scheduler: update_schedule failed for %s", name)
        return False


def delete_schedule(*, follow_up_id: int) -> bool:
    """Cancel a follow-up's schedule; a schedule that is already gone counts as success.

    Called when a follow-up is deleted, completed, or edited into a state that should not remind
    (acceptance #3 and #7). It never needs to know whether the reminder already fired: a fired
    one-time schedule deletes itself, and cancelling a schedule that does not exist is harmless.

    Parameters
    ----------
    follow_up_id : int
        The ``follow_ups.id`` whose schedule should be cancelled.

    Returns
    -------
    bool
        ``True`` when no schedule remains — deleted now or already absent. ``False`` when the
        scheduler environment is unconfigured or the API call failed, both already logged.
    """
    config = _config()
    if config is None:
        return False
    group, _, _ = config

    name = schedule_name(follow_up_id)
    client = _client()
    logger.info("scheduler: deleting schedule name=%s", name)
    try:
        client.delete_schedule(Name=name, GroupName=group)
        return True
    except _SCHEDULER_ERRORS as exc:
        code = exc.response.get("Error", {}).get("Code") if isinstance(exc, ClientError) else None
        if code == "ResourceNotFoundException":
            # Already fired and self-deleted, or never created (scheduler stack absent when the row
            # was made). Either way there is nothing to cancel, which is the desired end state.
            logger.info("scheduler: schedule %s already absent", name)
            return True
        logger.exception("scheduler: delete_schedule failed for %s", name)
        return False
