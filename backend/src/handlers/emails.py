"""Emails router — sending, replying, and reading threads.

Everything lives under ``/emails`` so the whole feature is one prefix in the CDK ROUTES table:
``POST /emails/send``, ``POST /emails/threads/{id}/replies``, ``GET /emails/threads``,
``GET /emails/threads/{id}``, ``POST /emails/threads/{id}/{read,close,reopen}``, and
``POST /emails/attachments`` for the presigned upload.

**Sending is the one place every piece of slice 6a meets**, and its ordering is the design
(DEV-PLAN acceptance #2, and see :mod:`repositories.email_sends` for why intent comes first):

1. mint the ``Message-ID``, fetch attachment bytes, build the MIME — all before any write, so a
   malformed request costs nothing;
2. store the raw MIME in S3;
3. **TXN 1** — thread + message (``sent_at`` NULL) + outreach;
4. **SES** — on a *clean* failure, compensate (**TXN 3**) and return the error: no rows survive;
5. IMAP ``APPEND`` to Sent — best-effort, WARNING only, never fatal (decision #2);
6. **TXN 2** — confirm, stamping ``sent_at`` and advancing the thread.

The ``APPEND`` deliberately precedes the confirm: the Sent folder is what 6b's poller reconciles
pending messages from, so populating it at the earliest possible moment widens the self-healing
window. A crash between 4 and 6 leaves a *pending* message — recorded, recoverable, never lost.

Reads reconstruct bodies and attachment metadata from the stored MIME, because ``0008`` has no
column for either. A message whose object cannot be fetched still appears in its thread with a
null body and a WARNING: one unreadable object must not 500 an entire conversation.
"""

from __future__ import annotations

import uuid

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors, mail, storage
from common.db import transaction
from common.imap import append_to_sent_best_effort
from common.logger import logger
from core.email_headers import build_reply_headers, generate_message_id
from handlers.context import authenticate
from handlers.params import path_int
from models.emails import (
    EmailAttachment,
    EmailMessageDetail,
    EmailMessageSummary,
    EmailReplyInput,
    EmailSendInput,
    EmailSendResult,
    EmailThreadDetail,
    EmailThreadSummary,
)
from repositories import email_sends, email_threads

router = Router()


def _sender_domain(sender: str) -> str:
    """Extract the domain from a ``From`` value, for minting the ``Message-ID``."""
    address = sender.rsplit("<", 1)[-1].rstrip(">") if "<" in sender else sender
    domain = address.rsplit("@", 1)[-1].strip()
    if not domain:
        raise errors.InvalidInput("sender address has no domain")
    return domain


def _fetch_attachments(attachments) -> list[mail.Attachment]:
    """Read each already-uploaded attachment out of S3 for MIME assembly."""
    fetched: list[mail.Attachment] = []
    for item in attachments:
        fetched.append(
            mail.Attachment(
                filename=item.filename,
                content_type=item.content_type,
                content=storage.get_object_bytes(item.s3_key),
            )
        )
    return fetched


