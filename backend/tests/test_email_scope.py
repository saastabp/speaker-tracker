"""Unit tests for the ingest/skip decision — pure, no database and no IMAP.

DEV-PLAN slice 6b acceptance #2 is the one criterion whose failure is a privacy problem rather than
a bug: mail from a non-tracked address must **never** be ingested, because the app polls Donna's
real mailbox and it holds years of unrelated personal correspondence. These tests pin the three
ways in and confirm nothing else qualifies. Acceptance #8 (inbound mail creates no ``outreaches``
row) is pinned structurally — the decision carries no outreach field to write.
"""

from __future__ import annotations

import pytest

from core.email_scope import (
    DIRECTION_IN,
    DIRECTION_OUT,
    FOLDER_IMPORT,
    FOLDER_INBOX,
    FOLDER_SENT,
    INGEST_IMPORT_FOLDER,
    INGEST_THREAD_MATCH,
    INGEST_TRACKED_CONTACT,
    SKIP_UNTRACKED_RECIPIENTS,
    SKIP_UNTRACKED_SENDER,
    FolderKind,
    classify_message,
)

DONNA = "donna.king@360balancedliving.com"
OWN = {DONNA}
VENUE = "events@kauairetreat.com"
TRACKED = {VENUE: 42}


def classify(folder_kind: FolderKind = FOLDER_INBOX, **kwargs):
    """Call classify_message with the mailbox defaults, so each test states only what it varies."""
    kwargs.setdefault("contact_by_address", TRACKED)
    kwargs.setdefault("own_addresses", OWN)
    return classify_message(folder_kind=folder_kind, **kwargs)


# --- INBOX: the never-the-whole-mailbox guarantee (#2) --------------------------------------


def test_mail_from_an_untracked_stranger_is_not_ingested() -> None:
    # The criterion verified live by emailing the mailbox from a personal address.
    decision = classify(from_addr="cousin@gmail.com", to_addrs=DONNA)

    assert decision.ingest is False
    assert decision.reason == SKIP_UNTRACKED_SENDER
    assert decision.contact_id is None


def test_mail_from_a_tracked_contact_is_ingested_and_attributed() -> None:
    decision = classify(from_addr=f"Kauai Retreat <{VENUE}>", to_addrs=DONNA)

    assert decision == (True, DIRECTION_IN, 42, INGEST_TRACKED_CONTACT)


def test_sender_matching_is_case_insensitive() -> None:
    # A venue capitalizing its own address must not read as a stranger and get skipped silently.
    decision = classify(from_addr="Events@KauaiRetreat.COM", to_addrs=DONNA)

    assert decision.ingest is True
    assert decision.contact_id == 42


def test_stranger_on_an_existing_thread_is_ingested_without_a_contact() -> None:
    # A colleague looped into a live conversation. The thread is ours, so the message is in scope;
    # the contact comes from the thread, which this module is never told.
    decision = classify(from_addr="colleague@kauairetreat.com", matched_thread_id=7)

    assert decision == (True, DIRECTION_IN, None, INGEST_THREAD_MATCH)


def test_stranger_with_no_thread_match_stays_out() -> None:
    decision = classify(from_addr="stranger@example.com", matched_thread_id=None)

    assert decision.ingest is False


# --- The import folder: Donna's explicit hand-off (#3) --------------------------------------


def test_dragged_stranger_mail_is_ingested_with_no_contact() -> None:
    # The pending-import state: the row exists with contact_id NULL and badges the app for triage.
    # Dragging the message into the folder IS the authorization, so no tracked contact is needed.
    decision = classify(folder_kind=FOLDER_IMPORT, from_addr="stranger@example.com")

    assert decision == (True, DIRECTION_IN, None, INGEST_IMPORT_FOLDER)


def test_dragged_mail_from_a_known_contact_keeps_its_attribution() -> None:
    # Re-dragging mail from someone already tracked should not lose the contact link.
    decision = classify(folder_kind=FOLDER_IMPORT, from_addr=VENUE)

    assert decision.ingest is True
    assert decision.contact_id == 42


# --- The Sent folder: reconciliation and Outlook-composed mail -------------------------------


