"""Raw-SQL persistence for per-user email signatures.

Owner-scoped and soft-deleted. A single default per user is a repository invariant (the schema does
not enforce it): whenever a signature is set default, :func:`_clear_other_defaults` clears the flag
on the caller's other signatures — the same one-of-many pattern as the single lead per gig.
"""

from __future__ import annotations

from pymysql.connections import Connection
from pymysql.cursors import Cursor

from models.signatures import SignatureInput

#: Columns returned for a signature (see models.signatures.Signature).
_SELECT = "SELECT id, name, body_html, is_default, created_at, updated_at FROM signatures "


def _clear_other_defaults(cur: Cursor, user_id: int, keep_id: int) -> None:
    """Clear `is_default` on every *other* signature of the user (single-default invariant)."""
    cur.execute(
        "UPDATE signatures SET is_default = FALSE "
        "WHERE user_id = %s AND id <> %s AND is_default AND deleted_at IS NULL",
        (user_id, keep_id),
    )


def list_signatures(conn: Connection, user_id: int) -> list[dict]:
    """Return the caller's non-deleted signatures, default first then by name."""
    with conn.cursor() as cur:
        cur.execute(
            _SELECT + "WHERE user_id = %s AND deleted_at IS NULL ORDER BY is_default DESC, name",
            (user_id,),
        )
        return list(cur.fetchall())


def get_signature(conn: Connection, user_id: int, sig_id: int) -> dict | None:
    """Return one of the caller's signatures, or None if absent/deleted."""
    with conn.cursor() as cur:
        cur.execute(
            _SELECT + "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (sig_id, user_id),
        )
        return cur.fetchone()


def get_default_signature(conn: Connection, user_id: int) -> dict | None:
    """Return the caller's default signature, or None when none is marked default."""
    with conn.cursor() as cur:
        cur.execute(
            _SELECT
            + "WHERE user_id = %s AND is_default AND deleted_at IS NULL ORDER BY id LIMIT 1",
            (user_id,),
        )
        return cur.fetchone()


def create_signature(conn: Connection, user_id: int, data: SignatureInput) -> int:
    """Insert a signature and return its id; enforces the single-default invariant."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signatures (user_id, name, body_html, is_default) VALUES (%s, %s, %s, %s)",
            (user_id, data.name, data.body_html, data.is_default),
        )
        sig_id = cur.lastrowid
        if data.is_default:
            _clear_other_defaults(cur, user_id, sig_id)
    return sig_id


def update_signature(conn: Connection, user_id: int, sig_id: int, data: SignatureInput) -> bool:
    """Full-replace a signature's fields; return whether it existed. Enforces single default."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM signatures WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (sig_id, user_id),
        )
        if cur.fetchone() is None:
            return False
        cur.execute(
            "UPDATE signatures SET name = %s, body_html = %s, is_default = %s "
            "WHERE id = %s AND user_id = %s",
            (data.name, data.body_html, data.is_default, sig_id, user_id),
        )
        if data.is_default:
            _clear_other_defaults(cur, user_id, sig_id)
    return True


def soft_delete_signature(conn: Connection, user_id: int, sig_id: int) -> bool:
    """Soft-delete one of the caller's signatures; return whether a live row was deleted."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE signatures SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (sig_id, user_id),
        )
        return cur.rowcount > 0
