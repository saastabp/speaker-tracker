"""Pending-import repository tests — the triage queue and the two link actions.

Skip without ``TEST_DATABASE_URL`` (see conftest).

The queue is the one surface where the poller's work becomes Donna's work, so most of these tests
are about what does and does not appear in it, and about the asymmetry between the two links: a
message's *contact* is independently derived at ingest from who sent it and must not be overwritten,
while a message's *opportunity* has no independent source and follows its thread absolutely.
"""

from __future__ import annotations

import datetime as dt

from common import errors
from repositories import email_imports


def _user(conn, sub: str = "other") -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES (%s, %s)", (sub, f"{sub}@x.com"))
        return cur.lastrowid


def _contact(conn, user_id: int, name: str = "Pat Host", email: str = "pat@riverbend.org") -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO contacts (user_id, name, email) VALUES (%s, %s, %s)",
            (user_id, name, email),
        )
        return cur.lastrowid


def _organization(conn, user_id: int, org_type: str, name: str, email_domain: str | None) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM organization_types WHERE short_name = %s", (org_type,))
        type_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO organizations (user_id, organization_type_id, name, email_domain) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, type_id, name, email_domain),
        )
        return cur.lastrowid


def _opportunity(conn, user_id: int, organization_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM opportunity_statuses ORDER BY sort_order LIMIT 1")
        status_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM payment_statuses LIMIT 1")
        payment_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM opportunity_formats LIMIT 1")
        format_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM comp_types LIMIT 1")
        comp_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO opportunities (user_id, organization_id, opportunity_format_id, "
            " current_status_id, comp_type_id, payment_status_id, title, currency) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'Fall keynote', 'USD')",
            (user_id, organization_id, format_id, status_id, comp_id, payment_id),
        )
        return cur.lastrowid


def _thread(conn, user_id: int, *, contact_id: int | None = None, closed: bool = False) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_threads "
            "(user_id, contact_id, subject_normalized, last_direction, last_message_at, closed_at) "
            "VALUES (%s, %s, 'Speaking inquiry', 'in', %s, %s)",
            (
                user_id,
                contact_id,
                dt.datetime(2026, 7, 27, 10, 0),
                "2026-01-01 00:00:00" if closed else None,
            ),
        )
        return cur.lastrowid


def _message(
    conn,
    user_id: int,
    thread_id: int,
    message_id: str,
    *,
    from_addr: str = "Pat Host <pat@riverbend.org>",
    direction: str = "in",
    received_at: dt.datetime | None = dt.datetime(2026, 7, 27, 10, 0),
    contact_id: int | None = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_messages "
            "(user_id, thread_id, contact_id, message_id, direction, from_addr, subject, "
            " received_at) VALUES (%s, %s, %s, %s, %s, %s, 'Speaking inquiry', %s)",
            (user_id, thread_id, contact_id, message_id, direction, from_addr, received_at),
        )
        return cur.lastrowid


def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


# --- what appears in the queue -------------------------------------------------------------------


