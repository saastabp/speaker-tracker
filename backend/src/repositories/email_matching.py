"""Raw-SQL lookups that feed the pure thread matcher — the read half of inbound ingest.

``core.email_threading`` and ``core.email_scope`` decide *which* thread a polled message joins and
*whether* it is ingested at all, but they are pure: they hold no connection and issue no query.
Everything they need arrives as a mapping or a sequence, and this module is where those come from.
The write half is :mod:`repositories.email_inbound`, mirroring how 6a split email reads
(:mod:`repositories.email_threads`) from email writes (:mod:`repositories.email_sends`).

Two deliberate non-features:

- **The fallback's time window is not in the SQL.** ``FALLBACK_WINDOW_DAYS`` (in
  :mod:`core.email_threading`)
  is domain policy, and applying it here too would put the same rule in two places that could
  disagree — with the SQL copy silently winning, since a row this module never returns is a row the
  core can never consider. The candidate set is filtered by subject only; the core applies the
  window, and it is also the only side that handles the naive/aware datetime mismatch correctly.
- **Nothing here writes.** A lookup that "helpfully" backfilled a thread's contact would make a
  pending-import row leave the triage queue without Donna acting, and the queue is only trustworthy
  if it changes when she says so.
"""

from __future__ import annotations

from collections.abc import Sequence

from pymysql.connections import Connection

from core.email_headers import addresses_in, normalize_address
from core.email_threading import ThreadCandidate

#: Most threads offered to the subject fallback for one message. A single user's identical
#: normalized subject ("Speaking inquiry") reaching this many live threads is already pathological.
#:
#: The cap is not silent: it drops the OLDEST candidates, and a dropped thread that would have
#: qualified yields a second thread on the same conversation — the cosmetic failure the fallback's
#: design already prefers over merging two venues' mail. It can never cause a wrong match, because
#: every candidate still has to pass the counterpart and window checks in the core.
CANDIDATE_LIMIT = 50


def _placeholders(count: int) -> str:
    """Return ``%s, %s, ...`` for an ``IN`` clause of `count` bound parameters."""
    return ", ".join(["%s"] * count)


def threads_by_message_id(
    conn: Connection, user_id: int, message_ids: Sequence[str]
) -> dict[str, int]:
    """Map stored ``Message-ID`` headers to the threads they belong to (acceptance #1).

    This is the header-chain half of ``core.email_threading.resolve_thread`` — the only strategy
    DESIGN.md relies on for correctness. The caller passes the whole ancestor chain from
    ``candidate_ancestors`` and gets back only the ids we actually stored, in one query rather than
    one per ancestor.

    **Two columns are searched, because an outbound message has two identities.** We mint
    ``message_id`` before sending, but the provider *replaces* that header on the way out and the
    recipient only ever sees ``external_message_id`` — so an external reply chains against the
    latter while an internal one (a reply composed in the mailbox itself, against the Sent-folder
    copy we appended) chains against the former. Searching only ``message_id`` is what silently
    broke threading for every reply from outside: the chain matched nothing, and the message
    survived only if its sender happened to be a tracked contact.

    Keys are returned in whichever form was matched, so the caller's map is keyed by the id it
    actually asked about. Both are stored **bracketed** — the form
    ``core.email_headers.generate_message_id`` mints, ``bracketed`` canonicalizes, and
    ``parse_message_ids`` extracts — so both sides of the lookup agree; a bare id would simply
    never match, and the resulting mis-thread would be invisible.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user; another account's threads are never visible.
    message_ids : sequence of str
        Bracketed msg-ids from the message's ``In-Reply-To`` / ``References`` chain. An empty
        sequence returns ``{}`` without issuing a query — ``IN ()`` is a syntax error in MySQL, and
        a message with no chain at all is the ordinary case for a first contact.

    Returns
    -------
    dict of str to int
        Stored ``message_id`` to ``thread_id``, containing only ids we know.
    """
    if not message_ids:
        return {}
    placeholders = _placeholders(len(message_ids))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT message_id, external_message_id, thread_id FROM email_messages "
            f"WHERE user_id = %s AND (message_id IN ({placeholders}) "
            f"                        OR external_message_id IN ({placeholders}))",
            (user_id, *message_ids, *message_ids),
        )
        rows = cur.fetchall()

    wanted = set(message_ids)
    found: dict[str, int] = {}
    for row in rows:
        # Key by whichever identity the caller asked about — a row can match on either column, and
        # returning the other one would leave `match_by_headers` unable to find its own lookup.
        for candidate in (row["message_id"], row["external_message_id"]):
            if candidate in wanted:
                found[candidate] = row["thread_id"]
    return found


