"""Raw MIME assembly and the SES send — the outbound half of the email path.

Two responsibilities, deliberately separable so the one with real logic needs no AWS to test:

- :func:`build_raw_message` is **pure**. It takes attachment *bytes* (fetched by
  :mod:`common.storage`, not here) and returns the exact octets that go to SES and to the IMAP
  ``APPEND``. Every header acceptance #3 depends on — ``Message-ID``, ``In-Reply-To``,
  ``References`` — is passed in from :mod:`core.email_headers`, so threading is verifiable with a
  string comparison and no mocking.
- :func:`send_raw` is the thin AWS edge: one ``send_raw_email`` call behind a lazy client seam.

**Every message is `multipart/alternative`.** Tiptap produces HTML, and a bare ``text/html`` body
with no plain alternative is a well-known spam signal — legitimate senders always carry both. The
plain part is derived mechanically from the HTML (see :func:`html_to_text`); nobody chooses
between them, the recipient's client picks. With attachments the alternative is nested inside a
``multipart/mixed``, which is the ordering every mail client expects:

    multipart/mixed
    ├── multipart/alternative
    │   ├── text/plain
    │   └── text/html
    └── application/pdf   (one part per attachment)

**SES is pinned to us-east-1** regardless of the app's region: that is where the sending identity
and the WorkMail mailbox live, and where production access was granted (per-region). Sending from
us-west-2 would hit an identity that does not exist there.

**Size is checked before sending.** SES rejects messages over 40 MB and base64 inflates
attachments by about a third. Failing here — before the caller's pending row is written and long
before SES sees it — turns a confusing mid-send rejection into an actionable error.
"""

from __future__ import annotations

import os
import re
import time
from email import message_from_bytes
from email.message import EmailMessage, Message
from email.utils import formataddr, formatdate
from typing import NamedTuple

import boto3

from common.logger import logger

#: Env var holding the sending address, set by the Messaging stack. Nothing hardcodes the mailbox.
MAIL_FROM_ENV = "MAIL_FROM_ADDRESS"

#: Optional display name paired with it, e.g. ``Donna King <donna@360balancedliving.com>``.
MAIL_FROM_NAME_ENV = "MAIL_FROM_NAME"

#: SES lives where the identity and mailbox are, not where the app runs.
SES_REGION = "us-east-1"

#: SES's hard cap on a raw message, in bytes (40 MB, base64 included).
MAX_MESSAGE_BYTES = 40 * 1024 * 1024

_client_instance = None

_TAG_RE = re.compile(r"<[^>]+>")

#: Block closes end a paragraph — a blank line between them, or the text reads as one run-on.
_PARAGRAPH_BREAK_RE = re.compile(r"(?i)</(?:p|div|h[1-6]|blockquote)>")

#: ``<br>`` and list/table rows are single line breaks; blank-lining every bullet would be worse.
_LINE_BREAK_RE = re.compile(r"(?i)<br\s*/?>|</(?:li|tr)>")

_WHITESPACE_RUN_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class Attachment(NamedTuple):
    """One attachment, already fetched from S3.

    Attributes
    ----------
    filename : str
        Name presented in ``Content-Disposition``.
    content_type : str
        Full MIME type, e.g. ``application/pdf``. A value without a ``/`` is treated as
        ``application/octet-stream``.
    content : bytes
        The bytes themselves.
    """

    filename: str
    content_type: str
    content: bytes


class AttachmentInfo(NamedTuple):
    """Attachment metadata recovered from a stored message.

    Metadata only: ``0008`` has no attachments table and the thread view only lists them, so the
    bytes stay inside the stored MIME rather than being extracted and re-stored.

    Attributes
    ----------
    filename : str
        Name from ``Content-Disposition``, or a generated placeholder when the part has none.
    content_type : str
        The part's MIME type.
    size_bytes : int
        Decoded size, so the UI shows the real size rather than the base64-inflated one.
    """

    filename: str
    content_type: str
    size_bytes: int


class ParsedMessage(NamedTuple):
    """What a stored message yields when read back for display.

    Attributes
    ----------
    body_html : str or None
        The ``text/html`` alternative when present. ``None`` for a message that carried only
        plain text — the caller decides how to render that, rather than this function inventing
        markup.
    body_text : str or None
        The ``text/plain`` alternative when present.
    attachments : list of AttachmentInfo
        Metadata for every attachment part, in message order.
    """

    body_html: str | None
    body_text: str | None
    attachments: list[AttachmentInfo]


