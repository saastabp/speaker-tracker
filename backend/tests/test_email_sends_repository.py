"""Send-path repository tests against a seeded MySQL — the intent-first transaction.

Skip without ``TEST_DATABASE_URL`` (see conftest). These mechanize DEV-PLAN slice 6a acceptance #2
at the repository level, where it can be proven **without mocking AWS**: SES lives in the handler,
so phase 1 (intent), phase 3 (confirm) and the compensation are all plain SQL here.

The properties that matter:

- phase 1 leaves a *pending* row — ``direction='out' AND sent_at IS NULL`` — so a crash before or
  during the send is detectable rather than a silent loss;
- the compensation removes every row phase 1 wrote, so a cleanly failed send leaves nothing;
- the compensation **refuses** to touch a confirmed message, which is the guard that keeps a real
  send from being deleted by a late or duplicated failure path;
- an emailed touch infers its outreach kind exactly as a manually logged one does.
"""

from __future__ import annotations

import pytest

from common import errors
from models.emails import EmailSendInput
from repositories import email_sends as sends


@pytest.fixture
def send_db(seeded_db):
    """A migrated DB with one user, a venue, two contacts, an opportunity, and a foreign contact.

    Returns ``(conn, user_id, ids)`` where ``ids`` has ``jane`` / ``ann`` (contacts), ``opp``,
    ``template`` (a seeded shared template), ``other_user`` and ``other_contact`` (a second
    tenant's, for cross-user checks).
    """
    conn, user_id, _, _ = seeded_db
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (user_id, organization_type_id, name) "
            "SELECT %s, id, 'Expo' FROM organization_types WHERE short_name = 'expo'",
            (user_id,),
        )
        org = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Jane')", (user_id,))
        jane = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Ann')", (user_id,))
        ann = cur.lastrowid
        cur.execute(
            "INSERT INTO opportunities "
            "(user_id, organization_id, opportunity_format_id, current_status_id, comp_type_id, "
            " payment_status_id, title) "
            "SELECT %s, %s, fmt.id, st.id, ct.id, pay.id, 'Gig' "
            "FROM opportunity_formats fmt, opportunity_statuses st, comp_types ct, "
            "     payment_statuses pay "
            "WHERE fmt.short_name = 'workshop' AND st.short_name = 'researching' "
            "  AND ct.short_name = 'paid' AND pay.short_name = 'unbilled'",
            (user_id, org),
        )
        opp = cur.lastrowid
        cur.execute("SELECT id FROM message_templates WHERE user_id IS NULL AND name = 'Cold DM'")
        template = cur.fetchone()["id"]
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('user2', 'user2@example.com')")
        other_user = cur.lastrowid
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, 'Foreign')", (other_user,))
        other_contact = cur.lastrowid
    return (
        conn,
        user_id,
        {
            "jane": jane,
            "ann": ann,
            "opp": opp,
            "template": template,
            "other_user": other_user,
            "other_contact": other_contact,
        },
    )


def _send_input(**overrides) -> EmailSendInput:
    """Build a valid composer payload, overriding any field."""
    payload = {
        "to": ["venue@example.com"],
        "subject": "Speaking at your event",
        "body_html": "<p>Hello</p>",
    }
    payload.update(overrides)
    return EmailSendInput(**payload)


