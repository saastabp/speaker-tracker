"""Raw-SQL reads for the catalog vocabularies.

One parameter-free read per table (``deleted_at IS NULL``, ordered for display). Table names
are internal constants, never request input, so they are safe to interpolate.
"""

from __future__ import annotations

from pymysql.connections import Connection

from models.catalogs import Catalogs

#: Columns every catalog exposes; extra flag columns are appended per table.
_STANDARD_COLUMNS = ("short_name", "description", "sort_order")


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
        All ten vocabularies, each ordered by ``sort_order`` then ``short_name``.
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