def _deliver(
    request,
    data: EmailSendInput,
    *,
    thread_id: int | None,
    in_reply_to: str | None,
    references: str | None,
) -> dict:
    """Run the six-step send for a new message or a reply, and return the result payload.

    Shared by :func:`send_email` and :func:`reply_to_thread` so the transaction ordering exists
    once — a second copy of this sequence is how the two paths would drift apart.
    """
    sender = mail.from_address()
    message_id = generate_message_id(_sender_domain(sender))
    destinations = [*data.to, *data.cc]

    # 1. Everything that can fail cheaply happens before the first write.
    raw = mail.build_raw_message(
        sender=sender,
        to=data.to,
        subject=data.subject,
        body_html=data.body_html,
        message_id=message_id,
        cc=data.cc,
        in_reply_to=in_reply_to,
        references=references,
        attachments=_fetch_attachments(data.attachments),
    )

    # 2. The stored copy is byte-identical to what SES and IMAP receive.
    s3_key = storage.put_object(
        storage.raw_message_key(request.user_id, message_id),
        raw,
        content_type="message/rfc822",
    )

    # 3. Intent first: after this commit the send is recorded, whatever happens next.
    with transaction(request.connection) as conn:
        pending = email_sends.create_pending_send(
            conn,
            request.user_id,
            data,
            message_id=message_id,
            from_addr=sender,
            s3_key=s3_key,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
            message_references=references,
        )
    logger.info(
        "Send pending message_id=%s row_id=%s thread_id=%s user_id=%s",
        message_id,
        pending.message_row_id,
        pending.thread_id,
        request.user_id,
    )

    # 4. SES. A clean failure means nothing was transmitted, so the intent is compensated away.
    try:
        ses_message_id = mail.send_raw(raw, sender=sender, destinations=destinations)
    except Exception:
        logger.exception(
            "SES send failed; compensating message_id=%s row_id=%s",
            message_id,
            pending.message_row_id,
        )
        with transaction(request.connection) as conn:
            email_sends.discard_pending_send(conn, request.user_id, pending)
        raise

    # 5. Sent-folder copy: best-effort by decision #2, and placed before the confirm so the
    #    folder 6b reconciles from is populated as early as possible.
    appended = append_to_sent_best_effort(raw)

    # 6. Confirm. A failure here leaves a pending row — recorded, not lost — so it is logged
    #    loudly rather than surfaced as a failed send the user would retry (and double-send).
    try:
        with transaction(request.connection) as conn:
            email_sends.confirm_send(conn, request.user_id, pending.message_row_id)
    except Exception:
        logger.exception(
            "Email SENT but confirm failed — message is pending reconciliation "
            "message_id=%s ses_message_id=%s row_id=%s user_id=%s",
            message_id,
            ses_message_id,
            pending.message_row_id,
            request.user_id,
        )

    logger.info(
        "Send complete message_id=%s ses_message_id=%s thread_id=%s outreach_id=%s "
        "sent_folder_copy=%s user_id=%s",
        message_id,
        ses_message_id,
        pending.thread_id,
        pending.outreach_id,
        appended,
        request.user_id,
    )

    row = email_threads.get_message(request.connection, request.user_id, pending.message_row_id)
    return EmailSendResult(
        message=EmailMessageSummary(**row),
        thread_id=pending.thread_id,
        outreach_id=pending.outreach_id,
    ).model_dump(mode="json")


@router.post("/emails/send")
def send_email() -> dict:
    """Send a new email, opening a thread."""
    request = authenticate(router.current_event.raw_event)
    data = EmailSendInput.model_validate(router.current_event.json_body or {})
    return _deliver(request, data, thread_id=None, in_reply_to=None, references=None)


@router.post("/emails/threads/<thread_id>/replies")
def reply_to_thread(thread_id: str) -> dict:
    """Reply on an existing thread, threading via the parent's stored ``Message-ID``.

    Recipients and subject default from the parent unless the client overrides them; the
    threading headers are always derived server-side (acceptance #3), never accepted from the
    client, so a malformed request cannot produce mail that threads nowhere.
    """
    request = authenticate(router.current_event.raw_event)
    thread_row_id = path_int(thread_id)
    data = EmailReplyInput.model_validate(router.current_event.json_body or {})

    thread = email_threads.get_thread(request.connection, request.user_id, thread_row_id)
    if thread is None:
        raise errors.NotFound("thread not found")

    if data.in_reply_to_message_id is not None:
        parent = email_threads.get_message(
            request.connection, request.user_id, data.in_reply_to_message_id
        )
        if parent is None or parent["thread_id"] != thread_row_id:
            raise errors.InvalidInput("in_reply_to_message_id is not a message on this thread")
    else:
        parent = email_threads.get_latest_message(
            request.connection, request.user_id, thread_row_id
        )
        if parent is None:
            raise errors.InvalidInput("thread has no confirmed message to reply to")

    headers = build_reply_headers(parent["message_id"], parent["message_references"])
    subject = parent["subject"] or thread["subject_normalized"]
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    send_input = EmailSendInput(
        to=data.to if data.to is not None else _reply_recipients(parent),
        cc=data.cc if data.cc is not None else parent["cc_addr"],
        subject=subject,
        body_html=data.body_html,
        contact_id=thread["contact_id"],
        opportunity_id=thread["opportunity_id"],
        outreach_kind=data.outreach_kind,
        attachments=data.attachments,
    )
    return _deliver(
        request,
        send_input,
        thread_id=thread_row_id,
        in_reply_to=headers.in_reply_to,
        references=headers.references,
    )


def _reply_recipients(parent: dict) -> list[str]:
    """Derive a reply's recipients from the message being replied to.

    Replying to inbound mail goes back to its sender; replying to our own outbound message goes
    to the same recipients (the venue), not to ourselves.
    """
    if parent["direction"] == "in":
        return [parent["from_addr"]]
    return list(parent["to_addr"])


