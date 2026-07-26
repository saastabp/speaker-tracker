"""Pydantic contracts for the email send path and thread reads.

Sending is the project's only multi-row atomic write (DESIGN.md §"Write invariant — one transaction
per send"): one ``email_messages`` row, its ``email_threads`` row (created or touched), and an
``outreaches`` row, committed together *after* SES has accepted the message.
:class:`EmailSendResult` returns the ids of all three so a client never has to re-query to know
what the send produced.

Shape notes, and why:

- **``body_html`` is the whole body, signature included.** The Tiptap composer appends the user's
  default signature into the editable body client-side so it can be edited before sending; the
  server never re-appends one. A server-side append would fight the editor and double up on reply.
- **No follow-up rider fields.** DEV-PLAN slice 6a acceptance #7 ("the rider is off by default")
  is satisfied here by there being no rider at all — ``follow_ups`` arrives in migration ``0009``,
  a later slice. Adding fields now would wire a checkbox to a table that does not exist.
- **Addresses are plain ``str``, not ``EmailStr``.** ``models/contacts.py`` already stores addresses
  as ``str``, and ``EmailStr`` would pull ``email-validator`` into the Lambda bundle for a check SES
  performs authoritatively anyway. :func:`_validated_addresses` enforces only the cheap invariant —
  non-blank and containing ``@`` — so an obvious typo fails before we pay for a send.
- **Bodies and attachment metadata are not columns.** ``0008`` stores neither, so reads reconstruct
  both from the raw MIME at ``email_messages.s3_key``, written to S3 at send time (the same bytes
  handed to the IMAP ``APPEND``). ``body_html``/``attachments`` on the detail models are therefore
  populated by the handler from S3, not by a SELECT.
- **Catalog vocabularies by short_name** (``outreach_kind``), entities by id — the project's
  Option-A wire rule, as in ``models/outreach.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

#: ``email_messages.direction`` / ``email_threads.last_direction`` — an ENUM('out','in') in MySQL.
EmailDirection = Literal["out", "in"]


def _validated_addresses(addresses: list[str]) -> list[str]:
    """Strip and sanity-check a list of recipient addresses.

    Parameters
    ----------
    addresses : list of str
        Raw addresses from the composer.

    Returns
    -------
    list of str
        The addresses, whitespace-stripped, in the order given.

    Raises
    ------
    ValueError
        If any entry is blank or lacks an ``@``. This is a typo guard, not RFC 5321 validation —
        SES remains the authority on deliverability.
    """
    cleaned: list[str] = []
    for address in addresses:
        stripped = address.strip()
        if not stripped or "@" not in stripped:
            raise ValueError(f"not a valid email address: {address!r}")
        cleaned.append(stripped)
    return cleaned


class EmailAttachmentInput(BaseModel):
    """An ad-hoc attachment the composer has already uploaded by presigned PUT.

    Slice 6a supports ad-hoc attachments only; the reusable materials library (attach a one-sheet
    in one step) is deferred to its own slice, so there is no ``material_id`` here.

    Parameters
    ----------
    s3_key : str
        Key the composer PUT the bytes to. The server fetches from this key when building MIME —
        the client never sends attachment bytes through the API.
    filename : str
        Name to present in the MIME part (``Content-Disposition: attachment; filename=``).
    content_type : str
        MIME type recorded at upload; defaults to ``application/octet-stream`` when unknown.
    size_bytes : int or None
        Byte count, used to reject an oversized send before fetching from S3.
    """

    s3_key: str = Field(min_length=1, max_length=255)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream", min_length=1, max_length=255)
    size_bytes: int | None = Field(default=None, ge=0)


class EmailAttachment(BaseModel):
    """An attachment as presented on a stored message.

    Parsed out of the raw MIME at read time (``0008`` has no attachments table), so it carries no
    ``s3_key`` — the part lives inside the stored message, not at a separate key.
    """

    filename: str
    content_type: str
    size_bytes: int | None = None


class EmailSendInput(BaseModel):
    """A new outbound email, opening a new thread.

    Parameters
    ----------
    to : list of str
        Primary recipients; at least one.
    subject : str
        Subject line as typed. The server derives ``email_threads.subject_normalized`` from it
        (``core.email_headers.normalize_subject``).
    body_html : str
        Full HTML body from the Tiptap composer, **including the signature** the editor appended.
    cc : list of str
        Carbon-copy recipients; may be empty.
    contact_id : int or None
        Contact this message goes to. Optional because a first email may precede the contact
        record, matching the nullable FKs on ``email_threads`` / ``email_messages``. When present,
        the send also writes the ``outreaches`` row that logs the touch.
    opportunity_id : int or None
        Gig to attribute the message to, when the conversation is about one.
    message_template_id : int or None
        Template the body was composed from, recorded on the ``outreaches`` row.
    outreach_kind : str or None
        ``outreach_kinds`` short_name overriding the inferred default. Omit to accept
        ``core.outreach.resolve_outreach_kind``'s contact-scoped inference (initial /
        correspondence).
    attachments : list of EmailAttachmentInput
        Already-uploaded attachments to include; may be empty.
    """

    to: list[str] = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=255)
    body_html: str = Field(min_length=1)
    cc: list[str] = Field(default_factory=list)
    contact_id: int | None = None
    opportunity_id: int | None = None
    message_template_id: int | None = None
    outreach_kind: str | None = None
    attachments: list[EmailAttachmentInput] = Field(default_factory=list)

    @field_validator("to", "cc")
    @classmethod
    def _check_addresses(cls, value: list[str]) -> list[str]:
        return _validated_addresses(value)


class EmailReplyInput(BaseModel):
    """A reply on an existing thread.

    Recipients and subject default from the thread server-side, so the common case is a body and
    nothing else. The reply's ``In-Reply-To`` / ``References`` are assembled from the parent's
    stored ``Message-ID`` (``core.email_headers.build_reply_headers``, acceptance #3) — never sent
    by the client.

    Parameters
    ----------
    body_html : str
        Full HTML body from the composer, signature included.
    in_reply_to_message_id : int or None
        ``email_messages.id`` of the message being replied to. Omit to reply to the thread's most
        recent message, which is what the inline reply box does.
    to : list of str or None
        Override the derived recipients; ``None`` keeps the server's derivation.
    cc : list of str or None
        Override the derived Cc list; ``None`` keeps the server's derivation.
    outreach_kind : str or None
        ``outreach_kinds`` short_name overriding the inferred default for the logged touch.
    attachments : list of EmailAttachmentInput
        Already-uploaded attachments to include; may be empty.
    """

    body_html: str = Field(min_length=1)
    in_reply_to_message_id: int | None = None
    to: list[str] | None = None
    cc: list[str] | None = None
    outreach_kind: str | None = None
    attachments: list[EmailAttachmentInput] = Field(default_factory=list)

    @field_validator("to", "cc")
    @classmethod
    def _check_addresses(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _validated_addresses(value)


class EmailMessageSummary(BaseModel):
    """A stored message's envelope — everything that comes from a single ``email_messages`` row.

    ``to_addr`` / ``cc_addr`` are TEXT columns holding comma-separated addresses; the repository
    splits them into lists so clients never parse header text.
    """

    id: int
    thread_id: int
    direction: EmailDirection
    message_id: str
    subject: str | None
    from_addr: str
    to_addr: list[str]
    cc_addr: list[str]
    sent_at: datetime | None
    received_at: datetime | None


class EmailMessageDetail(EmailMessageSummary):
    """A message with the parts that come from its stored MIME rather than its row.

    Both fields are ``None``/empty when ``s3_key`` is NULL or the object cannot be fetched — a
    message whose body is unavailable still lists in its thread rather than breaking the view.
    """

    body_html: str | None = None
    attachments: list[EmailAttachment] = Field(default_factory=list)


class EmailThreadSummary(BaseModel):
    """A conversation as it appears in the Emails inbox list.

    ``contact_name`` is denormalized for display, as on ``OutreachSummary``. ``closed_at`` is set
    explicitly or when the linked opportunity closes — never inferred from ``last_direction``.

    ``message_count`` and ``pending_count`` are **derived** — aggregates over the thread's
    ``email_messages`` rows computed on every read, not stored columns. ``pending_count`` counts
    outbound messages still awaiting confirmation (``direction='out' AND sent_at IS NULL``): one
    that reached SES but whose confirm never landed, or one still in flight. Non-zero drives the
    "pending" badge; such threads sort last, having no ``last_message_at`` to sort by. It returns
    to zero as soon as ``repositories.email_sends.confirm_send`` sets ``sent_at``.
    """

    id: int
    subject_normalized: str
    contact_id: int | None
    contact_name: str | None
    opportunity_id: int | None
    last_direction: EmailDirection
    last_message_at: datetime | None
    last_read_at: datetime | None
    closed_at: datetime | None
    message_count: int
    pending_count: int = 0


class EmailThreadDetail(EmailThreadSummary):
    """A thread with its full conversation, oldest first — the thread view."""

    messages: list[EmailMessageDetail] = Field(default_factory=list)


class EmailSendResult(BaseModel):
    """What one send produced: the three rows written in a single transaction.

    ``outreach_id`` is ``None`` when the message had no ``contact_id`` — there is no contact to log
    a touch against, and ``outreaches.contact_id`` is NOT NULL.
    """

    message: EmailMessageSummary
    thread_id: int
    outreach_id: int | None
