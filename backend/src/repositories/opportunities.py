"""Raw-SQL persistence for opportunities — the pipeline board, detail, and status journal.

This module is the translation seam for the pipeline: the API references catalogs by
``short_name`` (``opportunity_format``, ``comp_type``, ``current_status``, ``payment_status``),
so writes resolve each to its numeric FK and reads join back to the short_name. Entities
(``organization_id``, ``talk_id``) travel as ids.

``current_status_id`` and ``closed_at`` are denormalized and **never recomputed on read**
(DATABASE.md §4): the board/History reads simply trust the stored ``closed_at`` (``IS NULL`` =
active board, ``IS NOT NULL`` = History), and only the journaled write paths call
:mod:`core.opportunities` to recompute it. Reads do not filter the joined organization's or talk's
``deleted_at`` — a card whose venue or talk was later retired still resolves its name/title.

This file is built in pieces; piece 1 is the catalog resolvers and the board/detail reads.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pymysql.connections import Connection

from common import errors
from core.funnel import is_board_stage, is_close_status
from core.opportunities import initial_payment_status, is_closed, is_real_move
from models.opportunities import OpportunityInput

#: Every opportunity starts here (piece 2 create default); the first status_events row records it.
_INITIAL_STATUS = "researching"


class StatusPatchResult(Enum):
    """Outcome of :func:`patch_status`, so the handler distinguishes 404 / no-op / real move.

    ``NO_CHANGE`` (a drag onto the current column) is a success that writes nothing — no
    ``status_events`` row, no ``closed_at`` change (acceptance #1) — and the handler still returns
    200 with the unchanged detail.
    """

    NOT_FOUND = "not_found"
    NO_CHANGE = "no_change"
    MOVED = "moved"


#: Summary columns for the flat board payload / History rows (see models.OpportunitySummary).
_SUMMARY_SELECT = (
    "SELECT o.id, o.title, o.organization_id, org.name AS organization_name, "
    "       otype.short_name AS organization_type, tlk.title AS talk_title, "
    "       fmt.short_name AS opportunity_format, st.short_name AS current_status, "
    "       ct.short_name AS comp_type, o.fee_amount, o.currency, "
    "       pay.short_name AS payment_status, o.event_date, o.paid_on, "
    "       o.closed_at, o.created_at, o.updated_at "
    "FROM opportunities o "
    "JOIN organizations org ON org.id = o.organization_id "
    "JOIN organization_types otype ON otype.id = org.organization_type_id "
    "LEFT JOIN talks tlk ON tlk.id = o.talk_id "
    "JOIN opportunity_formats fmt ON fmt.id = o.opportunity_format_id "
    "JOIN opportunity_statuses st ON st.id = o.current_status_id "
    "JOIN comp_types ct ON ct.id = o.comp_type_id "
    "JOIN payment_statuses pay ON pay.id = o.payment_status_id "
)

#: Detail columns — the summary set plus the descriptive fields and the resolved talk title.
_DETAIL_SELECT = (
    "SELECT o.id, o.title, o.organization_id, org.name AS organization_name, "
    "       o.talk_id, tlk.title AS talk_title, "
    "       fmt.short_name AS opportunity_format, st.short_name AS current_status, "
    "       ct.short_name AS comp_type, o.fee_amount, o.currency, "
    "       pay.short_name AS payment_status, o.event_date, o.paid_on, "
    "       o.angle, o.outcome, o.closed_at, o.created_at, o.updated_at "
    "FROM opportunities o "
    "JOIN organizations org ON org.id = o.organization_id "
    "LEFT JOIN talks tlk ON tlk.id = o.talk_id "
    "JOIN opportunity_formats fmt ON fmt.id = o.opportunity_format_id "
    "JOIN opportunity_statuses st ON st.id = o.current_status_id "
    "JOIN comp_types ct ON ct.id = o.comp_type_id "
    "JOIN payment_statuses pay ON pay.id = o.payment_status_id "
)


def _resolve_format_id(conn: Connection, short_name: str) -> int:
    """Resolve an `opportunity_formats` short_name to its id, or raise InvalidInput."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM opportunity_formats WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.InvalidInput("unknown opportunity_format")
    return row["id"]


def _resolve_comp_type_id(conn: Connection, short_name: str) -> int:
    """Resolve a `comp_types` short_name to its id, or raise InvalidInput."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM comp_types WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.InvalidInput("unknown comp_type")
    return row["id"]


def _resolve_status(conn: Connection, short_name: str) -> dict:
    """Resolve an `opportunity_statuses` short_name to ``{id, is_terminal}``, or raise InvalidInput.

    The ``is_terminal`` flag comes back with the id because the write paths feed it straight into
    the ``closed_at`` predicate (:func:`core.opportunities.is_closed`).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, is_terminal FROM opportunity_statuses "
            "WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.InvalidInput("unknown status")
    return row


def _resolve_payment_status(conn: Connection, short_name: str) -> dict:
    """Resolve a `payment_statuses` short_name to ``{id, is_settled}``, or raise InvalidInput.

    ``is_settled`` accompanies the id for the same reason as ``is_terminal`` above: it is the money
    gate in the ``closed_at`` predicate.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, is_settled FROM payment_statuses "
            "WHERE short_name = %s AND deleted_at IS NULL",
            (short_name,),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.InvalidInput("unknown payment_status")
    return row


def _validate_organization(conn: Connection, user_id: int, org_id: int) -> str | None:
    """Return the organization's `how_to_approach`, or raise InvalidInput if it is not the user's.

    Doubles as the ownership check on the `organization_id` FK (which the database enforces for
    existence but not for tenancy): a live organization belonging to another user, or a
    soft-deleted one, is rejected. The `how_to_approach` seeds a new opportunity's `angle`.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT how_to_approach FROM organizations "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (org_id, user_id),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.InvalidInput("unknown organization")
    return row["how_to_approach"]


def _validate_talk(conn: Connection, user_id: int, talk_id: int | None) -> None:
    """Raise InvalidInput unless `talk_id` is None or a live talk owned by the user."""
    if talk_id is None:
        return
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM talks WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (talk_id, user_id),
        )
        if cur.fetchone() is None:
            raise errors.InvalidInput("unknown talk")


