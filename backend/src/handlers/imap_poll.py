"""Lambda entrypoint for the IMAP poller — the loop that brings inbound mail into the app.

Runs on an EventBridge ``rate(1 minute)`` schedule with **reserved concurrency 1**, so two
invocations can never process the same message (DEV-PLAN slice 6b acceptance #7). Every decision it
makes was made elsewhere: this module is assembly, and deliberately holds no domain logic. Per
folder it walks

    select_folder → plan_cursor → search_uids_above → cap_uids → fetch_messages
      → parse_headers → resolve_thread → classify_message → ingest_message
      → (Import only) move_uids
      → save_cursor

and returns a :class:`core.imap_cursor.PollSummary` for each.

**Three folders are polled, and the set is configuration rather than three hardcoded names.**
``INBOX``; the Sent folder, located by its ``\\Sent`` SPECIAL-USE flag because it is called ``Sent
Items`` on this mailbox and no folder named ``Sent`` exists; and ``Speaker Tracker/Import``.
``Processed`` is a *destination*, never a source — polling it would re-read everything the app has
already handled.

**Failure handling distinguishes two kinds of bad day, structurally rather than textually**
(acceptance #11, the project's worst failure mode — a poller that keeps running, finds nothing, and
stops threading mail with no error anywhere):

- :class:`~common.imap.ImapAuthError`, after one retry with refreshed credentials, is **allowed to
  propagate**. The invocation fails, the Lambda ``Errors`` metric ticks, and the alarm emails
  Brian. A rotated password is the likely cause and it will not fix itself.
- Everything else — an unreachable host, a timed-out socket, one folder the server will not open —
  is logged at WARNING and left for the next minute. A minute of missed mail costs nothing; paging
  on transient network noise would train everyone to ignore the alarm that matters.

**Ordering that is not interchangeable.** Each message is ingested and committed *before* it is
moved out of ``Import``. Moving first would put the message in ``Processed`` — which is never
polled — with no row to show for it, and it would be gone for good. In the other order a failed
move simply leaves the message in ``Import`` for the next poll, where ingest recognizes it as a
duplicate and the move is retried.

**Who "we" are.** ``own_addresses`` is the sending address and the IMAP username, both of which
identify this mailbox by definition. ``users.email`` is deliberately *not* included: it comes from
Cognito and may be a different address entirely, and mail arriving *from* it would then be
classified as outbound — inventing a message Donna never sent.
"""

from __future__ import annotations

import datetime as dt
import os
import time
from typing import NamedTuple

from aws_lambda_powertools.utilities.typing import LambdaContext
from imapclient import IMAPClient
from pymysql.connections import Connection

from common import imap, imap_poll, mail_parse
from common.db import get_connection, transaction
from common.logger import logger
from common.secrets import get_imap_credentials
from core.email_headers import addresses_in, normalize_address, normalize_subject
from core.email_scope import (
    FOLDER_IMPORT,
    FOLDER_INBOX,
    FOLDER_SENT,
    FolderKind,
    classify_message,
)
from core.email_threading import candidate_ancestors, resolve_thread
from core.imap_cursor import PollSummary, cap_uids, plan_cursor
from repositories import email_inbound, email_matching, email_sends, imap_cursors, users

#: The poller stores and compares everything in UTC; it has no user session to take a zone from.
POLL_TIMEZONE = "UTC"

#: Env var holding the sending address. Read directly rather than through ``common.mail`` so an
#: unset value degrades to "we know one of our addresses" instead of failing the whole poll.
MAIL_FROM_ENV = "MAIL_FROM_ADDRESS"

#: Stand-in anchor for a message carrying neither a parseable ``Date`` nor an ``INTERNALDATE``.
#: ``resolve_thread`` requires a timestamp, but such a message is never offered the subject
#: fallback — an empty candidate list is passed alongside — so this value is only ever compared
#: against nothing. It exists to keep the call total rather than to stand for a real time.
_NO_TIMESTAMP_ANCHOR = dt.datetime(1970, 1, 1)


class _PolledFolder(NamedTuple):
    """A folder to poll: its name on the server and which scoping rules apply to it."""

    name: str
    kind: FolderKind


