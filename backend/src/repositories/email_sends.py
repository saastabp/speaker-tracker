"""Raw-SQL write path for sending email — the three phases of the intent-first send.

Sending crosses a transaction boundary and an external service, so it cannot be one atomic act.
Rather than send first and hope the write lands (a dual write whose failure mode is a *silently
lost* record), this module records **intent first** and reconciles afterwards:

1. :func:`create_pending_send` — one transaction writing ``email_threads`` (created or reused),
   ``email_messages`` with ``sent_at`` NULL, and the ``outreaches`` touch. The ``Message-ID`` is
   minted by ``core.email_headers`` *before* this call, so the row is durable and identifiable
   before SES is ever contacted.
2. **SES send** — the caller's job (``handlers/emails.py``), deliberately outside this module so
   the transaction logic is testable without mocking AWS.
3. :func:`confirm_send` — one transaction setting ``sent_at`` and advancing the thread's
   ``last_direction`` / ``last_message_at``.

When SES fails *synchronously* — a clean error, so we know nothing went out —
:func:`discard_pending_send` compensates by hard-deleting what phase 1 wrote (DEV-PLAN slice 6a
acceptance #2: a forced SES failure leaves no rows). The rows are hard-deleted, not soft-deleted:
a send that never happened is not history to retain, and a soft-deleted ``outreaches`` row would
still be a claim that Donna touched the contact.

The one state this design cannot eliminate is a crash *between* the SES call and phase 3, which
leaves ``direction='out' AND sent_at IS NULL`` — a **pending** message. That is the point: no
pattern makes ``SendRawEmail`` exactly-once (it has no idempotency key), so the goal is to convert
a silent loss into a detectable state. Pending rows are reconciled by 6b's Sent-folder poller,
which is idempotent on ``UNIQUE(user_id, message_id)`` — our own minted id. Nothing here ever
auto-retries a send; a retry after an ambiguous SES outcome would double-send to a venue.

Thread aggregates are advanced in phase 3, never phase 1, so compensation only ever *deletes*
rows and never has to restore a previous ``last_message_at``. A freshly created thread therefore
carries ``last_message_at`` NULL until its first message is confirmed — which reads correctly as
"nothing has gone out on this thread yet". Thread and message *reads* live in
:mod:`repositories.email_threads`.
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from pymysql.connections import Connection

from common import errors
from core.email_headers import normalize_subject
from core.outreach import resolve_outreach_kind
from models.emails import EmailSendInput
from repositories._ownership import (
    has_prior_outbound_touch,
    validate_contact,
    validate_message_template,
    validate_opportunity,
)

#: ``outreach_channels`` short_name every emailed touch is logged under. Email is not offered as a
#: manual channel in the log-outreach composer (slice 4) — an email touch is only ever created here,
#: by actually sending one.
EMAIL_CHANNEL = "email"


class PendingSend(NamedTuple):
    """Ids written by phase 1, carried to phase 3 (or to the compensation).

    Attributes
    ----------
    message_row_id : int
        ``email_messages.id`` — the local row id, not the RFC 5322 ``Message-ID``.
    thread_id : int
        ``email_threads.id`` the message belongs to.
    outreach_id : int or None
        ``outreaches.id`` for the logged touch, or ``None`` when the send had no ``contact_id``
        (``outreaches.contact_id`` is NOT NULL, so an unlinked send logs no touch).
    thread_created : bool
        Whether phase 1 created the thread. Compensation deletes the thread only when it did.
    """

    message_row_id: int
    thread_id: int
    outreach_id: int | None
    thread_created: bool


def _resolve_catalog_id(conn: Connection, table: str, short_name: str, label: str) -> int:
    """Resolve a catalog short_name to its id, or raise InvalidInput.

    ``table`` is never caller-supplied — it is one of this module's two literals — so interpolating
    it into the statement introduces no injection surface, and the short_name stays parameterized.
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


def _create_thread(
    conn: Connection,
    user_id: int,
    subject: str,
    contact_id: int | None,
    opportunity_id: int | None,
) -> int:
    """Insert a thread for a new conversation and return its id.

    ``last_direction`` is ``out`` from the outset (this user is opening the conversation) but
    ``last_message_at`` stays NULL until :func:`confirm_send` — an unconfirmed thread has had
    nothing go out on it.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_threads "
            "(user_id, contact_id, opportunity_id, subject_normalized, last_direction) "
            "VALUES (%s, %s, %s, %s, 'out')",
            (user_id, contact_id, opportunity_id, normalize_subject(subject)),
        )
        return cur.lastrowid


def _require_thread(conn: Connection, user_id: int, thread_id: int) -> dict:
    """Return a live thread owned by ``user_id``, or raise NotFound."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, contact_id, opportunity_id, closed_at FROM email_threads "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (thread_id, user_id),
        )
        row = cur.fetchone()
    if row is None:
        raise errors.NotFound("unknown thread")
    return row