def list_opportunities(
    conn: Connection,
    user_id: int,
    closed: bool | None = None,
    status: str | None = None,
) -> list[dict]:
    """Return the caller's opportunities as flat board / History rows.

    The SPA buckets the flat list into columns by ``current_status`` (the flat-payload board
    decision) and splits board vs History by ``closed_at``. This read trusts the stored
    ``closed_at`` and never recomputes it (DATABASE.md §4).

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    closed : bool or None
        ``None`` returns everything; ``False`` the active board (``closed_at IS NULL``); ``True``
        History (``closed_at IS NOT NULL``).
    status : str or None
        Optional ``opportunity_statuses`` short_name filter.

    Returns
    -------
    list of dict
        One summary row per opportunity, upcoming-dated first (undated last), then oldest-created.
    """
    sql = _SUMMARY_SELECT + "WHERE o.user_id = %s AND o.deleted_at IS NULL "
    params: list = [user_id]
    if closed is True:
        sql += "AND o.closed_at IS NOT NULL "
    elif closed is False:
        sql += "AND o.closed_at IS NULL "
    if status is not None:
        sql += "AND st.short_name = %s "
        params.append(status)
    sql += "ORDER BY (o.event_date IS NULL), o.event_date, o.created_at"
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def get_opportunity(conn: Connection, user_id: int, opp_id: int) -> dict | None:
    """Return one opportunity's detail base row, or None if absent for this user.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    opp_id : int
        The opportunity id.

    Returns
    -------
    dict or None
        The detail row (descriptive fields, catalog short_names, ``organization_name``,
        ``talk_title``, lifecycle state, timestamps), or None when it does not exist for this user.
        Linked contacts, notes, and status events are read separately.
    """
    with conn.cursor() as cur:
        cur.execute(
            _DETAIL_SELECT + "WHERE o.user_id = %s AND o.id = %s AND o.deleted_at IS NULL",
            (user_id, opp_id),
        )
        return cur.fetchone()