def test_an_empty_queue_is_an_empty_list(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    assert email_imports.list_pending_imports(conn, user_id) == []


def test_a_contactless_thread_with_inbound_mail_is_pending(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@riverbend.org>")

    pending = email_imports.list_pending_imports(conn, user_id)
    assert [row["thread_id"] for row in pending] == [thread_id]


def test_the_sender_is_split_into_address_and_display_name(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>", from_addr='"Host, Pat" <Pat@RiverBend.org>')

    row = email_imports.list_pending_imports(conn, user_id)[0]
    assert row["from_addr"] == "pat@riverbend.org"
    assert row["from_name"] == "Host, Pat"


def test_a_bare_address_yields_no_display_name(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>", from_addr="pat@riverbend.org")

    assert email_imports.list_pending_imports(conn, user_id)[0]["from_name"] is None


def test_a_thread_that_already_has_a_contact_is_not_pending(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    contact_id = _contact(conn, user_id)
    thread_id = _thread(conn, user_id, contact_id=contact_id)
    _message(conn, user_id, thread_id, "<a@x.com>")

    assert email_imports.list_pending_imports(conn, user_id) == []


def test_a_contactless_thread_with_only_outbound_mail_is_not_pending(seeded_db) -> None:
    """That is an unlinked send, not mail awaiting triage — and listing it would show Donna her own
    address as the sender to identify."""
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>", direction="out", from_addr="donna@x.com")

    assert email_imports.list_pending_imports(conn, user_id) == []


def test_a_closed_thread_is_not_pending(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id, closed=True)
    _message(conn, user_id, thread_id, "<a@x.com>")

    assert email_imports.list_pending_imports(conn, user_id) == []


def test_another_users_pending_threads_are_invisible(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    other_id = _user(conn)
    other_thread = _thread(conn, other_id)
    _message(conn, other_id, other_thread, "<a@x.com>")

    assert email_imports.list_pending_imports(conn, user_id) == []


def test_the_queue_shows_the_earliest_inbound_message_of_each_thread(seeded_db) -> None:
    """That is the one whose ``From`` prefills Add Contact."""
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id)
    first = _message(
        conn,
        user_id,
        thread_id,
        "<first@x.com>",
        from_addr="First Sender <first@riverbend.org>",
        received_at=dt.datetime(2026, 7, 20, 9, 0),
    )
    _message(
        conn,
        user_id,
        thread_id,
        "<later@x.com>",
        from_addr="Later Sender <later@riverbend.org>",
        received_at=dt.datetime(2026, 7, 25, 9, 0),
    )

    row = email_imports.list_pending_imports(conn, user_id)[0]
    assert row["email_message_id"] == first
    assert row["from_addr"] == "first@riverbend.org"


def test_the_queue_is_newest_first(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    older = _thread(conn, user_id)
    _message(conn, user_id, older, "<old@x.com>", received_at=dt.datetime(2026, 7, 1, 9, 0))
    newer = _thread(conn, user_id)
    _message(conn, user_id, newer, "<new@x.com>", received_at=dt.datetime(2026, 7, 26, 9, 0))

    pending = email_imports.list_pending_imports(conn, user_id)
    assert [row["thread_id"] for row in pending] == [newer, older]


# --- the organization suggestion -----------------------------------------------------------------


def test_the_senders_domain_suggests_a_venue(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    org_id = _organization(conn, user_id, org_type, "Riverbend Center", "RiverBend.org")
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>", from_addr="Pat <pat@riverbend.org>")

    row = email_imports.list_pending_imports(conn, user_id)[0]
    assert row["suggested_organization_id"] == org_id
    assert row["suggested_organization_name"] == "Riverbend Center"


def test_an_unclaimed_domain_suggests_nothing(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>", from_addr="stranger@nowhere.com")

    row = email_imports.list_pending_imports(conn, user_id)[0]
    assert row["suggested_organization_id"] is None
    assert row["suggested_organization_name"] is None


def test_a_domain_claimed_by_two_venues_suggests_neither(seeded_db, caplog) -> None:
    """A shared domain identifies nobody, so withholding is the honest answer.

    This is also the real defence against consumer domains. A ``gmail.com`` blocklist catches the
    loud cases and misses the quiet ones — ``stanford.edu`` is on no freemail list and is shared by
    twenty thousand people — whereas ambiguity is the symptom every one of them shows as soon as a
    second venue claims the domain. Picking one deterministically would be a coin flip presented as
    knowledge, and the prefill is only worth having if Donna can trust it without checking.
    """
    conn, user_id, org_type, _ = seeded_db
    _organization(conn, user_id, org_type, "Riverbend Center", "shared.org")
    _organization(conn, user_id, org_type, "Riverbend Catering", "shared.org")
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>", from_addr="pat@shared.org")

    row = email_imports.list_pending_imports(conn, user_id)[0]
    assert row["suggested_organization_id"] is None
    assert row["suggested_organization_name"] is None
    assert any("claimed by 2 organizations" in record.message for record in caplog.records), (
        "withholding a suggestion must be visible in the log, not silent"
    )


def test_a_second_venue_claiming_a_domain_withdraws_an_existing_suggestion(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    org_id = _organization(conn, user_id, org_type, "Riverbend Center", "riverbend.org")
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>", from_addr="pat@riverbend.org")
    assert (
        email_imports.list_pending_imports(conn, user_id)[0]["suggested_organization_id"] == org_id
    )

    _organization(conn, user_id, org_type, "Riverbend Catering", "riverbend.org")
    assert email_imports.list_pending_imports(conn, user_id)[0]["suggested_organization_id"] is None


def test_a_soft_deleted_venue_does_not_make_a_domain_ambiguous(seeded_db) -> None:
    """Otherwise deleting a duplicate venue would silently disable the suggestion it left behind."""
    conn, user_id, org_type, _ = seeded_db
    org_id = _organization(conn, user_id, org_type, "Riverbend Center", "riverbend.org")
    duplicate = _organization(conn, user_id, org_type, "Riverbend dup", "riverbend.org")
    with conn.cursor() as cur:
        cur.execute("UPDATE organizations SET deleted_at = NOW() WHERE id = %s", (duplicate,))

    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>", from_addr="pat@riverbend.org")

    assert (
        email_imports.list_pending_imports(conn, user_id)[0]["suggested_organization_id"] == org_id
    )


def test_another_users_organization_is_never_suggested(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    other_id = _user(conn)
    _organization(conn, other_id, org_type, "Theirs", "riverbend.org")
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>", from_addr="pat@riverbend.org")

    assert email_imports.list_pending_imports(conn, user_id)[0]["suggested_organization_id"] is None


# --- link_contact ---------------------------------------------------------------------------------


def test_linking_a_contact_empties_the_thread_from_the_queue(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    contact_id = _contact(conn, user_id)
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>")

    assert email_imports.link_contact(conn, user_id, thread_id, contact_id) is True
    assert email_imports.list_pending_imports(conn, user_id) == []


def test_linking_fills_messages_that_have_no_contact_of_their_own(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    contact_id = _contact(conn, user_id)
    thread_id = _thread(conn, user_id)
    unattributed = _message(conn, user_id, thread_id, "<a@x.com>")

    email_imports.link_contact(conn, user_id, thread_id, contact_id)

    stored = _rows(conn, "SELECT contact_id FROM email_messages WHERE id = %s", (unattributed,))
    assert stored[0]["contact_id"] == contact_id


def test_linking_does_not_overwrite_a_message_attributed_to_someone_else(seeded_db) -> None:
    """A second tracked contact looped into a pending thread keeps their own attribution.

    Their message really did come from them; rewriting it as the person Donna links would record
    something false. The contact on a message has an independent source of truth — ingest derives
    it from who actually sent the message — which is why this link fills blanks only.
    """
    conn, user_id, _, _ = seeded_db
    linked = _contact(conn, user_id, "Pat Host", "pat@riverbend.org")
    other = _contact(conn, user_id, "Sam Colleague", "sam@riverbend.org")
    thread_id = _thread(conn, user_id)
    blank = _message(conn, user_id, thread_id, "<a@x.com>")
    theirs = _message(conn, user_id, thread_id, "<b@x.com>", contact_id=other)

    email_imports.link_contact(conn, user_id, thread_id, linked)

    stored = {
        row["id"]: row["contact_id"]
        for row in _rows(conn, "SELECT id, contact_id FROM email_messages")
    }
    assert stored[blank] == linked
    assert stored[theirs] == other


def test_linking_the_same_contact_twice_still_succeeds(seeded_db) -> None:
    """MySQL reports rows *changed*, not matched, so inferring existence from the UPDATE's rowcount
    would make a repeat link indistinguishable from a missing thread."""
    conn, user_id, _, _ = seeded_db
    contact_id = _contact(conn, user_id)
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>")

    assert email_imports.link_contact(conn, user_id, thread_id, contact_id) is True
    assert email_imports.link_contact(conn, user_id, thread_id, contact_id) is True


def test_detaching_returns_the_thread_to_the_queue(seeded_db) -> None:
    """The correction for linking the wrong person. Re-linking to a *different* contact always
    worked; "none" is the one someone who has just made a mistake reaches for."""
    conn, user_id, _, _ = seeded_db
    contact_id = _contact(conn, user_id)
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>")

    email_imports.link_contact(conn, user_id, thread_id, contact_id)
    assert email_imports.list_pending_imports(conn, user_id) == []

    assert email_imports.link_contact(conn, user_id, thread_id, None) is True
    pending = email_imports.list_pending_imports(conn, user_id)
    assert [row["thread_id"] for row in pending] == [thread_id]


def test_detaching_clears_only_what_the_link_filled(seeded_db) -> None:
    """Undoing Donna's link must not erase a fact her link never asserted.

    A second tracked contact who replied into the thread had their id put there by *ingest*, from
    who actually sent the message. Detaching is an undo of the link, not of the ingest.
    """
    conn, user_id, _, _ = seeded_db
    linked = _contact(conn, user_id, "Pat Host", "pat@riverbend.org")
    other = _contact(conn, user_id, "Sam Colleague", "sam@riverbend.org")
    thread_id = _thread(conn, user_id)
    filled_by_link = _message(conn, user_id, thread_id, "<a@x.com>")
    theirs = _message(conn, user_id, thread_id, "<b@x.com>", contact_id=other)

    email_imports.link_contact(conn, user_id, thread_id, linked)
    email_imports.link_contact(conn, user_id, thread_id, None)

    stored = {
        row["id"]: row["contact_id"]
        for row in _rows(conn, "SELECT id, contact_id FROM email_messages")
    }
    assert stored[filled_by_link] is None, "the link's own fill should be undone"
    assert stored[theirs] == other, "ingest's attribution must survive the undo"


def test_detaching_a_thread_that_was_never_linked_is_harmless(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>")

    assert email_imports.link_contact(conn, user_id, thread_id, None) is True
    assert len(email_imports.list_pending_imports(conn, user_id)) == 1


def test_linking_an_unknown_contact_is_rejected(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    thread_id = _thread(conn, user_id)
    try:
        email_imports.link_contact(conn, user_id, thread_id, 99999)
        raise AssertionError("expected InvalidInput")
    except errors.InvalidInput:
        pass


def test_linking_another_users_contact_is_rejected(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    other_id = _user(conn)
    theirs = _contact(conn, other_id, "Theirs", "theirs@x.com")
    thread_id = _thread(conn, user_id)

    try:
        email_imports.link_contact(conn, user_id, thread_id, theirs)
        raise AssertionError("expected InvalidInput")
    except errors.InvalidInput:
        pass


def test_linking_on_an_unknown_thread_reports_false(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    contact_id = _contact(conn, user_id)
    assert email_imports.link_contact(conn, user_id, 99999, contact_id) is False


def test_linking_on_another_users_thread_reports_false(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    other_id = _user(conn)
    contact_id = _contact(conn, user_id)
    theirs = _thread(conn, other_id)

    assert email_imports.link_contact(conn, user_id, theirs, contact_id) is False


# --- link_opportunity -----------------------------------------------------------------------------


def test_linking_a_gig_sets_the_thread_and_every_message(seeded_db) -> None:
    """Unlike the contact link, this overwrites unconditionally: a message's opportunity has no
    independent source — nothing may infer a gig from a message — so the thread is the sole
    authority."""
    conn, user_id, org_type, _ = seeded_db
    org_id = _organization(conn, user_id, org_type, "Riverbend", "riverbend.org")
    opportunity_id = _opportunity(conn, user_id, org_id)
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>")
    _message(conn, user_id, thread_id, "<b@x.com>")

    assert email_imports.link_opportunity(conn, user_id, thread_id, opportunity_id) is True

    thread = _rows(conn, "SELECT opportunity_id FROM email_threads WHERE id = %s", (thread_id,))
    assert thread[0]["opportunity_id"] == opportunity_id
    messages = _rows(
        conn, "SELECT opportunity_id FROM email_messages WHERE thread_id = %s", (thread_id,)
    )
    assert all(row["opportunity_id"] == opportunity_id for row in messages)


def test_detaching_clears_the_thread_and_its_messages(seeded_db) -> None:
    """Filling only blanks would strand messages pointing at a gig their thread has left."""
    conn, user_id, org_type, _ = seeded_db
    org_id = _organization(conn, user_id, org_type, "Riverbend", "riverbend.org")
    opportunity_id = _opportunity(conn, user_id, org_id)
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>")

    email_imports.link_opportunity(conn, user_id, thread_id, opportunity_id)
    assert email_imports.link_opportunity(conn, user_id, thread_id, None) is True

    thread = _rows(conn, "SELECT opportunity_id FROM email_threads WHERE id = %s", (thread_id,))
    assert thread[0]["opportunity_id"] is None
    messages = _rows(
        conn, "SELECT opportunity_id FROM email_messages WHERE thread_id = %s", (thread_id,)
    )
    assert all(row["opportunity_id"] is None for row in messages)


def test_linking_another_users_gig_is_rejected(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    other_id = _user(conn)
    their_org = _organization(conn, other_id, org_type, "Theirs", "theirs.org")
    theirs = _opportunity(conn, other_id, their_org)
    thread_id = _thread(conn, user_id)

    try:
        email_imports.link_opportunity(conn, user_id, thread_id, theirs)
        raise AssertionError("expected InvalidInput")
    except errors.InvalidInput:
        pass


def test_linking_a_gig_on_an_unknown_thread_reports_false(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    assert email_imports.link_opportunity(conn, user_id, 99999, None) is False


def test_linking_a_gig_does_not_remove_the_thread_from_the_queue(seeded_db) -> None:
    """The queue is about attribution to a *person*; a gig link is a separate axis."""
    conn, user_id, org_type, _ = seeded_db
    org_id = _organization(conn, user_id, org_type, "Riverbend", "riverbend.org")
    opportunity_id = _opportunity(conn, user_id, org_id)
    thread_id = _thread(conn, user_id)
    _message(conn, user_id, thread_id, "<a@x.com>")

    email_imports.link_opportunity(conn, user_id, thread_id, opportunity_id)
    assert len(email_imports.list_pending_imports(conn, user_id)) == 1
