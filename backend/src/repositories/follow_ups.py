"""Raw-SQL persistence for follow-up reminders.

A follow-up is a future, actionable reminder against a contact, an opportunity, or both
(``0010_followups.sql``, DEV-PLAN slice 7). Both links are individually optional but never both
absent — the ``ck_follow_ups_target`` CHECK is the real guarantee and ``models.follow_ups`` rejects
the empty case as a 400 before the insert is attempted. Rows are soft-deleted; every read filters
``deleted_at IS NULL``.

``completed_at IS NULL`` **is** the pending state; there is no ``status`` column (DATABASE.md,
overriding DESIGN.md §4), so "outstanding" is that one predicate everywhere in this module.

This module owns persistence only. It does **not** talk to EventBridge: the handler is the
composition root that reads a row, asks ``core.follow_ups`` what schedule that row should have, and
calls ``common.scheduler``. Two consequences worth stating, because they shape the signatures here:

- **The reminder recipient is not read from this layer.** It is ``users.email``, which the handler
  already holds as the JWT's email claim (``handlers/context.py``) — fresher than the column, since
  ``repositories/users.py`` deliberately never refreshes ``email`` on the upsert's conflict branch.
- **Edits need the row before *and* after.** ``core.follow_ups.reconcile`` compares two desired
  schedule states, so a handler patching a row reads it, patches, and reads again inside one
  transaction. :func:`patch_follow_up` therefore reports only whether it matched, and the caller
  re-reads — rather than this module inventing a half-row return shape.
"""

from __future__ import annotations

from datetime import date

from pymysql.connections import Connection

from models.follow_ups import FollowUpInput, FollowUpPatch
from repositories._ownership import validate_contact, validate_opportunity

#: Response columns for a follow-up. Both parents are **LEFT** joined: either link may be absent,
#: and joining either one inner would silently drop half the table. Neither join filters
#: ``deleted_at``, so a reminder still renders its contact's name after that contact is
#: soft-deleted — the same choice ``outreaches._SUMMARY_SELECT`` makes for a touch's contact.
_SUMMARY_SELECT = (
    "SELECT f.id, f.due_date, f.note, f.contact_id, c.name AS contact_name, "
    "       f.opportunity_id, o.title AS opportunity_title, f.remind_by_email, "
    "       f.completed_at, f.reminder_failed_at, f.created_at "
    "FROM follow_ups f "
    "LEFT JOIN contacts c ON c.id = f.contact_id "
    "LEFT JOIN opportunities o ON o.id = f.opportunity_id "
)

#: Ordering shared by every list read: soonest first, id breaking ties for a shared date.
_ORDER = "ORDER BY f.due_date ASC, f.id ASC"


def create_follow_up(conn: Connection, user_id: int, data: FollowUpInput) -> int:
    """Insert a follow-up and return its new id.

    Validates that whichever links were supplied are the caller's own live rows. Both guards accept
    ``None``, which is exactly this table's shape — a gig-level reminder names no contact and a
    person-level one names no gig.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    data : models.follow_ups.FollowUpInput
        The validated writable fields. Its model validator has already rejected the both-links-
        absent case, so the CHECK is not expected to fire here.

    Returns
    -------
    int
        The new follow-up's id — the value the deterministic schedule name ``followup-<id>`` is
        built from, so the caller needs it before it can create a schedule.

    Raises
    ------
    common.errors.InvalidInput
        When the contact or opportunity is not the caller's.
    """
    validate_contact(conn, user_id, data.contact_id)
    validate_opportunity(conn, user_id, data.opportunity_id)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO follow_ups "
            "(user_id, contact_id, opportunity_id, due_date, note, remind_by_email) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                user_id,
                data.contact_id,
                data.opportunity_id,
                data.due_date,
                data.note,
                data.remind_by_email,
            ),
        )
        return cur.lastrowid


