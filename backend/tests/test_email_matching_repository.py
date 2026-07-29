"""Email matching repository tests — the three lookups that feed the pure thread matcher.

Skip without ``TEST_DATABASE_URL`` (see conftest).

Each function here exists to answer one question the pure core cannot: which threads own these
Message-IDs, which of these addresses belong to tracked contacts, and which threads are eligible
for the subject fallback. The core's own logic is tested in ``test_email_threading.py`` and
``test_email_scope.py``; what is tested here is the SQL, the owner scoping, and two behaviours that
depend on the schema rather than on Python — the case-insensitive collation on ``contacts.email``
and the deliberate exclusions from the candidate set.
"""

from __future__ import annotations

import datetime as dt

from repositories import email_matching


def _user(conn, sub: str = "other") -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES (%s, %s)", (sub, f"{sub}@x.com"))
        return cur.lastrowid


def _contact(conn, user_id: int, name: str, email: str | None, *, deleted: bool = False) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO contacts (user_id, name, email, deleted_at) VALUES (%s, %s, %s, %s)",
            (user_id, name, email, "2026-01-01 00:00:00" if deleted else None),
        )
        return cur.lastrowid


def _thread(
    conn,
    user_id: int,
    subject_normalized: str,
    *,
    contact_id: int | None = None,
    last_message_at: dt.datetime | None = dt.datetime(2026, 7, 27, 10, 0),
    closed: bool = False,
    deleted: bool = False,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_threads "
            "(user_id, contact_id, subject_normalized, last_direction, last_message_at, "
            " closed_at, deleted_at) VALUES (%s, %s, %s, 'in', %s, %s, %s)",
            (
                user_id,
                contact_id,
                subject_normalized,
                last_message_at,
                "2026-01-01 00:00:00" if closed else None,
                "2026-01-01 00:00:00" if deleted else None,
            ),
        )
        return cur.lastrowid


def _message(
    conn,
    user_id: int,
    thread_id: int,
    message_id: str,
    *,
    from_addr: str = "pat@riverbend.org",
    to_addr: str | None = "donna@360balancedliving.com",
    cc_addr: str | None = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_messages "
            "(user_id, thread_id, message_id, direction, from_addr, to_addr, cc_addr) "
            "VALUES (%s, %s, %s, 'in', %s, %s, %s)",
            (user_id, thread_id, message_id, from_addr, to_addr, cc_addr),
        )
        return cur.lastrowid


# --- threads_by_message_id ---------------------------------------------------------------------


def test_an_empty_chain_returns_nothing_without_querying(seeded_db) -> None:
    """``IN ()`` is a syntax error in MySQL, and a message with no ancestry is the ordinary case
    for a first contact — so this path must not reach the database at all."""
    conn, user_id, _, _ = seeded_db
    assert email_matching.threads_by_message_id(conn, user_id, []) == {}


def test_known_message_ids_map_to_their_threads(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id, "Speaking inquiry")
    _message(conn, user_id, thread_id, "<a@x.com>")
    _message(conn, user_id, thread_id, "<b@x.com>")

    found = email_matching.threads_by_message_id(conn, user_id, ["<a@x.com>", "<b@x.com>"])
    assert found == {"<a@x.com>": thread_id, "<b@x.com>": thread_id}


def test_unknown_ids_in_the_chain_are_simply_absent(seeded_db) -> None:
    """A forwarded chain routinely names messages we never saw; that is not an error."""
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id, "Speaking inquiry")
    _message(conn, user_id, thread_id, "<ours@x.com>")

    found = email_matching.threads_by_message_id(
        conn, user_id, ["<theirs@elsewhere.com>", "<ours@x.com>"]
    )
    assert found == {"<ours@x.com>": thread_id}


