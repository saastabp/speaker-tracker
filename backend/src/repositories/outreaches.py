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

from core.outreach import resolve_outreach_kind
from models.outreach import OutreachInput, OutreachPatch
from repositories import catalogs as catalogs_repo
from repositories._ownership import (
    has_prior_outbound_touch,
    validate_contact,
    validate_message_template,
    validate_opportunity,
)

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
    return catalogs_repo.resolve_catalog_id(conn, "outreach_channels", short_name, "channel")


def _resolve_kind_id(conn: Connection, short_name: str) -> int:
    """Resolve an ``outreach_kinds`` short_name to its id, or raise InvalidInput.

    Also validates a caller-supplied ``kind`` override: an unknown short_name is rejected here
    rather than silently stored.
    """
    return catalogs_repo.resolve_catalog_id(conn, "outreach_kinds", short_name, "outreach kind")


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
    validate_contact(conn, user_id, data.contact_id)
    validate_opportunity(conn, user_id, data.opportunity_id)
    validate_message_template(conn, user_id, data.message_template_id)
    channel_id = _resolve_channel_id(conn, data.channel)
    has_prior = has_prior_outbound_touch(conn, user_id, data.contact_id)
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


def patch_outreach(conn: Connection, user_id: int, outreach_id: int, data: OutreachPatch) -> bool:
    """Apply a partial edit to a logged touch; return whether a live owned row was matched.

    Validates and resolves whatever the caller sent: an unknown ``channel`` / ``kind`` short_name or
    a foreign ``opportunity_id`` is rejected before the UPDATE, exactly as on create. ``None`` means
    unchanged for the NOT NULL columns; ``opportunity_id`` and ``note`` read ``model_fields_set``,
    so an explicit ``null`` clears them (``models.outreach.OutreachPatch``).

    **The kind is taken as given, never re-inferred.** ``resolve_outreach_kind`` runs once, at
    create, against the contact's touch history at that moment. Re-running it on an edit would make
    an unrelated change able to flip ``initial`` to ``correspondence`` — and with it, whether the
    touch counts toward the week's prospecting target.

    Editing a touch never writes a ``status_events`` row, for the same reason logging one does not:
    the journal is decoupled from pipeline stage (DEV-PLAN slice 4 acceptance #6).

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    outreach_id : int
        The touch to edit.
    data : models.outreach.OutreachPatch
        The fields to change.

    Returns
    -------
    bool
        ``True`` when a live, owned row matched — **not** whether any column changed, since MySQL
        reports ``rowcount`` 0 for an UPDATE that writes a column's existing value. A patch that
        sets nothing is a no-op returning ``True``, so a redundant request is not read as a 404.

    Raises
    ------
    common.errors.InvalidInput
        When the opportunity is not the caller's, or a ``channel`` / ``kind`` short_name is unknown.
    """
    if get_outreach(conn, user_id, outreach_id) is None:
        return False
    validate_opportunity(conn, user_id, data.opportunity_id)

    assignments: list[str] = []
    params: list[object] = []
    if data.channel is not None:
        assignments.append("outreach_channel_id = %s")
        params.append(_resolve_channel_id(conn, data.channel))
    if data.kind is not None:
        assignments.append("outreach_kind_id = %s")
        params.append(_resolve_kind_id(conn, data.kind))
    if data.occurred_at is not None:
        assignments.append("occurred_at = %s")
        params.append(data.occurred_at)
    if "opportunity_id" in data.model_fields_set:
        assignments.append("opportunity_id = %s")
        params.append(data.opportunity_id)
    if "note" in data.model_fields_set:
        assignments.append("note = %s")
        params.append(data.note)
    if not assignments:
        return True

    params.extend([outreach_id, user_id])
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE outreaches SET " + ", ".join(assignments) + " "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            params,
        )
    return True


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