def _pending(conn, message_row_id: int) -> dict | None:
    """Fetch a message row directly, bypassing the repository's reads."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM email_messages WHERE id = %s", (message_row_id,))
        return cur.fetchone()


def _count(conn, table: str, **where) -> int:
    """Count rows in ``table`` matching equality conditions (test helper, literals only)."""
    clause = " AND ".join(f"{col} = %s" for col in where)
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {clause}", tuple(where.values()))
        return cur.fetchone()["n"]


# --- phase 1: intent -------------------------------------------------------------------------


def test_new_send_writes_thread_message_and_outreach(send_db) -> None:
    conn, user_id, ids = send_db
    pending = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"], opportunity_id=ids["opp"]),
        message_id="<a@x.com>",
        from_addr="donna@x.com",
    )

    assert pending.thread_created is True
    assert pending.outreach_id is not None
    assert _count(conn, "email_threads", id=pending.thread_id) == 1
    assert _count(conn, "outreaches", id=pending.outreach_id) == 1

    row = _pending(conn, pending.message_row_id)
    assert row["direction"] == "out"
    assert row["sent_at"] is None, "phase 1 must leave the message pending, not sent"
    assert row["message_id"] == "<a@x.com>"
    assert row["to_addr"] == "venue@example.com"


def test_new_thread_has_no_last_message_at_until_confirmed(send_db) -> None:
    # Thread aggregates advance in phase 3 only — that is what lets compensation be a pure delete.
    conn, user_id, ids = send_db
    pending = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"]),
        message_id="<a@x.com>",
        from_addr="donna@x.com",
    )
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM email_threads WHERE id = %s", (pending.thread_id,))
        thread = cur.fetchone()
    assert thread["last_message_at"] is None
    assert thread["last_direction"] == "out"


def test_subject_is_normalized_onto_the_thread(send_db) -> None:
    conn, user_id, ids = send_db
    pending = sends.create_pending_send(
        conn,
        user_id,
        _send_input(subject="Re: Fwd: Keynote slot", contact_id=ids["jane"]),
        message_id="<a@x.com>",
        from_addr="donna@x.com",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT subject_normalized FROM email_threads WHERE id = %s", (pending.thread_id,)
        )
        assert cur.fetchone()["subject_normalized"] == "Keynote slot"


def test_reply_reuses_the_existing_thread(send_db) -> None:
    conn, user_id, ids = send_db
    first = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"]),
        message_id="<a@x.com>",
        from_addr="donna@x.com",
    )
    second = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"]),
        message_id="<b@x.com>",
        from_addr="donna@x.com",
        thread_id=first.thread_id,
        in_reply_to="<a@x.com>",
        message_references="<a@x.com>",
    )

    assert second.thread_id == first.thread_id
    assert second.thread_created is False
    row = _pending(conn, second.message_row_id)
    assert row["in_reply_to"] == "<a@x.com>"
    assert row["message_references"] == "<a@x.com>"


def test_send_without_contact_logs_no_outreach(send_db) -> None:
    # outreaches.contact_id is NOT NULL, so an unlinked send records the message but no touch.
    conn, user_id, _ = send_db
    pending = sends.create_pending_send(
        conn, user_id, _send_input(), message_id="<a@x.com>", from_addr="donna@x.com"
    )
    assert pending.outreach_id is None
    assert _count(conn, "email_messages", id=pending.message_row_id) == 1


def test_empty_cc_is_stored_as_null(send_db) -> None:
    conn, user_id, _ = send_db
    pending = sends.create_pending_send(
        conn, user_id, _send_input(), message_id="<a@x.com>", from_addr="donna@x.com"
    )
    assert _pending(conn, pending.message_row_id)["cc_addr"] is None


# --- phase 1: tenancy and idempotency --------------------------------------------------------


def test_foreign_contact_is_rejected(send_db) -> None:
    conn, user_id, ids = send_db
    with pytest.raises(errors.InvalidInput):
        sends.create_pending_send(
            conn,
            user_id,
            _send_input(contact_id=ids["other_contact"]),
            message_id="<a@x.com>",
            from_addr="donna@x.com",
        )


def test_unknown_opportunity_is_rejected(send_db) -> None:
    conn, user_id, _ = send_db
    with pytest.raises(errors.InvalidInput):
        sends.create_pending_send(
            conn,
            user_id,
            _send_input(opportunity_id=999_999),
            message_id="<a@x.com>",
            from_addr="donna@x.com",
        )


def test_unknown_template_is_rejected(send_db) -> None:
    conn, user_id, ids = send_db
    with pytest.raises(errors.InvalidInput):
        sends.create_pending_send(
            conn,
            user_id,
            _send_input(contact_id=ids["jane"], message_template_id=999_999),
            message_id="<a@x.com>",
            from_addr="donna@x.com",
        )


def test_foreign_thread_is_not_found(send_db) -> None:
    conn, user_id, ids = send_db
    other = sends.create_pending_send(
        conn,
        ids["other_user"],
        _send_input(),
        message_id="<other@x.com>",
        from_addr="them@x.com",
    )
    with pytest.raises(errors.NotFound):
        sends.create_pending_send(
            conn,
            user_id,
            _send_input(),
            message_id="<a@x.com>",
            from_addr="donna@x.com",
            thread_id=other.thread_id,
        )


def test_duplicate_message_id_conflicts_for_the_same_user(send_db) -> None:
    # UNIQUE(user_id, message_id) is the idempotency key a replayed request would collide with.
    conn, user_id, _ = send_db
    sends.create_pending_send(
        conn, user_id, _send_input(), message_id="<dupe@x.com>", from_addr="donna@x.com"
    )
    with pytest.raises(errors.Conflict):
        sends.create_pending_send(
            conn, user_id, _send_input(), message_id="<dupe@x.com>", from_addr="donna@x.com"
        )


def test_same_message_id_is_allowed_for_a_different_user(send_db) -> None:
    # The key is (user_id, message_id) — scoped per tenant, not global.
    conn, user_id, ids = send_db
    sends.create_pending_send(
        conn, user_id, _send_input(), message_id="<same@x.com>", from_addr="donna@x.com"
    )
    other = sends.create_pending_send(
        conn, ids["other_user"], _send_input(), message_id="<same@x.com>", from_addr="them@x.com"
    )
    assert other.message_row_id is not None


# --- outreach kind inference -----------------------------------------------------------------


def _kind_of(conn, outreach_id: int) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT k.short_name FROM outreaches o "
            "JOIN outreach_kinds k ON k.id = o.outreach_kind_id WHERE o.id = %s",
            (outreach_id,),
        )
        return cur.fetchone()["short_name"]


def test_first_emailed_touch_infers_initial(send_db) -> None:
    conn, user_id, ids = send_db
    pending = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"]),
        message_id="<a@x.com>",
        from_addr="donna@x.com",
    )
    assert _kind_of(conn, pending.outreach_id) == "initial"


def test_second_emailed_touch_infers_correspondence(send_db) -> None:
    conn, user_id, ids = send_db
    sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"]),
        message_id="<a@x.com>",
        from_addr="donna@x.com",
    )
    second = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"]),
        message_id="<b@x.com>",
        from_addr="donna@x.com",
    )
    assert _kind_of(conn, second.outreach_id) == "correspondence"


def test_kind_override_persists(send_db) -> None:
    conn, user_id, ids = send_db
    pending = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"], outreach_kind="follow_up"),
        message_id="<a@x.com>",
        from_addr="donna@x.com",
    )
    assert _kind_of(conn, pending.outreach_id) == "follow_up"


def test_emailed_touch_is_logged_under_the_email_channel(send_db) -> None:
    conn, user_id, ids = send_db
    pending = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"]),
        message_id="<a@x.com>",
        from_addr="donna@x.com",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ch.short_name FROM outreaches o "
            "JOIN outreach_channels ch ON ch.id = o.outreach_channel_id WHERE o.id = %s",
            (pending.outreach_id,),
        )
        assert cur.fetchone()["short_name"] == "email"


# --- phase 3: confirm ------------------------------------------------------------------------


def test_confirm_marks_sent_and_advances_the_thread(send_db) -> None:
    conn, user_id, ids = send_db
    pending = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"]),
        message_id="<a@x.com>",
        from_addr="donna@x.com",
    )

    assert sends.confirm_send(conn, user_id, pending.message_row_id) is True

    row = _pending(conn, pending.message_row_id)
    assert row["sent_at"] is not None
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM email_threads WHERE id = %s", (pending.thread_id,))
        thread = cur.fetchone()
    assert thread["last_direction"] == "out"
    assert thread["last_message_at"] == row["sent_at"]


def test_confirming_twice_reports_no_change(send_db) -> None:
    # A repeated confirm is a reconciliation signal, never a reason to re-send.
    conn, user_id, _ = send_db
    pending = sends.create_pending_send(
        conn, user_id, _send_input(), message_id="<a@x.com>", from_addr="donna@x.com"
    )
    assert sends.confirm_send(conn, user_id, pending.message_row_id) is True
    assert sends.confirm_send(conn, user_id, pending.message_row_id) is False


def test_confirm_is_owner_scoped(send_db) -> None:
    conn, user_id, ids = send_db
    pending = sends.create_pending_send(
        conn, user_id, _send_input(), message_id="<a@x.com>", from_addr="donna@x.com"
    )
    assert sends.confirm_send(conn, ids["other_user"], pending.message_row_id) is False
    assert _pending(conn, pending.message_row_id)["sent_at"] is None


# --- compensation (acceptance #2) ------------------------------------------------------------


def test_discard_removes_every_row_phase_one_wrote(send_db) -> None:
    # Acceptance #2: a forced SES failure leaves no rows.
    conn, user_id, ids = send_db
    pending = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"], opportunity_id=ids["opp"]),
        message_id="<a@x.com>",
        from_addr="donna@x.com",
    )

    sends.discard_pending_send(conn, user_id, pending)

    assert _count(conn, "email_messages", id=pending.message_row_id) == 0
    assert _count(conn, "outreaches", id=pending.outreach_id) == 0
    assert _count(conn, "email_threads", id=pending.thread_id) == 0


def test_discard_keeps_a_thread_it_did_not_create(send_db) -> None:
    # A failed reply must not delete the conversation it was replying to.
    conn, user_id, ids = send_db
    first = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"]),
        message_id="<a@x.com>",
        from_addr="donna@x.com",
    )
    reply = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"]),
        message_id="<b@x.com>",
        from_addr="donna@x.com",
        thread_id=first.thread_id,
    )

    sends.discard_pending_send(conn, user_id, reply)

    assert _count(conn, "email_messages", id=reply.message_row_id) == 0
    assert _count(conn, "email_threads", id=first.thread_id) == 1
    assert _count(conn, "email_messages", id=first.message_row_id) == 1


def test_discard_refuses_a_confirmed_message(send_db) -> None:
    # The guard that keeps a real send from being compensated away after the fact.
    conn, user_id, ids = send_db
    pending = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"]),
        message_id="<a@x.com>",
        from_addr="donna@x.com",
    )
    sends.confirm_send(conn, user_id, pending.message_row_id)

    with pytest.raises(errors.Conflict):
        sends.discard_pending_send(conn, user_id, pending)

    assert _count(conn, "email_messages", id=pending.message_row_id) == 1
    assert _count(conn, "outreaches", id=pending.outreach_id) == 1


def test_discard_is_owner_scoped(send_db) -> None:
    conn, user_id, ids = send_db
    pending = sends.create_pending_send(
        conn,
        user_id,
        _send_input(contact_id=ids["jane"]),
        message_id="<a@x.com>",
        from_addr="donna@x.com",
    )

    sends.discard_pending_send(conn, ids["other_user"], pending)

    assert _count(conn, "email_messages", id=pending.message_row_id) == 1
    assert _count(conn, "outreaches", id=pending.outreach_id) == 1
