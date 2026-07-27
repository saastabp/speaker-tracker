"""MIME assembly and SES send tests — no AWS, no database.

``build_raw_message`` is pure, so these assert against the real bytes: every message is parsed
back with ``email.message_from_bytes`` and inspected as a recipient's client would see it, rather
than checking that the builder was *called* a certain way.

Two properties carry real weight:

- **the threading headers land verbatim** (DEV-PLAN slice 6a acceptance #3) — a reply whose
  ``In-Reply-To`` does not exactly match the stored ``Message-ID`` threads nowhere;
- **every message carries a text alternative**, because a bare ``text/html`` body is a spam
  signal on cold outreach from a domain still at DMARC ``p=none``.
"""

from __future__ import annotations

import base64
import logging
from email import message_from_bytes
from email.message import Message

import pytest

from common import mail

SENDER = "Donna King <donna@360balancedliving.com>"
MESSAGE_ID = "<abc123@360balancedliving.com>"


@pytest.fixture(autouse=True)
def clean_client(monkeypatch: pytest.MonkeyPatch):
    """Reset the cached SES client and give every test a configured sender."""
    mail.reset_client()
    monkeypatch.setenv(mail.MAIL_FROM_ENV, "donna@360balancedliving.com")
    monkeypatch.delenv(mail.MAIL_FROM_NAME_ENV, raising=False)
    yield
    mail.reset_client()


class FakeSes:
    """Stand-in for the boto3 SES client."""

    def __init__(self, message_id: str = "ses-0001") -> None:
        self.message_id = message_id
        self.calls: list[dict] = []

    def send_raw_email(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"MessageId": self.message_id}


def build(**overrides) -> bytes:
    """Assemble a message with sensible defaults, overriding any argument."""
    kwargs: dict = {
        "sender": SENDER,
        "to": ["venue@example.com"],
        "subject": "Speaking at your event",
        "body_html": "<p>Hi Jane,</p><p>Are you booking speakers?</p>",
        "message_id": MESSAGE_ID,
    }
    kwargs.update(overrides)
    return mail.build_raw_message(**kwargs)


def parts_by_type(message: Message) -> dict[str, Message]:
    """Map content-type → part for every leaf part of a parsed message."""
    return {part.get_content_type(): part for part in message.walk() if not part.is_multipart()}


def text_of(part: Message) -> str:
    """Decode a leaf part's payload to str (legacy Message has no ``get_content``)."""
    return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")


# --- html_to_text -------------------------------------------------------------------------------


def test_paragraphs_are_separated_by_a_blank_line() -> None:
    assert mail.html_to_text("<p>One</p><p>Two</p>") == "One\n\nTwo"


def test_line_breaks_and_list_items_are_single_newlines() -> None:
    # Blank-lining every bullet would read worse than the HTML it replaces.
    assert mail.html_to_text("<p>A<br>B</p>") == "A\nB"
    assert mail.html_to_text("<ul><li>First</li><li>Second</li></ul>") == "First\nSecond"


def test_inline_tags_are_stripped_without_eating_text() -> None:
    assert mail.html_to_text("<p>Can you speak <b>Friday</b>?</p>") == "Can you speak Friday?"


def test_entities_a_rich_text_editor_emits_are_unescaped() -> None:
    assert mail.html_to_text("<p>Tom &amp; Jerry&nbsp;&quot;quoted&quot;</p>") == (
        'Tom & Jerry "quoted"'
    )


def test_runs_of_blank_lines_collapse() -> None:
    assert mail.html_to_text("<p>A</p><div></div><div></div><p>B</p>") == "A\n\nB"


def test_empty_html_is_empty_text() -> None:
    assert mail.html_to_text("") == ""


# --- from_address -------------------------------------------------------------------------------


def test_from_address_is_bare_without_a_display_name() -> None:
    assert mail.from_address() == "donna@360balancedliving.com"


def test_from_address_includes_the_display_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(mail.MAIL_FROM_NAME_ENV, "Donna King")
    assert mail.from_address() == "Donna King <donna@360balancedliving.com>"


def test_unset_from_address_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(mail.MAIL_FROM_ENV, raising=False)
    with pytest.raises(RuntimeError, match=mail.MAIL_FROM_ENV):
        mail.from_address()


