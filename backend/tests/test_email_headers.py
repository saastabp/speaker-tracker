"""Unit tests for RFC 5322 header helpers — pure, no database and no AWS.

These pin the three rules the send path depends on (DEV-PLAN slice 6a): the subject key written to
``email_threads.subject_normalized``, the ``Message-ID`` we mint before handing MIME to SES, and
the ``In-Reply-To``/``References`` assembly behind acceptance #3 (a reply threads correctly in the
recipient's client).
"""

from __future__ import annotations

import pytest

from core.email_headers import (
    MAX_REFERENCES,
    SUBJECT_MAX_LEN,
    ReplyHeaders,
    build_reply_headers,
    format_message_ids,
    generate_message_id,
    normalize_subject,
    parse_message_ids,
)

# --- normalize_subject ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "Re: Speaking at your event",
        "RE: Speaking at your event",
        "re:Speaking at your event",
        "Fwd: Speaking at your event",
        "FW: Speaking at your event",
        "Re : Speaking at your event",
        "RE[2]: Speaking at your event",
    ],
)
def test_single_prefix_is_stripped(raw: str) -> None:
    # Every prefix spelling Outlook and common webmail emit collapses to the same grouping key.
    assert normalize_subject(raw) == "Speaking at your event"


def test_prefix_chain_is_stripped_entirely() -> None:
    # A forwarded reply accumulates prefixes; all of them go, not just the outermost.
    assert normalize_subject("Re: Fwd: RE[3]: Keynote slot") == "Keynote slot"


def test_internal_whitespace_is_collapsed() -> None:
    assert normalize_subject("RE[2]:  Keynote   slot ") == "Keynote slot"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_blank_subject_normalizes_to_empty_string(raw: str | None) -> None:
    # Empty stays empty — core invents no "(no subject)" display text (the column is NOT NULL but
    # not required to be meaningful).
    assert normalize_subject(raw) == ""


def test_prefix_like_text_mid_subject_is_preserved() -> None:
    # Only a *leading* prefix is a reply marker. "re:" inside the subject is content.
    assert normalize_subject("Notes re: the fee") == "Notes re: the fee"
    assert normalize_subject("Fee schedule (fw: pending)") == "Fee schedule (fw: pending)"


def test_long_subject_truncates_to_column_width() -> None:
    # subject_normalized is VARCHAR(255): truncate here rather than let MySQL reject or cut it.
    normalized = normalize_subject("Re: " + "x" * 400)
    assert len(normalized) == SUBJECT_MAX_LEN
    assert normalized == "x" * SUBJECT_MAX_LEN


# --- generate_message_id --------------------------------------------------------------------


def test_generated_message_id_is_bracketed_and_domain_qualified() -> None:
    message_id = generate_message_id("360balancedliving.com")
    assert message_id.startswith("<")
    assert message_id.endswith("@360balancedliving.com>")


def test_generated_message_id_tolerates_leading_at_in_domain() -> None:
    assert generate_message_id("@example.com").endswith("@example.com>")


def test_generated_message_ids_are_unique() -> None:
    # The id is the UNIQUE(user_id, message_id) idempotency key — a collision would silently drop a
    # sent message on insert.
    ids = {generate_message_id("example.com") for _ in range(100)}
    assert len(ids) == 100


@pytest.mark.parametrize("domain", ["", "   ", "@"])
def test_empty_domain_raises(domain: str) -> None:
    # An unqualified Message-ID breaks threading quietly downstream; fail loudly at mint time.
    with pytest.raises(ValueError):
        generate_message_id(domain)


# --- parse_message_ids / format_message_ids -------------------------------------------------


def test_parses_bracketed_header_value() -> None:
    assert parse_message_ids("<a@x.com> <b@x.com>") == ["<a@x.com>", "<b@x.com>"]


def test_parses_bracketed_ids_across_folded_whitespace() -> None:
    # Long References headers arrive folded onto continuation lines.
    assert parse_message_ids("<a@x.com>\r\n <b@x.com>") == ["<a@x.com>", "<b@x.com>"]


def test_unbracketed_tokens_are_wrapped_rather_than_dropped() -> None:
    # Non-conformant senders omit the brackets; keep the ancestry instead of discarding it.
    assert parse_message_ids("a@x.com b@x.com") == ["<a@x.com>", "<b@x.com>"]


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_blank_references_parses_to_empty_list(raw: str | None) -> None:
    assert parse_message_ids(raw) == []


def test_format_joins_with_single_spaces() -> None:
    assert format_message_ids(["<a@x.com>", "<b@x.com>"]) == "<a@x.com> <b@x.com>"


def test_format_of_empty_list_is_none() -> None:
    # None so the caller writes SQL NULL and omits the header entirely.
    assert format_message_ids([]) is None


def test_parse_format_round_trip() -> None:
    raw = "<a@x.com> <b@x.com> <c@x.com>"
    assert format_message_ids(parse_message_ids(raw)) == raw


# --- build_reply_headers (acceptance #3) ----------------------------------------------------


def test_reply_to_thread_root_references_only_the_parent() -> None:
    headers = build_reply_headers("<a@x.com>")
    assert headers == ReplyHeaders(in_reply_to="<a@x.com>", references="<a@x.com>")


def test_reply_appends_parent_to_its_own_chain_in_order() -> None:
    # RFC 5322 §3.6.4: References = parent's References + parent's Message-ID, oldest first.
    headers = build_reply_headers("<c@x.com>", "<a@x.com> <b@x.com>")
    assert headers.in_reply_to == "<c@x.com>"
    assert headers.references == "<a@x.com> <b@x.com> <c@x.com>"


def test_parent_already_listed_in_its_chain_is_not_duplicated() -> None:
    headers = build_reply_headers("<b@x.com>", "<a@x.com> <b@x.com>")
    assert headers.references == "<a@x.com> <b@x.com>"


def test_unbracketed_parent_message_id_is_bracketed() -> None:
    headers = build_reply_headers("b@x.com", "<a@x.com>")
    assert headers.in_reply_to == "<b@x.com>"
    assert headers.references == "<a@x.com> <b@x.com>"


@pytest.mark.parametrize("parent", ["", "   "])
def test_missing_parent_message_id_raises(parent: str) -> None:
    # Better to refuse than to send a reply that cannot thread.
    with pytest.raises(ValueError):
        build_reply_headers(parent)


def test_long_chain_is_capped_keeping_root_and_newest() -> None:
    # 30 ancestors + the parent. The cap keeps the thread root (what receiving clients anchor on)
    # and the most recent MAX_REFERENCES - 1, dropping from the middle.
    ancestors = [f"<m{i:02d}@x.com>" for i in range(30)]
    headers = build_reply_headers("<parent@x.com>", " ".join(ancestors))

    chain = parse_message_ids(headers.references)
    assert len(chain) == MAX_REFERENCES
    assert chain[0] == "<m00@x.com>"
    assert chain[-1] == "<parent@x.com>"
    assert chain[1:] == [*ancestors[-(MAX_REFERENCES - 2) :], "<parent@x.com>"]


def test_chain_at_the_cap_is_untouched() -> None:
    # Exactly MAX_REFERENCES ids: nothing is dropped (off-by-one guard on the trim).
    ancestors = [f"<m{i:02d}@x.com>" for i in range(MAX_REFERENCES - 1)]
    headers = build_reply_headers("<parent@x.com>", " ".join(ancestors))

    assert parse_message_ids(headers.references) == [*ancestors, "<parent@x.com>"]
