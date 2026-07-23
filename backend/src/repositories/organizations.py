"""Raw-SQL persistence for organizations and their affiliated-contact reads.

Every query is scoped to ``user_id`` and ignores soft-deleted rows (``deleted_at IS NULL``).
Contact counts and affiliated-contact lists join through ``contact_organizations`` but count only
**non-deleted** contacts, so a soft-deleted person neither inflates a count nor appears anywhere.
Integrity violations map to domain errors, never a 500: a duplicate live name → ``Conflict`` (the
``UNIQUE(user_id, name_key)`` guard), a bad ``organization_type_id`` → ``InvalidInput``.
"""

from __future__ import annotations

from typing import NoReturn

from pymysql.connections import Connection
from pymysql.err import IntegrityError

from common import errors
from models.organizations import OrganizationInput

#: MySQL error codes we translate to domain errors.
_ER_DUP_ENTRY = 1062  # UNIQUE violation — a live org already has this name
_ER_NO_REFERENCED_ROW = 1452  # FK constraint fails — unknown organization_type_id

#: Writable columns, in the order create/update bind them.
_WRITABLE = (
    "organization_type_id",
    "name",
    "location",
    "website_url",
    "email_domain",
    "what_it_is",
    "why_it_fits",
    "how_to_approach",
    "notes",
)


def _values(data: OrganizationInput) -> tuple:
    """Return the writable field values in ``_WRITABLE`` order."""
    return tuple(getattr(data, column) for column in _WRITABLE)


def _raise_for_integrity(exc: IntegrityError) -> NoReturn:
    """Translate a known MySQL integrity error to a domain error, else re-raise."""
    code = exc.args[0]
    if code == _ER_DUP_ENTRY:
        raise errors.Conflict("an organization with this name already exists") from exc
    if code == _ER_NO_REFERENCED_ROW:
        raise errors.InvalidInput("unknown organization_type_id") from exc
    raise exc


def list_organizations(conn: Connection, user_id: int) -> list[dict]:
    """Return the caller's organizations with their non-deleted contact counts, name-ordered.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.

    Returns
    -------
    list of dict
        One row per organization with the three Kindling fields and ``contact_count`` — enough
        for the caller to build the list summary and compute research-readiness.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT o.id, o.organization_type_id, o.name, o.location, "
            "       o.what_it_is, o.why_it_fits, o.how_to_approach, o.created_at, o.updated_at, "
            "       COUNT(c.id) AS contact_count "
            "FROM organizations o "
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
        The organization row (all writable fields, ids, timestamps, ``contact_count``), or None
        when it does not exist, is soft-deleted, or belongs to another user.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT o.id, o.organization_type_id, o.name, o.location, o.website_url, "
            "       o.email_domain, o.what_it_is, o.why_it_fits, o.how_to_approach, o.notes, "
            "       o.created_at, o.updated_at, COUNT(c.id) AS contact_count "
            "FROM organizations o "
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
        Each with ``contact_id``, ``name``, ``title`` (role at this org), ``is_primary``,
        ``is_power_partner``.
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
        The validated writable fields.

    Returns
    -------
    int
        The new organization's id.

    Raises
    ------
    common.errors.Conflict
        When a live organization already has this name for the user.
    common.errors.InvalidInput
        When ``organization_type_id`` does not reference an existing catalog row.
    """
    placeholders = ", ".join(["%s"] * (len(_WRITABLE) + 1))
    with conn.cursor() as cur:
        try:
            cur.execute(
                f"INSERT INTO organizations (user_id, {', '.join(_WRITABLE)}) "
                f"VALUES ({placeholders})",
                (user_id, *_values(data)),
            )
        except IntegrityError as exc:
            _raise_for_integrity(exc)
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
        The validated replacement fields.

    Returns
    -------
    bool
        True if the organization existed and was updated; False if absent (caller maps to 404).
        Existence is checked separately so an unchanged-value update is not mistaken for absent.

    Raises
    ------
    common.errors.Conflict
        When the new name collides with another live organization of the user.
    common.errors.InvalidInput
        When ``organization_type_id`` does not reference an existing catalog row.
    """
    assignments = ", ".join(f"{column} = %s" for column in _WRITABLE)
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
                (*_values(data), org_id, user_id),
            )
        except IntegrityError as exc:
            _raise_for_integrity(exc)
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
        affiliations are left intact — reads hide them via the organization's ``deleted_at``.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE organizations SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (org_id, user_id),
        )
        return cur.rowcount > 0
