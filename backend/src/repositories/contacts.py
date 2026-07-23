"""Raw-SQL persistence for contacts and their organization affiliations.

The public API references the `warmth_tiers` catalog by **short_name** (ids are never exposed);
writes resolve `warmth_tier` → the numeric `warmth_tier_id`, reads join back to the short_name.
Contacts are user-scoped and soft-deleted; they have no uniqueness constraint — the add-contact
dedupe is a *search* (`list_contacts(query=...)`). Affiliation writes verify that **both** the
contact and the organization belong to the caller; a duplicate affiliation → `Conflict`.

Single-primary-per-venue is an application invariant (there is no DB constraint): setting an
affiliation `is_primary` demotes any other primary contact at that organization, so a venue has at
most one primary contact. It is enforced here on every write, so it holds whether the primary is
set from the contact side or the venue side.
"""

from __future__ import annotations

from pymysql.connections import Connection
from pymysql.cursors import Cursor
from pymysql.err import IntegrityError

from common import errors
from models.contacts import AffiliationInput, AffiliationUpdate, ContactInput

#: UNIQUE violation — affiliation already exists.
_ER_DUP_ENTRY = 1062

#: Writable contact columns that map 1:1 from the input (warmth_tier is resolved separately).
_PLAIN_COLUMNS = (
    "name",
    "email",
    "phone",
    "source",
    "how_you_know",
    "notes",
)


def _resolve_warmth_id(conn: Connection, short_name: str | None) -> int | None:
    """Resolve a `warmth_tiers` short_name to its id; None stays None; unknown → InvalidInput."""
    if short_name is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM warmth_tiers WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.InvalidInput("unknown warmth_tier")
    return row["id"]


def _plain_values(data: ContactInput) -> tuple:
    """Return the plain (non-catalog) writable values in `_PLAIN_COLUMNS` order."""
    return tuple(getattr(data, column) for column in _PLAIN_COLUMNS)


def list_contacts(conn: Connection, user_id: int, query: str | None = None) -> list[dict]:
    """Return the caller's contacts with their live-org counts; optionally filtered by `query`.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    query : str or None
        When given, a case-insensitive substring matched against name or email (the dedupe
        search). When None, returns all live contacts.

    Returns
    -------
    list of dict
        One row per contact with `warmth_tier` (short_name or None), `organization_count`, and
        `is_power_partner` (rolled up — true when a power partner at any live affiliated venue).
    """
    sql = (
        "SELECT c.id, c.name, c.email, wt.short_name AS warmth_tier, "
        "       COALESCE(MAX(CASE WHEN o.id IS NOT NULL THEN co.is_power_partner ELSE 0 END), 0) "
        "         AS is_power_partner, "
        "       c.created_at, c.updated_at, COUNT(DISTINCT o.id) AS organization_count "
        "FROM contacts c "
        "LEFT JOIN warmth_tiers wt ON wt.id = c.warmth_tier_id "
        "LEFT JOIN contact_organizations co ON co.contact_id = c.id "
        "LEFT JOIN organizations o ON o.id = co.organization_id AND o.deleted_at IS NULL "
        "WHERE c.user_id = %s AND c.deleted_at IS NULL "
    )
    params: list = [user_id]
    if query:
        sql += "AND (c.name LIKE %s OR c.email LIKE %s) "
        like = f"%{query}%"
        params += [like, like]
    sql += "GROUP BY c.id, wt.short_name ORDER BY c.name"
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def get_contact(conn: Connection, user_id: int, contact_id: int) -> dict | None:
    """Return one contact's writable fields, or None if absent/soft-deleted/not owned.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    contact_id : int
        The contact id.

    Returns
    -------
    dict or None
        The contact row (writable fields with `warmth_tier` short_name, id, timestamps), or None.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.id, c.name, c.email, c.phone, wt.short_name AS warmth_tier, "
            "       c.source, c.how_you_know, c.notes, "
            "       c.created_at, c.updated_at "
            "FROM contacts c "
            "LEFT JOIN warmth_tiers wt ON wt.id = c.warmth_tier_id "
            "WHERE c.id = %s AND c.user_id = %s AND c.deleted_at IS NULL",
            (contact_id, user_id),
        )
        return cur.fetchone()


def get_affiliations(conn: Connection, user_id: int, contact_id: int) -> list[dict]:
    """Return the live organizations a contact is affiliated with, primary first.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user (enforces ownership through the contact).
    contact_id : int
        The contact id.

    Returns
    -------
    list of dict
        Each with `organization_id`, `organization_name`, `title`, `is_primary`,
        `is_power_partner`.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT o.id AS organization_id, o.name AS organization_name, co.title, "
            "       co.is_primary, co.is_power_partner "
            "FROM contact_organizations co "
            "JOIN organizations o ON o.id = co.organization_id AND o.deleted_at IS NULL "
            "JOIN contacts c ON c.id = co.contact_id AND c.user_id = %s AND c.deleted_at IS NULL "
            "WHERE co.contact_id = %s "
            "ORDER BY co.is_primary DESC, o.name",
            (user_id, contact_id),
        )
        return list(cur.fetchall())


