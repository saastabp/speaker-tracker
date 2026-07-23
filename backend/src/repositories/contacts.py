"""Raw-SQL persistence for contacts and their organization affiliations.

Contacts are user-scoped and soft-deleted (``deleted_at IS NULL``); they have no uniqueness
constraint — the add-contact dedupe is a *search* (``list_contacts(query=...)``), because one
person legitimately spans venues and email may be absent. Affiliation writes go through
``contact_organizations`` and verify that **both** the contact and the organization belong to the
caller; a duplicate affiliation (the ``UNIQUE(contact_id, organization_id)`` guard) → ``Conflict``.
"""

from __future__ import annotations

from pymysql.connections import Connection
from pymysql.err import IntegrityError

from common import errors
from models.contacts import AffiliationInput, AffiliationUpdate, ContactInput

#: MySQL error codes we translate to domain errors.
_ER_DUP_ENTRY = 1062  # UNIQUE violation — affiliation already exists
_ER_NO_REFERENCED_ROW = 1452  # FK constraint fails — unknown warmth_tier_id

#: Writable contact columns, in the order create/update bind them.
_WRITABLE = (
    "name",
    "email",
    "phone",
    "warmth_tier_id",
    "is_power_partner",
    "source",
    "how_you_know",
    "notes",
)


def _values(data: ContactInput) -> tuple:
    """Return the writable field values in ``_WRITABLE`` order."""
    return tuple(getattr(data, column) for column in _WRITABLE)


def list_contacts(conn: Connection, user_id: int, query: str | None = None) -> list[dict]:
    """Return the caller's contacts with their live-org counts; optionally filtered by ``query``.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    query : str or None
        When given, a case-insensitive substring matched against name or email — this is the
        add-contact dedupe search. When None, returns all live contacts.

    Returns
    -------
    list of dict
        One row per contact with ``organization_count``, name-ordered.
    """
    sql = (
        "SELECT c.id, c.name, c.email, c.warmth_tier_id, c.is_power_partner, "
        "       c.created_at, c.updated_at, COUNT(DISTINCT o.id) AS organization_count "
        "FROM contacts c "
        "LEFT JOIN contact_organizations co ON co.contact_id = c.id "
        "LEFT JOIN organizations o ON o.id = co.organization_id AND o.deleted_at IS NULL "
        "WHERE c.user_id = %s AND c.deleted_at IS NULL "
    )
    params: list = [user_id]
    if query:
        sql += "AND (c.name LIKE %s OR c.email LIKE %s) "
        like = f"%{query}%"
        params += [like, like]
    sql += "GROUP BY c.id ORDER BY c.name"
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
        The contact row (writable fields, id, timestamps), or None.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, email, phone, warmth_tier_id, is_power_partner, source, "
            "       how_you_know, notes, created_at, updated_at "
            "FROM contacts WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
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
        Each with ``organization_id``, ``organization_name``, ``title``, ``is_primary``.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT o.id AS organization_id, o.name AS organization_name, co.title, co.is_primary "
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
        The validated writable fields.

    Returns
    -------
    int
        The new contact's id.

    Raises
    ------
    common.errors.InvalidInput
        When ``warmth_tier_id`` does not reference an existing catalog row.
    """
    placeholders = ", ".join(["%s"] * (len(_WRITABLE) + 1))
    with conn.cursor() as cur:
        try:
            cur.execute(
                f"INSERT INTO contacts (user_id, {', '.join(_WRITABLE)}) VALUES ({placeholders})",
                (user_id, *_values(data)),
            )
        except IntegrityError as exc:
            if exc.args[0] == _ER_NO_REFERENCED_ROW:
                raise errors.InvalidInput("unknown warmth_tier_id") from exc
            raise
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
        The validated replacement fields.

    Returns
    -------
    bool
        True if the contact existed and was updated; False if absent. Existence is checked
        separately so an unchanged-value update is not mistaken for absent.

    Raises
    ------
    common.errors.InvalidInput
        When ``warmth_tier_id`` does not reference an existing catalog row.
    """
    assignments = ", ".join(f"{column} = %s" for column in _WRITABLE)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM contacts WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (contact_id, user_id),
        )
        if cur.fetchone() is None:
            return False
        try:
            cur.execute(
                f"UPDATE contacts SET {assignments} WHERE id = %s AND user_id = %s",
                (*_values(data), contact_id, user_id),
            )
        except IntegrityError as exc:
            if exc.args[0] == _ER_NO_REFERENCED_ROW:
                raise errors.InvalidInput("unknown warmth_tier_id") from exc
            raise
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
        True if a non-deleted contact was marked deleted; False otherwise. Affiliation rows are
        left intact — reads hide them via the contact's ``deleted_at``.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE contacts SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (contact_id, user_id),
        )
        return cur.rowcount > 0


def add_affiliation(
    conn: Connection, user_id: int, contact_id: int, data: AffiliationInput
) -> None:
    """Affiliate a contact with an organization; both must belong to the caller.

    The insert is guarded by ``EXISTS`` checks on the owning contact and organization, so a
    missing/foreign id inserts nothing rather than creating a cross-user link.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    contact_id : int
        The contact to affiliate.
    data : models.contacts.AffiliationInput
        The target organization plus role/primary flag.

    Raises
    ------
    common.errors.NotFound
        When the contact or organization does not exist for this user.
    common.errors.Conflict
        When the affiliation already exists (``UNIQUE(contact_id, organization_id)``).
    """
    with conn.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO contact_organizations "
                "(contact_id, organization_id, title, is_primary) "
                "SELECT %s, %s, %s, %s FROM DUAL "
                "WHERE EXISTS (SELECT 1 FROM contacts "
                "              WHERE id = %s AND user_id = %s AND deleted_at IS NULL) "
                "  AND EXISTS (SELECT 1 FROM organizations "
                "              WHERE id = %s AND user_id = %s AND deleted_at IS NULL)",
                (
                    contact_id,
                    data.organization_id,
                    data.title,
                    data.is_primary,
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


def update_affiliation(
    conn: Connection, user_id: int, contact_id: int, org_id: int, data: AffiliationUpdate
) -> bool:
    """Update an affiliation's role/primary flag; return whether it existed (and was owned).

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    contact_id, org_id : int
        The affiliation's contact and organization.
    data : models.contacts.AffiliationUpdate
        The new role/primary values.

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
            "UPDATE contact_organizations SET title = %s, is_primary = %s "
            "WHERE contact_id = %s AND organization_id = %s",
            (data.title, data.is_primary, contact_id, org_id),
        )
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
