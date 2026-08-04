"""Dashboard repository tests against a seeded MySQL — the slice-5 aggregates.

Skip without ``TEST_DATABASE_URL`` (see conftest). Mechanize acceptance #1 (timezone bucketing),
#2 (venues_researched = current research-ready count), #3 (funnel reached-or-beyond), #4 (only
counts_toward_target kinds feed the outreach target), #5 (money excludes pro bono from currency
totals), and #6 (needs-attention rows).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from common.db import db_now_local
from core.periods import WEEKLY, period_bounds
from models.appointments import AppointmentInput
from models.contacts import AffiliationInput
from models.opportunities import OpportunityCreateInput
from models.outreach import OutreachInput
from models.targets import TargetInput
from repositories import appointments as appts_repo
from repositories import contacts as contacts_repo
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


def _appointment(conn, user_id: int, contact_id: int, when: datetime, title: str) -> int:
    return appts_repo.create_appointment(
        conn, user_id, AppointmentInput(contact_id=contact_id, title=title, scheduled_at=when)
    )


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


# --- #2: venues_researched counts venues that crossed the bar inside the window -------------------


def test_venues_researched_counts_only_venues_that_crossed_the_bar_in_the_window(seeded_db) -> None:
    """Slice 10 follow-up: a flow, not the all-time ready total.

    Goes through the real ``add_affiliation`` rather than the raw ``_affiliate`` helper, because
    stamping is part of that write path — inserting the row directly would leave the venue
    research-ready with no date and quietly measure nothing.
    """
    conn, user_id, _, _ = seeded_db
    ready = _org(conn, user_id, "Ready", kindling=True)
    contacts_repo.add_affiliation(
        conn, user_id, _contact(conn, user_id, "A"), AffiliationInput(organization_id=ready)
    )
    _org(conn, user_id, "NoKindling", kindling=False)  # missing Kindling → never stamped
    _org(conn, user_id, "NoContact", kindling=True)  # Kindling but no contact → never stamped
    targets_repo.upsert_target(
        conn, user_id, TargetInput(target_type="venues_researched", cadence="weekly", goal_count=10)
    )
    now = db_now_local(conn)

    def actual(at: datetime) -> int:
        tiles = dashboard.target_actuals(conn, user_id, at)
        return next(t for t in tiles if t["target_type"] == "venues_researched")["actual"]

    # The stamp is CURRENT_TIMESTAMP, so the crossing lands in this week.
    assert actual(now) == 1
    # And in no other week. This is the whole point of the change: the old query returned the
    # all-time ready count, so it answered 1 for April as readily as for today.
    assert actual(now - timedelta(days=35)) == 0


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
    assert {r["item_type"] for r in rows} == {"gig"}


def test_upcoming_events_interleaves_appointments_with_gigs(seeded_db) -> None:
    """One chronological list from two sources (slice 11), gigs first on a shared day."""
    conn, user_id, _, _ = seeded_db
    now = datetime(2026, 7, 20, 12, 0)
    org = _org(conn, user_id, "Venue")
    contact = _contact(conn, user_id, "Kalei")
    _opp(conn, user_id, org, title="Gig on the 25th", event_date=date(2026, 7, 25))
    _appointment(conn, user_id, contact, datetime(2026, 7, 22, 14, 0), "Coffee")
    # Same day as the gig: a gig has no hour, so midnight is the honest reading and it sorts first.
    _appointment(conn, user_id, contact, datetime(2026, 7, 25, 9, 0), "Same-day chat")

    rows = dashboard.upcoming_events(conn, user_id, now)

    assert [r["title"] for r in rows] == ["Coffee", "Gig on the 25th", "Same-day chat"]
    appointment = rows[0]
    assert appointment["item_type"] == "appointment"
    assert appointment["contact_name"] == "Kalei"
    assert appointment["organization_name"] is None
    assert (appointment["event_date"], appointment["event_time"]) == (
        date(2026, 7, 22),
        time(14, 0),
    )
    assert rows[1]["item_type"] == "gig"
    assert rows[1]["event_time"] is None and rows[1]["contact_name"] is None


def test_an_appointment_already_over_today_drops_off_but_a_gig_today_does_not(seeded_db) -> None:
    """The hour is what separates the two halves of this card — and the reason it is stored."""
    conn, user_id, _, _ = seeded_db
    now = datetime(2026, 7, 20, 12, 0)
    org = _org(conn, user_id, "Venue")
    contact = _contact(conn, user_id, "Kalei")
    _opp(conn, user_id, org, title="Gig today", event_date=date(2026, 7, 20))
    _appointment(conn, user_id, contact, datetime(2026, 7, 20, 9, 0), "This morning")
    _appointment(conn, user_id, contact, datetime(2026, 7, 20, 15, 0), "This afternoon")

    rows = dashboard.upcoming_events(conn, user_id, now)

    assert [r["title"] for r in rows] == ["Gig today", "This afternoon"]


# --- composite -----------------------------------------------------------------------------------


def test_build_dashboard_returns_all_sections(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    payload = dashboard.build_dashboard(conn, user_id)
    assert set(payload) == {
        "week",
        "targets",
        "funnel",
        "money",
        "needs_attention",
        "coming_up",
        "follow_ups",
    }
    assert len(payload["funnel"]) == 5  # all five funnel stages always present


# --- slice 10: the week the tiles report on ------------------------------------------------------


def _weekly_outreach_target(conn, user_id: int) -> None:
    targets_repo.upsert_target(
        conn, user_id, TargetInput(target_type="outreaches", cadence="weekly", goal_count=5)
    )


def _two_touches_in_consecutive_weeks(conn, user_id: int) -> None:
    """One counting touch in the week of Jul 12, one in the week of Jul 19."""
    contact = _contact(conn, user_id)
    out.create_outreach(  # inferred `initial` — counting
        conn,
        user_id,
        OutreachInput(contact_id=contact, channel="dm", occurred_at=datetime(2026, 7, 14, 9)),
    )
    out.create_outreach(  # `follow_up` — also counting
        conn,
        user_id,
        OutreachInput(
            contact_id=contact, channel="dm", kind="follow_up", occurred_at=datetime(2026, 7, 21, 9)
        ),
    )


def test_the_week_moves_the_tiles_to_that_week(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    _two_touches_in_consecutive_weeks(conn, user_id)
    _weekly_outreach_target(conn, user_id)

    payload = dashboard.build_dashboard(conn, user_id, week_of=date(2026, 7, 15))

    assert payload["week"] == {"start": date(2026, 7, 12), "end": date(2026, 7, 19)}
    tile = next(t for t in payload["targets"] if t["target_type"] == "outreaches")
    # Only the Jul 14 touch is in the anchored week; Jul 21 belongs to the next one.
    assert tile["actual"] == 1
    # The tile's drill-down bounds follow the anchor too, or the link would open a different week
    # from the number it sits under (acceptance #2).
    assert (tile["period_start"], tile["period_end"]) == (date(2026, 7, 12), date(2026, 7, 19))


def test_any_day_in_the_week_resolves_to_the_same_week(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    _two_touches_in_consecutive_weeks(conn, user_id)
    _weekly_outreach_target(conn, user_id)

    sunday = dashboard.build_dashboard(conn, user_id, week_of=date(2026, 7, 12))
    saturday = dashboard.build_dashboard(conn, user_id, week_of=date(2026, 7, 18))

    expected = {"start": date(2026, 7, 12), "end": date(2026, 7, 19)}
    assert sunday["week"] == saturday["week"] == expected
    assert sunday["targets"] == saturday["targets"]


def test_a_monthly_tile_follows_the_anchor_into_its_own_month(seeded_db) -> None:
    """A tile keeps its cadence and reports the period of that cadence containing the anchor."""
    conn, user_id, _, _ = seeded_db
    _two_touches_in_consecutive_weeks(conn, user_id)  # both in July
    targets_repo.upsert_target(
        conn, user_id, TargetInput(target_type="outreaches", cadence="monthly", goal_count=20)
    )

    payload = dashboard.build_dashboard(conn, user_id, week_of=date(2026, 7, 15))

    tile = next(t for t in payload["targets"] if t["cadence"] == "monthly")
    assert (tile["period_start"], tile["period_end"]) == (date(2026, 7, 1), date(2026, 8, 1))
    assert tile["actual"] == 2  # the whole month, not the anchored week


def test_only_the_tiles_move_with_the_week(seeded_db) -> None:
    """Acceptance #4 — sliding the week must not restate what is overdue or coming up as of then.

    Seeded so this genuinely discriminates: both rows below would change if the anchor were passed
    to ``needs_attention`` or ``upcoming_events`` instead of the real clock.
    """
    conn, user_id, _, _ = seeded_db
    now = db_now_local(conn)
    org = _org(conn, user_id, "Anchor Venue")
    # Already happened 3 days ago: not coming up now, but it would be under a two-week-old anchor.
    _opp(conn, user_id, org, title="Recent gig", event_date=(now - timedelta(days=3)).date())
    # Quiet for 16 days: stale against a now-14d cutoff, not against a now-28d one.
    quiet = _opp(conn, user_id, org, title="Quiet gig")
    _set_last_event(conn, quiet, now - timedelta(days=16))

    live = dashboard.build_dashboard(conn, user_id)
    slid = dashboard.build_dashboard(conn, user_id, week_of=(now - timedelta(days=14)).date())

    assert slid["week"] != live["week"]  # the anchor really moved, so the rest is not vacuous
    assert slid["coming_up"] == live["coming_up"]
    assert slid["needs_attention"] == live["needs_attention"]
    assert slid["follow_ups"] == live["follow_ups"]  # rides along; nothing seeded to discriminate
    assert slid["money"] == live["money"]
    assert slid["funnel"] == live["funnel"]

    # The equalities above are only worth asserting if the seed could break them. Prove it does:
    # fed the anchor directly, both sections return something different. If this ever stops being
    # true the seed has rotted and the guard above has quietly become vacuous.
    anchor = datetime.combine((now - timedelta(days=14)).date(), time.min)
    assert dashboard.upcoming_events(conn, user_id, anchor) != live["coming_up"]
    assert dashboard.needs_attention(conn, user_id, anchor) != live["needs_attention"]


def test_without_a_week_the_dashboard_reports_on_the_current_week(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    start, end = period_bounds(WEEKLY, db_now_local(conn))
    payload = dashboard.build_dashboard(conn, user_id)
    assert payload["week"] == {"start": start.date(), "end": end.date()}
