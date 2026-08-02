"""Raw-SQL reads for the catalog vocabularies, and the shared short_name → id resolver.

One parameter-free read per table (``deleted_at IS NULL``, ordered for display). Table names
are internal constants, never request input, so they are safe to interpolate.

:func:`resolve_catalog_id` lives here rather than in each repository because writes everywhere
translate a request's ``short_name`` into a foreign key, and that lookup had been hand-written
once per catalog across eight repository modules — including two byte-identical copies of the
``outreach_channels`` one. The per-catalog wrappers that remain are one-line delegations that
name their table; the query itself exists once.
"""

from __future__ import annotations

from pymysql.connections import Connection

from common import errors
from models.catalogs import Catalogs

#: Columns every catalog exposes; extra flag columns are appended per table.
_STANDARD_COLUMNS = ("short_name", "description", "sort_order")


def resolve_catalog_id(conn: Connection, table: str, short_name: str, label: str) -> int:
    """Resolve a catalog ``short_name`` to its primary key.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    table : str
        The catalog table. **Never caller-supplied** — every call site passes a module literal, so
        interpolating it introduces no injection surface while the ``short_name`` stays a bound
        parameter.
    short_name : str
        The catalog short_name from the request.
    label : str
        Human name of the catalog, used in the error message ("unknown channel").

    Returns
    -------
    int
        The catalog row's id.

    Raises
    ------
    common.errors.InvalidInput
        When no live row has that short_name — an unknown value is rejected at the write rather
        than stored.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id FROM {table} WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.InvalidInput(f"unknown {label}")
    return row["id"]


def resolve_optional_catalog_id(
    conn: Connection, table: str, short_name: str | None, label: str
) -> int | None:
    """Resolve an **optional** catalog short_name; ``None`` passes through as ``None``.

    For the catalogs backing nullable columns (a contact's warmth, a role on a gig), where "unset"
    and "unknown" are different answers: unset is allowed, unknown is still rejected.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    table : str
        The catalog table; see :func:`resolve_catalog_id`.
    short_name : str or None
        The catalog short_name, or None for an unset field.
    label : str
        Human name of the catalog, used in the error message.

    Returns
    -------
    int or None
        The catalog row's id, or None when ``short_name`` was None.

    Raises
    ------
    common.errors.InvalidInput
        When a non-None short_name matches no live row.
    """
    if short_name is None:
        return None
    return resolve_catalog_id(conn, table, short_name, label)


def _fetch(conn: Connection, table: str, *extra_columns: str) -> list[dict]:
    """Return the non-deleted rows of one catalog table, ordered for display."""
    columns = ", ".join((*_STANDARD_COLUMNS, *extra_columns))
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {columns} FROM {table} WHERE deleted_at IS NULL "
            "ORDER BY sort_order, short_name"
        )
        return cur.fetchall()


def fetch_catalogs(conn: Connection) -> Catalogs:
    """Return every catalog vocabulary as a validated :class:`Catalogs`.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection from :func:`common.db.get_connection`.

    Returns
    -------
    models.catalogs.Catalogs
        All eleven vocabularies, each ordered by ``sort_order`` then ``short_name``.
    """
    return Catalogs(
        organization_types=_fetch(conn, "organization_types"),
        warmth_tiers=_fetch(conn, "warmth_tiers"),
        contact_roles=_fetch(conn, "contact_roles"),
        opportunity_formats=_fetch(conn, "opportunity_formats"),
        opportunity_statuses=_fetch(conn, "opportunity_statuses", "is_terminal"),
        comp_types=_fetch(conn, "comp_types"),
        payment_statuses=_fetch(conn, "payment_statuses", "is_settled"),
        outreach_kinds=_fetch(conn, "outreach_kinds", "counts_toward_target"),
        outreach_channels=_fetch(conn, "outreach_channels"),
        message_template_kinds=_fetch(conn, "message_template_kinds"),
        target_types=_fetch(conn, "target_types"),
    )


def list_opportunity_statuses(conn: Connection) -> list[dict]:
    """Return the ``opportunity_statuses`` catalog rows, ordered by ``sort_order``.

    Each row carries ``short_name``, ``description``, ``sort_order``, and ``is_terminal`` — the
    inputs the server-owned funnel (:mod:`core.funnel`) needs to build the board columns, without
    fetching the other nine catalogs.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.

    Returns
    -------
    list of dict
        The non-deleted status rows.
    """
    return _fetch(conn, "opportunity_statuses", "is_terminal")