def test_keys_come_back_in_the_stored_bracketed_form(seeded_db) -> None:
    """Both sides of the lookup must agree on bracketing: ``generate_message_id`` mints bracketed
    ids and ``parse_message_ids`` extracts them, so a bare id would simply never match and the
    resulting mis-thread would be invisible."""
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id, "Speaking inquiry")
    _message(conn, user_id, thread_id, "<bracketed@x.com>")

    assert email_matching.threads_by_message_id(conn, user_id, ["bracketed@x.com"]) == {}
    assert email_matching.threads_by_message_id(conn, user_id, ["<bracketed@x.com>"]) != {}


def test_another_users_messages_are_never_matched(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    other_id = _user(conn)
    other_thread = _thread(conn, other_id, "Speaking inquiry")
    _message(conn, other_id, other_thread, "<theirs@x.com>")

    assert email_matching.threads_by_message_id(conn, user_id, ["<theirs@x.com>"]) == {}


# --- contacts_by_address -----------------------------------------------------------------------


def test_no_addresses_returns_nothing_without_querying(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    assert email_matching.contacts_by_address(conn, user_id, []) == {}


def test_a_lowercased_address_matches_a_contact_stored_in_mixed_case(seeded_db) -> None:
    """Load-bearing and invisible at the call site: the match relies on ``contacts.email`` carrying
    the ``utf8mb4_0900_ai_ci`` collation, which is what lets callers pass normalized addresses."""
    conn, user_id, _, _ = seeded_db
    contact_id = _contact(conn, user_id, "Pat Host", "Pat.Host@RiverBend.org")

    found = email_matching.contacts_by_address(conn, user_id, ["pat.host@riverbend.org"])
    assert found == {"pat.host@riverbend.org": contact_id}


def test_a_soft_deleted_contact_no_longer_matches(seeded_db) -> None:
    """Mail from someone Donna deleted should stop being ingested, not keep flowing to a hidden
    record."""
    conn, user_id, _, _ = seeded_db
    _contact(conn, user_id, "Gone", "gone@x.com", deleted=True)

    assert email_matching.contacts_by_address(conn, user_id, ["gone@x.com"]) == {}


def test_a_contact_with_no_email_matches_nothing(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    _contact(conn, user_id, "No Email", None)

    assert email_matching.contacts_by_address(conn, user_id, ["", "someone@x.com"]) == {}


def test_only_the_askers_contacts_are_returned(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    other_id = _user(conn)
    _contact(conn, other_id, "Theirs", "shared@x.com")

    assert email_matching.contacts_by_address(conn, user_id, ["shared@x.com"]) == {}


def test_two_contacts_sharing_an_address_resolve_deterministically(seeded_db) -> None:
    """A data problem slice 2 dedupes; picking one beats raising inside a poll."""
    conn, user_id, _, _ = seeded_db
    first = _contact(conn, user_id, "First", "dup@x.com")
    _contact(conn, user_id, "Second", "dup@x.com")

    found = email_matching.contacts_by_address(conn, user_id, ["dup@x.com"])
    assert found == {"dup@x.com": first}


# --- fallback_candidates -----------------------------------------------------------------------


def test_a_blank_subject_offers_no_candidates(seeded_db) -> None:
    """Every blank subject normalizes alike, so matching on one would join unrelated
    conversations — the merge the fallback's guards exist to prevent."""
    conn, user_id, _, _ = seeded_db
    _thread(conn, user_id, "")

    assert email_matching.fallback_candidates(conn, user_id, "") == []


def test_a_thread_with_the_same_normalized_subject_is_offered(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    contact_id = _contact(conn, user_id, "Pat Host", "pat@riverbend.org")
    thread_id = _thread(conn, user_id, "Speaking inquiry", contact_id=contact_id)
    _message(conn, user_id, thread_id, "<a@x.com>")

    candidates = email_matching.fallback_candidates(conn, user_id, "Speaking inquiry")
    assert [candidate.thread_id for candidate in candidates] == [thread_id]


def test_counterpart_addresses_gather_the_contact_and_every_address_on_the_thread(
    seeded_db,
) -> None:
    conn, user_id, _, _ = seeded_db
    contact_id = _contact(conn, user_id, "Pat Host", "pat@riverbend.org")
    thread_id = _thread(conn, user_id, "Speaking inquiry", contact_id=contact_id)
    _message(
        conn,
        user_id,
        thread_id,
        "<a@x.com>",
        from_addr="Pat Host <pat@riverbend.org>",
        to_addr="donna@360balancedliving.com",
        cc_addr="ops@riverbend.org",
    )

    candidate = email_matching.fallback_candidates(conn, user_id, "Speaking inquiry")[0]
    assert set(candidate.counterpart_addresses) >= {
        "pat@riverbend.org",
        "donna@360balancedliving.com",
        "ops@riverbend.org",
    }


def test_a_closed_thread_is_never_offered_to_the_fallback(seeded_db) -> None:
    """A header-chain match still joins a closed thread — the chain is proof — but the fallback is
    a guess, and a wrong guess would resurrect a conversation Donna deliberately ended."""
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id, "Speaking inquiry", closed=True)
    _message(conn, user_id, thread_id, "<a@x.com>")

    assert email_matching.fallback_candidates(conn, user_id, "Speaking inquiry") == []


def test_a_soft_deleted_thread_is_never_offered(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id, "Speaking inquiry", deleted=True)
    _message(conn, user_id, thread_id, "<a@x.com>")

    assert email_matching.fallback_candidates(conn, user_id, "Speaking inquiry") == []


def test_a_thread_with_no_activity_yet_is_excluded(seeded_db) -> None:
    """An unconfirmed pending send has no ``last_message_at``, so the core has no anchor to
    window against."""
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id, "Speaking inquiry", last_message_at=None)
    _message(conn, user_id, thread_id, "<a@x.com>")

    assert email_matching.fallback_candidates(conn, user_id, "Speaking inquiry") == []


def test_another_users_threads_are_never_offered(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    other_id = _user(conn)
    other_thread = _thread(conn, other_id, "Speaking inquiry")
    _message(conn, other_id, other_thread, "<a@x.com>")

    assert email_matching.fallback_candidates(conn, user_id, "Speaking inquiry") == []


def test_candidates_come_back_most_recently_active_first(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    older = _thread(
        conn, user_id, "Speaking inquiry", last_message_at=dt.datetime(2026, 1, 1, 9, 0)
    )
    newer = _thread(
        conn, user_id, "Speaking inquiry", last_message_at=dt.datetime(2026, 7, 1, 9, 0)
    )
    _message(conn, user_id, older, "<old@x.com>")
    _message(conn, user_id, newer, "<new@x.com>")

    candidates = email_matching.fallback_candidates(conn, user_id, "Speaking inquiry")
    assert [candidate.thread_id for candidate in candidates] == [newer, older]


def test_the_cap_drops_the_oldest_candidates_not_arbitrary_ones(seeded_db) -> None:
    """The cap can never cause a wrong match — every survivor still has to pass the counterpart and
    window checks in the core — and dropping oldest-first means a missed match yields a duplicate
    thread, the cosmetic failure the fallback's design already prefers."""
    conn, user_id, _, _ = seeded_db
    thread_ids = []
    for day in range(1, 6):
        thread_id = _thread(
            conn, user_id, "Speaking inquiry", last_message_at=dt.datetime(2026, 7, day, 9, 0)
        )
        _message(conn, user_id, thread_id, f"<m{day}@x.com>")
        thread_ids.append(thread_id)

    candidates = email_matching.fallback_candidates(conn, user_id, "Speaking inquiry", limit=2)
    assert [candidate.thread_id for candidate in candidates] == [thread_ids[4], thread_ids[3]]


def test_a_thread_whose_subject_differs_is_not_offered(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id, "Budget question")
    _message(conn, user_id, thread_id, "<a@x.com>")

    assert email_matching.fallback_candidates(conn, user_id, "Speaking inquiry") == []