def get_follow_up(conn: Connection, user_id: int, follow_up_id: int) -> dict | None:
    """Return one follow-up owned by ``user_id`` as a summary row, or None if absent/deleted."""
    with conn.cursor() as cur:
        cur.execute(
            _SUMMARY_SELECT + "WHERE f.id = %s AND f.user_id = %s AND f.deleted_at IS NULL",
            (follow_up_id, user_id),
        )
        return cur.fetchone()


def list_follow_ups(
    conn: Connection,
    user_id: int,
    *,
    contact_id: int | None = None,
    opportunity_id: int | None = None,
    organization_id: int | None = None,
    pending_only: bool = False,
) -> list[dict]:
    """Return the caller's follow-ups, soonest first, optionally narrowed.

    Owner-scoped, so a foreign or unknown ``contact_id`` / ``opportunity_id`` yields an empty list
    rather than leaking another account's rows. The two link filters are ANDed when both are given,
    which is what a reminder attached to a specific person *on* a specific gig needs.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    contact_id : int or None, optional
        Restrict to reminders linked to this contact.
    opportunity_id : int or None, optional
        Restrict to reminders linked to this opportunity.
    organization_id : int or None, optional
        Restrict to reminders that concern this venue — attached to **any gig there** or to **any
        contact affiliated with it**. ``follow_ups`` has no ``organization_id`` of its own (a
        reminder is about a person or a gig, never a building), so this reaches the venue through
        those two links rather than by a column.
    pending_only : bool, optional
        When ``True``, exclude completed rows (``completed_at IS NOT NULL``). The Follow-ups page
        shows completed history by default; the detail-page panels do not.

    Returns
    -------
    list of dict
        Summary rows ordered by ``due_date`` then ``id``.
    """
    clauses = ["f.user_id = %s", "f.deleted_at IS NULL"]
    params: list[object] = [user_id]
    if contact_id is not None:
        clauses.append("f.contact_id = %s")
        params.append(contact_id)
    if opportunity_id is not None:
        clauses.append("f.opportunity_id = %s")
        params.append(opportunity_id)
    if organization_id is not None:
        # OR, not AND: a venue's reminders are the union of its gigs' and its people's. Deleted
        # opportunities are excluded, but the affiliation subquery deliberately is not filtered on
        # the contact's own deleted_at — a reminder about a since-removed person still concerns
        # this venue, and the row itself is what decides whether it is live.
        clauses.append(
            "(f.opportunity_id IN (SELECT id FROM opportunities "
            "                      WHERE organization_id = %s AND deleted_at IS NULL) "
            " OR f.contact_id IN (SELECT contact_id FROM contact_organizations "
            "                     WHERE organization_id = %s))"
        )
        params.extend([organization_id, organization_id])
    if pending_only:
        clauses.append("f.completed_at IS NULL")
    with conn.cursor() as cur:
        cur.execute(_SUMMARY_SELECT + "WHERE " + " AND ".join(clauses) + " " + _ORDER, params)
        return list(cur.fetchall())


def list_due(conn: Connection, user_id: int, *, due_through: date) -> list[dict]:
    """Return pending follow-ups due on or before ``due_through``, soonest first.

    The Dashboard card's query. ``due_through`` is the caller's local *today*, so the card shows
    what is due today **plus anything overdue** — a reminder that came due last week and was never
    actioned must get louder rather than scroll off a future-facing list. Marking one done drops it
    from this result immediately, which is acceptance #4.

    Hits ``ix_follow_ups_user_due (user_id, due_date, completed_at)`` in the order the index is
    declared: equality on the user, a range on the date, then the pending flag.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    due_through : date
        Inclusive upper bound — the user's local today, derived from the session clock by
        ``repositories.dashboard``, never from the Lambda's UTC wall clock.

    Returns
    -------
    list of dict
        Summary rows ordered by ``due_date`` then ``id``, oldest (most overdue) first.
    """
    with conn.cursor() as cur:
        cur.execute(
            _SUMMARY_SELECT + "WHERE f.user_id = %s AND f.deleted_at IS NULL "
            "AND f.completed_at IS NULL AND f.due_date <= %s " + _ORDER,
            (user_id, due_through),
        )
        return list(cur.fetchall())


