"""Dashboard repository tests against a seeded MySQL — the slice-5 aggregates.

Skip without ``TEST_DATABASE_URL`` (see conftest). Mechanize acceptance #1 (timezone bucketing),
#2 (venues_researched = current research-ready count), #3 (funnel reached-or-beyond), #4 (only
counts_toward_target kinds feed the outreach target), #5 (money excludes pro bono from currency
totals), and #6 (needs-attention rows).
"""

from __future__ import annotations

from datetime import date, datetime

from models.opportunities import OpportunityCreateInput
from models.outreach import OutreachInput
from models.targets import TargetInput
from repositories import dashboard
from repositories import opportunities as opp
from repositories import outreaches as out
from repositories import targets as targets_repo


def _org(conn, user_id: int, name: str, kindling: bool = True) -> int:
    with conn.cursor() as cur:
        if kindling:
            cur.execute(
                "INSERT INTO organizations "
                "(user_id, organization_type_id, name, what_it_is, why_it_fits, how_to_approach) "
                "SELECT %s, id, %s, 'what', 'why', 'how' FROM organization_types "
                "WHERE short_name = 'expo'",
                (user_id, name),
            )
        else:
            cur.execute(
                "INSERT INTO organizations (user_id, organization_type_id, name) "
                "SELECT %s, id, %s FROM organization_types WHERE short_name = 'expo'",
                (user_id, name),
            )
        return cur.lastrowid


def _contact(conn, user_id: int, name: str = "Contact") -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, %s)", (user_id, name))
        return cur.lastrowid


def _affiliate(conn, contact_id: int, org_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO contact_organizations (contact_id, organization_id) VALUES (%s, %s)",
            (contact_id, org_id),
        )


def _opp(conn, user_id: int, org_id: int, **kw) -> int:
    base = {
        "title": "Gig",
        "organization_id": org_id,
        "opportunity_format": "workshop",
        "comp_type": "paid",
    }
    base.update(kw)
    return opp.create_opportunity(conn, user_id, OpportunityCreateInput(**base))


def _set_last_event(conn, opp_id: int, when: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE status_events SET occurred_at = %s WHERE opportunity_id = %s", (when, opp_id)
        )


# --- #4 + #1: outreach target actual --------------------------------------------------------------