def get_opportunity_contacts(conn: Connection, user_id: int, opp_id: int) -> list[dict]:
    """Return the non-deleted contacts linked to an opportunity, lead first.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user (enforces ownership through the opportunity).
    opp_id : int
        The opportunity id.

    Returns
    -------
    list of dict
        Each with `contact_id`, `name`, `contact_role` (short_name or None), and `is_primary`.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.id AS contact_id, c.name, cr.short_name AS contact_role, oc.is_primary "
            "FROM opportunity_contacts oc "
            "JOIN opportunities o ON o.id = oc.opportunity_id "
            "  AND o.user_id = %s AND o.deleted_at IS NULL "
            "JOIN contacts c ON c.id = oc.contact_id AND c.deleted_at IS NULL "
            "LEFT JOIN contact_roles cr ON cr.id = oc.contact_role_id "
            "WHERE oc.opportunity_id = %s "
            "ORDER BY oc.is_primary DESC, c.name",
            (user_id, opp_id),
        )
        return list(cur.fetchall())


def get_opportunity_notes(conn: Connection, user_id: int, opp_id: int) -> list[dict]:
    """Return an opportunity's non-deleted notes, most recent first.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    opp_id : int
        The opportunity id.

    Returns
    -------
    list of dict
        Each with `id`, `body`, `occurred_at`, `created_at`, ordered by `occurred_at` descending.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, body, occurred_at, created_at FROM opportunity_notes "
            "WHERE opportunity_id = %s AND user_id = %s AND deleted_at IS NULL "
            "ORDER BY occurred_at DESC, id DESC",
            (opp_id, user_id),
        )
        return list(cur.fetchall())


def get_status_events(conn: Connection, user_id: int, opp_id: int) -> list[dict]:
    """Return an opportunity's status journal, most recent first.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    opp_id : int
        The opportunity id.

    Returns
    -------
    list of dict
        Each with `id`, `status` (short_name), `note` (close reason or None), and `occurred_at`,
        ordered by `occurred_at` descending. This is an append-only log.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT se.id, st.short_name AS status, se.note, se.occurred_at "
            "FROM status_events se "
            "JOIN opportunity_statuses st ON st.id = se.status_id "
            "WHERE se.opportunity_id = %s AND se.user_id = %s "
            "ORDER BY se.occurred_at DESC, se.id DESC",
            (opp_id, user_id),
        )
        return list(cur.fetchall())


def create_opportunity(conn: Connection, user_id: int, data: OpportunityInput) -> int:
    """Insert an opportunity in ``researching`` and return its new id.

    Resolves the four catalog short_names to their ids, derives the initial payment status from the
    comp type (:func:`core.opportunities.initial_payment_status`), and seeds ``angle`` from the
    venue's ``how_to_approach`` when the caller did not supply one. The opportunity row and its
    first ``status_events`` row (status ``researching``) are written together in the caller's
    transaction; ``researching`` is non-terminal, so ``closed_at`` starts NULL.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    data : models.opportunities.OpportunityInput
        The validated writable fields (catalogs as short_names, entities as ids).

    Returns
    -------
    int
        The new opportunity's id.

    Raises
    ------
    common.errors.InvalidInput
        When the organization or talk is not the user's, or a catalog short_name is unknown.
    """
    how_to_approach = _validate_organization(conn, user_id, data.organization_id)
    _validate_talk(conn, user_id, data.talk_id)
    format_id = _resolve_format_id(conn, data.opportunity_format)
    comp_type_id = _resolve_comp_type_id(conn, data.comp_type)
    status = _resolve_status(conn, _INITIAL_STATUS)
    payment = _resolve_payment_status(conn, initial_payment_status(data.comp_type))
    angle = data.angle if (data.angle and data.angle.strip()) else how_to_approach
    columns = [
        ("user_id", user_id),
        ("organization_id", data.organization_id),
        ("talk_id", data.talk_id),
        ("opportunity_format_id", format_id),
        ("current_status_id", status["id"]),
        ("comp_type_id", comp_type_id),
        ("payment_status_id", payment["id"]),
        ("title", data.title),
        ("event_date", data.event_date),
        ("fee_amount", data.fee_amount),
        ("currency", data.currency),
        ("angle", angle),
        ("outcome", data.outcome),
    ]
    names = [name for name, _ in columns]
    placeholders = ", ".join(["%s"] * len(columns))
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO opportunities ({', '.join(names)}) VALUES ({placeholders})",
            tuple(value for _, value in columns),
        )
        opp_id = cur.lastrowid
        cur.execute(
            "INSERT INTO status_events (user_id, opportunity_id, status_id) VALUES (%s, %s, %s)",
            (user_id, opp_id, status["id"]),
        )
    return opp_id


def update_opportunity(conn: Connection, user_id: int, opp_id: int, data: OpportunityInput) -> bool:
    """Full-replace an opportunity's descriptive/money-setup fields; return whether it existed.

    This touches only the descriptive columns — never ``current_status_id``, ``payment_status_id``,
    ``paid_on``, or ``closed_at``. Those lifecycle fields move solely through the journaled paths
    (``patch_status`` / ``patch_payment`` / ``close``), which keeps the status journal faithful and
    ``closed_at`` recomputed only where it is owned. Unlike create, ``angle`` is replaced verbatim
    (no re-seed from the venue).

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    opp_id : int
        The opportunity id.
    data : models.opportunities.OpportunityInput
        The validated replacement fields.

    Returns
    -------
    bool
        True if the opportunity existed and was updated; False if absent (caller maps to 404).

    Raises
    ------
    common.errors.InvalidInput
        When the organization or talk is not the user's, or a catalog short_name is unknown.
    """
    _validate_organization(conn, user_id, data.organization_id)
    _validate_talk(conn, user_id, data.talk_id)
    format_id = _resolve_format_id(conn, data.opportunity_format)
    comp_type_id = _resolve_comp_type_id(conn, data.comp_type)
    columns = [
        ("organization_id", data.organization_id),
        ("talk_id", data.talk_id),
        ("opportunity_format_id", format_id),
        ("comp_type_id", comp_type_id),
        ("title", data.title),
        ("event_date", data.event_date),
        ("fee_amount", data.fee_amount),
        ("currency", data.currency),
        ("angle", data.angle),
        ("outcome", data.outcome),
    ]
    assignments = ", ".join(f"{name} = %s" for name, _ in columns)
    values = [value for _, value in columns]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM opportunities WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (opp_id, user_id),
        )
        if cur.fetchone() is None:
            return False
        cur.execute(
            f"UPDATE opportunities SET {assignments} WHERE id = %s AND user_id = %s",
            (*values, opp_id, user_id),
        )
    return True


def soft_delete_opportunity(conn: Connection, user_id: int, opp_id: int) -> bool:
    """Soft-delete an opportunity; return whether a live row was deleted.

    The board removes *outcomes* via :func:`close` (cancelled / lost → History); this is the
    separate path for deleting a mistaken gig entirely. Reads hide it via ``deleted_at``; its
    status events, notes, and contact links are left in place (unreachable through the hidden row).

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    opp_id : int
        The opportunity id.

    Returns
    -------
    bool
        True if a non-deleted opportunity was marked deleted; False otherwise.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE opportunities SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (opp_id, user_id),
        )
        return cur.rowcount > 0


