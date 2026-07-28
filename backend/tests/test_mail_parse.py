"""Unit tests for ``common.mail_parse.parse_headers`` — the poller's envelope seam.

Pure and I/O-free. The body-and-attachment half of the module (``parse_raw_message``) is exercised
in ``test_mail.py``, where it sits alongside ``build_raw_message`` so the round trip is asserted
against the real assembler rather than against hand-written MIME.

Most of these cases are shaped by mail this app does **not** send: the poller is a guest on a real
mailbox, and every sender's client makes its own choices about encoding, folding, and which headers
to bother writing.
"""

from __future__ import annotations

import datetime as dt

from common.mail_parse import parse_headers


def raw(*header_lines: str, body: str = "body text") -> bytes:
    """Assemble a message from explicit header lines, so each test states exactly its input."""
    return ("\r\n".join(header_lines) + "\r\n\r\n" + body).encode()


# --- the ordinary case -------------------------------------------------------------------------


def test_a_plain_message_yields_every_field() -> None:
    headers = parse_headers(
        raw(
            "Message-ID: <a@x.com>",
            "From: Pat Host <pat@riverbend.org>",
            "To: donna@360balancedliving.com",
            "Cc: ops@riverbend.org",
            "Subject: Speaking inquiry",
            "Date: Mon, 27 Jul 2026 10:00:00 -0400",
            "In-Reply-To: <parent@x.com>",
            "References: <root@x.com> <parent@x.com>",
        )
    )
    assert headers.message_id == "<a@x.com>"
    assert headers.from_addr == "Pat Host <pat@riverbend.org>"
    assert headers.to_addr == "donna@360balancedliving.com"
    assert headers.cc_addr == "ops@riverbend.org"
    assert headers.subject == "Speaking inquiry"
    assert headers.in_reply_to == "<parent@x.com>"
    assert headers.references == "<root@x.com> <parent@x.com>"


def test_addresses_are_returned_as_written_not_split() -> None:
    """The database column holds header text; splitting is ``core.email_headers``' job."""
    headers = parse_headers(
        raw("From: a@x.com", 'To: "King, Donna" <donna@x.com>, ops@riverbend.org')
    )
    assert headers.to_addr == '"King, Donna" <donna@x.com>, ops@riverbend.org'


# --- RFC 2047, which is not cosmetic -----------------------------------------------------------


def test_a_base64_utf8_subject_is_decoded() -> None:
    """Stored undecoded, this string *becomes* ``email_threads.subject_normalized``.

    The subject-fallback matcher would then compare encoded blobs, and the thread list would show
    Donna ``=?utf-8?B?...?=`` instead of words.
    """
    headers = parse_headers(raw("From: a@x.com", "Subject: =?utf-8?B?U3BlYWtpbmcgaW5xdWlyeQ==?="))
    assert headers.subject == "Speaking inquiry"


def test_a_quoted_printable_latin1_display_name_is_decoded() -> None:
    headers = parse_headers(raw("From: =?iso-8859-1?Q?Ren=E9e?= <renee@x.com>"))
    assert headers.from_addr == "Renée <renee@x.com>"


def test_a_mixed_charset_header_is_decoded_across_both_encodings() -> None:
    headers = parse_headers(
        raw("From: =?utf-8?B?QmrDtnJu?= =?iso-8859-1?Q?_H=E5kansson?= <bjorn@x.com>")
    )
    assert headers.from_addr == "Björn Håkansson <bjorn@x.com>"


def test_an_undecodable_header_is_kept_raw_rather_than_dropped() -> None:
    """The raw form is wrong but readable; dropping it would lose the sender entirely."""
    headers = parse_headers(raw("From: =?not-a-charset?Q?whatever?= <a@x.com>", "Subject: Hi"))
    assert "a@x.com" in headers.from_addr


def test_a_folded_subject_is_unfolded() -> None:
    headers = parse_headers(
        raw("From: a@x.com", "Subject: Speaking at your", "\tspring conference")
    )
    assert "Speaking at your" in headers.subject
    assert "spring conference" in headers.subject


# --- the Date header ---------------------------------------------------------------------------


def test_a_date_with_an_offset_comes_back_aware() -> None:
    """Left aware on purpose: ``common/`` does not import ``core/``, and the repository layer
    normalizes to naive UTC before the value can reach a query."""
    headers = parse_headers(raw("From: a@x.com", "Date: Mon, 27 Jul 2026 12:00:00 -0400"))
    assert headers.date.tzinfo is not None
    assert headers.date.utcoffset() == dt.timedelta(hours=-4)


def test_a_date_without_an_offset_comes_back_naive() -> None:
    headers = parse_headers(raw("From: a@x.com", "Date: Mon, 27 Jul 2026 12:00:00 -0000"))
    assert headers.date is not None


def test_an_unparseable_date_is_none_and_logged_rather_than_invented(caplog) -> None:
    """Substituting the poll time would fabricate history; INTERNALDATE is the honest fallback."""
    headers = parse_headers(raw("From: a@x.com", "Date: sometime last Tuesday"))
    assert headers.date is None
    assert any("Unparseable Date" in record.message for record in caplog.records)


def test_a_missing_date_is_none() -> None:
    assert parse_headers(raw("From: a@x.com")).date is None


# --- absent, blank, and malformed --------------------------------------------------------------


def test_missing_headers_are_none() -> None:
    headers = parse_headers(raw("From: a@x.com"))
    assert headers.message_id is None
    assert headers.in_reply_to is None
    assert headers.references is None
    assert headers.to_addr is None
    assert headers.cc_addr is None
    assert headers.subject is None


def test_a_message_with_no_from_yields_an_empty_string_not_none() -> None:
    """``from_addr`` backs a NOT NULL column, and an empty sender matches no contact, so such a
    message is skipped downstream rather than special-cased here."""
    assert parse_headers(raw("Subject: Anonymous")).from_addr == ""


def test_a_whitespace_only_message_id_is_treated_as_absent() -> None:
    """A blank id must not become the ``UNIQUE(user_id, message_id)`` key for every such message."""
    assert parse_headers(raw("From: a@x.com", "Message-ID:    ")).message_id is None


def test_a_message_id_is_returned_exactly_as_received() -> None:
    """Canonicalization belongs to ``repositories.email_inbound``, beside its unique key."""
    assert parse_headers(raw("From: a@x.com", "Message-ID: bare@x.com")).message_id == "bare@x.com"


def test_an_empty_message_parses_to_empty_fields_rather_than_raising() -> None:
    headers = parse_headers(b"")
    assert headers.from_addr == ""
    assert headers.subject is None
    assert headers.date is None


def test_headers_only_are_read_so_a_body_that_cannot_be_decoded_is_irrelevant() -> None:
    """The poller discards most messages it fetches; deciding to ignore a 20 MB attachment must
    not cost decoding the 20 MB."""
    message = b"From: a@x.com\r\nSubject: Has a nasty body\r\n\r\n" + b"\xff\xfe" * 4096
    headers = parse_headers(message)
    assert headers.subject == "Has a nasty body"


def test_a_subjectless_message_is_legal_mail_not_an_error() -> None:
    assert parse_headers(raw("From: a@x.com", "Message-ID: <a@x.com>")).subject is None