# --- message structure --------------------------------------------------------------------------


def test_plain_message_is_multipart_alternative_with_both_representations() -> None:
    message = message_from_bytes(build())

    assert message.get_content_type() == "multipart/alternative"
    parts = parts_by_type(message)
    assert set(parts) == {"text/plain", "text/html"}
    assert "Are you booking speakers?" in text_of(parts["text/plain"])
    assert "<p>Hi Jane,</p>" in text_of(parts["text/html"])


def test_text_part_comes_before_html() -> None:
    # Least-capable representation first is what clients expect; reversing it makes some show
    # the plain text.
    message = message_from_bytes(build())
    ordered = [p.get_content_type() for p in message.get_payload()]
    assert ordered == ["text/plain", "text/html"]


def test_envelope_headers_are_set() -> None:
    message = message_from_bytes(build(cc=["assistant@example.com"]))

    assert message["From"] == SENDER
    assert message["To"] == "venue@example.com"
    assert message["Cc"] == "assistant@example.com"
    assert message["Subject"] == "Speaking at your event"
    assert message["Date"] is not None


def test_multiple_recipients_are_comma_joined() -> None:
    message = message_from_bytes(build(to=["a@example.com", "b@example.com"]))
    assert message["To"] == "a@example.com, b@example.com"


def test_no_cc_header_when_cc_is_empty() -> None:
    assert message_from_bytes(build(cc=[]))["Cc"] is None


def test_empty_recipient_list_raises() -> None:
    with pytest.raises(ValueError, match="at least one recipient"):
        build(to=[])


# --- threading headers (acceptance #3) ------------------------------------------------------------


def test_our_message_id_is_used_verbatim() -> None:
    # The stored value and the transmitted value must be the same string, or inbound replies
    # match nothing.
    assert message_from_bytes(build())["Message-ID"] == MESSAGE_ID


def test_a_new_message_carries_no_reply_headers() -> None:
    message = message_from_bytes(build())
    assert message["In-Reply-To"] is None
    assert message["References"] is None


def test_reply_headers_are_written_exactly_as_given() -> None:
    message = message_from_bytes(
        build(
            message_id="<new@x.com>",
            in_reply_to="<parent@x.com>",
            references="<root@x.com> <parent@x.com>",
        )
    )

    assert message["Message-ID"] == "<new@x.com>"
    assert message["In-Reply-To"] == "<parent@x.com>"
    assert message["References"] == "<root@x.com> <parent@x.com>"


def test_reply_headers_survive_a_long_references_chain() -> None:
    # Long headers get folded across lines; the parsed value must still compare equal.
    chain = " ".join(f"<m{i:02d}@x.com>" for i in range(20))
    message = message_from_bytes(build(in_reply_to="<m19@x.com>", references=chain))

    assert " ".join(message["References"].split()) == chain


# --- inline images (the signature logo) -----------------------------------------------------------

LOGO_BYTES = b"\x89PNG\r\n\x1a\n" + b"logo-pixels"
LOGO_DATA_URI = f"data:image/png;base64,{base64.b64encode(LOGO_BYTES).decode()}"


def signature_html(extra_attrs: str = 'width="180" height="60"') -> str:
    """Body HTML with a signature logo, as the composer stores it."""
    return f'<p>Hi Jane,</p><p>Warmly,<br>Donna</p><img src="{LOGO_DATA_URI}" {extra_attrs}>'


def parts_of_type(message: Message, content_type: str) -> list[Message]:
    return [p for p in message.walk() if p.get_content_type() == content_type]


def test_data_uri_never_survives_into_the_sent_bytes() -> None:
    # Gmail strips data: images and Outlook desktop will not render them, so a data URI on the wire
    # is a broken logo for every recipient. This is the assertion that matters most here.
    raw = build(body_html=signature_html())

    assert b"data:image" not in raw


def test_inline_image_becomes_a_related_part_referenced_by_cid() -> None:
    raw = build(body_html=signature_html())
    message = message_from_bytes(raw)

    image = parts_of_type(message, "image/png")[0]
    cid = image["Content-ID"]
    assert cid is not None
    # The HTML must reference exactly the id the part declares, brackets stripped.
    html = text_of(parts_of_type(message, "text/html")[0])
    assert f"cid:{cid.strip('<>')}" in html


