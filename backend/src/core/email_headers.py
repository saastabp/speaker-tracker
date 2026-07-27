"""RFC 5322 header helpers for the email send path — pure domain logic, no I/O.

The three header concerns slice 6a needs before it can build MIME or write a row (DEV-PLAN slice 6a
acceptance #3, DATABASE.md §"email_messages"):

- **Subject normalization** — ``Re:``/``Fwd:`` chains stripped, giving the value stored in
  ``email_threads.subject_normalized``. 6a writes it at send; 6b (``core/email_threading.py``) uses
  it as the *fallback* grouping key when a reply arrives with no usable ``In-Reply-To``. That module
  imports :func:`normalize_subject` from here rather than redefining the rule.
- **Message-ID minting** — we generate the ``Message-ID`` ourselves *before* handing raw MIME to
  SES, so the id in the transmitted header and the id in ``email_messages.message_id`` are the same
  string. Acceptance #3 (a reply threads correctly) rests on that: a reply's ``In-Reply-To`` points
  at an id we minted, and the poller matches inbound mail against the same column.
- **Reply chaining** — ``In-Reply-To`` and ``References`` assembled per RFC 5322 §3.6.4.
- **Address normalization** — a ``From``/``To``/``Cc`` value reduced to bare, lowercased addresses.
  Added for 6b, which compares addresses on every polled message to decide whether it is in scope
  at all; it lives here because parsing an address header is header work, and putting it in
  ``core/email_scope`` would have ``core/email_threading`` importing from a sibling that exists for
  an unrelated decision.

This module is named ``email_headers`` and not ``email`` on purpose: ``src/`` is on ``sys.path``,
so a ``core/email.py`` would shadow the stdlib ``email`` package that ``common/mail.py`` needs.
Importing ``email.utils`` *from* this module is unaffected — the hazard is a file's own name.
"""

from __future__ import annotations

import re
import uuid
from email.utils import getaddresses, parseaddr
from typing import NamedTuple

#: Width of ``email_threads.subject_normalized`` (VARCHAR(255)). Normalization truncates to fit
#: rather than letting MySQL reject or silently cut the value.
SUBJECT_MAX_LEN = 255

#: Most Message-IDs carried in an outgoing ``References`` header. The chain grows by one id per
#: reply and is otherwise unbounded; past this many, :func:`build_reply_headers` keeps the **first**
#: id — the thread root, which is what receiving clients anchor a conversation on — plus the most
#: recent ``MAX_REFERENCES - 1``. Dropping from the middle loses the least. No RFC requires this
#: (§3.6.4 only defines the accumulate rule); trimming-but-keeping-the-root is the convention mail
#: clients and RFC 5537 §3.4.4 follow. In practice a booking thread never reaches this length.
MAX_REFERENCES = 20

#: One leading reply/forward prefix: ``Re:``, ``RE :``, ``Fwd:``, ``FW:``, and the counted form
#: ``Re[2]:`` that Outlook and some webmail emit. Applied repeatedly to strip a whole chain.
_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:re|fwd|fw)\s*(?:\[\d+\])?\s*:\s*", re.IGNORECASE)

_WHITESPACE_RE = re.compile(r"\s+")

#: A bracketed msg-id token as it appears in a ``References``/``In-Reply-To`` header value.
_MESSAGE_ID_RE = re.compile(r"<[^<>\s]+>")


class ReplyHeaders(NamedTuple):
    """The two threading headers a reply carries.

    Attributes
    ----------
    in_reply_to : str
        Value for the ``In-Reply-To`` header — the parent's ``Message-ID``, bracketed.
    references : str
        Value for the ``References`` header — the parent's own chain with the parent's
        ``Message-ID`` appended, space-separated and capped at :data:`MAX_REFERENCES` ids.
    """

    in_reply_to: str
    references: str


