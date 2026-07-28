"""Unit tests for ``common.imap_poll`` — the message-level IMAP seam.

No database and no network: everything runs against ``fake_imap.FakeIMAP``, which reproduces
the protocol quirks that make this module worth having. See its module docstring for why the fake
is written to the protocol rather than to the obvious mental model.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fake_imap import IMPORT_FOLDER, PROCESSED_FOLDER, FakeIMAP, build_message
from imapclient.exceptions import IMAPClientError

from common.imap import ImapError
from common.imap_poll import (
    MAX_UID,
    fetch_messages,
    move_uids,
    search_uids_above,
    select_folder,
)


@pytest.fixture
def server() -> FakeIMAP:
    return FakeIMAP()


# --- select_folder ---------------------------------------------------------------------------


def test_select_reports_the_uid_generation_and_next_uid(server) -> None:
    server.add("INBOX", 900, build_message(message_id="<a@x.com>", from_addr="a@x.com"))
    status = select_folder(server, "INBOX")
    assert (status.uid_validity, status.uid_next, status.message_count) == (42, 901, 1)


def test_select_defaults_to_readonly_so_a_poll_cannot_alter_the_mailbox(server) -> None:
    select_folder(server, "INBOX")
    assert server.readonly is True


def test_import_folder_can_be_selected_writable_because_messages_move_out_of_it(server) -> None:
    select_folder(server, IMPORT_FOLDER, readonly=False)
    assert server.readonly is False


def test_an_empty_folder_reports_uidnext_one(server) -> None:
    status = select_folder(server, "INBOX")
    assert status.uid_next == 1
    assert status.message_count == 0


def test_selecting_a_missing_folder_raises_imap_error(server) -> None:
    with pytest.raises(ImapError, match="could not select folder"):
        select_folder(server, "No Such Folder")


def test_a_folder_without_uidvalidity_is_refused_rather_than_polled_blind(
    server, monkeypatch
) -> None:
    # Without UIDVALIDITY no cursor can be trusted, and polling anyway risks skipping mail
    # silently — the one outcome worse than failing this poll.
    monkeypatch.setattr(server, "select_folder", lambda folder, readonly=True: {b"EXISTS": 0})
    with pytest.raises(ImapError, match="no UIDVALIDITY"):
        select_folder(server, "INBOX")


# --- search_uids_above: the `*` trap ----------------------------------------------------------


def test_search_finds_only_uids_above_the_cursor(server) -> None:
    for uid in (900, 901, 902):
        server.add("INBOX", uid, build_message(message_id=f"<{uid}@x.com>", from_addr="a@x.com"))
    select_folder(server, "INBOX")
    assert search_uids_above(server, 900) == [901, 902]


def test_a_quiet_folder_returns_nothing_rather_than_the_newest_message(server) -> None:
    """The regression this module exists to prevent.

    ``UID 901:*`` on a folder whose highest UID is 900 returns 900 — the message already
    processed. On a quiet folder that is *every* poll, so the phantom would re-fetch a message a
    minute forever and leave ``PollSummary.duplicates`` permanently non-zero, destroying the one
    signal meant to say "a rescan is under way".
    """
    server.add("INBOX", 900, build_message(message_id="<a@x.com>", from_addr="a@x.com"))
    select_folder(server, "INBOX")
    assert search_uids_above(server, 900) == []


def test_the_search_range_is_bounded_by_a_number_not_a_star(server) -> None:
    select_folder(server, "INBOX")
    search_uids_above(server, 900)
    assert server.searches == [["UID", f"901:{MAX_UID}"]]
    assert not str(server.searches[-1][1]).endswith(":*")


def test_the_fake_itself_reproduces_the_star_quirk(server) -> None:
    """Proves the fake can catch a regression, rather than passing either way.

    If this fails, every other test in this file is worthless: it would mean the fake returns
    "UIDs above n" regardless of the range syntax, so swapping ``MAX_UID`` back to ``*`` would go
    unnoticed.
    """
    server.add("INBOX", 900, build_message(message_id="<a@x.com>", from_addr="a@x.com"))
    select_folder(server, "INBOX")
    assert server.search(["UID", "901:*"]) == [900], "fake does not model the `*` normalization"
    assert server.search(["UID", f"901:{MAX_UID}"]) == []


def test_max_uid_is_the_rfc_maximum_so_no_real_message_falls_outside_it() -> None:
    assert MAX_UID == 2**32 - 1


def test_a_uid_at_or_below_the_floor_is_dropped_even_if_the_server_returns_it(
    server, monkeypatch, caplog
) -> None:
    """The floor filter is tested independently of the range bounding, on purpose.

    The two are redundant defences: with :data:`MAX_UID` bounding the range the server never
    returns a boundary UID, so the filter is never exercised by the other tests and could be
    deleted without any of them failing. Mutation-checked — removing the filter passed the whole
    file until this test existed.

    Here the server is made to answer with a UID at the floor regardless of the range, which is
    what a non-conforming server (or a regression to a ``*`` range) would do.
    """
    monkeypatch.setattr(server, "search", lambda criteria: [899, 900, 901])
    select_folder(server, "INBOX")

    assert search_uids_above(server, 900) == [901]
    assert any("regressed" in record.message for record in caplog.records), (
        "dropping UIDs at or below the floor must be visible in the log, not silent"
    )


def test_search_failure_becomes_an_imap_error(server, monkeypatch) -> None:
    select_folder(server, "INBOX")
    monkeypatch.setattr(
        server, "search", lambda criteria: (_ for _ in ()).throw(IMAPClientError("nope"))
    )
    with pytest.raises(ImapError, match="UID SEARCH"):
        search_uids_above(server, 0)


# --- fetch_messages --------------------------------------------------------------------------


def test_fetch_returns_messages_in_ascending_uid_order(server) -> None:
    for uid in (903, 901, 902):
        server.add("INBOX", uid, build_message(message_id=f"<{uid}@x.com>", from_addr="a@x.com"))
    select_folder(server, "INBOX")
    fetched = fetch_messages(server, [903, 901, 902])
    assert [message.uid for message in fetched] == [901, 902, 903]


def test_fetch_never_marks_a_message_seen(server) -> None:
    """Polling must not turn Donna's unread mail read in Outlook."""
    server.add("INBOX", 901, build_message(message_id="<a@x.com>", from_addr="a@x.com"))
    select_folder(server, "INBOX")
    fetch_messages(server, [901])
    assert server.marked_seen == []


