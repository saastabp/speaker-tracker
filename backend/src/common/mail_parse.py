"""Reading a stored or received MIME message — headers, body, attachment metadata.

The inbound counterpart to :mod:`common.mail`, which assembles outgoing MIME and hands it to SES.
Split apart when 6b added header extraction: the two halves share only the ``cid:``/``data:``
convention, and combined they were well past the size the coding guidelines call a refactor.

Two entry points, deliberately separate because they are read at different moments and cost
different amounts:

- :func:`parse_headers` — envelope only, used by the **poller** on every message it fetches, most
  of which turn out to be none of the app's business. It stops at the blank line after the
  headers, so deciding to ignore a 20 MB message never decodes the 20 MB.
- :func:`parse_raw_message` — body and attachment metadata, used when **displaying** a thread.
  ``0008`` stores neither, so both are reconstructed from the object at ``email_messages.s3_key``.

Both tolerate mail written by clients other than this one: single-part messages, absent charsets,
attachments with no filename, and headers that are missing, malformed, or RFC 2047-encoded. That
tolerance is the point of the module — the app is one client on a mailbox full of other people's
formatting decisions.
"""

from __future__ import annotations

import base64
import datetime as dt
import re
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.parser import BytesHeaderParser
from email.utils import parsedate_to_datetime
from typing import NamedTuple

from common.logger import logger

#: A `src="cid:<token>"` attribute, for turning a stored message back into something a browser can
#: render (`cid:` resolves only inside a mail client).
_CID_IMAGE_SRC_RE = re.compile(
    r'(?P<prefix>src\s*=\s*(?P<quote>["\']))cid:(?P<cid>[^"\']+)(?P<suffix>(?P=quote))',
    re.IGNORECASE,
)


class MessageHeaders(NamedTuple):
    """The envelope of a received message, decoded.

    Values are returned in the form the database stores: header text as written, not addresses
    parsed into lists. ``email_messages.from_addr`` holds the full ``Display Name <addr>`` form for
    outbound mail, and inbound mail matches it — the splitting into bare addresses happens in
    ``core.email_headers`` where the comparison rules live.

    Attributes
    ----------
    message_id : str or None
        The ``Message-ID`` as received, brackets included when the sender supplied them. **Not**
        canonicalized here — ``repositories.email_inbound`` applies
        ``core.email_headers.bracketed`` on the way into the ``UNIQUE(user_id, message_id)`` key,
        so canonicalization happens once, next to the constraint that depends on it. ``None`` for a
        message with no ``Message-ID``, which is malformed but does occur.
    in_reply_to : str or None
        ``In-Reply-To``, naming the immediate parent.
    references : str or None
        ``References``, the accumulated ancestry, root first.
    from_addr : str
        ``From``, RFC 2047-decoded. ``""`` when the header is absent — a message with no sender
        cannot match a contact, so it is skipped downstream rather than special-cased here.
    to_addr, cc_addr : str or None
        ``To`` / ``Cc``, decoded, comma-separated as received.
    subject : str or None
        ``Subject``, decoded.
    date : datetime or None
        The ``Date`` header, timezone-aware when it carried an offset. ``None`` when absent or
        unparseable — the caller falls back to the IMAP ``INTERNALDATE``, which is an observed
        fact rather than an invention. Normalizing to naive UTC happens in the repository layer,
        which is the layer permitted to import ``core``.
    """

    message_id: str | None
    in_reply_to: str | None
    references: str | None
    from_addr: str
    to_addr: str | None
    cc_addr: str | None
    subject: str | None
    date: dt.datetime | None


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


def _decoded(value: str | None) -> str | None:
    """Decode an RFC 2047-encoded header value, tolerating malformed encodings.

    Real subjects arrive as ``=?utf-8?B?U3BlYWtpbmcgaW5xdWlyeQ==?=``. Stored undecoded, that string
    becomes ``email_threads.subject_normalized`` — so the subject-fallback matcher would compare
    encoded blobs, and the thread view would show them to Donna. Display names in ``From``/``To``
    have the same problem, surfacing in the pending-import queue.

    A header that cannot be decoded is returned as-is: the raw form is wrong but readable, whereas
    dropping it loses the sender entirely.
    """
    if value is None:
        return None
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError, ValueError):
        logger.warning("Could not RFC 2047-decode a header; keeping the raw value")
        return value