def _log_outreach(
    conn: Connection,
    user_id: int,
    contact_id: int,
    opportunity_id: int | None,
    message_template_id: int | None,
    kind_override: str | None,
) -> int:
    """Insert the ``outreaches`` row for an emailed touch and return its id.

    The kind is the caller's override when given, else inferred contact-scoped by
    ``core.outreach.resolve_outreach_kind`` — identical to a manually logged touch, so an emailed
    first contact counts toward the outreaches target exactly as a logged one does.
    """
    channel_id = _resolve_catalog_id(conn, "outreach_channels", EMAIL_CHANNEL, "channel")
    has_prior = has_prior_outbound_touch(conn, user_id, contact_id)
    kind = resolve_outreach_kind(has_prior, kind_override)
    kind_id = _resolve_catalog_id(conn, "outreach_kinds", kind, "outreach kind")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO outreaches "
            "(user_id, contact_id, opportunity_id, outreach_kind_id, outreach_channel_id, "
            " message_template_id, occurred_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)",
            (user_id, contact_id, opportunity_id, kind_id, channel_id, message_template_id),
        )
        return cur.lastrowid


def create_pending_send(
    conn: Connection,
    user_id: int,
    data: EmailSendInput,
    *,
    message_id: str,
    from_addr: str,
    s3_key: str | None = None,
    thread_id: int | None = None,
    in_reply_to: str | None = None,
    message_references: str | None = None,
) -> PendingSend:
    """Phase 1 — record the intent to send, inside one transaction.

    Writes the thread (created for a new conversation, reused for a reply), the
    ``email_messages`` row with ``sent_at`` NULL, and the ``outreaches`` touch when the send is
    linked to a contact. Nothing here contacts SES; the caller sends *after* this commits, so a
    crash before the send leaves a pending row rather than a lost one.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection, already inside ``common.db.transaction``.
    user_id : int
        The owning user.
    data : models.emails.EmailSendInput
        The validated composer payload. Only its persisted fields are read here — the body and
        attachments go into the MIME, not into a column.
    message_id : str
        The RFC 5322 ``Message-ID`` minted by ``core.email_headers.generate_message_id``, already
        placed in the outgoing MIME. Stored now so the row is identifiable before the send.
    from_addr : str
        The sending address, as it appears in the MIME ``From`` header.
    s3_key : str or None, optional
        Key of the stored raw MIME; reads reconstruct the body and attachments from it.
    thread_id : int or None, optional
        Existing thread to append to (a reply). ``None`` creates a new thread.
    in_reply_to : str or None, optional
        ``In-Reply-To`` header value, from ``core.email_headers.build_reply_headers``.
    message_references : str or None, optional
        ``References`` header value, from the same call. Stored in ``message_references`` —
        ``references`` is a reserved word in MySQL.

    Returns
    -------
    PendingSend
        The ids written, plus whether the thread was created (compensation needs to know).

    Raises
    ------
    common.errors.InvalidInput
        When the contact, opportunity, or template is not the caller's.
    common.errors.NotFound
        When ``thread_id`` is given but is not a live thread of ``user_id``.
    common.errors.Conflict
        When ``message_id`` is already stored for this user — the ``UNIQUE(user_id, message_id)``
        idempotency key, which a replayed request would collide with.
    """
    validate_contact(conn, user_id, data.contact_id)
    validate_opportunity(conn, user_id, data.opportunity_id)
    validate_message_template(conn, user_id, data.message_template_id)

    thread_created = thread_id is None
    if thread_id is None:
        resolved_thread_id = _create_thread(
            conn, user_id, data.subject, data.contact_id, data.opportunity_id
        )
    else:
        _require_thread(conn, user_id, thread_id)
        resolved_thread_id = thread_id

    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM email_messages WHERE user_id = %s AND message_id = %s",
            (user_id, message_id),
        )
        if cur.fetchone() is not None:
            raise errors.Conflict("message already recorded")

        cur.execute(
            "INSERT INTO email_messages "
            "(user_id, thread_id, contact_id, opportunity_id, message_id, in_reply_to, "
            " message_references, direction, subject, from_addr, to_addr, cc_addr, s3_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'out', %s, %s, %s, %s, %s)",
            (
                user_id,
                resolved_thread_id,
                data.contact_id,
                data.opportunity_id,
                message_id,
                in_reply_to,
                message_references,
                data.subject,
                from_addr,
                ", ".join(data.to),
                ", ".join(data.cc) or None,
                s3_key,
            ),
        )
        message_row_id = cur.lastrowid

    outreach_id = None
    if data.contact_id is not None:
        outreach_id = _log_outreach(
            conn,
            user_id,
            data.contact_id,
            data.opportunity_id,
            data.message_template_id,
            data.outreach_kind,
        )

    return PendingSend(
        message_row_id=message_row_id,
        thread_id=resolved_thread_id,
        outreach_id=outreach_id,
        thread_created=thread_created,
    )