def normalize_subject(subject: str | None) -> str:
    """Strip reply/forward prefixes from a subject and collapse it to a stable grouping key.

    Removes a whole leading chain (``Re: Fwd: RE[2]: Speaking at your event`` →
    ``Speaking at your event``), collapses internal whitespace runs to single spaces, trims, and
    truncates to :data:`SUBJECT_MAX_LEN`. Only English ``Re``/``Fw``/``Fwd`` prefixes are
    recognized — the mailbox is Donna's English Outlook, and treating a foreign prefix as part of
    the subject merely starts a new thread rather than mis-threading an existing one.

    Parameters
    ----------
    subject : str or None
        Raw ``Subject`` header value, or ``None`` for a message that carries none.

    Returns
    -------
    str
        The normalized subject, possibly empty. An empty result is returned as ``""`` rather than a
        placeholder such as ``"(no subject)"``: the column is NOT NULL but not required to be
        meaningful, and core does not invent display text. Callers should note that blank subjects
        all normalize alike, so 6b must not thread on subject alone when it is empty.

    Examples
    --------
    >>> normalize_subject("Re: Fwd: Speaking at your event")
    'Speaking at your event'
    >>> normalize_subject("RE[2]:  Keynote   slot ")
    'Keynote slot'
    >>> normalize_subject(None)
    ''
    """
    if not subject:
        return ""
    text = subject
    while True:
        stripped = _SUBJECT_PREFIX_RE.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    return _WHITESPACE_RE.sub(" ", text).strip()[:SUBJECT_MAX_LEN]


def generate_message_id(domain: str) -> str:
    """Mint a new globally-unique ``Message-ID`` for an outgoing message.

    Called before the MIME is built so the same string goes into the transmitted header and into
    ``email_messages.message_id`` (the ``UNIQUE(user_id, message_id)`` idempotency key). The local
    part is a uuid4 hex — unguessable and collision-free without consulting the database.

    Parameters
    ----------
    domain : str
        Right-hand side of the id, normally the sending domain (``360balancedliving.com``). A
        leading ``@`` is tolerated and stripped.

    Returns
    -------
    str
        A bracketed msg-id such as ``<3f2b...@360balancedliving.com>``.

    Raises
    ------
    ValueError
        If `domain` is empty or whitespace-only — an unqualified Message-ID is not addressable and
        would quietly break threading rather than fail loudly here.

    Examples
    --------
    >>> generate_message_id("360balancedliving.com").endswith("@360balancedliving.com>")
    True
    >>> generate_message_id("example.com") != generate_message_id("example.com")
    True
    """
    cleaned = domain.strip().lstrip("@").strip()
    if not cleaned:
        raise ValueError("domain is required to mint a Message-ID")
    return f"<{uuid.uuid4().hex}@{cleaned}>"


def parse_message_ids(raw: str | None) -> list[str]:
    """Split a stored ``References`` or ``In-Reply-To`` value into individual msg-ids.

    Parameters
    ----------
    raw : str or None
        Header value as received or as stored in ``email_messages.message_references``.

    Returns
    -------
    list of str
        Bracketed ids in header order; ``[]`` when `raw` is ``None`` or holds none. Well-formed
        input is matched on the angle brackets. If a value contains no bracketed token at all —
        a non-conformant sender 6b will eventually meet — each whitespace-separated token is
        wrapped instead, so the ancestry is preserved rather than discarded. This is lenient
        parsing of tolerated input, not a suppressed error, so it stays silent and I/O-free.

    Examples
    --------
    >>> parse_message_ids("<a@x.com> <b@x.com>")
    ['<a@x.com>', '<b@x.com>']
    >>> parse_message_ids("a@x.com")
    ['<a@x.com>']
    >>> parse_message_ids(None)
    []
    """
    if not raw or not raw.strip():
        return []
    bracketed = _MESSAGE_ID_RE.findall(raw)
    if bracketed:
        return bracketed
    return [_bracketed(token) for token in raw.split() if token]


def format_message_ids(message_ids: list[str]) -> str | None:
    """Join msg-ids back into a single header value.

    Parameters
    ----------
    message_ids : list of str
        Bracketed ids in header order.

    Returns
    -------
    str or None
        Space-separated ids, or ``None`` when the list is empty — so the caller writes SQL NULL and
        omits the header rather than emitting an empty one.

    Examples
    --------
    >>> format_message_ids(["<a@x.com>", "<b@x.com>"])
    '<a@x.com> <b@x.com>'
    >>> format_message_ids([]) is None
    True
    """
    if not message_ids:
        return None
    return " ".join(message_ids)


