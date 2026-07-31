"""Raw-SQL aggregates for the composite dashboard (DEV-PLAN slice 5).

Everything the home screen shows, computed on the fly (DATABASE.md §4) and owner-scoped:

- **target actuals** — per set target, the current-period count. Windowed by ``core.periods`` in the
  user's timezone (the session ``time_zone`` is already the user's zone, and ``occurred_at`` is a
  UTC ``TIMESTAMP``, so local-naive window bounds compare on the right local day — acceptance #1).
  ``outreaches`` counts only ``counts_toward_target`` kinds (#4); ``venues_researched`` is a
  **current-state** count of research-ready orgs (readiness is a state, not a dated event);
  ``pitches`` / ``bookings`` count distinct gigs reaching that stage in the window.
- **funnel** — reached-or-beyond distinct-gig counts for outreach_sent → in_conversation → pitched →
  booked (#3), mirroring ``core.funnel.reached_or_beyond`` in SQL.
- **money** — Booked / Received / Outstanding over paid gigs; pro bono is excluded from the currency
  totals and reported as a separate count (#5).
- **needs-attention** — the single "what needs me?" panel: awaiting payment, past-event
  still-pre-Booked, research incomplete, an unanswered outbound thread, and **stale** (no status
  change or outreach in the stale window). Stale was its own card until 2026-07-30; see
  :func:`needs_attention` for why it was folded in.
- **follow-ups** — pending reminders due today or earlier (slice 7), from
  :mod:`repositories.follow_ups`.

Actuals and needs-attention take an injected ``now_local`` (the caller passes the DB's session-tz
``NOW()`` in production; tests pass a fixed value) so the period math stays deterministic.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pymysql.connections import Connection

from common.db import db_now_local
from core.periods import awaiting_reply_cutoff, period_bounds, stale_cutoff
from repositories.follow_ups import list_due

#: Money totals assume a single currency (the app default); Donna's gigs are all USD.
_CURRENCY = "USD"

#: The funnel ratio stages in order (DATABASE.md §"funnel ratio stages"), plus `delivered` which the
#: dashboard funnel card shows as its final row (the approved mockup renders 5 rows).
_FUNNEL_STAGES = ("outreach_sent", "in_conversation", "pitched", "booked", "delivered")

#: Research-ready predicate as SQL — mirrors ``core.research.is_research_ready`` (all three Kindling
#: fields filled AND ≥1 non-deleted affiliated contact).
_RESEARCH_READY = (
    "TRIM(COALESCE(o.what_it_is, '')) <> '' "
    "AND TRIM(COALESCE(o.why_it_fits, '')) <> '' "
    "AND TRIM(COALESCE(o.how_to_approach, '')) <> '' "
    "AND EXISTS (SELECT 1 FROM contact_organizations co "
    "            JOIN contacts c ON c.id = co.contact_id AND c.deleted_at IS NULL "
    "            WHERE co.organization_id = o.id)"
)


def _scalar(conn: Connection, sql: str, params: tuple) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return int(next(iter(row.values())))


def _actual_for(
    conn: Connection, user_id: int, target_type: str, start: datetime, end: datetime
) -> int:
    """Return the current-period actual for one target type."""
    if target_type == "outreaches":
        return _scalar(
            conn,
            "SELECT COUNT(*) FROM outreaches o "
            "JOIN outreach_kinds k ON k.id = o.outreach_kind_id "
            "WHERE o.user_id = %s AND o.deleted_at IS NULL AND k.counts_toward_target = TRUE "
            "AND o.occurred_at >= %s AND o.occurred_at < %s",
            (user_id, start, end),
        )
    if target_type == "venues_researched":
        # Current-state: how many orgs are research-ready now (not windowed).
        return _scalar(
            conn,
            "SELECT COUNT(*) FROM organizations o "
            "WHERE o.user_id = %s AND o.deleted_at IS NULL AND " + _RESEARCH_READY,
            (user_id,),
        )
    if target_type in ("pitches", "bookings"):
        status = "pitched" if target_type == "pitches" else "booked"
        return _scalar(
            conn,
            "SELECT COUNT(DISTINCT e.opportunity_id) FROM status_events e "
            "JOIN opportunity_statuses s ON s.id = e.status_id "
            "WHERE e.user_id = %s AND s.short_name = %s "
            "AND e.occurred_at >= %s AND e.occurred_at < %s",
            (user_id, status, start, end),
        )
    return 0


def target_actuals(conn: Connection, user_id: int, now_local: datetime) -> list[dict]:
    """Return an actual-vs-target tile per set target, using ``now_local`` for period windows."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tt.short_name AS target_type, t.cadence, t.goal_count "
            "FROM targets t JOIN target_types tt ON tt.id = t.target_type_id "
            "WHERE t.user_id = %s ORDER BY tt.sort_order, t.cadence",
            (user_id,),
        )
        targets = list(cur.fetchall())
    tiles = []
    for t in targets:
        start, end = period_bounds(t["cadence"], now_local)
        tiles.append(
            {
                "target_type": t["target_type"],
                "cadence": t["cadence"],
                "goal": t["goal_count"],
                "actual": _actual_for(conn, user_id, t["target_type"], start, end),
            }
        )
    return tiles