def create_contact(conn: Connection, user_id: int, data: ContactInput) -> int:
    """Insert a contact and return its new id.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    data : models.contacts.ContactInput
        The validated writable fields (`warmth_tier` is a short_name or None).

    Returns
    -------
    int
        The new contact's id.

    Raises
    ------
    common.errors.InvalidInput
        When `warmth_tier` is not a known catalog short_name.
    """
    warmth_id = _resolve_warmth_id(conn, data.warmth_tier)
    columns = ("user_id", "warmth_tier_id", *_PLAIN_COLUMNS)
    placeholders = ", ".join(["%s"] * len(columns))
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO contacts ({', '.join(columns)}) VALUES ({placeholders})",
            (user_id, warmth_id, *_plain_values(data)),
        )
        return cur.lastrowid


def update_contact(conn: Connection, user_id: int, contact_id: int, data: ContactInput) -> bool:
    """Full-replace a contact's writable fields; return whether it existed.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    contact_id : int
        The contact id.
    data : models.contacts.ContactInput
        The validated replacement fields (`warmth_tier` is a short_name or None).

    Returns
    -------
    bool
        True if the contact existed and was updated; False if absent.

    Raises
    ------
    common.errors.InvalidInput
        When `warmth_tier` is not a known catalog short_name.
    """
    warmth_id = _resolve_warmth_id(conn, data.warmth_tier)
    columns = ("warmth_tier_id", *_PLAIN_COLUMNS)
    assignments = ", ".join(f"{column} = %s" for column in columns)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM contacts WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (contact_id, user_id),
        )
        if cur.fetchone() is None:
            return False
        cur.execute(
            f"UPDATE contacts SET {assignments} WHERE id = %s AND user_id = %s",
            (warmth_id, *_plain_values(data), contact_id, user_id),
        )
    return True