def build_reply_headers(
    parent_message_id: str, parent_references: str | None = None
) -> ReplyHeaders:
    """Assemble the ``In-Reply-To`` and ``References`` headers for a reply (RFC 5322 §3.6.4).

    ``In-Reply-To`` is the parent's ``Message-ID``. ``References`` is the parent's own
    ``References`` chain with the parent's ``Message-ID`` appended, deduplicated (a malformed
    parent may already list itself) and capped per :data:`MAX_REFERENCES`.

    Parameters
    ----------
    parent_message_id : str
        ``email_messages.message_id`` of the message being replied to. Brackets are added if absent.
    parent_references : str or None, optional
        The parent's ``email_messages.message_references``, or ``None`` when the parent opened the
        thread and therefore has no ancestry.

    Returns
    -------
    ReplyHeaders
        The two header values, both non-empty — ``references`` always contains at least the parent.

    Raises
    ------
    ValueError
        If `parent_message_id` is empty or whitespace-only. Without it the reply cannot thread, and
        sending an unthreaded reply is worse than refusing to build one.

    Examples
    --------
    >>> build_reply_headers("<b@x.com>", "<a@x.com>")
    ReplyHeaders(in_reply_to='<b@x.com>', references='<a@x.com> <b@x.com>')
    >>> build_reply_headers("<a@x.com>")
    ReplyHeaders(in_reply_to='<a@x.com>', references='<a@x.com>')
    """
    if not parent_message_id or not parent_message_id.strip():
        raise ValueError("parent_message_id is required to build reply headers")
    parent = _bracketed(parent_message_id)

    chain: list[str] = []
    for message_id in [*parse_message_ids(parent_references), parent]:
        if message_id not in chain:
            chain.append(message_id)
    if len(chain) > MAX_REFERENCES:
        chain = [chain[0], *chain[-(MAX_REFERENCES - 1) :]]

    return ReplyHeaders(in_reply_to=parent, references=" ".join(chain))


def normalize_address(value: str | None) -> str:
    """Reduce a single address header value to a bare, lowercased address.

    Use this for ``From``; :func:`addresses_in` handles comma-separated lists, which
    :func:`email.utils.parseaddr` mis-parses.

    Parameters
    ----------
    value : str or None
        A ``From``-style header value, with or without a display name.

    Returns
    -------
    str
        The bare address, lowercased, or ``""`` when there is none to extract. Lowercasing the
        domain is required and lowercasing the local part is technically lossy per RFC 5321 §2.4,
        but every real mail host treats it case-insensitively, and matching a contact's stored
        address must not fail because a sender capitalized their own name.

    Examples
    --------
    >>> normalize_address('"Donna King" <Donna.King@360BalancedLiving.com>')
    'donna.king@360balancedliving.com'
    >>> normalize_address(None)
    ''
    """
    if not value:
        return ""
    _, address = parseaddr(value)
    return address.strip().lower()


def addresses_in(*values: str | None) -> list[str]:
    """Extract every normalized address from one or more address-*list* header values.

    Parameters
    ----------
    *values : str or None
        Header values, e.g. the ``To`` and ``Cc`` lines. ``None`` and empty values are ignored, so
        a message with no ``Cc`` needs no special-casing at the call site.

    Returns
    -------
    list of str
        Lowercased bare addresses in header order, deduplicated. Order is preserved because a
        caller choosing among several tracked recipients attributes the message to the first.

    Examples
    --------
    >>> addresses_in('A <a@x.com>, b@x.com', 'A@x.com')
    ['a@x.com', 'b@x.com']
    >>> addresses_in(None, '')
    []
    """
    present = [value for value in values if value]
    if not present:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for _, address in getaddresses(present):
        normalized = address.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _bracketed(message_id: str) -> str:
    """Return `message_id` wrapped in angle brackets, adding them only if absent."""
    token = message_id.strip()
    if token.startswith("<") and token.endswith(">"):
        return token
    return f"<{token.lstrip('<').rstrip('>')}>"
