"""Raw-SQL persistence for the message-template library.

A template carries two catalog references — ``kind`` (``message_template_kinds``, the purpose) and
``channel`` (``outreach_channels``, how it is sent) — resolved from short_names on write and joined
back on read (Option A, DATABASE.md §5). ``user_id NULL`` marks a **shared** reference row visible
to everyone; a non-null ``user_id`` is a personal template.

Visibility on every read and edit is "mine or shared" (``user_id = %s OR user_id IS NULL``). Shared
rows are editable in place (single-user; admin-gated later) and **Duplicate** writes a personal copy
(acceptance #4). Deletion is restricted to a caller's own templates, so the seeded reference content
cannot be removed. Rows are soft-deleted; reads filter ``deleted_at IS NULL``.
"""

from __future__ import annotations

from pymysql.connections import Connection

from common import errors
from models.message_templates import MessageTemplateInput

#: Response columns, catalog ids joined back to short_names. ``is_shared`` is derived from a null
#: owner so the SPA can show the edit-in-place vs Duplicate affordances without seeing owner ids.
_SUMMARY_SELECT = (
    "SELECT mt.id, k.short_name AS kind, ch.short_name AS channel, mt.name, mt.subject, mt.body, "
    "       (mt.user_id IS NULL) AS is_shared, mt.created_at, mt.updated_at "
    "FROM message_templates mt "
    "JOIN message_template_kinds k ON k.id = mt.message_template_kind_id "
    "JOIN outreach_channels ch ON ch.id = mt.channel_id "
)

#: Rows visible to a caller: their own plus shared reference rows.
_VISIBLE = "(mt.user_id = %s OR mt.user_id IS NULL) AND mt.deleted_at IS NULL"


def _resolve_kind_id(conn: Connection, short_name: str) -> int:
    """Resolve a ``message_template_kinds`` short_name to its id, or raise InvalidInput."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM message_template_kinds WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.InvalidInput("unknown message template kind")
    return row["id"]


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


def list_message_templates(conn: Connection, user_id: int) -> list[dict]:
    """Return every template visible to ``user_id`` (own + shared), shared first then by name."""
    with conn.cursor() as cur:
        cur.execute(
            _SUMMARY_SELECT + "WHERE " + _VISIBLE + " ORDER BY (mt.user_id IS NULL) DESC, mt.name",
            (user_id,),
        )
        return list(cur.fetchall())


def get_message_template(conn: Connection, user_id: int, template_id: int) -> dict | None:
    """Return one template visible to ``user_id``, or None if absent/deleted/not visible."""
    with conn.cursor() as cur:
        cur.execute(
            _SUMMARY_SELECT + "WHERE mt.id = %s AND " + _VISIBLE,
            (template_id, user_id),
        )
        return cur.fetchone()


def create_message_template(conn: Connection, user_id: int, data: MessageTemplateInput) -> int:
    """Insert a **personal** template owned by ``user_id`` and return its new id.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user (the new row is personal, never shared).
    data : models.message_templates.MessageTemplateInput
        The validated fields (``kind`` / ``channel`` as short_names).

    Returns
    -------
    int
        The new template's id.

    Raises
    ------
    common.errors.InvalidInput
        When ``kind`` or ``channel`` is not a known catalog short_name.
    """
    kind_id = _resolve_kind_id(conn, data.kind)
    channel_id = _resolve_channel_id(conn, data.channel)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO message_templates "
            "(user_id, message_template_kind_id, channel_id, name, subject, body) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, kind_id, channel_id, data.name, data.subject, data.body),
        )
        return cur.lastrowid


def update_message_template(
    conn: Connection, user_id: int, template_id: int, data: MessageTemplateInput
) -> bool:
    """Full-replace a visible template's fields; return whether it existed.

    Edits a template in place — including a **shared** row (``user_id IS NULL``, acceptance #4) — as
    long as it is visible to the caller. ``updated_at`` bumps via the column's ``ON UPDATE`` clause.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The caller.
    template_id : int
        The template id.
    data : models.message_templates.MessageTemplateInput
        The validated replacement fields.

    Returns
    -------
    bool
        True if a visible template was updated; False if absent (caller maps to 404).

    Raises
    ------
    common.errors.InvalidInput
        When ``kind`` or ``channel`` is not a known catalog short_name.
    """
    kind_id = _resolve_kind_id(conn, data.kind)
    channel_id = _resolve_channel_id(conn, data.channel)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE message_templates mt "
            "SET mt.message_template_kind_id = %s, mt.channel_id = %s, mt.name = %s, "
            "    mt.subject = %s, mt.body = %s "
            "WHERE mt.id = %s AND " + _VISIBLE,
            (kind_id, channel_id, data.name, data.subject, data.body, template_id, user_id),
        )
        return cur.rowcount > 0


def duplicate_message_template(conn: Connection, user_id: int, template_id: int) -> int:
    """Copy a visible template into a **personal** row owned by ``user_id``; return the new id.

    The copy's name is the source name suffixed with `` (copy)`` (truncated to fit 255 chars). Used
    to fork a shared reference template into an editable personal variant (acceptance #4).

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The user who will own the copy.
    template_id : int
        The source template id (must be visible: own or shared).

    Returns
    -------
    int
        The new personal template's id.

    Raises
    ------
    common.errors.NotFound
        When the source template is not visible to the caller.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO message_templates "
            "(user_id, message_template_kind_id, channel_id, name, subject, body) "
            "SELECT %s, mt.message_template_kind_id, mt.channel_id, "
            "       CONCAT(LEFT(mt.name, 248), ' (copy)'), mt.subject, mt.body "
            "FROM message_templates mt WHERE mt.id = %s AND " + _VISIBLE,
            (user_id, template_id, user_id),
        )
        if cur.rowcount == 0:
            raise errors.NotFound("message template not found")
        return cur.lastrowid


def soft_delete_message_template(conn: Connection, user_id: int, template_id: int) -> bool:
    """Soft-delete one of the caller's **own** templates; return whether a live row was deleted.

    Restricted to owned rows (``user_id = %s``): shared reference templates cannot be deleted, only
    edited in place or duplicated.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE message_templates SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (template_id, user_id),
        )
        return cur.rowcount > 0
