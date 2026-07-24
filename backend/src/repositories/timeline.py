"""Raw-SQL assembly of the unified contact timeline (DATABASE.md §4, DEV-PLAN slice 4 #5).

A contact's timeline is computed at read time — there is no backing table — as a ``UNION ALL`` over
three journals, ordered by ``occurred_at`` descending:

- **outreaches** logged directly against the contact (outbound touches);
- **opportunity_notes** on any opportunity the contact is linked to (via ``opportunity_contacts``);
- **status_events** on any opportunity the contact is linked to (the gig's lifecycle history).

Notes and status events hang off *opportunities*, not contacts, so they reach the contact page
through the ``opportunity_contacts`` link — which is what lets ``outreaches`` stay outbound-only
without the contact losing sight of the gig history it belongs to. ``email_messages`` joins this
union in the email slice (0008). Every branch is owner-scoped, so an unknown or foreign contact
yields an empty list. The three branches emit one shared column set projecting
:class:`models.timeline.TimelineItem`; a branch fills only its own type's fields and NULLs the rest.
"""

from __future__ import annotations

from pymysql.connections import Connection

#: The contact's own opportunities, by the many-to-many link. Notes and status events on these
#: opportunities surface on the contact timeline. ``opportunity_contacts`` is hard-deleted, so there
#: is no ``deleted_at`` to filter.
_CONTACT_OPPS = "SELECT oc.opportunity_id FROM opportunity_contacts oc WHERE oc.contact_id = %s"

#: The unified timeline query. Column names come from the first SELECT's aliases (DictCursor keys);
#: the later branches must keep the same column order. Ordered newest-first, ``source_id`` breaking
#: ties for entries sharing a timestamp.
_TIMELINE_SQL = (
    "SELECT 'outreach' AS item_type, o.id AS source_id, o.occurred_at AS occurred_at, "
    "       o.note AS text, o.opportunity_id AS opportunity_id, opp.title AS opportunity_title, "
    "       ch.short_name AS channel, k.short_name AS kind, NULL AS status "
    "FROM outreaches o "
    "JOIN outreach_channels ch ON ch.id = o.outreach_channel_id "
    "JOIN outreach_kinds k ON k.id = o.outreach_kind_id "
    "LEFT JOIN opportunities opp ON opp.id = o.opportunity_id "
    "WHERE o.user_id = %s AND o.contact_id = %s AND o.deleted_at IS NULL "
    "UNION ALL "
    "SELECT 'note', n.id, n.occurred_at, n.body, n.opportunity_id, opp.title, "
    "       NULL, NULL, NULL "
    "FROM opportunity_notes n "
    "JOIN opportunities opp ON opp.id = n.opportunity_id AND opp.deleted_at IS NULL "
    "WHERE n.user_id = %s AND n.deleted_at IS NULL "
    "  AND n.opportunity_id IN (" + _CONTACT_OPPS + ") "
    "UNION ALL "
    "SELECT 'status_event', e.id, e.occurred_at, e.note, e.opportunity_id, opp.title, "
    "       NULL, NULL, st.short_name "
    "FROM status_events e "
    "JOIN opportunities opp ON opp.id = e.opportunity_id AND opp.deleted_at IS NULL "
    "JOIN opportunity_statuses st ON st.id = e.status_id "
    "WHERE e.user_id = %s "
    "  AND e.opportunity_id IN (" + _CONTACT_OPPS + ") "
    "ORDER BY occurred_at DESC, source_id DESC"
)


def contact_timeline(conn: Connection, user_id: int, contact_id: int) -> list[dict]:
    """Return a contact's unified timeline, newest first, owner-scoped.

    Interleaves the contact's outreaches with the notes and status events of every opportunity the
    contact is linked to. Owner-scoped on every branch, so a foreign or unknown ``contact_id``
    yields an empty list rather than leaking another user's history.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    contact_id : int
        The contact whose timeline to assemble.

    Returns
    -------
    list of dict
        Rows shaped for :class:`models.timeline.TimelineItem`, ordered by ``occurred_at`` descending
        (``source_id`` breaks ties). Empty when the contact has no history or is not the caller's.
    """
    with conn.cursor() as cur:
        cur.execute(
            _TIMELINE_SQL,
            (user_id, contact_id, user_id, contact_id, user_id, contact_id),
        )
        return list(cur.fetchall())