def _part_text(part: Message) -> str:
    """Decode a leaf part's payload to str, tolerating a mislabelled charset.

    The trailing newline MIME serialization adds to every text part is stripped, so parsing is an
    exact inverse of :func:`build_raw_message` for a body that did not end in one.
    """
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        decoded = payload.decode(charset, errors="replace")
    except LookupError:
        # An unknown charset label must not lose the body; utf-8 with replacement is closer to
        # right than dropping the part.
        logger.warning("Unknown charset %r on a message part; decoding as utf-8", charset)
        decoded = payload.decode("utf-8", errors="replace")
    return decoded.rstrip("\r\n")


def parse_raw_message(raw: bytes) -> ParsedMessage:
    """Recover the displayable body and attachment list from stored MIME.

    The inverse of :func:`build_raw_message`, used when reading a thread: bodies and attachment
    metadata are not columns (see ``0008``), so they are reconstructed from the object at
    ``email_messages.s3_key``. Also handles inbound mail from other clients, which is why it
    tolerates single-part messages, missing charsets, and attachments with no filename.

    Parameters
    ----------
    raw : bytes
        The stored message.

    Returns
    -------
    ParsedMessage
        Body parts (either may be ``None``) and attachment metadata.

    Examples
    --------
    >>> built = build_raw_message(
    ...     sender="a@x.com", to=["b@x.com"], subject="Hi",
    ...     body_html="<p>Hello</p>", message_id="<1@x.com>",
    ... )
    >>> parsed = parse_raw_message(built)
    >>> parsed.body_html
    '<p>Hello</p>'
    >>> parsed.body_text
    'Hello'
    >>> parsed.attachments
    []
    """
    message = message_from_bytes(raw)
    body_html: str | None = None
    body_text: str | None = None
    attachments: list[AttachmentInfo] = []

    for index, part in enumerate(message.walk()):
        if part.is_multipart():
            continue

        disposition = (part.get_content_disposition() or "").lower()
        content_type = part.get_content_type()

        if disposition == "attachment" or part.get_filename():
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                AttachmentInfo(
                    filename=part.get_filename() or f"attachment-{index}",
                    content_type=content_type,
                    size_bytes=len(payload),
                )
            )
            continue

        # First of each body type wins: a quoted reply chain can carry several, and the topmost
        # is the one this message actually says.
        if content_type == "text/html" and body_html is None:
            body_html = _part_text(part)
        elif content_type == "text/plain" and body_text is None:
            body_text = _part_text(part)

    return ParsedMessage(body_html=body_html, body_text=body_text, attachments=attachments)


def _client():
    """Return the module-cached boto3 SES client, created on first use in :data:`SES_REGION`.

    Tests monkeypatch this function, matching ``common.secrets._client`` and
    ``common.storage._client``.
    """
    global _client_instance
    if _client_instance is None:
        _client_instance = boto3.client("ses", region_name=SES_REGION)
    return _client_instance


def from_address() -> str:
    """Return the ``From`` header value, display name included when configured.

    Returns
    -------
    str
        Either ``donna@example.com`` or ``Donna King <donna@example.com>``.

    Raises
    ------
    RuntimeError
        When ``MAIL_FROM_ADDRESS`` is unset — a deployment fault.
    """
    address = os.environ.get(MAIL_FROM_ENV)
    if not address:
        raise RuntimeError(f"Required environment variable {MAIL_FROM_ENV} is not set")
    name = os.environ.get(MAIL_FROM_NAME_ENV)
    return formataddr((name, address)) if name else address