def contacts_by_address(conn: Connection, user_id: int, addresses: Sequence[str]) -> dict[str, int]:
    """Map email addresses to the contacts that own them, for the scope decision.

    ``core.email_scope.classify_message`` uses this to answer "is this correspondence with someone
    we track" — the check that keeps the app from ingesting the whole mailbox (acceptance #2).

    Matching leans on the schema in two ways worth stating, because both are load-bearing and
    invisible at the call site. The ``IN`` runs against ``ix_contacts_user_email (user_id, email)``,
    and the column's ``utf8mb4_0900_ai_ci`` collation makes the comparison case-insensitive — which
    is what lets a caller pass lowercased addresses and still match a contact stored as
    ``Bob@Venue.com``. Keys are re-normalized from the stored value on the way out so the returned
    mapping is keyed identically to what the core looks up.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    addresses : sequence of str
        Normalized bare addresses (``core.email_headers.normalize_address`` /
        :func:`~core.email_headers.addresses_in`). Empty returns ``{}`` without a query.

    Returns
    -------
    dict of str to int
        Normalized address to ``contacts.id``. Soft-deleted contacts are excluded: mail from
        someone Donna deleted should stop being ingested, not keep flowing to a hidden record.
    """
    if not addresses:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, email FROM contacts WHERE user_id = %s AND deleted_at IS NULL "
            f"AND email IN ({_placeholders(len(addresses))})",
            (user_id, *addresses),
        )
        rows = cur.fetchall()
    resolved: dict[str, int] = {}
    for row in rows:
        key = normalize_address(row["email"])
        # First contact wins on a duplicated address. Two contacts sharing one address is a data
        # problem slice 2's dedupe exists to prevent; picking deterministically (lowest id, from
        # the ORDER BY absent here — insertion order under the index) beats raising inside a poll.
        if key and key not in resolved:
            resolved[key] = row["id"]
    return resolved


def fallback_candidates(
    conn: Connection,
    user_id: int,
    subject_normalized: str,
    *,
    limit: int = CANDIDATE_LIMIT,
) -> list[ThreadCandidate]:
    """Return threads the subject fallback may join a message to (acceptance #10).

    For clients that drop or mangle ``References``. The core requires an identical normalized
    subject **and** a shared counterpart address **and** a recent enough thread; this supplies the
    subject match and the addresses, and leaves the rest to
    ``core.email_threading.match_by_subject``.

    **Closed threads are excluded.** A header-chain match still joins a closed thread — the chain is
    proof — but the fallback is a guess, and a wrong guess would silently resurrect a conversation
    Donna deliberately ended. Starting a new thread instead is the recoverable direction.

    Threads whose ``last_message_at`` is NULL are excluded too: those are unconfirmed pending sends,
    and the core cannot window-check a thread with no anchor.

    ``counterpart_addresses`` is a superset — it holds every address on the thread, including
    Donna's own. That is safe rather than sloppy: the caller strips her addresses from the *message*
    side before calling the core, and the core intersects the two, so an address that only ever
    appears on the candidate side cannot produce a match.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    subject_normalized : str
        The incoming message's subject after ``core.email_headers.normalize_subject``. Blank
        returns ``[]`` — every blank subject normalizes alike, so matching on one would join
        unrelated conversations.
    limit : int, optional
        Override for :data:`CANDIDATE_LIMIT`.

    Returns
    -------
    list of ThreadCandidate
        Most recently active first.
    """
    if not subject_normalized:
        return []

    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.id, t.subject_normalized, t.last_message_at, c.email AS contact_email "
            "FROM email_threads t "
            "LEFT JOIN contacts c ON c.id = t.contact_id "
            "WHERE t.user_id = %s AND t.deleted_at IS NULL AND t.closed_at IS NULL "
            "  AND t.subject_normalized = %s AND t.last_message_at IS NOT NULL "
            "ORDER BY t.last_message_at DESC LIMIT %s",
            (user_id, subject_normalized, limit),
        )
        threads = cur.fetchall()

    if not threads:
        return []

    thread_ids = [row["id"] for row in threads]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thread_id, from_addr, to_addr, cc_addr FROM email_messages "
            f"WHERE user_id = %s AND thread_id IN ({_placeholders(len(thread_ids))})",
            (user_id, *thread_ids),
        )
        message_rows = cur.fetchall()

    # Gathered in Python rather than with GROUP_CONCAT, whose 1024-byte default would truncate a
    # long thread's address list silently — and a silently short candidate set is a silently
    # missed match.
    addresses_by_thread: dict[int, list[str]] = {thread_id: [] for thread_id in thread_ids}
    for row in message_rows:
        bucket = addresses_by_thread[row["thread_id"]]
        for address in addresses_in(row["from_addr"], row["to_addr"], row["cc_addr"]):
            if address not in bucket:
                bucket.append(address)

    candidates: list[ThreadCandidate] = []
    for row in threads:
        bucket = addresses_by_thread[row["id"]]
        contact_email = normalize_address(row["contact_email"])
        if contact_email and contact_email not in bucket:
            bucket.append(contact_email)
        candidates.append(
            ThreadCandidate(
                thread_id=row["id"],
                subject_normalized=row["subject_normalized"],
                counterpart_addresses=tuple(bucket),
                last_message_at=row["last_message_at"],
            )
        )
    return candidates