def test_outreaches_actual_counts_only_counting_kinds_in_window(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    contact = _contact(conn, user_id)
    now = datetime(2026, 7, 22, 12, 0)  # a Wednesday; weekly window is [07-19, 07-26)
    # In-window, counting (initial inferred): counts.
    out.create_outreach(
        conn,
        user_id,
        OutreachInput(contact_id=contact, channel="dm", occurred_at=datetime(2026, 7, 20, 9)),
    )
    # In-window but correspondence (non-counting): excluded (#4).
    out.create_outreach(
        conn,
        user_id,
        OutreachInput(
            contact_id=contact,
            channel="dm",
            kind="correspondence",
            occurred_at=datetime(2026, 7, 21, 9),
        ),
    )
    # Out-of-window (last week): excluded.
    out.create_outreach(
        conn,
        user_id,
        OutreachInput(
            contact_id=contact, channel="dm", kind="follow_up", occurred_at=datetime(2026, 7, 10, 9)
        ),
    )
    targets_repo.upsert_target(
        conn, user_id, TargetInput(target_type="outreaches", cadence="weekly", goal_count=5)
    )
    tiles = dashboard.target_actuals(conn, user_id, now)
    tile = next(t for t in tiles if t["target_type"] == "outreaches")
    # The window travels with the tile so its drill-down can ask the list for exactly the period
    # the number was counted over. Sunday-start week, end exclusive — the same bounds that put the
    # Jul 10 touch above out of scope.
    assert tile == {
        "target_type": "outreaches",
        "cadence": "weekly",
        "goal": 5,
        "actual": 1,
        "period_start": date(2026, 7, 19),
        "period_end": date(2026, 7, 26),
    }


def test_actuals_bucket_in_user_timezone(seeded_db) -> None:
    # #1: a 22:00-Kauaʻi touch on July 31 is Aug 1 in UTC — it must still count toward July.
    conn, user_id, _, _ = seeded_db
    with conn.cursor() as cur:
        cur.execute("SET time_zone = '-10:00'")  # Kauaʻi (no DST); named-tz tables not needed
    contact = _contact(conn, user_id)
    out.create_outreach(
        conn,
        user_id,
        OutreachInput(contact_id=contact, channel="dm", occurred_at=datetime(2026, 7, 31, 22, 0)),
    )
    targets_repo.upsert_target(
        conn, user_id, TargetInput(target_type="outreaches", cadence="monthly", goal_count=5)
    )
    july = dashboard.target_actuals(conn, user_id, datetime(2026, 7, 15, 12, 0))
    assert next(t for t in july if t["target_type"] == "outreaches")["actual"] == 1  # July, local
    # The same touch does NOT count toward August (would if it were bucketed by the UTC day).
    august = dashboard.target_actuals(conn, user_id, datetime(2026, 8, 15, 12, 0))
    assert next(t for t in august if t["target_type"] == "outreaches")["actual"] == 0


# --- #2: venues_researched actual is current research-ready count ---------------------------------


def test_venues_researched_actual_is_current_ready_count(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    ready = _org(conn, user_id, "Ready", kindling=True)
    _affiliate(conn, _contact(conn, user_id, "A"), ready)
    _org(conn, user_id, "NoKindling", kindling=False)  # missing Kindling → not ready
    missing_contact = _org(conn, user_id, "NoContact", kindling=True)  # Kindling but no contact
    assert missing_contact  # referenced for clarity
    targets_repo.upsert_target(
        conn, user_id, TargetInput(target_type="venues_researched", cadence="weekly", goal_count=10)
    )
    tiles = dashboard.target_actuals(conn, user_id, datetime(2026, 7, 22))
    assert next(t for t in tiles if t["target_type"] == "venues_researched")["actual"] == 1


# --- #3: funnel reached-or-beyond ----------------------------------------------------------------


def test_funnel_is_reached_or_beyond(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    org = _org(conn, user_id, "Venue")
    a = _opp(conn, user_id, org)
    opp.patch_status(
        conn, user_id, a, "pitched"
    )  # jumped straight to Pitched (no outreach_sent event)
    b = _opp(conn, user_id, org)
    opp.patch_status(conn, user_id, b, "booked")
    rows = dashboard.funnel_counts(conn, user_id)
    counts = {r["status"]: r["count"] for r in rows}
    # All five stages present; a gig that jumped to Pitched still counts toward Outreach Sent (#3).
    # `delivered` is the display-only final row (neither gig reached it).
    assert counts == {
        "outreach_sent": 2,
        "in_conversation": 2,
        "pitched": 2,
        "booked": 1,
        "delivered": 0,
    }
    # `current` is where each gig sits *now*, so it does not accumulate down the funnel: one gig is
    # parked at Pitched and one at Booked, and neither is counted at the stages it passed through.
    # This is the number the card links to, precisely because it is not monotonic.
    assert {r["status"]: r["current"] for r in rows} == {
        "outreach_sent": 0,
        "in_conversation": 0,
        "pitched": 1,
        "booked": 1,
        "delivered": 0,
    }


# --- #5: money rollup ----------------------------------------------------------------------------


def test_money_rollup_excludes_pro_bono_from_currency_totals(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    org = _org(conn, user_id, "Venue")
    booked_unpaid = _opp(conn, user_id, org, fee_amount="1000")
    opp.patch_status(conn, user_id, booked_unpaid, "booked")
    delivered_paid = _opp(conn, user_id, org, fee_amount="500")
    opp.patch_status(conn, user_id, delivered_paid, "delivered")
    opp.patch_payment(conn, user_id, delivered_paid, "paid", date(2026, 7, 1))
    pro_bono = _opp(conn, user_id, org, comp_type="pro_bono")
    opp.patch_status(conn, user_id, pro_bono, "booked")
    money = dashboard.money_rollup(conn, user_id)
    assert str(money["booked"]) == "1500.00"  # 1000 + 500, pro bono excluded
    assert str(money["received"]) == "500.00"
    assert str(money["outstanding"]) == "1000.00"
    assert money["pro_bono_count"] == 1
    assert money["currency"] == "USD"
    # Sub-counts behind each figure (money-card sub-labels).
    assert money["booked_count"] == 2  # booked_unpaid + delivered_paid
    assert money["received_count"] == 1  # delivered_paid
    assert money["invoiced_count"] == 0  # none invoiced


# --- gone quiet (14d), a needs-attention reason since 2026-07-30 ----------------------------------


def _quiet(rows: list[dict]) -> list[str]:
    """Titles of the `stale` (Gone quiet) rows in a needs-attention result."""
    return [r["title"] for r in rows if r["reason"] == "stale"]


def test_gone_quiet_lists_only_inactive_open_gigs(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    org = _org(conn, user_id, "Venue")
    _affiliate(conn, _contact(conn, user_id), org)  # research-ready → no research_incomplete noise
    old = _opp(conn, user_id, org, title="Old")
    _set_last_event(conn, old, datetime(2026, 7, 1, 10, 0))
    recent = _opp(conn, user_id, org, title="Recent")
    _set_last_event(conn, recent, datetime(2026, 7, 19, 10, 0))
    closed = _opp(conn, user_id, org, title="Closed")
    _set_last_event(conn, closed, datetime(2026, 7, 1, 10, 0))
    opp.close(conn, user_id, closed, "lost", "went cold")
    now = datetime(2026, 7, 20, 12, 0)  # cutoff = 07-06

    assert _quiet(dashboard.needs_attention(conn, user_id, now)) == ["Old"]


def test_gone_quiet_ignores_booked_gigs_waiting_on_a_distant_event(seeded_db) -> None:
    """A booked gig is *supposed* to be quiet until its date — flagging it fortnightly was noise.

    This is what separates "Gone quiet" from the old Stale card: it means a **pursuit** has
    stalled, not that a calendar is waiting.
    """
    conn, user_id, _, _ = seeded_db
    org = _org(conn, user_id, "Venue")
    _affiliate(conn, _contact(conn, user_id), org)
    booked = _opp(conn, user_id, org, title="Booked far out", event_date=date(2026, 12, 1))
    opp.patch_status(conn, user_id, booked, "booked")
    _set_last_event(conn, booked, datetime(2026, 7, 1, 10, 0))
    now = datetime(2026, 7, 20, 12, 0)

    assert _quiet(dashboard.needs_attention(conn, user_id, now)) == []


def test_gone_quiet_yields_to_the_more_specific_overdue_reason(seeded_db) -> None:
    """One row, one reason. A quiet gig whose event date has passed is *overdue*, not just quiet."""
    conn, user_id, _, _ = seeded_db
    org = _org(conn, user_id, "Venue")
    _affiliate(conn, _contact(conn, user_id), org)
    past = _opp(conn, user_id, org, title="Past event", event_date=date(2026, 7, 10))
    _set_last_event(conn, past, datetime(2026, 7, 1, 10, 0))
    now = datetime(2026, 7, 20, 12, 0)

    rows = dashboard.needs_attention(conn, user_id, now)
    reasons = [r["reason"] for r in rows if r["title"] == "Past event"]
    assert reasons == ["overdue_unbooked"]  # not also 'stale'


def test_gone_quiet_reports_the_date_it_went_quiet(seeded_db) -> None:
    """`since` is what lets the SPA render an age chip instead of a bare label."""
    conn, user_id, _, _ = seeded_db
    org = _org(conn, user_id, "Venue")
    _affiliate(conn, _contact(conn, user_id), org)
    quiet = _opp(conn, user_id, org, title="Quiet")
    _set_last_event(conn, quiet, datetime(2026, 7, 1, 10, 0))
    now = datetime(2026, 7, 20, 12, 0)

    row = next(r for r in dashboard.needs_attention(conn, user_id, now) if r["title"] == "Quiet")
    assert row["since"] == date(2026, 7, 1)


# --- #6: needs-attention -------------------------------------------------------------------------


def test_needs_attention_flags_awaiting_payment_and_overdue_unbooked(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    org = _org(conn, user_id, "Venue")
    _affiliate(conn, _contact(conn, user_id), org)  # research-ready → not a research_incomplete row
    # Delivered but unpaid → awaiting_payment (stays open).
    awaiting = _opp(conn, user_id, org, title="Awaiting", fee_amount="800")
    opp.patch_status(conn, user_id, awaiting, "delivered")
    # Past event date, still pre-Booked → overdue_unbooked.
    overdue = _opp(conn, user_id, org, title="Overdue", event_date=date(2026, 7, 1))
    opp.patch_status(conn, user_id, overdue, "pitched")
    # A healthy future booked gig → not flagged.
    healthy = _opp(conn, user_id, org, title="Healthy", event_date=date(2026, 12, 1))
    opp.patch_status(conn, user_id, healthy, "booked")
    rows = dashboard.needs_attention(conn, user_id, datetime(2026, 7, 20, 12, 0))
    by_title = {r["title"]: r["reason"] for r in rows}
    assert by_title == {"Awaiting": "awaiting_payment", "Overdue": "overdue_unbooked"}


def test_needs_attention_flags_research_incomplete_venues(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    ready = _org(conn, user_id, "Ready", kindling=True)
    _affiliate(conn, _contact(conn, user_id, "C1"), ready)  # 3 fields + contact → research-ready
    _org(conn, user_id, "NoKindling", kindling=False)  # missing Kindling fields → incomplete
    _org(conn, user_id, "NoContact", kindling=True)  # fields filled but no contact → incomplete
    rows = dashboard.needs_attention(conn, user_id, datetime(2026, 7, 20, 12, 0))
    incomplete = {r["title"] for r in rows if r["reason"] == "research_incomplete"}
    assert incomplete == {"NoKindling", "NoContact"}


def _email_thread(
    conn,
    user_id: int,
    *,
    subject: str = "Speaking inquiry",
    last_direction: str = "out",
    last_message_at: datetime | None = datetime(2026, 7, 1, 9, 0),
    closed: bool = False,
    contact_id: int | None = None,
    deleted: bool = False,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_threads (user_id, contact_id, subject_normalized, last_direction, "
            " last_message_at, closed_at, deleted_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                user_id,
                contact_id,
                subject,
                last_direction,
                last_message_at,
                "2026-01-01 00:00:00" if closed else None,
                "2026-01-01 00:00:00" if deleted else None,
            ),
        )
        return cur.lastrowid


def _awaiting_reply(rows: list[dict]) -> set[str]:
    return {r["title"] for r in rows if r["reason"] == "awaiting_reply"}


def test_needs_attention_flags_an_unanswered_outbound_thread(seeded_db) -> None:
    """Acceptance #9: an open thread whose last message went out and has gone quiet.

    Threshold is 7 days (``core.periods.AWAITING_REPLY_AFTER_DAYS``) — shorter than the 14-day
    stale window on purpose, because a venue that has not answered in a week is the moment a nudge
    still reads as attentive rather than impatient.
    """
    conn, user_id, _, _ = seeded_db
    _email_thread(conn, user_id, subject="Waited", last_message_at=datetime(2026, 7, 10, 9, 0))
    _email_thread(conn, user_id, subject="Fresh", last_message_at=datetime(2026, 7, 19, 9, 0))

    rows = dashboard.needs_attention(conn, user_id, datetime(2026, 7, 20, 12, 0))
    assert _awaiting_reply(rows) == {"Waited"}


def test_a_thread_awaiting_donnas_own_reply_is_not_flagged(seeded_db) -> None:
    """``last_direction='in'`` means the ball is in *her* court, which is a different prompt this
    deliberately does not make — flagging it would tell her a venue owes her a reply when she owes
    them one."""
    conn, user_id, _, _ = seeded_db
    _email_thread(
        conn,
        user_id,
        subject="Their turn",
        last_direction="in",
        last_message_at=datetime(2026, 7, 1, 9, 0),
    )

    rows = dashboard.needs_attention(conn, user_id, datetime(2026, 7, 20, 12, 0))
    assert _awaiting_reply(rows) == set()


def test_a_closed_thread_raises_no_needs_attention(seeded_db) -> None:
    """The other half of acceptance #9 — closing is how a conversation stops nagging."""
    conn, user_id, _, _ = seeded_db
    _email_thread(
        conn, user_id, subject="Closed", last_message_at=datetime(2026, 7, 1, 9, 0), closed=True
    )

    rows = dashboard.needs_attention(conn, user_id, datetime(2026, 7, 20, 12, 0))
    assert _awaiting_reply(rows) == set()


def test_a_soft_deleted_thread_is_not_flagged(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    _email_thread(
        conn, user_id, subject="Deleted", last_message_at=datetime(2026, 7, 1, 9, 0), deleted=True
    )

    rows = dashboard.needs_attention(conn, user_id, datetime(2026, 7, 20, 12, 0))
    assert _awaiting_reply(rows) == set()


def test_a_thread_with_nothing_sent_yet_is_not_flagged(seeded_db) -> None:
    """A thread whose only message is an unconfirmed send has no ``last_message_at``; it has not
    gone unanswered, it has not gone out."""
    conn, user_id, _, _ = seeded_db
    _email_thread(conn, user_id, subject="Never sent", last_message_at=None)

    rows = dashboard.needs_attention(conn, user_id, datetime(2026, 7, 20, 12, 0))
    assert _awaiting_reply(rows) == set()


def test_an_awaiting_reply_row_carries_the_thread_id_so_the_spa_can_link_to_it(seeded_db) -> None:
    """The ``reason`` token is the only thing saying which id-space ``id`` is in. If this row
    carried anything but the thread id, the dashboard link would land on an unrelated gig."""
    conn, user_id, _, _ = seeded_db
    thread_id = _email_thread(conn, user_id, last_message_at=datetime(2026, 7, 1, 9, 0))

    rows = dashboard.needs_attention(conn, user_id, datetime(2026, 7, 20, 12, 0))
    row = next(r for r in rows if r["reason"] == "awaiting_reply")
    assert row["id"] == thread_id
    assert row["event_date"] is None


def test_a_subjectless_thread_gets_placeholder_link_text(seeded_db) -> None:
    """``title`` is rendered as the anchor, and ``subject_normalized`` is NOT NULL but may be empty
    — an empty subject would otherwise draw a blank, unclickable-looking link."""
    conn, user_id, _, _ = seeded_db
    _email_thread(conn, user_id, subject="", last_message_at=datetime(2026, 7, 1, 9, 0))

    rows = dashboard.needs_attention(conn, user_id, datetime(2026, 7, 20, 12, 0))
    assert _awaiting_reply(rows) == {"(no subject)"}


def test_another_users_threads_are_never_flagged(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('other', 'o@x.com')")
        other_id = cur.lastrowid
    _email_thread(conn, other_id, subject="Theirs", last_message_at=datetime(2026, 7, 1, 9, 0))

    rows = dashboard.needs_attention(conn, user_id, datetime(2026, 7, 20, 12, 0))
    assert _awaiting_reply(rows) == set()


# --- coming up -----------------------------------------------------------------------------------


def test_upcoming_events_lists_future_dated_open_gigs_soonest_first(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    org = _org(conn, user_id, "Venue")
    _opp(conn, user_id, org, title="Soon", event_date=date(2026, 7, 25))
    _opp(conn, user_id, org, title="Later", event_date=date(2026, 8, 10))
    _opp(conn, user_id, org, title="Past", event_date=date(2026, 7, 1))  # before now → excluded
    _opp(conn, user_id, org, title="Undated")  # no event_date → excluded
    closed = _opp(conn, user_id, org, title="Closed", event_date=date(2026, 7, 26))
    opp.close(conn, user_id, closed, "lost", "cold")  # closed → excluded
    rows = dashboard.upcoming_events(conn, user_id, datetime(2026, 7, 20, 12, 0))
    assert [r["title"] for r in rows] == ["Soon", "Later"]  # soonest first, future/open only


# --- composite -----------------------------------------------------------------------------------


def test_build_dashboard_returns_all_sections(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    payload = dashboard.build_dashboard(conn, user_id)
    assert set(payload) == {
        "targets",
        "funnel",
        "money",
        "needs_attention",
        "coming_up",
        "follow_ups",
    }
    assert len(payload["funnel"]) == 5  # all five funnel stages always present
