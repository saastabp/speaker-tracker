"""Raw-SQL persistence for organizations and their affiliated-contact reads.

The public API references the `organization_types` catalog by **short_name**, matching `/catalogs`
(ids are never exposed). This module is the translation seam: writes resolve `organization_type`
(short_name) → the numeric FK stored in `organization_type_id`; reads join back to the short_name.
Every query is scoped to `user_id` and ignores soft-deleted rows. A duplicate live name →
`Conflict` (the `UNIQUE(user_id, name_key)` guard); an unknown short_name → `InvalidInput`.
"""

from __future__ import annotations

from pymysql.connections import Connection
from pymysql.err import IntegrityError

from common import errors
from models.organizations import OrganizationInput

#: UNIQUE violation — a live org already has this name.
_ER_DUP_ENTRY = 1062

#: Writable columns that map 1:1 from the input (organization_type is resolved separately).
_PLAIN_COLUMNS = (
    "name",
    "location",
    "website_url",
    "email_domain",
    "what_it_is",
    "why_it_fits",
    "how_to_approach",
    "notes",
)


def _resolve_type_id(conn: Connection, short_name: str) -> int:
    """Resolve an `organization_types` short_name to its id, or raise InvalidInput."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM organization_types WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.InvalidInput("unknown organization_type")
    return row["id"]


def _plain_values(data: OrganizationInput) -> tuple:
    """Return the plain (non-catalog) writable values in `_PLAIN_COLUMNS` order."""
    return tuple(getattr(data, column) for column in _PLAIN_COLUMNS)


def list_organizations(conn: Connection, user_id: int) -> list[dict]:
    """Return the caller's organizations with non-deleted contact counts, name-ordered.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.

    Returns
    -------
    list of dict
        One row per organization with `organization_type` (short_name), the three Kindling
        fields, and `contact_count` — enough to build the list summary and compute readiness.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT o.id, ot.short_name AS organization_type, o.name, o.location, "
            "       o.what_it_is, o.why_it_fits, o.how_to_approach, o.created_at, o.updated_at, "
            "       COUNT(c.id) AS contact_count "
            "FROM organizations o "
            "JOIN organization_types ot ON ot.id = o.organization_type_id "
            "LEFT JOIN contact_organizations co ON co.organization_id = o.id "
            "LEFT JOIN contacts c ON c.id = co.contact_id AND c.deleted_at IS NULL "
            "WHERE o.user_id = %s AND o.deleted_at IS NULL "
            "GROUP BY o.id ORDER BY o.name",
            (user_id,),
        )
        return list(cur.fetchall())


def get_organization(conn: Connection, user_id: int, org_id: int) -> dict | None:
    """Return one organization with its non-deleted contact count, or None if absent.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    org_id : int
        The organization id.

    Returns
    -------
    dict or None
        The organization row (writable fields with `organization_type` short_name, ids,
        timestamps, `contact_count`), or None when it does not exist for this user.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT o.id, ot.short_name AS organization_type, o.name, o.location, o.website_url, "
            "       o.email_domain, o.what_it_is, o.why_it_fits, o.how_to_approach, o.notes, "
            "       o.created_at, o.updated_at, COUNT(c.id) AS contact_count "
            "FROM organizations o "
            "JOIN organization_types ot ON ot.id = o.organization_type_id "
            "LEFT JOIN contact_organizations co ON co.organization_id = o.id "
            "LEFT JOIN contacts c ON c.id = co.contact_id AND c.deleted_at IS NULL "
            "WHERE o.user_id = %s AND o.id = %s AND o.deleted_at IS NULL "
            "GROUP BY o.id",
            (user_id, org_id),
        )
        return cur.fetchone()


def get_affiliated_contacts(conn: Connection, user_id: int, org_id: int) -> list[dict]:
    """Return the non-deleted contacts affiliated with an organization, primary first.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user (enforces ownership through the organization).
    org_id : int
        The organization id.

    Returns
    -------
    list of dict
        Each with `contact_id`, `name`, `title`, `is_primary`, `is_power_partner`.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.id AS contact_id, c.name, co.title, co.is_primary, c.is_power_partner "
            "FROM contact_organizations co "
            "JOIN contacts c ON c.id = co.contact_id AND c.deleted_at IS NULL "
            "JOIN organizations o ON o.id = co.organization_id "
            "  AND o.user_id = %s AND o.deleted_at IS NULL "
            "WHERE co.organization_id = %s "
            "ORDER BY co.is_primary DESC, c.name",
            (user_id, org_id),
        )
        return list(cur.fetchall())


def create_organization(conn: Connection, user_id: int, data: OrganizationInput) -> int:
    """Insert an organization and return its new id.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    data : models.organizations.OrganizationInput
        The validated writable fields (`organization_type` is a short_name).

    Returns
    -------
    int
        The new organization's id.

    Raises
    ------
    common.errors.Conflict
        When a live organization already has this name for the user.
    common.errors.InvalidInput
        When `organization_type` is not a known catalog short_name.
    """
    type_id = _resolve_type_id(conn, data.organization_type)
    columns = ("user_id", "organization_type_id", *_PLAIN_COLUMNS)
    placeholders = ", ".join(["%s"] * len(columns))
    with conn.cursor() as cur:
        try:
            cur.execute(
                f"INSERT INTO organizations ({', '.join(columns)}) VALUES ({placeholders})",
                (user_id, type_id, *_plain_values(data)),
            )
        except IntegrityError as exc:
            if exc.args[0] == _ER_DUP_ENTRY:
                raise errors.Conflict("an organization with this name already exists") from exc
            raise
        return cur.lastrowid


def update_organization(
    conn: Connection, user_id: int, org_id: int, data: OrganizationInput
) -> bool:
    """Full-replace an organization's writable fields; return whether it existed.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    org_id : int
        The organization id.
    data : models.organizations.OrganizationInput
        The validated replacement fields (`organization_type` is a short_name).

    Returns
    -------
    bool
        True if the organization existed and was updated; False if absent (caller maps to 404).

    Raises
    ------
    common.errors.Conflict
        When the new name collides with another live organization of the user.
    common.errors.InvalidInput
        When `organization_type` is not a known catalog short_name.
    """
    type_id = _resolve_type_id(conn, data.organization_type)
    columns = ("organization_type_id", *_PLAIN_COLUMNS)
    assignments = ", ".join(f"{column} = %s" for column in columns)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM organizations WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (org_id, user_id),
        )
        if cur.fetchone() is None:
            return False
        try:
            cur.execute(
                f"UPDATE organizations SET {assignments} WHERE id = %s AND user_id = %s",
                (type_id, *_plain_values(data), org_id, user_id),
            )
        except IntegrityError as exc:
            if exc.args[0] == _ER_DUP_ENTRY:
                raise errors.Conflict("an organization with this name already exists") from exc
            raise
    return True


def soft_delete_organization(conn: Connection, user_id: int, org_id: int) -> bool:
    """Soft-delete an organization; return whether a live row was deleted.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    org_id : int
        The organization id.

    Returns
    -------
    bool
        True if a non-deleted organization was marked deleted; False otherwise. Existing
        affiliations are left intact — reads hide them via the organization's `deleted_at`.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE organizations SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (org_id, user_id),
        )
        return cur.rowcount > 0