def patch_status(
    conn: Connection, user_id: int, opp_id: int, target_status: str
) -> StatusPatchResult:
    """Move an opportunity to a new board stage, journaling exactly one status event per real move.

    A move to the status the card already sits in is a no-op — no ``status_events`` row and no
    ``closed_at`` change (acceptance #1). A real move writes one status event, updates
    ``current_status_id``, and recomputes ``closed_at`` (:func:`core.opportunities.is_closed`
    against the target status and the *current* payment settlement) — all in the caller's
    transaction. The target must be a board stage; cancelled / lost go through :func:`close`.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    opp_id : int
        The opportunity id.
    target_status : str
        Target ``opportunity_statuses`` short_name.

    Returns
    -------
    StatusPatchResult
        ``NOT_FOUND`` if absent (caller maps to 404); ``NO_CHANGE`` for a same-status no-op;
        ``MOVED`` when a status event was written.

    Raises
    ------
    common.errors.InvalidInput
        When ``target_status`` is unknown or is not a board stage (cancelled / lost need
        :func:`close`).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT st.short_name AS current_status, pay.is_settled AS payment_settled "
            "FROM opportunities o "
            "JOIN opportunity_statuses st ON st.id = o.current_status_id "
            "JOIN payment_statuses pay ON pay.id = o.payment_status_id "
            "WHERE o.id = %s AND o.user_id = %s AND o.deleted_at IS NULL",
            (opp_id, user_id),
        )
        current = cur.fetchone()
    if current is None:
        return StatusPatchResult.NOT_FOUND
    if not is_real_move(current["current_status"], target_status):
        return StatusPatchResult.NO_CHANGE

    target = _resolve_status(conn, target_status)
    if not is_board_stage(target_status, bool(target["is_terminal"])):
        raise errors.InvalidInput("status is not a board stage; use close")

    closed = is_closed(target_status, bool(target["is_terminal"]), bool(current["payment_settled"]))
    closed_at_sql = "COALESCE(closed_at, CURRENT_TIMESTAMP)" if closed else "NULL"
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO status_events (user_id, opportunity_id, status_id) VALUES (%s, %s, %s)",
            (user_id, opp_id, target["id"]),
        )
        cur.execute(
            f"UPDATE opportunities SET current_status_id = %s, closed_at = {closed_at_sql} "
            "WHERE id = %s AND user_id = %s",
            (target["id"], opp_id, user_id),
        )
    return StatusPatchResult.MOVED


def patch_payment(
    conn: Connection,
    user_id: int,
    opp_id: int,
    payment_status: str,
    paid_on: date | None,
) -> bool:
    """Update an opportunity's payment state and recompute ``closed_at``; return whether it existed.

    Payment is not a status move, so this writes no ``status_events`` row — but it does re-run the
    ``closed_at`` predicate against the *current* status and the new settlement: marking a delivered
    gig paid closes it into History (acceptance #4), and correcting payment back off a settled state
    clears ``closed_at`` and returns the card to the board (acceptance #5).

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    opp_id : int
        The opportunity id.
    payment_status : str
        ``payment_statuses`` catalog short_name.
    paid_on : datetime.date or None
        Date the payment was received, if applicable.

    Returns
    -------
    bool
        True if the opportunity existed and was updated; False if absent (caller maps to 404).

    Raises
    ------
    common.errors.InvalidInput
        When ``payment_status`` is not a known catalog short_name.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT st.short_name AS current_status, st.is_terminal AS current_is_terminal "
            "FROM opportunities o "
            "JOIN opportunity_statuses st ON st.id = o.current_status_id "
            "WHERE o.id = %s AND o.user_id = %s AND o.deleted_at IS NULL",
            (opp_id, user_id),
        )
        current = cur.fetchone()
    if current is None:
        return False

    payment = _resolve_payment_status(conn, payment_status)
    closed = is_closed(
        current["current_status"], bool(current["current_is_terminal"]), bool(payment["is_settled"])
    )
    closed_at_sql = "COALESCE(closed_at, CURRENT_TIMESTAMP)" if closed else "NULL"
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE opportunities SET payment_status_id = %s, paid_on = %s, "
            f"closed_at = {closed_at_sql} WHERE id = %s AND user_id = %s",
            (payment["id"], paid_on, opp_id, user_id),
        )
    return True