def _own_addresses(imap_username: str) -> set[str]:
    """Return the addresses that identify this mailbox, normalized.

    See the module docstring for why ``users.email`` is excluded. An unset ``MAIL_FROM_ADDRESS``
    is a deployment fault, but not one worth failing a poll over — the IMAP username alone still
    identifies the mailbox — so it logs a WARNING and carries on.
    """
    addresses = {normalize_address(imap_username)}
    configured = os.environ.get(MAIL_FROM_ENV)
    if configured:
        addresses.add(normalize_address(configured))
    else:
        logger.warning(
            "%s is unset; falling back to the IMAP username alone to recognise our own mail",
            MAIL_FROM_ENV,
        )
    return {address for address in addresses if address}


def _folders_to_poll(client: IMAPClient) -> tuple[list[_PolledFolder], str]:
    """Resolve the polled folders and the move destination.

    ``ensure_app_folders`` runs on **every** poll, not once at deploy: acceptance #13 requires that
    deleting the Import folder and re-polling recreates it, and the call is idempotent. It is
    called exactly once here, and both of its results are used — the Import folder is polled, the
    Processed folder is only ever moved into.
    """
    import_folder, processed_folder = imap.ensure_app_folders(client)
    folders = [
        _PolledFolder("INBOX", FOLDER_INBOX),
        _PolledFolder(imap.find_sent_folder(client), FOLDER_SENT),
        _PolledFolder(import_folder, FOLDER_IMPORT),
    ]
    return folders, processed_folder


def _ingest_one(
    conn: Connection,
    user_id: int,
    folder: _PolledFolder,
    fetched: imap_poll.FetchedMessage,
    own_addresses: set[str],
) -> str:
    """Decide on one fetched message and write it if it is in scope.

    Returns
    -------
    str
        ``"ingested"``, ``"duplicate"``, or ``"skipped"`` — the counters
        :class:`core.imap_cursor.PollSummary` reports.
    """
    headers = mail_parse.parse_headers(fetched.raw)
    if not headers.message_id:
        # Without a Message-ID there is no idempotency key, so re-reading the folder would insert
        # the message again. Skipping is the conservative choice; such mail is malformed.
        logger.warning(
            "Message uid=%d in %s has no Message-ID; skipping (no idempotency key)",
            fetched.uid,
            folder.name,
        )
        return "skipped"

    occurred_at = headers.date or fetched.internaldate
    message_addresses = addresses_in(headers.from_addr, headers.to_addr, headers.cc_addr)
    contact_by_address = email_matching.contacts_by_address(conn, user_id, message_addresses)

    # The ancestor chain is resolved in one query rather than one per ancestor;
    # `candidate_ancestors` orders it nearest-first, which is what `match_by_headers` relies on.
    chain = email_matching.threads_by_message_id(
        conn,
        user_id,
        candidate_ancestors(headers.in_reply_to, headers.references),
    )
    # The subject fallback windows against a timestamp. A message with neither a parseable Date nor
    # an INTERNALDATE gets header matching only — offering it the fallback with an invented anchor
    # would let it join a thread on subject alone, which is the merge the fallback's guards exist
    # to prevent.
    candidates = (
        email_matching.fallback_candidates(conn, user_id, normalize_subject(headers.subject))
        if occurred_at is not None
        else ()
    )
    counterparts = [address for address in message_addresses if address not in own_addresses]
    match = resolve_thread(
        in_reply_to=headers.in_reply_to,
        references=headers.references,
        subject=headers.subject,
        counterpart_addresses=counterparts,
        occurred_at=occurred_at or _NO_TIMESTAMP_ANCHOR,
        thread_by_message_id=chain,
        candidates=candidates,
    )

    decision = classify_message(
        folder_kind=folder.kind,
        from_addr=headers.from_addr,
        to_addrs=headers.to_addr,
        cc_addrs=headers.cc_addr,
        matched_thread_id=match.thread_id,
        contact_by_address=contact_by_address,
        own_addresses=own_addresses,
    )
    if not decision.ingest:
        logger.info(
            "Skipped uid=%d folder=%s reason=%s direction=%s",
            fetched.uid,
            folder.name,
            decision.reason,
            decision.direction,
        )
        return "skipped"

    message = email_inbound.InboundMessage(
        message_id=headers.message_id,
        from_addr=headers.from_addr,
        to_addr=headers.to_addr,
        cc_addr=headers.cc_addr,
        subject=headers.subject,
        in_reply_to=headers.in_reply_to,
        message_references=headers.references,
        occurred_at=occurred_at,
        imap_folder=folder.name,
        imap_uid=fetched.uid,
    )
    with transaction(conn):
        result = email_inbound.ingest_message(
            conn,
            user_id,
            message,
            direction=decision.direction,
            contact_id=decision.contact_id,
            thread_id=match.thread_id,
        )
        if result.pending:
            # One of our own sends, seen coming back through Sent: the process died between SES
            # accepting it and phase 3. Repositories here do not call one another, so the
            # reconciliation is the handler's to perform.
            confirmed = email_sends.confirm_send(conn, user_id, result.message_row_id)
            logger.info(
                "Reconciled a pending send message_row_id=%d confirmed=%s",
                result.message_row_id,
                confirmed,
            )

    logger.info(
        "Ingested uid=%d folder=%s duplicate=%s thread_id=%d thread_created=%s reason=%s/%s",
        fetched.uid,
        folder.name,
        result.duplicate,
        result.thread_id,
        result.thread_created,
        match.reason,
        decision.reason,
    )
    return "duplicate" if result.duplicate else "ingested"