def funnel_counts(conn: Connection, user_id: int) -> list[dict]:
    """Return reached-or-beyond distinct-gig counts for the four funnel stages (all present)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT fs.short_name AS status, COUNT(DISTINCT reach.opportunity_id) AS count "
            "FROM opportunity_statuses fs "
            "LEFT JOIN ("
            "  SELECT e.opportunity_id, MAX(s.sort_order) AS max_sort "
            "  FROM status_events e JOIN opportunity_statuses s ON s.id = e.status_id "
            "  WHERE e.user_id = %s GROUP BY e.opportunity_id"
            ") reach ON reach.max_sort >= fs.sort_order "
            "WHERE fs.short_name IN %s AND fs.deleted_at IS NULL "
            "GROUP BY fs.short_name, fs.sort_order ORDER BY fs.sort_order",
            (user_id, _FUNNEL_STAGES),
        )
        return [{"status": r["status"], "count": int(r["count"])} for r in cur.fetchall()]


def money_rollup(conn: Connection, user_id: int) -> dict:
    """Return Booked / Received / Outstanding + pro-bono count (pro bono out of currency totals)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT "
            "  COALESCE(SUM(CASE WHEN ct.short_name = 'paid' "
            "    AND st.short_name IN ('booked', 'delivered') "
            "THEN o.fee_amount END), 0) AS booked, "
            "  COALESCE(SUM(CASE WHEN ct.short_name = 'paid' "
            "    AND pay.short_name = 'paid' THEN o.fee_amount END), 0) AS received, "
            "  SUM(CASE WHEN ct.short_name = 'paid' "
            "    AND st.short_name IN ('booked', 'delivered') THEN 1 ELSE 0 END) AS booked_count, "
            "  SUM(CASE WHEN ct.short_name = 'paid' AND pay.short_name = 'paid' "
            "    THEN 1 ELSE 0 END) AS received_count, "
            "  SUM(CASE WHEN ct.short_name = 'paid' AND pay.short_name = 'invoiced' "
            "    THEN 1 ELSE 0 END) AS invoiced_count, "
            "  SUM(CASE WHEN ct.short_name = 'pro_bono' "
            "    AND st.short_name IN ('booked', 'delivered') THEN 1 ELSE 0 END) AS pro_bono_count "
            "FROM opportunities o "
            "JOIN comp_types ct ON ct.id = o.comp_type_id "
            "JOIN opportunity_statuses st ON st.id = o.current_status_id "
            "JOIN payment_statuses pay ON pay.id = o.payment_status_id "
            "WHERE o.user_id = %s AND o.deleted_at IS NULL",
            (user_id,),
        )
        row = cur.fetchone()
    booked = Decimal(row["booked"])
    received = Decimal(row["received"])
    return {
        "currency": _CURRENCY,
        "booked": booked,
        "received": received,
        "outstanding": booked - received,
        "booked_count": int(row["booked_count"] or 0),
        "received_count": int(row["received_count"] or 0),
        "invoiced_count": int(row["invoiced_count"] or 0),
        "pro_bono_count": int(row["pro_bono_count"] or 0),
    }


#: Last dated activity on an opportunity — the most recent status change or outreach. Written as a
#: SQL expression rather than a column because it is derived: `opportunities` has no
#: `last_activity_at`, deliberately (it would be one more denormalized field to keep in step).
_LAST_ACTIVITY = (
    "GREATEST("
    "  COALESCE((SELECT MAX(occurred_at) FROM status_events "
    "            WHERE opportunity_id = o.id), '1970-01-01 00:00:00'), "
    "  COALESCE((SELECT MAX(occurred_at) FROM outreaches "
    "            WHERE opportunity_id = o.id AND deleted_at IS NULL), '1970-01-01 00:00:00')"
    ")"
)