def test_sent_mail_to_a_tracked_contact_is_outbound_and_attributed() -> None:
    # Reconciles 6a's intent-first pending sends, and captures mail Donna writes in Outlook.
    decision = classify(folder_kind=FOLDER_SENT, from_addr=DONNA, to_addrs=VENUE)

    assert decision == (True, DIRECTION_OUT, 42, INGEST_TRACKED_CONTACT)


def test_sent_mail_to_an_untracked_recipient_is_not_ingested() -> None:
    # Donna's personal outbound mail is as out of scope as her personal inbound mail.
    decision = classify(folder_kind=FOLDER_SENT, from_addr=DONNA, to_addrs="friend@gmail.com")

    assert decision.ingest is False
    assert decision.reason == SKIP_UNTRACKED_RECIPIENTS


def test_a_tracked_contact_on_cc_brings_the_message_into_scope() -> None:
    decision = classify(
        folder_kind=FOLDER_SENT, from_addr=DONNA, to_addrs="friend@gmail.com", cc_addrs=VENUE
    )

    assert decision.ingest is True
    assert decision.contact_id == 42


def test_first_tracked_recipient_in_header_order_wins() -> None:
    # Deterministic attribution when a message goes to two tracked contacts at once.
    contacts = {VENUE: 42, "chair@pwn.org": 99}
    decision = classify(
        folder_kind=FOLDER_SENT,
        from_addr=DONNA,
        to_addrs=f"chair@pwn.org, {VENUE}",
        contact_by_address=contacts,
    )

    assert decision.contact_id == 99


def test_donnas_own_address_is_not_treated_as_a_recipient() -> None:
    # A note to self has no counterpart, so it is out of scope even though she is "known".
    decision = classify(
        folder_kind=FOLDER_SENT, from_addr=DONNA, to_addrs=DONNA, contact_by_address={DONNA: 1}
    )

    assert decision.ingest is False
    assert decision.reason == SKIP_UNTRACKED_RECIPIENTS


# --- Direction derivation --------------------------------------------------------------------


def test_message_donna_cc_d_to_herself_is_outbound_even_though_it_sits_in_inbox() -> None:
    # Direction comes from the sender, not the folder. Keying on the folder here would record her
    # own message as mail she received, inventing a reply that never happened.
    decision = classify(from_addr=DONNA, to_addrs=VENUE)

    assert decision.direction == DIRECTION_OUT
    assert decision.contact_id == 42


def test_sent_folder_forces_outbound_even_from_an_unlisted_alias() -> None:
    # A send from an alias missing from own_addresses is still hers; misfiling it as inbound would
    # show a reply from the venue that the venue never wrote.
    decision = classify(
        folder_kind=FOLDER_SENT, from_addr="donna@360balancedliving.com", to_addrs=VENUE
    )

    assert decision.direction == DIRECTION_OUT


@pytest.mark.parametrize("folder_kind", [FOLDER_INBOX, FOLDER_IMPORT])
def test_mail_from_a_third_party_is_inbound(folder_kind: FolderKind) -> None:
    decision = classify(folder_kind=folder_kind, from_addr=VENUE, to_addrs=DONNA)

    assert decision.direction == DIRECTION_IN


def test_direction_is_reported_even_when_the_message_is_skipped() -> None:
    # The poller logs it, so it must be meaningful on the skip path too.
    decision = classify(from_addr="stranger@example.com")

    assert decision.ingest is False
    assert decision.direction == DIRECTION_IN


# --- What the decision deliberately cannot express -------------------------------------------


def test_decision_carries_no_opportunity_and_no_outreach_field() -> None:
    # Structural guards. Acceptance #8: receiving email must never move a target, so there is no
    # outreach for a caller to write. And an inbound-first thread never guesses an opportunity from
    # the contact having one — a single open gig is not evidence that this email concerns it.
    decision = classify(from_addr=VENUE)

    assert decision._fields == ("ingest", "direction", "contact_id", "reason")


def test_unreadable_from_header_degrades_to_a_skip_rather_than_raising() -> None:
    # A malformed From must not take the whole poll down with it.
    decision = classify(from_addr=None)

    assert decision.ingest is False
    assert decision.reason == SKIP_UNTRACKED_SENDER
