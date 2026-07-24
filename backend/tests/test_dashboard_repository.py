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
    assert tile == {"target_type": "outreaches", "cadence": "weekly", "goal": 5, "actual": 1}


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
    counts = {r["status"]: r["count"] for r in dashboard.funnel_counts(conn, user_id)}
    # All five stages present; a gig that jumped to Pitched still counts toward Outreach Sent (#3).
    # `delivered` is the display-only final row (neither gig reached it).
    assert counts == {
        "outreach_sent": 2,
        "in_conversation": 2,
        "pitched": 2,
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


# --- stale (14d) ---------------------------------------------------------------------------------


def test_stale_lists_only_inactive_open_gigs(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    org = _org(conn, user_id, "Venue")
    old = _opp(conn, user_id, org, title="Old")
    _set_last_event(conn, old, datetime(2026, 7, 1, 10, 0))
    recent = _opp(conn, user_id, org, title="Recent")
    _set_last_event(conn, recent, datetime(2026, 7, 19, 10, 0))
    closed = _opp(conn, user_id, org, title="Closed")
    _set_last_event(conn, closed, datetime(2026, 7, 1, 10, 0))
    opp.close(conn, user_id, closed, "lost", "went cold")
    now = datetime(2026, 7, 20, 12, 0)  # cutoff = 07-06
    stale = dashboard.stale_opportunities(conn, user_id, now)
    titles = [s["title"] for s in stale]
    assert titles == ["Old"]  # Recent is within 14d; Closed is excluded


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
        "stale",
        "needs_attention",
        "coming_up",
    }
    assert len(payload["funnel"]) == 5  # all five funnel stages always present