def test_related_wraps_the_html_part_not_the_whole_message() -> None:
    # If the image were attached to the top-level message it would be a sibling of the alternative,
    # and Outlook would list the logo as an attachment instead of rendering it in the body.
    message = message_from_bytes(build(body_html=signature_html()))

    assert message.get_content_type() == "multipart/alternative"
    related = parts_of_type(message, "multipart/related")
    assert len(related) == 1
    assert {p.get_content_type() for p in related[0].get_payload()} == {"text/html", "image/png"}


def test_inline_image_is_disposition_inline() -> None:
    # `inline` is what makes Outlook render it in place rather than listing it as an attachment.
    message = message_from_bytes(build(body_html=signature_html()))

    assert parts_of_type(message, "image/png")[0].get_content_disposition() == "inline"


def test_inline_image_bytes_round_trip() -> None:
    message = message_from_bytes(build(body_html=signature_html()))

    assert parts_of_type(message, "image/png")[0].get_payload(decode=True) == LOGO_BYTES


def test_width_and_height_attributes_survive_the_rewrite() -> None:
    # Outlook honours the width/height HTML attributes but frequently ignores CSS width — a logo
    # that loses them renders at full native size and blows out the signature.
    message = message_from_bytes(build(body_html=signature_html()))
    html = text_of(parts_of_type(message, "text/html")[0])

    assert 'width="180"' in html
    assert 'height="60"' in html


def test_logo_and_file_attachment_nest_correctly() -> None:
    message = message_from_bytes(build(body_html=signature_html(), attachments=[pdf()]))

    assert message.get_content_type() == "multipart/mixed"
    # The PDF is a sibling of the alternative; the logo stays inside related, inline.
    assert parts_of_type(message, "application/pdf")[0].get_content_disposition() == "attachment"
    assert parts_of_type(message, "image/png")[0].get_content_disposition() == "inline"
    assert len(parts_of_type(message, "multipart/related")) == 1


def test_message_without_images_keeps_its_original_shape() -> None:
    # No `related` layer when there is nothing to relate — plain sends must not regress.
    message = message_from_bytes(build(body_html="<p>No images here</p>"))

    assert message.get_content_type() == "multipart/alternative"
    assert parts_of_type(message, "multipart/related") == []


def test_several_inline_images_get_distinct_cids() -> None:
    html = f'<img src="{LOGO_DATA_URI}"><img src="{LOGO_DATA_URI}">'
    message = message_from_bytes(build(body_html=html))

    cids = [p["Content-ID"] for p in parts_of_type(message, "image/png")]
    assert len(cids) == 2
    assert len(set(cids)) == 2, "each part needs its own Content-ID or clients mis-resolve them"


def test_plaintext_alternative_carries_no_cid_token() -> None:
    # The text part is derived from the original HTML, so a cid: token must never leak into what a
    # text-only client shows the reader.
    message = message_from_bytes(build(body_html=signature_html()))

    assert "cid:" not in text_of(parts_of_type(message, "text/plain")[0])


def test_svg_data_uri_is_refused_and_warned(caplog: pytest.LogCaptureFixture) -> None:
    # SVG can carry script; inlining one would turn a logo into an XSS vector in a webmail client.
    svg = "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode()

    with caplog.at_level(logging.WARNING):
        raw = build(body_html=f'<img src="{svg}">')

    message = message_from_bytes(raw)
    assert parts_of_type(message, "image/svg+xml") == []
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_undecodable_base64_is_dropped_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    # A malformed payload must not fail the send — the rest of the message is still deliverable —
    # and the broken data: URI must not ride out onto the wire either.
    with caplog.at_level(logging.WARNING):
        raw = build(body_html='<p>Hi</p><img src="data:image/png;base64,!!!not-base64!!!">')

    assert b"Hi" in raw
    assert b"data:image" not in raw
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_no_data_uri_survives_whatever_the_payload(caplog: pytest.LogCaptureFixture) -> None:
    # The invariant, across every rejection path: valid, wrong type, and malformed together.
    svg = "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode()
    html = (
        f'<img src="{LOGO_DATA_URI}">'
        f'<img src="{svg}">'
        '<img src="data:image/png;base64,%%%broken%%%">'
    )

    with caplog.at_level(logging.WARNING):
        raw = build(body_html=html)

    assert b"data:image" not in raw