def needs_attention(conn: Connection, user_id: int, now_local: datetime) -> list[dict]:
    """Return the rows the dashboard flags as wanting Donna's attention.

    Five reasons, across three different id-spaces — the ``reason`` token is what tells the SPA
    which one an ``id`` belongs to, so adding a reason means teaching it a new link target:

    - ``awaiting_payment`` (delivered gig, unsettled), ``overdue_unbooked`` (past-event gig still
      pre-Booked) and ``stale`` — shown as **"Gone quiet"**: a gig still being *pursued* with no
      status change or outreach in the stale window — are **opportunity**-scoped;
    - ``research_incomplete`` is **organization**-scoped (a venue that is not research-ready —
      missing a Kindling field or a contact), so its ``id`` is the org id;
    - ``awaiting_reply`` is **email-thread**-scoped (slice 6b acceptance #9): an open thread whose
      last message went out and has gone unanswered past
      :data:`core.periods.AWAITING_REPLY_AFTER_DAYS`.

    **``stale`` used to be its own dashboard card and is now a reason here** (2026-07-30). It was a
    beyond-mockup addition; the approved design has one such panel, and two of them asked the user
    the same question — "what needs me?" — while showing overlapping rows with no relationship
    between them. A delivered-but-unpaid gig with no recent activity appeared in *both*.

    That overlap is why the ``stale`` branch excludes anything another reason already covers: a row
    flagged for a specific reason is more actionable than the same row flagged for a vague one, and
    listing it twice is the redundancy that prompted the merge. It is a genuine
    least-specific-wins rule, not an optimization.

    The same conversation narrowed what ``stale`` *means*. It now covers only gigs **before
    Booked** — see the SQL comment — because a booked gig awaiting a distant event is quiet for
    good reason, and flagging it fortnightly was noise the old card also produced.

    ``since`` carries the date the condition started mattering, where there is one — last activity
    for ``stale``, last outbound message for ``awaiting_reply`` — so the SPA can say *how long*
    rather than just *what*. It is NULL for reasons whose urgency is not a duration.

    Opportunity rows carry an ``event_date`` and sort first; the dateless reasons follow. A richer
    tickler model with per-type timing thresholds is future work (its own table).
    """
    booked_sort = "(SELECT sort_order FROM opportunity_statuses WHERE short_name = 'booked')"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT o.id, o.title, org.name AS organization_name, "
            "       'awaiting_payment' AS reason, o.event_date, NULL AS since "
            "FROM opportunities o "
            "JOIN organizations org ON org.id = o.organization_id "
            "JOIN opportunity_statuses st ON st.id = o.current_status_id "
            "JOIN payment_statuses pay ON pay.id = o.payment_status_id "
            "WHERE o.user_id = %s AND o.deleted_at IS NULL "
            "  AND st.short_name = 'delivered' AND pay.is_settled = FALSE "
            "UNION ALL "
            "SELECT o.id, o.title, org.name, 'overdue_unbooked', o.event_date, NULL "
            "FROM opportunities o "
            "JOIN organizations org ON org.id = o.organization_id "
            "JOIN opportunity_statuses st ON st.id = o.current_status_id "
            "WHERE o.user_id = %s AND o.deleted_at IS NULL AND o.closed_at IS NULL "
            "  AND o.event_date IS NOT NULL AND o.event_date < %s "
            "  AND st.sort_order < " + booked_sort + " "
            "UNION ALL "
            "SELECT o.id, o.name AS title, o.name, 'research_incomplete', NULL, NULL "
            "FROM organizations o "
            "WHERE o.user_id = %s AND o.deleted_at IS NULL AND NOT (" + _RESEARCH_READY + ") "
            # An open thread whose last message went OUT and has gone unanswered. All three
            # conditions are load-bearing: a closed thread raises nothing (acceptance #9), and a
            # thread whose last message came IN is Donna's turn rather than the venue's.
            #
            # `last_message_at` is a TIMESTAMP, which MySQL converts to the session timezone on
            # comparison — the session tz is already the user's — so comparing it against a
            # local-naive cutoff is right, exactly as the stale branch below does.
            #
            # NULLIF guards the link text: `subject_normalized` is NOT NULL but may be empty, and
            # the SPA renders `title` as the anchor, so an empty subject would draw a blank link.
            "UNION ALL "
            "SELECT t.id, COALESCE(NULLIF(t.subject_normalized, ''), '(no subject)'), "
            "       COALESCE(c.name, ''), 'awaiting_reply', NULL, DATE(t.last_message_at) "
            "FROM email_threads t "
            "LEFT JOIN contacts c ON c.id = t.contact_id "
            "WHERE t.user_id = %s AND t.deleted_at IS NULL AND t.closed_at IS NULL "
            "  AND t.last_direction = 'out' AND t.last_message_at IS NOT NULL "
            "  AND t.last_message_at < %s "
            # Gone quiet — a gig still being *pursued* that nothing has happened to in a while.
            #
            # `sort_order < booked` is the load-bearing clause. Silence on a pursuit means the
            # pursuit has stalled and the gig will die without a nudge; silence on a *booked* gig
            # is just a calendar waiting, and flagging it every fortnight until the event was pure
            # noise. That was true of the standalone Stale card too, and is the reason this is
            # scoped rather than merely renamed.
            #
            # The event-date clause then de-duplicates against `overdue_unbooked`, which covers the
            # same pre-Booked gigs once their event date has passed — a gig already flagged for the
            # specific problem is not also reported as merely quiet.
            "UNION ALL "
            "SELECT o.id, o.title, org.name, 'stale', o.event_date, DATE(" + _LAST_ACTIVITY + ") "
            "FROM opportunities o "
            "JOIN organizations org ON org.id = o.organization_id "
            "JOIN opportunity_statuses st ON st.id = o.current_status_id "
            "WHERE o.user_id = %s AND o.deleted_at IS NULL AND o.closed_at IS NULL "
            "  AND st.sort_order < " + booked_sort + " "
            "  AND " + _LAST_ACTIVITY + " < %s "
            "  AND (o.event_date IS NULL OR o.event_date >= %s) "
            "ORDER BY event_date IS NULL, event_date ASC",
            (
                user_id,
                user_id,
                now_local.date(),
                user_id,
                user_id,
                awaiting_reply_cutoff(now_local),
                user_id,
                stale_cutoff(now_local),
                now_local.date(),
            ),
        )
        return list(cur.fetchall())