def test_fetch_carries_the_internaldate_through(server) -> None:
    arrived = dt.datetime(2026, 7, 27, 14, 30)
    server.add("INBOX", 901, build_message(message_id="<a@x.com>", from_addr="a@x.com"), arrived)
    select_folder(server, "INBOX")
    assert fetch_messages(server, [901])[0].internaldate == arrived


def test_a_uid_that_vanished_between_search_and_fetch_is_skipped_not_fatal(server) -> None:
    server.add("INBOX", 901, build_message(message_id="<a@x.com>", from_addr="a@x.com"))
    select_folder(server, "INBOX")
    fetched = fetch_messages(server, [901, 999])
    assert [message.uid for message in fetched] == [901]


def test_fetching_nothing_makes_no_round_trip(server) -> None:
    select_folder(server, "INBOX")
    assert fetch_messages(server, []) == []


def test_fetch_failure_becomes_an_imap_error(server, monkeypatch) -> None:
    select_folder(server, "INBOX")
    monkeypatch.setattr(
        server, "fetch", lambda uids, items: (_ for _ in ()).throw(IMAPClientError("nope"))
    )
    with pytest.raises(ImapError, match="UID FETCH"):
        fetch_messages(server, [1])


# --- move_uids and its fallback ----------------------------------------------------------------


def test_move_relocates_the_message(server) -> None:
    server.add(IMPORT_FOLDER, 1, build_message(message_id="<a@x.com>", from_addr="a@x.com"))
    select_folder(server, IMPORT_FOLDER, readonly=False)
    assert move_uids(server, [1], PROCESSED_FOLDER) == 1
    assert server.uids_in(IMPORT_FOLDER) == []
    assert len(server.uids_in(PROCESSED_FOLDER)) == 1


def test_moving_nothing_makes_no_round_trip(server) -> None:
    select_folder(server, IMPORT_FOLDER, readonly=False)
    assert move_uids(server, [], PROCESSED_FOLDER) == 0


def test_a_server_without_move_falls_back_to_copy_and_expunge() -> None:
    server = FakeIMAP(supports_move=False)
    server.add(IMPORT_FOLDER, 1, build_message(message_id="<a@x.com>", from_addr="a@x.com"))
    select_folder(server, IMPORT_FOLDER, readonly=False)
    assert move_uids(server, [1], PROCESSED_FOLDER) == 1
    assert server.uids_in(IMPORT_FOLDER) == []
    assert len(server.uids_in(PROCESSED_FOLDER)) == 1


def test_without_uidplus_the_copy_lands_and_the_original_is_left_flagged(caplog) -> None:
    """The blunt instrument is deliberately not used.

    A plain ``EXPUNGE`` would purge every ``\\Deleted`` message in the folder, including mail
    Outlook flagged and has not yet purged. Leaving the original flagged means the next poll sees
    it again and dedupes it — recoverable, unlike deleting someone else's mail.
    """
    server = FakeIMAP(supports_move=False, supports_uidplus=False)
    server.add(IMPORT_FOLDER, 1, build_message(message_id="<a@x.com>", from_addr="a@x.com"))
    select_folder(server, IMPORT_FOLDER, readonly=False)

    assert move_uids(server, [1], PROCESSED_FOLDER) == 1
    assert len(server.uids_in(PROCESSED_FOLDER)) == 1, "the copy must still have landed"
    assert server.uids_in(IMPORT_FOLDER) == [1], "the original stays until a client expunges"
    assert 1 in server.deleted_flagged[IMPORT_FOLDER]
    assert any("UIDPLUS" in record.message for record in caplog.records)


def test_the_fallback_copies_before_deleting_so_a_failure_never_loses_the_message(
    monkeypatch, caplog
) -> None:
    """Ordering, not politeness: a failure after COPY leaves a duplicate, which the next poll
    dedupes on Message-ID. The other order would leave the message in neither folder."""
    server = FakeIMAP(supports_move=False)
    server.add(IMPORT_FOLDER, 1, build_message(message_id="<a@x.com>", from_addr="a@x.com"))
    select_folder(server, IMPORT_FOLDER, readonly=False)
    monkeypatch.setattr(
        server, "add_flags", lambda uids, flags: (_ for _ in ()).throw(IMAPClientError("nope"))
    )

    move_uids(server, [1], PROCESSED_FOLDER)
    assert len(server.uids_in(PROCESSED_FOLDER)) == 1
    assert server.uids_in(IMPORT_FOLDER) == [1]


def test_a_failed_copy_raises_so_the_message_is_retried_next_poll() -> None:
    server = FakeIMAP(supports_move=False)
    server.add(IMPORT_FOLDER, 1, build_message(message_id="<a@x.com>", from_addr="a@x.com"))
    select_folder(server, IMPORT_FOLDER, readonly=False)
    with pytest.raises(ImapError, match="COPY"):
        move_uids(server, [1], "No Such Folder")
    assert server.uids_in(IMPORT_FOLDER) == [1]