def html_to_text(html: str) -> str:
    """Derive a plaintext alternative from composer HTML.

    Block-level closes and ``<br>`` become newlines, remaining tags are dropped, and the few
    entities a rich-text editor actually emits are unescaped. This is deliberately mechanical —
    the plain part exists so the message is not HTML-only, not to be a faithful rendering.

    Parameters
    ----------
    html : str
        The composer's HTML body.

    Returns
    -------
    str
        Plain text with collapsed whitespace and at most one blank line between blocks.

    Examples
    --------
    >>> html_to_text("<p>Hi Jane,</p><p>Can you speak <b>Friday</b>?</p>")
    'Hi Jane,\\n\\nCan you speak Friday?'
    >>> html_to_text("<p>One<br>Two</p>")
    'One\\nTwo'
    >>> html_to_text("<ul><li>First</li><li>Second</li></ul>")
    'First\\nSecond'
    """
    text = _PARAGRAPH_BREAK_RE.sub("\n\n", html)
    text = _LINE_BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    for entity, char in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
    ):
        text = text.replace(entity, char)
    text = _WHITESPACE_RUN_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def build_raw_message(
    *,
    sender: str,
    to: list[str],
    subject: str,
    body_html: str,
    message_id: str,
    cc: list[str] | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: list[Attachment] | None = None,
) -> bytes:
    """Assemble the raw MIME for one outgoing message.

    Pure: no AWS, no I/O, no clock beyond the ``Date`` header. The returned bytes are what SES
    transmits *and* what gets appended to the Sent folder and stored in S3, so all three agree
    byte-for-byte and the stored copy is a faithful record of what the recipient received.

    Parameters
    ----------
    sender : str
        ``From`` value, normally from :func:`from_address`.
    to : list of str
        Primary recipients; at least one.
    subject : str
        Subject line, sent as typed (normalization is for thread grouping, not the wire).
    body_html : str
        Composer HTML, signature already included by the client.
    message_id : str
        The bracketed ``Message-ID`` minted by ``core.email_headers.generate_message_id``. Set
        explicitly rather than left to SES, because the same value is stored on the row and is
        what inbound replies are matched against.
    cc : list of str or None, optional
        Carbon-copy recipients.
    in_reply_to : str or None, optional
        ``In-Reply-To`` for a reply, from ``core.email_headers.build_reply_headers``.
    references : str or None, optional
        ``References`` for a reply, from the same call.
    attachments : list of Attachment or None, optional
        Already-fetched attachments; omitted headers when empty.

    Returns
    -------
    bytes
        The complete RFC 5322 message.

    Raises
    ------
    ValueError
        If `to` is empty, or the assembled message exceeds :data:`MAX_MESSAGE_BYTES` — better a
        clear error here than an opaque SES rejection after a pending row exists.
    """
    if not to:
        raise ValueError("at least one recipient is required")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message["Date"] = formatdate(localtime=True)
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references

    # set_content + add_alternative produces multipart/alternative with text first, html second —
    # the order clients expect, least-capable representation first.
    message.set_content(html_to_text(body_html))
    message.add_alternative(body_html, subtype="html")

    for attachment in attachments or []:
        maintype, _, subtype = attachment.content_type.partition("/")
        if not subtype:
            maintype, subtype = "application", "octet-stream"
        # Adding a part to a multipart/alternative message promotes it to multipart/mixed.
        message.add_attachment(
            attachment.content,
            maintype=maintype,
            subtype=subtype,
            filename=attachment.filename,
        )

    raw = message.as_bytes()
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError(
            f"message is {len(raw)} bytes, over the {MAX_MESSAGE_BYTES}-byte limit "
            "(attachments are base64-encoded, adding about a third)"
        )
    return raw


def send_raw(raw_message: bytes, *, sender: str, destinations: list[str]) -> str:
    """Hand raw MIME to SES and return SES's own message id.

    The returned id is SES's, **not** the RFC 5322 ``Message-ID`` in the headers — the two are
    different identifiers. Threading and the ``UNIQUE(user_id, message_id)`` idempotency key use
    the header value we minted; the SES id is useful only for correlating with SES logs.

    Parameters
    ----------
    raw_message : bytes
        Output of :func:`build_raw_message`.
    sender : str
        Envelope sender; must be an address SES is authorized to send as.
    destinations : list of str
        Every envelope recipient — To **and** Cc. SES delivers to this list, not to the headers,
        so omitting Cc here silently drops those copies.

    Returns
    -------
    str
        SES's ``MessageId``.

    Raises
    ------
    botocore.exceptions.ClientError
        Propagated unchanged. A clean failure here is what lets the caller compensate its pending
        rows and know nothing was transmitted.
    """
    started = time.monotonic()
    logger.info(
        "SES send starting region=%s recipients=%d bytes=%d",
        SES_REGION,
        len(destinations),
        len(raw_message),
    )
    response = _client().send_raw_email(
        Source=sender,
        Destinations=destinations,
        RawMessage={"Data": raw_message},
    )
    ses_message_id = response["MessageId"]
    logger.info(
        "SES send accepted ses_message_id=%s duration_ms=%d",
        ses_message_id,
        int((time.monotonic() - started) * 1000),
    )
    return ses_message_id


def reset_client() -> None:
    """Clear the cached SES client. For tests, and for nothing else."""
    global _client_instance
    _client_instance = None
