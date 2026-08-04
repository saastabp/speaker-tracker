"""Raw-SQL persistence for logged appointments.

An appointment is a scheduled meeting with a contact, recorded for display and nothing else
(``0014_appointments.sql``, DEV-PLAN slice 11). Rows are soft-deleted; every read filters
``deleted_at IS NULL``. There is no completion state to track — "past" is ``scheduled_at`` against
now, which is why the scope filter here is the whole of this table's lifecycle logic.

**Why the now-comparison is a plain SQL predicate.** ``scheduled_at`` is a DATETIME holding a wall
clock, and ``CURRENT_TIMESTAMP`` evaluates in the session time zone — which ``common.db`` has
already set to the user's. Comparing them therefore compares Donna's 2pm against Donna's now, with
no conversion on either side. That is the property the DATETIME choice bought, and it is why this
module never reads the clock in Python. The dividing instant is nonetheless *injectable*
(``as_of``), because the Dashboard anchors every panel to one value and half its "Coming up" list
quietly filtering against a different clock would be a bug nothing would surface.

**Upcoming means from this instant, not from midnight.** A 9am appointment is off the list by 10am,
unlike a gig, whose ``event_date`` has no time and so stays up all day. An appointment carries the
hour it happens, so honouring it is the whole reason the hour is stored.
"""

from __future__ import annotations

from datetime import datetime

from pymysql.connections import Connection

from models.appointments import AppointmentInput, AppointmentPatch, AppointmentScope
from repositories._ownership import validate_contact

#: Response columns for an appointment. ``contacts`` is joined without a ``deleted_at`` filter so an
#: appointment still resolves its person's name after that contact is soft-deleted — the same choice
#: ``outreaches._SUMMARY_SELECT`` and ``follow_ups._SUMMARY_SELECT`` make.
_SUMMARY_SELECT = (
    "SELECT a.id, a.contact_id, c.name AS contact_name, a.title, a.scheduled_at, "
    "       a.details, a.created_at "
    "FROM appointments a "
    "JOIN contacts c ON c.id = a.contact_id "
)

#: Scope predicates, keyed by the wire value. ``all`` adds nothing. The placeholder takes an
#: injected "now" and falls back to the session clock, so a caller with its own anchor (the
#: Dashboard) filters against the same instant it uses everywhere else.
_SCOPE_WHERE: dict[str, str] = {
    "upcoming": " AND a.scheduled_at >= COALESCE(%s, CURRENT_TIMESTAMP)",
    "past": " AND a.scheduled_at < COALESCE(%s, CURRENT_TIMESTAMP)",
    "all": "",
}


def create_appointment(conn: Connection, user_id: int, data: AppointmentInput) -> int:
    """Insert an appointment and return its new id.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    data : models.appointments.AppointmentInput
        The validated writable fields.

    Returns
    -------
    int
        The new appointment's id.

    Raises
    ------
    common.errors.InvalidInput
        When the contact is not the caller's own live row.
    """
    validate_contact(conn, user_id, data.contact_id)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO appointments (user_id, contact_id, title, scheduled_at, details) "
            "VALUES (%s, %s, %s, %s, %s)",
            (user_id, data.contact_id, data.title, data.scheduled_at, data.details),
        )
        return cur.lastrowid


def get_appointment(conn: Connection, user_id: int, appointment_id: int) -> dict | None:
    """Return one appointment owned by ``user_id`` as a summary row, or None if absent/deleted."""
    with conn.cursor() as cur:
        cur.execute(
            _SUMMARY_SELECT + "WHERE a.id = %s AND a.user_id = %s AND a.deleted_at IS NULL",
            (appointment_id, user_id),
        )
        return cur.fetchone()


