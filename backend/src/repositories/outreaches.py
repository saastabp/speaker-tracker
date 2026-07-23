"""Raw-SQL persistence for the outbound outreach journal.

A touch is logged against a contact (required) with an optional opportunity attribution, decoupled
from pipeline stage (DATABASE.md §"outreaches"). Writes resolve the ``channel`` / ``kind`` catalog
short_names to their FK ids and validate the contact / opportunity / template references are the
caller's (or, for a template, a shared row); reads join the ids back to short_names (Option A).

The ``kind`` is inferred here when the caller omits it: ``core.outreach`` decides ``initial`` vs
``correspondence`` from whether a prior outbound touch to the contact exists, and an explicit kind
overrides that (DEV-PLAN slice 4 acceptance #1). Inference is **contact-scoped** — the optional
opportunity is a display/filter axis, never part of the inference or a separate metric. Rows are
soft-deleted; reads filter ``deleted_at IS NULL``. This module owns writes and the contact-scoped
reads; the unified contact timeline is assembled in :mod:`repositories.timeline`.
"""

from __future__ import annotations

from pymysql.connections import Connection

from common import errors
from core.outreach import resolve_outreach_kind
from models.outreach import OutreachInput

#: Response columns for an outreach, catalogs joined back to short_names. ``contacts`` is joined
#: without a ``deleted_at`` filter so a touch still resolves its contact's name after the contact is
#: soft-deleted (mirrors how opportunity reads keep a retired venue's name).
_SUMMARY_SELECT = (
    "SELECT o.id, o.contact_id, c.name AS contact_name, o.opportunity_id, "
    "       ch.short_name AS channel, k.short_name AS kind, o.message_template_id, "
    "       o.note, o.occurred_at, o.created_at "
    "FROM outreaches o "
    "JOIN contacts c ON c.id = o.contact_id "
    "JOIN outreach_channels ch ON ch.id = o.outreach_channel_id "
    "JOIN outreach_kinds k ON k.id = o.outreach_kind_id "
)


def _resolve_channel_id(conn: Connection, short_name: str) -> int:
    """Resolve an ``outreach_channels`` short_name to its id, or raise InvalidInput."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM outreach_channels WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.InvalidInput("unknown channel")
    return row["id"]


def _resolve_kind_id(conn: Connection, short_name: str) -> int:
    """Resolve an ``outreach_kinds`` short_name to its id, or raise InvalidInput.

    Also validates a caller-supplied ``kind`` override: an unknown short_name is rejected here
    rather than silently stored.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM outreach_kinds WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.InvalidInput("unknown outreach kind")
    return row["id"]


def _validate_contact(conn: Connection, user_id: int, contact_id: int) -> None:
    """Raise InvalidInput unless ``contact_id`` is a live contact owned by ``user_id``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM contacts WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (contact_id, user_id),
        )
        if cur.fetchone() is None:
            raise errors.InvalidInput("unknown contact")


def _validate_opportunity(conn: Connection, user_id: int, opportunity_id: int | None) -> None:
    """Raise InvalidInput if ``opportunity_id`` is set but not a live opportunity of ``user_id``."""
    if opportunity_id is None:
        return
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM opportunities WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (opportunity_id, user_id),
        )
        if cur.fetchone() is None:
            raise errors.InvalidInput("unknown opportunity")


def _validate_message_template(conn: Connection, user_id: int, template_id: int | None) -> None:
    """Raise InvalidInput if ``template_id`` is given but not visible to ``user_id``.

    Visible means the caller's own template or a shared reference row (``user_id IS NULL``).
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


def _has_prior_outbound_touch(conn: Connection, user_id: int, contact_id: int) -> bool:
    """Return whether a non-deleted outreach to this contact already exists (for kind inference)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM outreaches "
            "WHERE user_id = %s AND contact_id = %s AND deleted_at IS NULL LIMIT 1",
            (user_id, contact_id),
        )
        return cur.fetchone() is not None


def create_outreach(conn: Connection, user_id: int, data: OutreachInput) -> int:
    """Insert an outbound touch and return its new id.

    Validates the contact / opportunity / template references, resolves the ``channel`` short_name,
    and determines the ``kind``: the caller's override when supplied, otherwise inferred from
    whether a prior outbound touch to the contact exists (``initial`` first, ``correspondence``
    after — acceptance #1). ``occurred_at`` uses the supplied value or ``CURRENT_TIMESTAMP`` when
    omitted. Logging a touch never changes pipeline stage (#6): no ``status_events`` row is written.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    data : models.outreach.OutreachInput
        The validated writable fields (contact/opportunity/template as ids, channel/kind as
        short_names, ``kind`` optional).

    Returns
    -------
    int
        The new outreach's id.

    Raises
    ------
    common.errors.InvalidInput
        When the contact, opportunity, or template is not the caller's (or a shared template), or a
        ``channel`` / ``kind`` short_name is unknown.
    """
    _validate_contact(conn, user_id, data.contact_id)
    _validate_opportunity(conn, user_id, data.opportunity_id)
    _validate_message_template(conn, user_id, data.message_template_id)
    channel_id = _resolve_channel_id(conn, data.channel)
    has_prior = _has_prior_outbound_touch(conn, user_id, data.contact_id)
    kind_id = _resolve_kind_id(conn, resolve_outreach_kind(has_prior, data.kind))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO outreaches "
            "(user_id, contact_id, opportunity_id, outreach_kind_id, outreach_channel_id, "
            " message_template_id, note, occurred_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP))",
            (
                user_id,
                data.contact_id,
                data.opportunity_id,
                kind_id,
                channel_id,
                data.message_template_id,
                data.note,
                data.occurred_at,
            ),
        )
        return cur.lastrowid


def get_outreach(conn: Connection, user_id: int, outreach_id: int) -> dict | None:
    """Return one outreach owned by ``user_id`` as a summary row, or None if absent/deleted."""
    with conn.cursor() as cur:
        cur.execute(
            _SUMMARY_SELECT + "WHERE o.id = %s AND o.user_id = %s AND o.deleted_at IS NULL",
            (outreach_id, user_id),
        )
        return cur.fetchone()


def list_outreaches_for_contact(conn: Connection, user_id: int, contact_id: int) -> list[dict]:
    """Return a contact's outreaches, newest first, owner-scoped.

    Ordered by ``occurred_at`` descending (``id`` breaks ties for touches sharing a timestamp).
    Owner-scoped, so a foreign or unknown ``contact_id`` yields an empty list rather than leaking.
    """
    with conn.cursor() as cur:
        cur.execute(
            _SUMMARY_SELECT + "WHERE o.user_id = %s AND o.contact_id = %s AND o.deleted_at IS NULL "
            "ORDER BY o.occurred_at DESC, o.id DESC",
            (user_id, contact_id),
        )
        return list(cur.fetchall())


def soft_delete_outreach(conn: Connection, user_id: int, outreach_id: int) -> bool:
    """Soft-delete an outreach; return whether a live row owned by ``user_id`` was deleted.

    The journal is append-only for *history*, but a mis-logged touch can be retracted — the
    ``deleted_at`` filter on every read (and the timeline union) then drops it.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE outreaches SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (outreach_id, user_id),
        )
        return cur.rowcount > 0
