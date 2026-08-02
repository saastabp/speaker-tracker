"""Follow-up reminder notifier — the Lambda an EventBridge schedule invokes when a reminder is due.

**This function never touches the database, and the module deliberately imports nothing that could
let it.** No ``common.db``, no ``repositories``. Everything the email renders arrives in the
schedule's ``Input``, frozen when the schedule was created (``core.follow_ups.ReminderSchedule``),
which is what lets this run outside the VPC with no RDS handshake and no connection to manage.

The cost of that is the rule the rest of slice 7 is built around: because this function cannot see
the row, **any edit to a field rendered below must cancel and recreate the schedule** — including
marking the follow-up done, or it would email Donna about something she has already finished. That
reconciliation lives in ``handlers/follow_ups.py``; this end simply trusts its payload.

The event **is** the payload. EventBridge Scheduler delivers the target's ``Input`` as the entire
event, so there is no envelope to unwrap and no ``Records`` list to iterate.

**Failures raise.** A malformed payload or a rejected send fails the invocation on purpose: it ticks
the Lambda ``Errors`` metric the alarm watches, and lets the schedule's retry policy re-attempt a
transient SES problem. Returning a cheerful ``{"status": "ok"}`` after failing to send would make a
reminder that never arrived indistinguishable from one that did — and since nothing is written back
to the database, these logs are the *only* record that a reminder fired at all.
"""

from __future__ import annotations

import html
import time
from datetime import date

from aws_lambda_powertools.utilities.typing import LambdaContext

from common import mail
from common.logger import elapsed_ms, logger
from core.email_headers import generate_message_id

#: Joins the contact and gig labels when a reminder names both.
_LABEL_SEPARATOR = " · "


def _label(payload: dict) -> str:
    """Return the human label for what this reminder is about.

    A follow-up names a contact, a gig, or both (``ck_follow_ups_target`` guarantees at least one),
    so the subject line is built from whichever are present rather than assuming either.
    """
    parts = [payload.get("contact_name"), payload.get("opportunity_title")]
    return _LABEL_SEPARATOR.join(p for p in parts if p) or "reminder"


def _format_due(raw: str | None) -> str:
    """Render the ISO due date for display, falling back to the raw value.

    The payload carries ``due_date`` as an ISO string because the schedule's ``Input`` is JSON. A
    value that will not parse is shown as-is rather than raising: the note is the substance of the
    reminder, and losing the whole email over a malformed date would be a worse outcome than a
    date that reads oddly.
    """
    if not raw:
        return "today"
    try:
        return date.fromisoformat(raw).strftime("%A, %B %-d, %Y")
    except ValueError:
        logger.warning("Reminder due_date %r is not ISO; rendering it verbatim", raw)
        return raw


def _body_html(payload: dict) -> str:
    """Build the reminder's HTML body.

    The note is **escaped** — it is free text Donna typed, and it is being interpolated into HTML.
    Newlines become ``<br>`` so a multi-line note survives, since the composer stores it as plain
    text rather than markup.
    """
    note = html.escape(payload.get("note") or "").replace("\n", "<br>")
    return (
        f"<p>Follow-up due <strong>{html.escape(_format_due(payload.get('due_date')))}</strong>"
        f" — {html.escape(_label(payload))}</p>"
        f"<p>{note}</p>"
    )


@logger.inject_lambda_context(log_event=False)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """Send one follow-up reminder email and report what happened.

    Parameters
    ----------
    event : dict
        The schedule's ``Input``, verbatim: ``follow_up_id``, ``to_address``, ``note``,
        ``due_date``, ``contact_name``, ``opportunity_title``. Not an envelope — EventBridge
        Scheduler delivers the target input as the whole event.
    context : LambdaContext
        ``aws_request_id`` is the correlation id tying the entry and exit lines together.

    Returns
    -------
    dict
        ``{"status": "sent", "follow_up_id": ..., "ses_message_id": ...}``.

    Raises
    ------
    ValueError
        When the payload carries no ``to_address`` — there is nobody to send to, and a schedule
        built without one is a bug in the creating handler that must be visible.
    Exception
        Any SES or MIME failure is logged with a stack trace and re-raised, so the invocation fails
        and EventBridge retries.
    """
    correlation_id = context.aws_request_id
    start = time.monotonic()
    follow_up_id = event.get("follow_up_id")
    logger.info("Reminder start correlation_id=%s follow_up_id=%s", correlation_id, follow_up_id)

    try:
        to_address = event.get("to_address")
        if not to_address:
            raise ValueError(f"reminder payload has no to_address (follow_up_id={follow_up_id})")

        sender = mail.from_address()
        raw = mail.build_raw_message(
            sender=sender,
            to=[to_address],
            subject=f"Follow-up due: {_label(event)}",
            body_html=_body_html(event),
            message_id=generate_message_id(mail.domain_of(sender)),
        )
        ses_message_id = mail.send_raw(raw, sender=sender, destinations=[to_address])
    except Exception:
        # The only record that this reminder was attempted — nothing is written back to the DB.
        logger.exception(
            "Reminder failed correlation_id=%s follow_up_id=%s duration_ms=%s",
            correlation_id,
            follow_up_id,
            elapsed_ms(start),
        )
        raise

    logger.info(
        "Reminder end correlation_id=%s follow_up_id=%s status=sent ses_message_id=%s "
        "duration_ms=%s",
        correlation_id,
        follow_up_id,
        ses_message_id,
        elapsed_ms(start),
    )
    return {"status": "sent", "follow_up_id": follow_up_id, "ses_message_id": ses_message_id}