def soft_delete_contact(conn: Connection, user_id: int, contact_id: int) -> bool:
    """Soft-delete a contact; return whether a live row was deleted.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    contact_id : int
        The contact id.

    Returns
    -------
    bool
        True if a non-deleted contact was marked deleted; False otherwise.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE contacts SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (contact_id, user_id),
        )
        return cur.rowcount > 0


def _demote_other_primaries(cur: Cursor, org_id: int, keep_contact_id: int) -> None:
    """Clear `is_primary` on every *other* contact at an organization (single-primary invariant).

    Runs on the caller's open cursor so it shares the surrounding write transaction. Scoping by
    `organization_id` alone is safe: every affiliation of a user's org is one of that user's
    contacts (both sides are ownership-checked at write time).
    """
    cur.execute(
        "UPDATE contact_organizations SET is_primary = FALSE "
        "WHERE organization_id = %s AND contact_id <> %s AND is_primary",
        (org_id, keep_contact_id),
    )


def add_affiliation(
    conn: Connection, user_id: int, contact_id: int, data: AffiliationInput
) -> None:
    """Affiliate a contact with an organization; both must belong to the caller.

    The insert is guarded by `EXISTS` checks on the owning contact and organization, so a
    missing/foreign id inserts nothing rather than creating a cross-user link. If `is_primary` is
    set, any other primary contact at that organization is demoted (one primary per venue).

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    contact_id : int
        The contact to affiliate.
    data : models.contacts.AffiliationInput
        The target organization plus the per-venue role fields (title, primary, power-partner).

    Raises
    ------
    common.errors.NotFound
        When the contact or organization does not exist for this user.
    common.errors.Conflict
        When the affiliation already exists (`UNIQUE(contact_id, organization_id)`).
    """
    with conn.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO contact_organizations "
                "(contact_id, organization_id, title, is_primary, is_power_partner) "
                "SELECT %s, %s, %s, %s, %s FROM DUAL "
                "WHERE EXISTS (SELECT 1 FROM contacts "
                "              WHERE id = %s AND user_id = %s AND deleted_at IS NULL) "
                "  AND EXISTS (SELECT 1 FROM organizations "
                "              WHERE id = %s AND user_id = %s AND deleted_at IS NULL)",
                (
                    contact_id,
                    data.organization_id,
                    data.title,
                    data.is_primary,
                    data.is_power_partner,
                    contact_id,
                    user_id,
                    data.organization_id,
                    user_id,
                ),
            )
        except IntegrityError as exc:
            if exc.args[0] == _ER_DUP_ENTRY:
                raise errors.Conflict(
                    "contact is already affiliated with this organization"
                ) from exc
            raise
        if cur.rowcount == 0:
            raise errors.NotFound("contact or organization not found")
        if data.is_primary:
            _demote_other_primaries(cur, data.organization_id, contact_id)


def update_affiliation(
    conn: Connection, user_id: int, contact_id: int, org_id: int, data: AffiliationUpdate
) -> bool:
    """Update an affiliation's per-venue role fields; return whether it existed (and was owned).

    Setting `is_primary` demotes any other primary contact at that organization (one per venue).

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    contact_id, org_id : int
        The affiliation's contact and organization.
    data : models.contacts.AffiliationUpdate
        The new per-venue role values (title, primary, power-partner).

    Returns
    -------
    bool
        True if the affiliation existed for this user and was updated; False otherwise.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM contact_organizations co "
            "JOIN contacts c ON c.id = co.contact_id AND c.user_id = %s AND c.deleted_at IS NULL "
            "JOIN organizations o ON o.id = co.organization_id "
            "  AND o.user_id = %s AND o.deleted_at IS NULL "
            "WHERE co.contact_id = %s AND co.organization_id = %s",
            (user_id, user_id, contact_id, org_id),
        )
        if cur.fetchone() is None:
            return False
        cur.execute(
            "UPDATE contact_organizations "
            "SET title = %s, is_primary = %s, is_power_partner = %s "
            "WHERE contact_id = %s AND organization_id = %s",
            (data.title, data.is_primary, data.is_power_partner, contact_id, org_id),
        )
        if data.is_primary:
            _demote_other_primaries(cur, org_id, contact_id)
    return True


def remove_affiliation(conn: Connection, user_id: int, contact_id: int, org_id: int) -> bool:
    """Delete an affiliation (hard delete); return whether one was removed.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    contact_id, org_id : int
        The affiliation's contact and organization.

    Returns
    -------
    bool
        True if an owned affiliation was deleted; False otherwise.
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE co FROM contact_organizations co "
            "JOIN contacts c ON c.id = co.contact_id AND c.user_id = %s AND c.deleted_at IS NULL "
            "JOIN organizations o ON o.id = co.organization_id "
            "  AND o.user_id = %s AND o.deleted_at IS NULL "
            "WHERE co.contact_id = %s AND co.organization_id = %s",
            (user_id, user_id, contact_id, org_id),
        )
        return cur.rowcount > 0