def test_extract_inline_images_returns_html_unchanged_when_there_are_none() -> None:
    html = "<p>Nothing to extract</p>"
    rewritten, images = mail.extract_inline_images(html, domain="example.com")

    assert rewritten == html
    assert images == []


def test_cid_is_domain_qualified() -> None:
    # Content-IDs are globally unique identifiers; qualifying with the sending domain keeps them
    # from colliding with another sender's.
    _rewritten, images = mail.extract_inline_images(
        f'<img src="{LOGO_DATA_URI}">', domain="360balancedliving.com"
    )

    assert images[0].cid.endswith("@360balancedliving.com")


# --- attachments --------------------------------------------------------------------------------


def pdf(name: str = "one-sheet.pdf", content: bytes = b"%PDF-1.4 fake") -> mail.Attachment:
    return mail.Attachment(filename=name, content_type="application/pdf", content=content)


def test_attachments_promote_the_message_to_multipart_mixed() -> None:
    message = message_from_bytes(build(attachments=[pdf()]))

    assert message.get_content_type() == "multipart/mixed"
    # The alternative must survive nested inside, not be flattened away.
    assert any(p.get_content_type() == "multipart/alternative" for p in message.walk())
    assert set(parts_by_type(message)) == {"text/plain", "text/html", "application/pdf"}


def test_attachment_bytes_round_trip_intact() -> None:
    # Acceptance #6: attachments arrive intact.
    content = bytes(range(256)) * 4
    message = message_from_bytes(build(attachments=[pdf(content=content)]))

    attached = parts_by_type(message)["application/pdf"]
    assert attached.get_payload(decode=True) == content
    assert attached.get_filename() == "one-sheet.pdf"


