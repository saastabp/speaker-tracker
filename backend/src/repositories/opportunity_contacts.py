"""Raw-SQL persistence for the opportunity↔contact join (the people on a gig).

Each row links a contact to an opportunity with their per-gig role (`contact_role`, a
`contact_roles` short_name resolved to its FK) and an `is_primary` flag meaning "lead on this gig"
— unrelated to `contact_organizations.is_primary` (the default contact for a venue). Writes are
guarded by `EXISTS` checks so a link is only ever created between the user's own opportunity and
contact; the row is hard-deleted when the person is unlinked. At most one lead per gig: setting a
new lead demotes any other (the gig-scoped analogue of the single-primary-per-venue invariant).
"""

from __future__ import annotations

from pymysql.connections import Connection
from pymysql.cursors import Cursor
from pymysql.err import IntegrityError

from common import errors
from models.opportunities import OpportunityContactInput, OpportunityContactUpdate

#: UNIQUE violation — this contact is already linked to this opportunity.
_ER_DUP_ENTRY = 1062


def _resolve_role_id(conn: Connection, short_name: str | None) -> int | None:
    """Resolve a `contact_roles` short_name to its id (None passes through), or InvalidInput."""
    if short_name is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM contact_roles WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.InvalidInput("unknown contact_role")
    return row["id"]


def _demote_other_leads(cur: Cursor, opp_id: int, keep_contact_id: int) -> None:
    """Clear `is_primary` on every *other* contact on an opportunity (single-lead invariant).

    Runs on the caller's open cursor so it shares the surrounding write transaction. Scoping by
    `opportunity_id` alone is safe: every link on a user's opportunity is one of that user's
    contacts (both sides are ownership-checked at write time).
    """
    cur.execute(
        "UPDATE opportunity_contacts SET is_primary = FALSE "
        "WHERE opportunity_id = %s AND contact_id <> %s AND is_primary",
        (opp_id, keep_contact_id),
    )


def add_contact(conn: Connection, user_id: int, opp_id: int, data: OpportunityContactInput) -> None:
    """Link a contact to an opportunity; both must belong to the caller.

    The insert is guarded by `EXISTS` checks on the owning opportunity and contact, so a
    missing/foreign id inserts nothing rather than creating a cross-user link. If `is_primary` is
    set, any other lead on that opportunity is demoted (one lead per gig).

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    opp_id : int
        The opportunity id.
    data : models.opportunities.OpportunityContactInput
        The contact to link plus the per-gig role fields (contact_role short_name, is_primary).

    Raises
    ------
    common.errors.NotFound
        When the opportunity or contact does not exist for this user.
    common.errors.Conflict
        When the contact is already linked (`UNIQUE(opportunity_id, contact_id)`).
    common.errors.InvalidInput
        When `contact_role` is not a known catalog short_name.
    """
    role_id = _resolve_role_id(conn, data.contact_role)
    with conn.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO opportunity_contacts "
                "(opportunity_id, contact_id, contact_role_id, is_primary) "
                "SELECT %s, %s, %s, %s FROM DUAL "
                "WHERE EXISTS (SELECT 1 FROM opportunities "
                "              WHERE id = %s AND user_id = %s AND deleted_at IS NULL) "
                "  AND EXISTS (SELECT 1 FROM contacts "
                "              WHERE id = %s AND user_id = %s AND deleted_at IS NULL)",
                (
                    opp_id,
                    data.contact_id,
                    role_id,
                    data.is_primary,
                    opp_id,
                    user_id,
                    data.contact_id,
                    user_id,
                ),
            )
        except IntegrityError as exc:
            if exc.args[0] == _ER_DUP_ENTRY:
                raise errors.Conflict("contact is already on this opportunity") from exc
            raise
        if cur.rowcount == 0:
            raise errors.NotFound("opportunity or contact not found")
        if data.is_primary:
            _demote_other_leads(cur, opp_id, data.contact_id)


def update_contact(
    conn: Connection,
    user_id: int,
    opp_id: int,
    contact_id: int,
    data: OpportunityContactUpdate,
) -> bool:
    """Update a linked contact's per-gig role fields; return whether the link existed.

    Setting `is_primary` demotes any other lead on that opportunity (one lead per gig).

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    opp_id : int
        The opportunity id.
    contact_id : int
        The linked contact.
    data : models.opportunities.OpportunityContactUpdate
        The new per-gig role values (contact_role short_name, is_primary).

    Returns
    -------
    bool
        True if the link existed for this user and was updated; False otherwise.

    Raises
    ------
    common.errors.InvalidInput
        When `contact_role` is not a known catalog short_name.
    """
    role_id = _resolve_role_id(conn, data.contact_role)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM opportunity_contacts oc "
            "JOIN opportunities o ON o.id = oc.opportunity_id "
            "  AND o.user_id = %s AND o.deleted_at IS NULL "
            "WHERE oc.opportunity_id = %s AND oc.contact_id = %s",
            (user_id, opp_id, contact_id),
        )
        if cur.fetchone() is None:
            return False
        cur.execute(
            "UPDATE opportunity_contacts SET contact_role_id = %s, is_primary = %s "
            "WHERE opportunity_id = %s AND contact_id = %s",
            (role_id, data.is_primary, opp_id, contact_id),
        )
        if data.is_primary:
            _demote_other_leads(cur, opp_id, contact_id)
    return True


def remove_contact(conn: Connection, user_id: int, opp_id: int, contact_id: int) -> bool:
    """Unlink a contact from an opportunity (hard delete); return whether a link was removed.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    opp_id : int
        The opportunity id.
    contact_id : int
        The linked contact.

    Returns
    -------
    bool
        True if a link owned by this user was removed; False otherwise. The join is scoped through
        the opportunity's ownership so a foreign opportunity id deletes nothing.
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE oc FROM opportunity_contacts oc "
            "JOIN opportunities o ON o.id = oc.opportunity_id "
            "  AND o.user_id = %s AND o.deleted_at IS NULL "
            "WHERE oc.opportunity_id = %s AND oc.contact_id = %s",
            (user_id, opp_id, contact_id),
        )
        return cur.rowcount > 0