def _poll_folder(
    conn: Connection,
    client: IMAPClient,
    user_id: int,
    folder: _PolledFolder,
    *,
    own_addresses: set[str],
    processed_folder: str,
) -> PollSummary:
    """Poll one folder end to end and return what it did.

    The Import folder is selected **writable**, because its messages are moved out; INBOX and Sent
    are opened read-only so a background job cannot alter a mailbox it does not own.
    """
    status = imap_poll.select_folder(client, folder.name, readonly=folder.kind != FOLDER_IMPORT)
    stored = imap_cursors.get_cursor(conn, user_id, folder.name)
    plan = plan_cursor(
        stored_uid_validity=None if stored is None else stored["uid_validity"],
        stored_last_seen_uid=None if stored is None else stored["last_seen_uid"],
        server_uid_validity=status.uid_validity,
        server_uid_next=status.uid_next,
    )

    if plan.baseline:
        # First sight of this folder: record where "new" begins and read nothing. Starting at UID 0
        # would import years of unrelated personal mail.
        _save(conn, user_id, folder.name, status.uid_validity, plan.floor_uid)
        return PollSummary(
            folder=folder.name,
            reason=plan.reason,
            floor_uid=plan.floor_uid,
            examined=0,
            remaining=0,
            ingested=0,
            duplicates=0,
            skipped=0,
            moved=0,
            last_seen_uid=plan.floor_uid,
        )

    available = imap_poll.search_uids_above(client, plan.floor_uid)
    uids = cap_uids(available, plan.floor_uid)
    remaining = len(available) - len(uids)
    if remaining:
        logger.info(
            "Capped this poll folder=%s taking=%d deferring=%d to later polls",
            folder.name,
            len(uids),
            remaining,
        )

    counts = {"ingested": 0, "duplicate": 0, "skipped": 0}
    moved = 0
    last_seen = plan.floor_uid

    for fetched in imap_poll.fetch_messages(client, uids):
        try:
            counts[_ingest_one(conn, user_id, folder, fetched, own_addresses)] += 1
        except Exception:
            # Stop at the first failure rather than skipping past it: the watermark has only
            # advanced to the previous message, so this one is retried next minute instead of
            # being silently stranded below a cursor that moved on without it.
            logger.exception(
                "Failed on uid=%d folder=%s; leaving the cursor at %d for the next poll",
                fetched.uid,
                folder.name,
                last_seen,
            )
            break

        if folder.kind == FOLDER_IMPORT:
            # Only after the row is committed: a move that outran the write would file the message
            # into Processed, which is never polled, with nothing to show for it.
            moved += imap_poll.move_uids(client, [fetched.uid], processed_folder)
        last_seen = fetched.uid

    _save(conn, user_id, folder.name, status.uid_validity, last_seen)
    return PollSummary(
        folder=folder.name,
        reason=plan.reason,
        floor_uid=plan.floor_uid,
        examined=len(uids),
        remaining=remaining,
        ingested=counts["ingested"],
        duplicates=counts["duplicate"],
        skipped=counts["skipped"],
        moved=moved,
        last_seen_uid=last_seen,
    )