def test_several_attachments_all_appear() -> None:
    message = message_from_bytes(build(attachments=[pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf")]))
    names = [p.get_filename() for p in message.walk() if p.get_filename()]
    assert names == ["a.pdf", "b.pdf", "c.pdf"]


def test_malformed_content_type_falls_back_to_octet_stream() -> None:
    weird = mail.Attachment(filename="mystery.bin", content_type="nonsense", content=b"x")
    message = message_from_bytes(build(attachments=[weird]))

    assert "application/octet-stream" in parts_by_type(message)


def test_oversized_message_raises_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    # Better a clear error here than an opaque SES rejection after a pending row exists.
    monkeypatch.setattr(mail, "MAX_MESSAGE_BYTES", 1024)

    with pytest.raises(ValueError, match="over the 1024-byte limit"):
        build(attachments=[pdf(content=b"x" * 4096)])


# --- SES send -----------------------------------------------------------------------------------


def test_send_raw_passes_source_destinations_and_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeSes()
    monkeypatch.setattr(mail, "_client", lambda: client)
    raw = build()

    ses_id = mail.send_raw(raw, sender=SENDER, destinations=["venue@example.com"])

    assert ses_id == "ses-0001"
    assert client.calls == [
        {
            "Source": SENDER,
            "Destinations": ["venue@example.com"],
            "RawMessage": {"Data": raw},
        }
    ]


def test_send_raw_delivers_to_every_envelope_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    # SES delivers to Destinations, not to the headers — dropping Cc here silently loses copies.
    client = FakeSes()
    monkeypatch.setattr(mail, "_client", lambda: client)

    mail.send_raw(build(), sender=SENDER, destinations=["venue@example.com", "cc@example.com"])

    assert client.calls[0]["Destinations"] == ["venue@example.com", "cc@example.com"]


def test_send_raw_propagates_a_clean_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # This is the failure the caller compensates on: it must raise, not return a falsy id.
    class Boom:
        def send_raw_email(self, **kwargs):
            raise PermissionError("MessageRejected")

    monkeypatch.setattr(mail, "_client", lambda: Boom())
    with pytest.raises(PermissionError, match="MessageRejected"):
        mail.send_raw(build(), sender=SENDER, destinations=["venue@example.com"])


def test_ses_region_is_pinned_to_the_identity_region() -> None:
    # The identity and mailbox live in us-east-1; sending from us-west-2 would find no identity.
    assert mail.SES_REGION == "us-east-1"


# --- parse_raw_message ----------------------------------------------------------------------------


def test_parse_round_trips_a_message_we_built() -> None:
    # The strongest available check: what the thread view shows must equal what was sent.
    html = "<p>Hi Jane,</p><p>Are you booking speakers?</p>"
    parsed = mail.parse_raw_message(build(body_html=html))

    assert parsed.body_html == html
    assert parsed.body_text == mail.html_to_text(html)
    assert parsed.attachments == []


def test_parse_recovers_attachment_metadata_without_the_bytes() -> None:
    # 0008 has no attachments table; the thread view lists them, the bytes stay in the MIME.
    content = b"%PDF-1.4" + bytes(range(256))
    raw = build(attachments=[pdf("one-sheet.pdf", content), pdf("menu.pdf", b"short")])

    parsed = mail.parse_raw_message(raw)

    assert [(a.filename, a.content_type) for a in parsed.attachments] == [
        ("one-sheet.pdf", "application/pdf"),
        ("menu.pdf", "application/pdf"),
    ]
    # Decoded size, not the base64-inflated wire size.
    assert parsed.attachments[0].size_bytes == len(content)
    assert parsed.attachments[1].size_bytes == len(b"short")


def test_parse_keeps_the_body_when_attachments_are_present() -> None:
    parsed = mail.parse_raw_message(build(attachments=[pdf()]))

    assert parsed.body_html is not None
    assert "Are you booking speakers?" in parsed.body_text


def test_parse_handles_a_plain_text_only_message() -> None:
    # Inbound mail from another client may carry no HTML at all; that is not an error, and this
    # function does not invent markup for it.
    raw = b"From: a@x.com\r\nTo: b@x.com\r\nSubject: Hi\r\n\r\nJust text.\r\n"

    parsed = mail.parse_raw_message(raw)

    assert parsed.body_text == "Just text."
    assert parsed.body_html is None
    assert parsed.attachments == []


def test_parse_handles_an_html_only_message() -> None:
    raw = (
        b"From: a@x.com\r\nTo: b@x.com\r\nSubject: Hi\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n<p>Only HTML.</p>\r\n"
    )

    parsed = mail.parse_raw_message(raw)

    assert parsed.body_html == "<p>Only HTML.</p>"
    assert parsed.body_text is None


def test_parse_prefers_the_first_body_of_each_type() -> None:
    # A quoted reply chain can carry several; the topmost is what this message actually says.
    outer = build(body_html="<p>Newest</p>")
    parsed = mail.parse_raw_message(outer)
    assert parsed.body_html == "<p>Newest</p>"


def test_parse_tolerates_an_unknown_charset(caplog: pytest.LogCaptureFixture) -> None:
    # A bogus charset label must not lose the body — that would blank a message in the UI.
    raw = (
        b"From: a@x.com\r\nTo: b@x.com\r\nSubject: Hi\r\n"
        b"Content-Type: text/plain; charset=definitely-not-a-charset\r\n\r\nStill readable.\r\n"
    )

    with caplog.at_level(logging.WARNING):
        parsed = mail.parse_raw_message(raw)

    assert parsed.body_text == "Still readable."
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_parse_names_an_attachment_that_has_no_filename() -> None:
    raw = (
        b"From: a@x.com\r\nTo: b@x.com\r\nSubject: Hi\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nBody\r\n"
        b"--B\r\nContent-Type: application/pdf\r\n"
        b"Content-Disposition: attachment\r\n\r\nbytes\r\n"
        b"--B--\r\n"
    )

    parsed = mail.parse_raw_message(raw)

    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].filename.startswith("attachment-")
    assert parsed.attachments[0].content_type == "application/pdf"


def test_parse_of_an_empty_message_is_empty_not_an_error() -> None:
    parsed = mail.parse_raw_message(b"")

    assert parsed.body_html is None
    assert parsed.attachments == []