def confirm_send(
    conn: Connection,
    user_id: int,
    message_row_id: int,
    sent_at: datetime | None = None,
    external_message_id: str | None = None,
) -> bool:
    """Phase 3 — mark a pending message sent and advance its thread, inside one transaction.

    Called only after SES has accepted the message. Advancing ``last_message_at`` here rather than
    in phase 1 is what lets compensation be a pure delete.

    This is also the only moment ``external_message_id`` can be recorded: the provider replaces the
    ``Message-ID`` we minted, and does not tell us the substitute until it accepts the message. That
    substitute is what every reply's ``In-Reply-To`` points at, so without it the header chain
    cannot match a single reply to a thread this app originated.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection, already inside ``common.db.transaction``.
    user_id : int
        The owning user.
    message_row_id : int
        ``email_messages.id`` returned by :func:`create_pending_send`.
    sent_at : datetime or None, optional
        Send timestamp; ``None`` uses the database's ``CURRENT_TIMESTAMP``.
    external_message_id : str or None, optional
        The ``Message-ID`` the recipient will see (``common.mail.external_message_id``). ``None``
        leaves the column untouched rather than blanking it, so a retry that lacks the value cannot
        erase one already recorded.

    Returns
    -------
    bool
        Whether a pending row was confirmed. ``False`` means the row was already confirmed or does
        not belong to ``user_id`` — the caller should treat that as a reconciliation signal, not a
        reason to re-send.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE email_messages SET sent_at = COALESCE(%s, CURRENT_TIMESTAMP), "
            "  external_message_id = COALESCE(%s, external_message_id) "
            "WHERE id = %s AND user_id = %s AND direction = 'out' AND sent_at IS NULL",
            (sent_at, external_message_id, message_row_id, user_id),
        )
        if cur.rowcount == 0:
            return False

        cur.execute(
            "UPDATE email_threads t "
            "JOIN email_messages m ON m.thread_id = t.id "
            "SET t.last_direction = 'out', t.last_message_at = m.sent_at "
            "WHERE m.id = %s AND t.user_id = %s",
            (message_row_id, user_id),
        )
    return True


def discard_pending_send(
    conn: Connection,
    user_id: int,
    pending: PendingSend,
) -> None:
    """Compensate for a synchronous SES failure by deleting what phase 1 wrote.

    Safe **only** when the send failed cleanly — SES raised, so nothing was transmitted. Never
    call this after an ambiguous outcome (a timeout): the message may have gone out, and deleting
    the row would recreate exactly the silent loss this design exists to prevent. Leave those
    pending for the poller.

    Deletes in FK order — outreach, message, then the thread if phase 1 created it. Hard deletes,
    because a send that never happened is not history: a soft-deleted ``outreaches`` row would
    still assert a touch that never occurred.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection, already inside ``common.db.transaction``.
    user_id : int
        The owning user.
    pending : PendingSend
        Exactly what :func:`create_pending_send` returned.

    Raises
    ------
    common.errors.Conflict
        If the message has since been confirmed — a confirmed send must never be compensated away.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sent_at FROM email_messages WHERE id = %s AND user_id = %s",
            (pending.message_row_id, user_id),
        )
        row = cur.fetchone()
        if row is not None and row["sent_at"] is not None:
            raise errors.Conflict("message already confirmed sent")

        if pending.outreach_id is not None:
            cur.execute(
                "DELETE FROM outreaches WHERE id = %s AND user_id = %s",
                (pending.outreach_id, user_id),
            )
        cur.execute(
            "DELETE FROM email_messages WHERE id = %s AND user_id = %s AND sent_at IS NULL",
            (pending.message_row_id, user_id),
        )
        if pending.thread_created:
            cur.execute(
                "DELETE FROM email_threads WHERE id = %s AND user_id = %s "
                "AND NOT EXISTS (SELECT 1 FROM email_messages m WHERE m.thread_id = %s)",
                (pending.thread_id, user_id, pending.thread_id),
            )