def _save(
    conn: Connection,
    user_id: int,
    folder_name: str,
    uid_validity: int,
    last_seen_uid: int,
) -> None:
    """Persist the watermark in its own transaction, so it survives whatever the loop did."""
    with transaction(conn):
        imap_cursors.save_cursor(
            conn,
            user_id,
            folder_name,
            uid_validity=uid_validity,
            last_seen_uid=last_seen_uid,
        )


def _poll_all(conn: Connection, user_id: int, *, refresh_credentials: bool) -> list[PollSummary]:
    """Open one IMAP connection and poll every folder through it."""
    credentials = get_imap_credentials(refresh=refresh_credentials)
    own_addresses = _own_addresses(credentials.username)
    summaries: list[PollSummary] = []

    with imap.connection(refresh_credentials=refresh_credentials) as client:
        folders, processed_folder = _folders_to_poll(client)
        for folder in folders:
            try:
                summaries.append(
                    _poll_folder(
                        conn,
                        client,
                        user_id,
                        folder,
                        own_addresses=own_addresses,
                        processed_folder=processed_folder,
                    )
                )
            except imap.ImapAuthError:
                # Never downgraded to a per-folder problem — this is the alarm path.
                raise
            except Exception:
                # One unreadable folder must not stop the other two: a reply in INBOX still
                # threads even while Import is misbehaving.
                logger.exception(
                    "Folder %s failed this poll; continuing with the rest", folder.name
                )
    return summaries


@logger.inject_lambda_context(log_event=False)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """Poll the mailbox once and return what each folder did.

    Parameters
    ----------
    event : dict
        The EventBridge scheduled event. Unused — what to poll is configuration, not payload.
    context : LambdaContext
        ``aws_request_id`` is the correlation id tying the entry and exit lines together.

    Returns
    -------
    dict
        ``{"status": ..., "folders": [...]}``. ``status`` is ``"ok"``, ``"no_user"`` when nobody
        has signed in yet, or ``"transient_error"`` when the mailbox was unreachable this minute.

    Raises
    ------
    common.imap.ImapAuthError
        When the credentials are rejected twice, the second time with a freshly fetched secret.
        Allowed to fail the invocation on purpose: it ticks the Lambda ``Errors`` metric, which is
        what the SNS alarm watches (acceptance #11).
    repositories.users.MultipleUsersError
        When more than one user exists and the poller cannot tell whose mailbox this is.
    """
    correlation_id = context.aws_request_id
    start = time.monotonic()
    logger.info("Poll start correlation_id=%s", correlation_id)

    conn = get_connection(POLL_TIMEZONE)
    user_id = users.resolve_solo_user_id(conn)
    if user_id is None:
        # Already logged at WARNING by the resolver. Expected on a fresh deploy.
        logger.info(
            "Poll end correlation_id=%s status=no_user duration_ms=%s",
            correlation_id,
            _elapsed_ms(start),
        )
        return {"status": "no_user", "folders": []}

    try:
        summaries = _poll_all(conn, user_id, refresh_credentials=False)
    except imap.ImapAuthError:
        logger.warning(
            "IMAP auth rejected correlation_id=%s; retrying once with a freshly fetched secret",
            correlation_id,
        )
        try:
            summaries = _poll_all(conn, user_id, refresh_credentials=True)
        except imap.ImapAuthError:
            logger.exception(
                "Poll failed correlation_id=%s status=auth_rejected duration_ms=%s — "
                "credentials rejected after refresh; inbound mail is NOT being processed",
                correlation_id,
                _elapsed_ms(start),
            )
            raise
    except (imap.ImapError, OSError):
        # Transient by construction: anything that is not an auth failure. The next minute retries.
        logger.warning(
            "Poll skipped correlation_id=%s status=transient_error duration_ms=%s",
            correlation_id,
            _elapsed_ms(start),
            exc_info=True,
        )
        return {"status": "transient_error", "folders": []}

    logger.info(
        "Poll end correlation_id=%s status=ok duration_ms=%s folders=%s",
        correlation_id,
        _elapsed_ms(start),
        [summary._asdict() for summary in summaries],
    )
    return {"status": "ok", "folders": [summary._asdict() for summary in summaries]}


def _elapsed_ms(start: float) -> float:
    """Milliseconds since `start`, rounded, for the paired entry/exit log lines."""
    return round((time.monotonic() - start) * 1000, 1)