def list_appointments(
    conn: Connection,
    user_id: int,
    *,
    scope: AppointmentScope = "all",
    contact_id: int | None = None,
    as_of: datetime | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Return the caller's appointments, optionally narrowed by scope, person and count.

    Owner-scoped, so a foreign or unknown ``contact_id`` yields an empty list rather than leaking
    another account's rows.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    scope : {'all', 'upcoming', 'past'}, optional
        Which slice of the journal. Defaults to ``all`` — an unfiltered call means everything.
    contact_id : int or None, optional
        Narrow to one person's appointments.
    as_of : datetime or None, optional
        The instant that divides upcoming from past. ``None`` — the usual case — uses the session
        clock. The Dashboard passes its own anchor so every panel it renders agrees on "now".
        Ignored when ``scope`` is ``all``, which compares against nothing.
    limit : int or None, optional
        Cap the number of rows. Used by the Dashboard, which needs only the next few.

    Returns
    -------
    list of dict
        Rows shaped for :class:`models.appointments.AppointmentSummary`. **``past`` comes back
        newest-first; every other scope comes back soonest-first.** A past list is read backwards
        from now — the meeting last week matters more than the one last year — while an upcoming
        list is read forwards from now. ``id`` breaks ties for appointments sharing a timestamp.
    """
    where = "WHERE a.user_id = %s AND a.deleted_at IS NULL" + _SCOPE_WHERE[scope]
    params: list[object] = [user_id]
    if scope != "all":
        params.append(as_of)
    if contact_id is not None:
        where += " AND a.contact_id = %s"
        params.append(contact_id)
    direction = "DESC" if scope == "past" else "ASC"
    sql = f"{_SUMMARY_SELECT}{where} ORDER BY a.scheduled_at {direction}, a.id {direction}"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def patch_appointment(
    conn: Connection, user_id: int, appointment_id: int, data: AppointmentPatch
) -> bool:
    """Apply a partial edit; return whether a live row owned by ``user_id`` was matched.

    ``contact_id``, ``title`` and ``scheduled_at`` are NOT NULL columns, so ``None`` means unchanged
    for them. ``details`` is nullable and therefore reads ``model_fields_set`` instead: an
    explicitly sent ``null`` clears it, an omitted key leaves it alone (``models.appointments``).

    A patch that sets nothing is a no-op returning ``True`` when the row exists, so a redundant
    request is not mistaken for a missing one (the handler maps ``False`` to 404).

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection (inside a transaction).
    user_id : int
        The owning user.
    appointment_id : int
        The appointment to edit.
    data : models.appointments.AppointmentPatch
        The fields to change.

    Returns
    -------
    bool
        ``True`` when a live, owned row matched — **not** whether any column changed. MySQL reports
        ``rowcount`` 0 for an UPDATE that sets a column to the value it already holds, so
        ``rowcount`` cannot distinguish "no such row" from "no change"; this checks existence
        separately and lets the caller re-read for the new state.

    Raises
    ------
    common.errors.InvalidInput
        When ``contact_id`` names a contact that is not the caller's own live row.
    """
    if get_appointment(conn, user_id, appointment_id) is None:
        return False
    # Accepts None, which is this patch's "leave the person alone".
    validate_contact(conn, user_id, data.contact_id)

    assignments: list[str] = []
    params: list[object] = []
    if data.contact_id is not None:
        assignments.append("contact_id = %s")
        params.append(data.contact_id)
    if data.title is not None:
        assignments.append("title = %s")
        params.append(data.title)
    if data.scheduled_at is not None:
        assignments.append("scheduled_at = %s")
        params.append(data.scheduled_at)
    if "details" in data.model_fields_set:
        assignments.append("details = %s")
        params.append(data.details)
    if not assignments:
        return True

    params.extend([appointment_id, user_id])
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET " + ", ".join(assignments) + " "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            params,
        )
    return True


def soft_delete_appointment(conn: Connection, user_id: int, appointment_id: int) -> bool:
    """Soft-delete an appointment; return whether a live row owned by ``user_id`` was deleted."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (appointment_id, user_id),
        )
        return cur.rowcount > 0