def close(conn: Connection, user_id: int, opp_id: int, target_status: str, reason: str) -> bool:
    """Close an opportunity through the Close flow; return whether it existed.

    Writes a terminal ``status_events`` row carrying the reason **and** an ``opportunity_notes`` row
    with the same reason (acceptance #8 — a terminal event *and* a note), updates
    ``current_status_id``, and sets ``closed_at``, all in the caller's transaction. The target must
    be a Close-flow status (cancelled / lost); those close unconditionally, so ``closed_at`` is
    always set.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    opp_id : int
        The opportunity id.
    target_status : str
        Terminal ``opportunity_statuses`` short_name — cancelled or lost.
    reason : str
        Why it closed; recorded on both the status event and the note.

    Returns
    -------
    bool
        True if the opportunity existed and was closed; False if absent (caller maps to 404).

    Raises
    ------
    common.errors.InvalidInput
        When ``target_status`` is unknown or is not a Close-flow status (delivered / board stages
        are not closed this way).
    """
    target = _resolve_status(conn, target_status)
    if not is_close_status(target_status, bool(target["is_terminal"])):
        raise errors.InvalidInput("status is not a close status")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM opportunities WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (opp_id, user_id),
        )
        if cur.fetchone() is None:
            return False
        cur.execute(
            "INSERT INTO status_events (user_id, opportunity_id, status_id, note) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, opp_id, target["id"], reason),
        )
        cur.execute(
            "INSERT INTO opportunity_notes (user_id, opportunity_id, body) VALUES (%s, %s, %s)",
            (user_id, opp_id, reason),
        )
        cur.execute(
            "UPDATE opportunities SET current_status_id = %s, "
            "closed_at = COALESCE(closed_at, CURRENT_TIMESTAMP) WHERE id = %s AND user_id = %s",
            (target["id"], opp_id, user_id),
        )
    return True