def patch_follow_up(conn: Connection, user_id: int, follow_up_id: int, data: FollowUpPatch) -> bool:
    """Apply a partial edit; return whether a live row owned by ``user_id`` was matched.

    Only the fields the caller set are written — ``None`` means unchanged, and no patchable column
    is nullable, so nothing here can be cleared by accident. ``completed`` is translated to the
    timestamp: ``True`` stamps ``completed_at`` with the session clock, ``False`` clears it and
    reopens the follow-up.

    A patch that sets nothing is a no-op returning ``True`` when the row exists, so a redundant
    request is not mistaken for a missing row (the handler maps ``False`` to 404).

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    follow_up_id : int
        The follow-up to edit.
    data : models.follow_ups.FollowUpPatch
        The fields to change.

    Returns
    -------
    bool
        ``True`` when a live, owned row matched — **not** whether any column changed. MySQL reports
        ``rowcount`` 0 for an UPDATE that sets a column to the value it already holds, so
        ``rowcount`` cannot distinguish "no such row" from "no change"; this checks existence
        separately and lets the caller re-read for the new state.
    """
    if get_follow_up(conn, user_id, follow_up_id) is None:
        return False

    assignments: list[str] = []
    params: list[object] = []
    if data.due_date is not None:
        assignments.append("due_date = %s")
        params.append(data.due_date)
    if data.note is not None:
        assignments.append("note = %s")
        params.append(data.note)
    if data.remind_by_email is not None:
        assignments.append("remind_by_email = %s")
        params.append(data.remind_by_email)
    if data.completed is not None:
        assignments.append(
            "completed_at = CURRENT_TIMESTAMP" if data.completed else "completed_at = NULL"
        )
    if not assignments:
        return True

    # A *real* edit clears any recorded reminder failure — but an empty patch does not, since it
    # changes nothing and the flag still describes the current schedule accurately. The flag
    # describes the last attempt, and an edit means a new schedule is about to be put, so keeping
    # it would leave a follow-up that failed on Monday, was rescheduled, and sent fine on Friday
    # still showing as failed forever.
    assignments.append("reminder_failed_at = NULL")

    params.extend([follow_up_id, user_id])
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE follow_ups SET " + ", ".join(assignments) + " "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            params,
        )
    return True


def mark_reminder_failed(conn: Connection, follow_up_id: int) -> bool:
    """Record that this follow-up's reminder email was dead-lettered; return whether a row matched.

    **Not owner-scoped, unlike every other write here, and deliberately so.** There is no requesting
    user on this path: it runs from the dead-letter consumer, driven by a payload the app itself
    minted, so the only id it can carry is one we put there. Requiring a ``user_id`` would mean
    either inventing one or threading it through the schedule payload for no benefit.

    Idempotent — re-stamping an already-failed row is a no-op in effect, which matters because SQS
    delivers at least once and the same dead-lettered reminder may arrive twice.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    follow_up_id : int
        The ``follow_ups.id`` from the dead-lettered payload.

    Returns
    -------
    bool
        ``True`` when a live row matched. ``False`` means the follow-up was deleted between the
        reminder failing and this running — not an error, just nothing left to annotate.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE follow_ups SET reminder_failed_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND deleted_at IS NULL",
            (follow_up_id,),
        )
        return cur.rowcount > 0


def soft_delete_follow_up(conn: Connection, user_id: int, follow_up_id: int) -> bool:
    """Soft-delete a follow-up; return whether a live row owned by ``user_id`` was deleted.

    The caller cancels the schedule afterwards (acceptance #3). Cancelling one that has already
    fired is harmless, so a delete never needs to know whether the reminder went out.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE follow_ups SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (follow_up_id, user_id),
        )
        return cur.rowcount > 0
