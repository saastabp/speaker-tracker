"""Unit tests for inbound thread matching — pure, no database and no IMAP.

These pin the two strategies behind DEV-PLAN slice 6b acceptance #1 (a reply from a tracked contact
links to the right thread) and #10 (the broken-``References`` fallback threads correctly), plus the
guards that stop the fallback from merging conversations that merely share a subject line.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core.email_threading import (
    FALLBACK_WINDOW_DAYS,
    MATCH_HEADER_CHAIN,
    MATCH_NEW_THREAD,
    MATCH_SUBJECT_FALLBACK,
    ThreadCandidate,
    candidate_ancestors,
    match_by_headers,
    match_by_subject,
    resolve_thread,
)

NOW = dt.datetime(2026, 7, 26, 12, 0, 0)


def candidate(
    thread_id: int = 1,
    subject: str = "Speaking at your event",
    addresses: tuple[str, ...] = ("events@kauairetreat.com",),
    last_message_at: dt.datetime | None = NOW,
) -> ThreadCandidate:
    """Build a ThreadCandidate with sensible defaults, so each test states only what it varies."""
    return ThreadCandidate(thread_id, subject, addresses, last_message_at)


# --- candidate_ancestors --------------------------------------------------------------------


def test_in_reply_to_is_tried_before_references() -> None:
    assert candidate_ancestors("<c@x.com>", "<a@x.com> <b@x.com>") == [
        "<c@x.com>",
        "<b@x.com>",
        "<a@x.com>",
    ]


def test_references_is_walked_from_nearest_ancestor_to_root() -> None:
    # References accumulates root-first, so the tail is the closest ancestor. Walking it forwards
    # would prefer the thread root, which lands a forwarded chain on the wrong conversation.
    assert candidate_ancestors(None, "<root@x.com> <mid@x.com> <parent@x.com>") == [
        "<parent@x.com>",
        "<mid@x.com>",
        "<root@x.com>",
    ]


def test_id_repeated_across_both_headers_appears_once() -> None:
    assert candidate_ancestors("<b@x.com>", "<a@x.com> <b@x.com>") == ["<b@x.com>", "<a@x.com>"]


@pytest.mark.parametrize(("in_reply_to", "references"), [(None, None), ("", ""), (None, "   ")])
def test_message_with_no_chain_has_no_ancestors(
    in_reply_to: str | None, references: str | None
) -> None:
    assert candidate_ancestors(in_reply_to, references) == []


# --- match_by_headers -----------------------------------------------------------------------


def test_reply_matches_its_parent_by_in_reply_to() -> None:
    assert match_by_headers("<parent@x.com>", None, {"<parent@x.com>": 7}) == 7


def test_reply_falls_through_to_references_when_the_parent_is_not_ours() -> None:
    # A venue looping in a colleague who replies to *their* message: In-Reply-To names an id we
    # never stored, but our own id is still in the chain.
    known = {"<ours@360balancedliving.com>": 7}
    assert match_by_headers("<theirs@venue.com>", "<ours@360balancedliving.com>", known) == 7


def test_nearest_known_ancestor_wins_over_the_thread_root() -> None:
    # Both ids are ours but belong to different threads (a chain forwarded between conversations).
    # The immediate parent is the correct answer; the root is not.
    known = {"<root@x.com>": 1, "<parent@x.com>": 2}
    assert match_by_headers(None, "<root@x.com> <parent@x.com>", known) == 2


def test_chain_of_ids_we_never_stored_does_not_match() -> None:
    assert match_by_headers("<stranger@x.com>", "<other@x.com>", {"<ours@x.com>": 1}) is None


def test_message_with_no_chain_does_not_match() -> None:
    assert match_by_headers(None, None, {"<ours@x.com>": 1}) is None


# --- match_by_subject: the three conditions -------------------------------------------------


def test_matches_when_subject_counterpart_and_window_all_agree() -> None:
    found = match_by_subject(
        "Re: Speaking at your event", ["events@kauairetreat.com"], NOW, [candidate(thread_id=5)]
    )
    assert found == 5


def test_reply_prefixes_are_normalized_before_comparison() -> None:
    # The stored key is already stripped; the arriving subject is not.
    found = match_by_subject(
        "RE: Fwd: Speaking at your event", ["events@kauairetreat.com"], NOW, [candidate()]
    )
    assert found == 1


def test_different_subject_does_not_match() -> None:
    found = match_by_subject("Invoice question", ["events@kauairetreat.com"], NOW, [candidate()])
    assert found is None


def test_same_subject_from_a_different_venue_does_not_match() -> None:
    # THE case the fallback exists to get right. Two venues both writing "Speaking at your event"
    # must stay separate threads — merging them puts one venue's mail on the other's timeline.
    found = match_by_subject(
        "Speaking at your event", ["events@othervenue.com"], NOW, [candidate()]
    )
    assert found is None


def test_message_outside_the_window_does_not_match() -> None:
    # Next year's identical inquiry starts a new thread rather than resurrecting a dead one.
    stale = candidate(last_message_at=NOW - dt.timedelta(days=FALLBACK_WINDOW_DAYS + 1))
    found = match_by_subject("Speaking at your event", ["events@kauairetreat.com"], NOW, [stale])
    assert found is None


def test_message_at_the_window_edge_matches() -> None:
    # Off-by-one guard: exactly at the boundary is inside it.
    edge = candidate(last_message_at=NOW - dt.timedelta(days=FALLBACK_WINDOW_DAYS))
    assert match_by_subject("Speaking at your event", ["events@kauairetreat.com"], NOW, [edge]) == 1


def test_window_is_symmetric_for_a_message_older_than_the_thread() -> None:
    # Mail delivered out of order, or a Sent-folder backfill, arrives with a timestamp behind the
    # thread's last activity. The gap is what matters, not its sign.
    found = match_by_subject(
        "Speaking at your event",
        ["events@kauairetreat.com"],
        NOW - dt.timedelta(days=2),
        [candidate()],
    )
    assert found == 1


# --- match_by_subject: guards and selection -------------------------------------------------


@pytest.mark.parametrize("subject", [None, "", "   ", "Re: "])
def test_blank_subject_never_matches(subject: str | None) -> None:
    # Every blank subject normalizes alike, so matching on one would join unrelated conversations.
    found = match_by_subject(subject, ["events@kauairetreat.com"], NOW, [candidate(subject="")])
    assert found is None


@pytest.mark.parametrize("addresses", [[], [""], ["   "]])
def test_message_with_no_counterpart_address_never_matches(addresses: list[str]) -> None:
    found = match_by_subject("Speaking at your event", addresses, NOW, [candidate()])
    assert found is None


def test_thread_with_no_last_message_at_never_matches() -> None:
    # A thread whose only message is an unsent pending send has no anchor for the window.
    pending = candidate(last_message_at=None)
    found = match_by_subject("Speaking at your event", ["events@kauairetreat.com"], NOW, [pending])
    assert found is None


def test_addresses_compare_case_and_whitespace_insensitively() -> None:
    stored = candidate(addresses=("  Events@KauaiRetreat.com  ",))
    found = match_by_subject("Speaking at your event", ["EVENTS@kauairetreat.COM"], NOW, [stored])
    assert found == 1


def test_most_recently_active_qualifying_thread_wins() -> None:
    older = candidate(thread_id=1, last_message_at=NOW - dt.timedelta(days=20))
    newer = candidate(thread_id=2, last_message_at=NOW - dt.timedelta(days=1))
    found = match_by_subject(
        "Speaking at your event", ["events@kauairetreat.com"], NOW, [older, newer]
    )
    assert found == 2


def test_any_shared_counterpart_address_is_enough() -> None:
    # A thread carries several addresses (the contact's plus whatever its messages used); one
    # overlap identifies the same conversation.
    stored = candidate(addresses=("old@kauairetreat.com", "events@kauairetreat.com"))
    found = match_by_subject("Speaking at your event", ["events@kauairetreat.com"], NOW, [stored])
    assert found == 1


def test_aware_message_timestamp_does_not_crash_against_a_naive_thread_timestamp() -> None:
    # MySQL returns naive datetimes; a parsed Date header is aware. Subtracting one from the other
    # raises TypeError — a crash reachable only in production, on a real reply.
    aware = NOW.replace(tzinfo=dt.timezone(dt.timedelta(hours=-10)))
    found = match_by_subject(
        "Speaking at your event",
        ["events@kauairetreat.com"],
        aware,
        [candidate(last_message_at=NOW - dt.timedelta(hours=10))],
    )
    assert found == 1


# --- resolve_thread -------------------------------------------------------------------------


def test_header_chain_wins_over_the_subject_fallback() -> None:
    # The fallback would also match here; headers are authoritative and must be tried first.
    match = resolve_thread(
        in_reply_to="<parent@x.com>",
        references=None,
        subject="Speaking at your event",
        counterpart_addresses=["events@kauairetreat.com"],
        occurred_at=NOW,
        thread_by_message_id={"<parent@x.com>": 9},
        candidates=[candidate(thread_id=1)],
    )
    assert match == (9, MATCH_HEADER_CHAIN)


def test_fallback_is_used_when_the_chain_is_broken() -> None:
    match = resolve_thread(
        in_reply_to=None,
        references=None,
        subject="Re: Speaking at your event",
        counterpart_addresses=["events@kauairetreat.com"],
        occurred_at=NOW,
        thread_by_message_id={},
        candidates=[candidate(thread_id=4)],
    )
    assert match == (4, MATCH_SUBJECT_FALLBACK)


def test_unmatched_message_starts_a_new_thread() -> None:
    match = resolve_thread(
        in_reply_to=None,
        references=None,
        subject="A brand new inquiry",
        counterpart_addresses=["events@kauairetreat.com"],
        occurred_at=NOW,
        thread_by_message_id={},
        candidates=[candidate()],
    )
    assert match == (None, MATCH_NEW_THREAD)


def test_omitting_candidates_disables_the_fallback_rather_than_erroring() -> None:
    # A caller that has not looked candidates up gets header matching only — an explicit default,
    # not an accidental empty-sequence bug.
    match = resolve_thread(
        in_reply_to=None,
        references=None,
        subject="Speaking at your event",
        counterpart_addresses=["events@kauairetreat.com"],
        occurred_at=NOW,
        thread_by_message_id={},
    )
    assert match == (None, MATCH_NEW_THREAD)