@router.get("/emails/threads")
def list_threads() -> dict:
    """Return the caller's threads for the Emails inbox."""
    request = authenticate(router.current_event.raw_event)
    params = router.current_event.query_string_parameters or {}
    include_closed = str(params.get("include_closed", "")).lower() in {"1", "true", "yes"}

    rows = email_threads.list_threads(
        request.connection, request.user_id, include_closed=include_closed
    )
    return {
        "threads": [EmailThreadSummary(**row).model_dump(mode="json") for row in rows],
    }


@router.get("/emails/threads/<thread_id>")
def get_thread(thread_id: str) -> dict:
    """Return one thread with its full conversation, bodies reconstructed from stored MIME."""
    request = authenticate(router.current_event.raw_event)
    thread_row_id = path_int(thread_id)

    thread = email_threads.get_thread_with_messages(
        request.connection, request.user_id, thread_row_id
    )
    if thread is None:
        raise errors.NotFound("thread not found")

    messages = [_message_detail(row) for row in thread.pop("messages")]
    return EmailThreadDetail(**thread, messages=messages).model_dump(mode="json")


def _message_detail(row: dict) -> EmailMessageDetail:
    """Build a message detail, filling body and attachments from S3 when the object is readable.

    A fetch or parse failure degrades to a null body rather than propagating: one unreadable
    object must not 500 the whole conversation, and the WARNING makes the gap visible.
    """
    detail = EmailMessageDetail(**row)
    if not row.get("s3_key"):
        return detail

    try:
        parsed = mail.parse_raw_message(storage.get_object_bytes(row["s3_key"]))
    except Exception:
        logger.warning(
            "Could not read stored MIME; message listed without a body s3_key=%s message_id=%s",
            row["s3_key"],
            row["message_id"],
            exc_info=True,
        )
        return detail

    # Constructed, not passed as dicts: model_copy(update=...) skips validation, so raw dicts
    # would survive here and only surface as a serializer warning at response time.
    return detail.model_copy(
        update={
            "body_html": parsed.body_html or parsed.body_text,
            "attachments": [
                EmailAttachment(
                    filename=a.filename,
                    content_type=a.content_type,
                    size_bytes=a.size_bytes,
                )
                for a in parsed.attachments
            ],
        }
    )


@router.post("/emails/threads/<thread_id>/read")
def mark_thread_read(thread_id: str) -> dict:
    """Stamp the thread's read marker."""
    request = authenticate(router.current_event.raw_event)
    thread_row_id = path_int(thread_id)
    with transaction(request.connection) as conn:
        updated = email_threads.mark_thread_read(conn, request.user_id, thread_row_id)
    if not updated:
        raise errors.NotFound("thread not found")
    return {"read": True}


@router.post("/emails/threads/<thread_id>/close")
def close_thread(thread_id: str) -> dict:
    """Close a thread explicitly — never inferred from who replied last."""
    request = authenticate(router.current_event.raw_event)
    thread_row_id = path_int(thread_id)
    with transaction(request.connection) as conn:
        closed = email_threads.close_thread(conn, request.user_id, thread_row_id)
    if not closed:
        raise errors.NotFound("thread not found or already closed")
    logger.info("Closed thread id=%s user_id=%s", thread_row_id, request.user_id)
    return {"closed": True}


@router.post("/emails/threads/<thread_id>/reopen")
def reopen_thread(thread_id: str) -> dict:
    """Reopen a closed thread."""
    request = authenticate(router.current_event.raw_event)
    thread_row_id = path_int(thread_id)
    with transaction(request.connection) as conn:
        reopened = email_threads.reopen_thread(conn, request.user_id, thread_row_id)
    if not reopened:
        raise errors.NotFound("thread not found or already open")
    return {"reopened": True}


@router.post("/emails/attachments")
def create_attachment_upload() -> dict:
    """Issue a presigned PUT so the composer uploads attachment bytes directly to S3.

    Attachment bytes never pass through the API (acceptance #6). The key is server-generated and
    user-scoped — accepting a client-supplied key would let one caller write under another's
    prefix.
    """
    request = authenticate(router.current_event.raw_event)
    body = router.current_event.json_body or {}
    filename = str(body.get("filename") or "").strip()
    if not filename:
        raise errors.InvalidInput("filename is required")
    content_type = str(body.get("content_type") or "application/octet-stream").strip()

    key = f"{storage.ATTACHMENT_PREFIX}{request.user_id}/{uuid.uuid4().hex}/{filename}"
    url = storage.presigned_put_url(key, content_type=content_type)
    logger.info(
        "Issued attachment upload key=%s user_id=%s content_type=%s",
        key,
        request.user_id,
        content_type,
    )
    return {"upload_url": url, "s3_key": key, "content_type": content_type}
