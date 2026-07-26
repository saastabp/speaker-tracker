"""Shared owner-scoping guards used by more than one repository.

Every write that references another entity must prove the reference belongs to the calling user —
otherwise an id guessed from another account would silently attach a row to someone else's data.
The checks are identical wherever they appear, so they live here once rather than being copied per
repository: a change to what "visible" means (soft-delete semantics, shared reference rows) then
happens in one place instead of drifting between call sites.

Each guard raises :class:`common.errors.InvalidInput` rather than ``NotFound``: from the caller's
point of view the *request* is bad — it named an entity that is not theirs — and the response must
not distinguish "does not exist" from "exists but is not yours", which would confirm the existence
of another account's row.
"""

from __future__ import annotations

from pymysql.connections import Connection

from common import errors


def validate_contact(conn: Connection, user_id: int, contact_id: int | None) -> None:
    """Raise InvalidInput unless ``contact_id`` is a live contact owned by ``user_id``.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    contact_id : int or None
        Contact to check; ``None`` passes, for callers where the link is optional.

    Raises
    ------
    common.errors.InvalidInput
        When the contact does not exist, is soft-deleted, or belongs to another user.
    """
    if contact_id is None:
        return
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM contacts WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (contact_id, user_id),
        )
        if cur.fetchone() is None:
            raise errors.InvalidInput("unknown contact")


def validate_opportunity(conn: Connection, user_id: int, opportunity_id: int | None) -> None:
    """Raise InvalidInput unless ``opportunity_id`` is a live opportunity owned by ``user_id``.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    opportunity_id : int or None
        Opportunity to check; ``None`` passes, since attribution to a gig is always optional.

    Raises
    ------
    common.errors.InvalidInput
        When the opportunity does not exist, is soft-deleted, or belongs to another user.
    """
    if opportunity_id is None:
        return
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM opportunities WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (opportunity_id, user_id),
        )
        if cur.fetchone() is None:
            raise errors.InvalidInput("unknown opportunity")


def validate_message_template(conn: Connection, user_id: int, template_id: int | None) -> None:
    """Raise InvalidInput unless ``template_id`` is a template visible to ``user_id``.

    Visible means the caller's own template **or** a shared reference row (``user_id IS NULL``) —
    the seeded starter templates every user can compose from.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    template_id : int or None
        Template to check; ``None`` passes, since composing without a template is normal.

    Raises
    ------
    common.errors.InvalidInput
        When the template does not exist, is soft-deleted, or is another user's.
    """
    if template_id is None:
        return
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM message_templates "
            "WHERE id = %s AND (user_id = %s OR user_id IS NULL) AND deleted_at IS NULL",
            (template_id, user_id),
        )
        if cur.fetchone() is None:
            raise errors.InvalidInput("unknown message_template")


def has_prior_outbound_touch(conn: Connection, user_id: int, contact_id: int) -> bool:
    """Return whether a non-deleted outreach to this contact already exists.

    Feeds ``core.outreach.resolve_outreach_kind``'s contact-scoped inference — ``initial`` for the
    first touch, ``correspondence`` after — for both manually logged touches and emailed ones, so
    the two paths infer identically.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    contact_id : int
        The contact being touched.

    Returns
    -------
    bool
        ``True`` when a prior live outreach to this contact exists.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM outreaches "
            "WHERE user_id = %s AND contact_id = %s AND deleted_at IS NULL LIMIT 1",
            (user_id, contact_id),
        )
        return cur.fetchone() is not None
