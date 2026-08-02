"""Dead-letter consumer — records that a follow-up's reminder email never went out.

When a reminder exhausts its retry budget, EventBridge Scheduler drops the event on an SQS queue and
this function marks the row, so the app can say *"this nudge did not go out"* instead of showing a
follow-up that looks identical to one whose email arrived fine.

**Why a separate function rather than a write-back in ``followup_notify``.** That handler's defining
property is that it never touches the database — no VPC, no RDS handshake, nothing to fail on the
happy path. Making it report its own failures would trade that away for a case that should be rare.
Putting the write here means the **happy path stays database-free and only the failure path pays**,
which is the right side of that trade.

**What this can and cannot say.** It records that a reminder *failed*; nothing records that one
*arrived*. There is no ``reminder_sent_at`` and this function cannot supply one — it only ever runs
when something went wrong. So ``reminder_failed_at IS NULL`` means "nothing has gone wrong",
covering both a reminder that sent and one that has not fired yet.

**Idempotency.** SQS delivers at least once, so the same dead-lettered reminder can arrive twice.
Stamping an already-stamped row is harmless, and a follow-up deleted in the meantime simply matches
nothing — neither is an error.

**Partial batch failure.** A record this cannot handle is reported through
``batchItemFailures`` rather than raising, so one malformed message does not send an entire batch of
otherwise-fine failures back for redelivery.
"""

from __future__ import annotations

import json
import time
from typing import Any

from aws_lambda_powertools.utilities.typing import LambdaContext

from common.db import get_connection, transaction
from common.logger import elapsed_ms, logger
from repositories import follow_ups as follow_ups_repo

#: The zone the connection's session clock is set to. Nothing here is timezone-sensitive — the
#: column is stamped with the database's ``CURRENT_TIMESTAMP`` — but ``get_connection`` requires a
#: zone, and using the user's keeps the stamp consistent with every other timestamp on the row.
CONSUMER_TIMEZONE = "Pacific/Honolulu"


def extract_follow_up_id(body: str) -> int | None:
    """Pull the ``follow_up_id`` out of a dead-lettered message body.

    The body is expected to be the schedule target's ``Input`` — the payload
    ``core.follow_ups.ReminderSchedule.payload`` built — in which case ``follow_up_id`` is a
    top-level key. **That shape is documented rather than observed:** no reminder has yet been
    dead-lettered in this app, so this also searches one level of nesting and, failing that, returns
    ``None`` so the caller can log the raw body. The first real failure will settle the shape, and
    the log line is what will show it.

    Parameters
    ----------
    body : str
        Raw SQS message body.

    Returns
    -------
    int or None
        The follow-up id, or ``None`` when the body cannot be parsed or carries no id.

    Examples
    --------
    >>> extract_follow_up_id('{"follow_up_id": 42, "note": "x"}')
    42
    >>> extract_follow_up_id('{"detail": {"follow_up_id": 7}}')
    7
    >>> extract_follow_up_id('not json') is None
    True
    """
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None

    candidate = parsed.get("follow_up_id")
    if candidate is None:
        for value in parsed.values():
            if isinstance(value, dict) and "follow_up_id" in value:
                candidate = value["follow_up_id"]
                break
    try:
        return int(candidate)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@logger.inject_lambda_context(log_event=False)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """Mark every follow-up named in this batch of dead-lettered reminders.

    Parameters
    ----------
    event : dict
        An SQS event: ``{"Records": [{"messageId": ..., "body": ...}, ...]}``.
    context : LambdaContext
        ``aws_request_id`` is the correlation id tying the entry and exit lines together.

    Returns
    -------
    dict
        ``{"batchItemFailures": [{"itemIdentifier": <messageId>}, ...]}`` — empty when every record
        was handled. Records listed here are redelivered; everything else is deleted from the queue.
    """
    correlation_id = context.aws_request_id
    start = time.monotonic()
    records: list[dict[str, Any]] = event.get("Records") or []
    logger.info(
        "Reminder-failure batch start correlation_id=%s records=%s", correlation_id, len(records)
    )

    failures: list[dict[str, str]] = []
    marked = 0

    for record in records:
        message_id = record.get("messageId", "")
        follow_up_id = extract_follow_up_id(record.get("body") or "")
        if follow_up_id is None:
            # Not retryable — redelivering an unparseable body just repeats this. Logged with the
            # raw body because that body is the only description of the payload shape we have.
            logger.error(
                "Dead-lettered reminder carried no follow_up_id; dropping it. "
                "correlation_id=%s message_id=%s body=%r",
                correlation_id,
                message_id,
                record.get("body"),
            )
            continue

        try:
            connection = get_connection(CONSUMER_TIMEZONE)
            with transaction(connection) as conn:
                found = follow_ups_repo.mark_reminder_failed(conn, follow_up_id)
        except Exception:
            # Retryable: the database was unreachable, not the message's fault.
            logger.exception(
                "Could not record a reminder failure; the message will be redelivered. "
                "correlation_id=%s message_id=%s follow_up_id=%s",
                correlation_id,
                message_id,
                follow_up_id,
            )
            failures.append({"itemIdentifier": message_id})
            continue

        if found:
            marked += 1
            # ERROR, not INFO: a reminder Donna was relying on did not reach her.
            logger.error(
                "Reminder was NOT delivered and is now flagged in the app. "
                "correlation_id=%s follow_up_id=%s",
                correlation_id,
                follow_up_id,
            )
        else:
            logger.warning(
                "Dead-lettered reminder names a follow-up that no longer exists; nothing to flag. "
                "correlation_id=%s follow_up_id=%s",
                correlation_id,
                follow_up_id,
            )

    logger.info(
        "Reminder-failure batch end correlation_id=%s records=%s marked=%s retryable=%s "
        "duration_ms=%s",
        correlation_id,
        len(records),
        marked,
        len(failures),
        elapsed_ms(start),
    )
    return {"batchItemFailures": failures}