def due_follow_ups(conn: Connection, user_id: int, now_local: datetime) -> list[dict]:
    """Return pending follow-up reminders due today or earlier, most overdue first (slice 7).

    Delegates to :func:`repositories.follow_ups.list_due` rather than repeating the query, so the
    Dashboard card and the Follow-ups page cannot drift apart on what "due" means.

    ``now_local.date()`` is the user's local today — the same session-clock value every other panel
    here is windowed by, so a reminder set for Donna's Tuesday appears on Donna's Tuesday.
    """
    return list_due(conn, user_id, due_through=now_local.date())


def upcoming_events(conn: Connection, user_id: int, now_local: datetime) -> list[dict]:
    """Return active gigs with a today-or-future event date, soonest first (the "Coming up" card).

    Gigs only. Follow-up reminders get their **own** card via :func:`due_follow_ups` rather than
    being merged in here (settled with Brian 2026-07-30, revising the earlier note that promised
    this panel would absorb them): "Coming up" is future-facing, and an overdue reminder is the
    opposite — it has to get louder, not scroll off the top. Ad-hoc calendar items remain out of
    scope, having no data model at all.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT o.id, o.title, org.name AS organization_name, o.event_date, "
            "       st.short_name AS current_status "
            "FROM opportunities o "
            "JOIN organizations org ON org.id = o.organization_id "
            "JOIN opportunity_statuses st ON st.id = o.current_status_id "
            "WHERE o.user_id = %s AND o.deleted_at IS NULL AND o.closed_at IS NULL "
            "  AND o.event_date IS NOT NULL AND o.event_date >= %s "
            "ORDER BY o.event_date ASC LIMIT 6",
            (user_id, now_local.date()),
        )
        return list(cur.fetchall())


def build_dashboard(conn: Connection, user_id: int) -> dict:
    """Assemble the full dashboard payload, using the DB's session ``NOW()`` for period windows."""
    now_local = db_now_local(conn)
    return {
        "targets": target_actuals(conn, user_id, now_local),
        "funnel": funnel_counts(conn, user_id),
        "money": money_rollup(conn, user_id),
        "needs_attention": needs_attention(conn, user_id, now_local),
        "coming_up": upcoming_events(conn, user_id, now_local),
        "follow_ups": due_follow_ups(conn, user_id, now_local),
    }