def _parsed_date(value: str | None) -> dt.datetime | None:
    """Parse a ``Date`` header, returning ``None`` rather than raising or inventing a time."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        # Falling back to the poll time would fabricate history; INTERNALDATE is the honest
        # substitute and the caller already has it.
        logger.warning(
            "Unparseable Date header %r; falling back to the server's INTERNALDATE", value
        )
        return None


def parse_headers(raw: bytes) -> MessageHeaders:
    """Extract a received message's envelope without decoding its body.

    Uses a header-only parser, so a message the poller is about to discard — most of them — costs
    the headers rather than the attachments. Everything the threading and scoping decisions need
    comes from here: the ``Message-ID`` for idempotency, the chain for
    ``core.email_threading.resolve_thread``, and the addresses for
    ``core.email_scope.classify_message``.

    Parameters
    ----------
    raw : bytes
        The complete message as fetched from IMAP.

    Returns
    -------
    MessageHeaders
        Decoded envelope. Absent headers are ``None`` (``""`` for ``From``); nothing is invented.

    Examples
    --------
    >>> parse_headers(
    ...     b"Message-ID: <a@x.com>\\r\\n"
    ...     b"From: =?utf-8?B?QmrDtnJu?= <bjorn@x.com>\\r\\n"
    ...     b"Subject: =?utf-8?B?U3BlYWtpbmcgaW5xdWlyeQ==?=\\r\\n"
    ...     b"\\r\\nbody text"
    ... ).subject
    'Speaking inquiry'
    >>> parse_headers(b"From: a@x.com\\r\\n\\r\\n").message_id is None
    True
    """
    message = BytesHeaderParser().parsebytes(raw)
    return MessageHeaders(
        message_id=(message.get("Message-ID") or "").strip() or None,
        in_reply_to=(message.get("In-Reply-To") or "").strip() or None,
        references=(message.get("References") or "").strip() or None,
        from_addr=_decoded(message.get("From")) or "",
        to_addr=_decoded(message.get("To")),
        cc_addr=_decoded(message.get("Cc")),
        subject=_decoded(message.get("Subject")),
        date=_parsed_date(message.get("Date")),
    )


def _part_text(part: Message) -> str:
    """Decode a leaf part's payload to str, tolerating a mislabelled charset.

    The trailing newline MIME serialization adds to every text part is stripped, so parsing is an
    exact inverse of ``common.mail.build_raw_message`` for a body that did not end in one.
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

    The inverse of ``common.mail.build_raw_message``, used when reading a thread: bodies and
    attachment metadata are not columns (see ``0008``), so they are reconstructed from the object
    at ``email_messages.s3_key``. Also handles inbound mail from other clients, which is why it
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
    >>> from common.mail import build_raw_message
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

    inline_by_cid: dict[str, str] = {}

    for index, part in enumerate(message.walk()):
        if part.is_multipart():
            continue

        disposition = (part.get_content_disposition() or "").lower()
        content_type = part.get_content_type()
        content_id = (part.get("Content-ID") or "").strip().strip("<>")

        # An inline part with a Content-ID is a referenced image, not an attachment to list. Keep
        # it aside as a data: URI so the `cid:` references in the body can be resolved below.
        if content_id and content_type.startswith("image/"):
            payload = part.get_payload(decode=True) or b""
            encoded = base64.b64encode(payload).decode("ascii")
            inline_by_cid[content_id] = f"data:{content_type};base64,{encoded}"
            continue

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

    # Gated on the body containing references, NOT on having parts to resolve them with: a message
    # referencing a part we do not have is exactly the case worth warning about, and gating on
    # `inline_by_cid` would skip it silently.
    if body_html and "cid:" in body_html:
        body_html = _resolve_cid_references(body_html, inline_by_cid)

    return ParsedMessage(body_html=body_html, body_text=body_text, attachments=attachments)


def _resolve_cid_references(body_html: str, inline_by_cid: dict[str, str]) -> str:
    """Swap ``cid:`` image sources back to ``data:`` URIs for display.

    The exact inverse of what ``common.mail.extract_inline_images`` does on the way out, and the
    reason this exists: ``cid:`` resolves **only inside a mail client**. A browser cannot fetch it,
    so the stored MIME — which is what the recipient received, `cid:` references and all — renders
    with a broken image in the app's own thread view unless the references are put back.

    So the two representations stay split the way the design intends: ``data:`` at rest and on
    screen, ``cid:`` on the wire. A reference with no matching part is left untouched rather than
    blanked; that is inbound mail from another client whose part we do not have, and a visibly
    broken image is more honest than silently deleting the tag.
    """

    def replace(match: re.Match[str]) -> str:
        cid = match.group("cid").strip()
        resolved = inline_by_cid.get(cid)
        if resolved is None:
            logger.warning("Message references cid:%s with no matching inline part", cid)
            return match.group(0)
        return f"{match.group('prefix')}{resolved}{match.group('suffix')}"

    return _CID_IMAGE_SRC_RE.sub(replace, body_html)
