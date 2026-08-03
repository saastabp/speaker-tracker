"""Raw-SQL persistence for materials — the index over the files kept in S3.

This table is not the file; it is the record that a file exists, what it is called, and how big it
is. The bytes are in the content bucket under ``storage.MATERIAL_PREFIX``, and nothing here reads
or writes them.

**Nothing already sent depends on these rows.** Attaching a material copies its bytes into the
message — ``mail.build_raw_message`` embeds attachment *bytes* as MIME parts and the assembled
message is stored whole under ``email/raw/`` — so renaming, replacing or removing a material never
alters an email that has gone out. The library is editable, not append-only.

Removal is a soft delete for the ordinary reason: it is recoverable, and a removed material may be
a file the user cannot easily re-source. The S3 object stays so undelete remains possible.
"""

from __future__ import annotations

from pymysql.connections import Connection

from models.materials import MaterialInput, MaterialUpdate

#: Columns returned by the list/get reads, shared so the two stay in step.
_READ_COLUMNS = (
    "id, talk_id, name, s3_key, content_type, size_bytes, sort_order, created_at, updated_at"
)


def list_materials(conn: Connection, user_id: int, talk_id: int | None = None) -> list[dict]:
    """Return the caller's live materials, newest first within their manual order.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    talk_id : int or None
        When given, only materials attached to that talk. ``None`` returns everything, which is
        what the library page and the composer's picker both want.

    Returns
    -------
    list of dict
        One summary row per material.
    """
    sql = f"SELECT {_READ_COLUMNS} FROM materials WHERE user_id = %s AND deleted_at IS NULL "
    params: list = [user_id]
    if talk_id is not None:
        sql += "AND talk_id = %s "
        params.append(talk_id)
    sql += "ORDER BY sort_order, created_at DESC, id DESC"
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def get_material(conn: Connection, user_id: int, material_id: int) -> dict | None:
    """Return one live material owned by ``user_id``, or None.

    Owner-scoped rather than looked up by id alone: the row carries the S3 key, and handing that
    to the wrong caller would hand them a presigned URL for someone else's file.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_READ_COLUMNS} FROM materials "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (material_id, user_id),
        )
        return cur.fetchone()


def create_material(
    conn: Connection,
    user_id: int,
    data: MaterialInput,
    *,
    content_type: str,
    size_bytes: int,
) -> int:
    """Insert a material row for an object already uploaded to S3.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    data : models.materials.MaterialInput
        Name, key, and optional talk — the parts the client is allowed to choose.
    content_type, size_bytes : str, int
        Read from S3 by the handler, **not** from the request. Passed separately from ``data`` so
        the split is visible in the signature: what the caller asserted, and what was verified.

    Returns
    -------
    int
        The new material's id.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO materials (user_id, talk_id, name, s3_key, content_type, size_bytes) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, data.talk_id, data.name, data.s3_key, content_type, size_bytes),
        )
        return cur.lastrowid


def update_material(conn: Connection, user_id: int, material_id: int, data: MaterialUpdate) -> bool:
    """Rename a material or move it between talks; return whether a live row was updated.

    Metadata only. The bytes are replaced by :func:`replace_material_file`, which has to re-read
    the object's size and type and therefore cannot be the same statement.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE materials SET name = %s, talk_id = %s "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (data.name, data.talk_id, material_id, user_id),
        )
        return cur.rowcount > 0


def replace_material_file(
    conn: Connection,
    user_id: int,
    material_id: int,
    *,
    s3_key: str,
    content_type: str,
    size_bytes: int,
) -> str | None:
    """Point a material at newly uploaded bytes; return the **superseded** key, or None.

    Overwriting a one-sheet in place is how this library stays current: the material keeps its id,
    name and talk, so anything referring to it still does. Emails already sent carry their own copy
    of the old bytes and are untouched.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    material_id : int
        The material to repoint.
    s3_key : str
        Key of the newly uploaded object. A new key rather than the old one reused, so a half-
        finished upload cannot leave the row pointing at a truncated file.
    content_type, size_bytes : str, int
        Read back from S3 by the handler, never taken from the client.

    Returns
    -------
    str or None
        The key this material pointed at before, so the caller can clean the old object up once
        the transaction commits — or None when no live row matched, in which case nothing moved
        and there is nothing to clean.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s3_key FROM materials WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (material_id, user_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute(
            "UPDATE materials SET s3_key = %s, content_type = %s, size_bytes = %s "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (s3_key, content_type, size_bytes, material_id, user_id),
        )
        return row["s3_key"]


def soft_delete_material(conn: Connection, user_id: int, material_id: int) -> bool:
    """Hide a material; return whether a live row owned by ``user_id`` was removed.

    The S3 object is left in place on purpose — see the module docstring.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE materials SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (material_id, user_id),
        )
        return cur.rowcount > 0
