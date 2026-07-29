"""Raw-SQL for the pending-import queue and the two link actions that empty it.

A "pending import" is not a table — it is an ``email_threads`` row whose ``contact_id`` is NULL
(DESIGN.md §3), holding mail the poller was authorized to ingest but which is not yet attached to
anyone we track. It arrives there one of two ways: Donna drags a stranger's message into the
``Import`` folder (the drag is the authorization), or an inbound message's header chain joins a
stored thread that itself has no contact.

Everything here is driven by an explicit human action. The poller deliberately never links a
thread to a contact on its own (:mod:`repositories.email_inbound`), so a row leaves this queue
because Donna said so — which is the only reason the queue's count is worth showing her.

Two shape notes:

- **The queue shows the earliest INBOUND message of each thread.** That is the one whose ``From``
  prefills Add Contact (acceptance #4). A contactless thread with no inbound message at all is not
  awaiting import — it is an unlinked send — so it is excluded rather than listed with Donna's own
  address as the sender to identify.
- **The organization suggestion is resolved in Python, not SQL.** ``email_messages.from_addr``
  stores the full header form (``Bob Venue <bob@venue.com>``), and picking the domain out of that
  in SQL means nested ``SUBSTRING_INDEX`` calls that are wrong in ways nobody notices. The address
  parser that already exists is used instead, and the domains go back to the database as an
  ordinary ``IN``.
"""

from __future__ import annotations

from email.utils import parseaddr

from pymysql.connections import Connection

from common.logger import logger
from core.email_headers import normalize_address
from repositories._ownership import validate_contact, validate_opportunity

#: Selects each contactless thread together with its earliest inbound message. ROW_NUMBER is used
#: rather than a correlated subquery so the message columns come back in the same pass; the
#: ordering mirrors ``repositories.email_threads.list_messages`` (oldest first, id breaking ties)
#: so "the first message" means the same thing in the queue as it does in the thread view.
_PENDING_SQL = (
    "SELECT t.id AS thread_id, m.id AS email_message_id, m.from_addr, m.subject, m.received_at "
    "FROM email_threads t "
    "JOIN ("
    "  SELECT thread_id, id, from_addr, subject, received_at, "
    "         ROW_NUMBER() OVER ("
    "           PARTITION BY thread_id "
    "           ORDER BY received_at IS NULL, received_at ASC, id ASC"
    "         ) AS rn "
    "  FROM email_messages "
    "  WHERE user_id = %s AND direction = 'in'"
    ") m ON m.thread_id = t.id AND m.rn = 1 "
    "WHERE t.user_id = %s AND t.contact_id IS NULL AND t.deleted_at IS NULL "
    "  AND t.closed_at IS NULL "
    "ORDER BY m.received_at IS NULL, m.received_at DESC, t.id DESC"
)


def _domain_of(address: str) -> str:
    """Return the lowercased domain of a bare address, or ``""`` when there is none."""
    _, _, domain = address.partition("@")
    return domain.strip().lower()


def _suggested_organizations(conn: Connection, user_id: int, domains: set[str]) -> dict[str, dict]:
    """Map sender domains to the organization that claims them, for the Add Contact prefill.

    Runs against ``ix_organizations_user_email_domain (user_id, email_domain)``, present in
    ``0002`` for exactly this flow. A stored ``email_domain`` is user-entered free text, so it is
    normalized on the way out rather than trusted to already be lowercase and bare.

    **An ambiguous domain suggests nothing.** If two or more organizations claim the same domain,
    that domain is not identifying anyone and the suggestion is withheld. The property this feature
    actually needs is "does this domain uniquely identify this venue", and a shared domain answers
    no — a conference centre and its catering arm, a parent company hosting several venues, or a
    university address shared by thousands. Picking one deterministically would be a coin flip
    presented as knowledge, and the whole point of the prefill is that Donna can trust it enough
    not to check it.

    That is also the honest defence against consumer domains. A ``gmail.com`` blocklist catches the
    loud cases and misses the quiet ones — ``stanford.edu`` appears on no freemail list and is
    shared by twenty thousand people — whereas ambiguity is the symptom every one of them has in
    common as soon as a second venue claims the domain. A blocklist remains worth adding as a fast
    reject for the *first* such venue; see the deferred domain-learning work.
    """
    if not domains:
        return {}
    placeholders = ", ".join(["%s"] * len(domains))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, email_domain FROM organizations "
            f"WHERE user_id = %s AND deleted_at IS NULL AND email_domain IN ({placeholders})",
            (user_id, *domains),
        )
        rows = cur.fetchall()

    by_domain: dict[str, list[dict]] = {}
    for row in rows:
        key = (row["email_domain"] or "").strip().lower().lstrip("@")
        if key:
            by_domain.setdefault(key, []).append(row)

    suggestions: dict[str, dict] = {}
    for key, claimants in by_domain.items():
        if len(claimants) > 1:
            logger.warning(
                "Domain %r is claimed by %d organizations (%s); suggesting none, because a shared "
                "domain identifies nobody",
                key,
                len(claimants),
                ", ".join(str(row["id"]) for row in claimants),
            )
            continue
        suggestions[key] = claimants[0]
    return suggestions


def list_pending_imports(conn: Connection, user_id: int) -> list[dict]:
    """Return threads awaiting import, newest first — the badge's contents (acceptance #3, #4).

    The badge count is the length of this list; there is no separate count endpoint, because the
    queue holds a handful of rows and a second query could disagree with the first.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.

    Returns
    -------
    list of dict
        Rows shaped for ``models.email_inbound.PendingImportSummary``: ``thread_id``,
        ``email_message_id``, the sender split into ``from_addr`` / ``from_name``, ``subject``,
        ``received_at``, and the organization suggested by the sender's domain (``None`` when no
        venue claims it).
    """
    with conn.cursor() as cur:
        cur.execute(_PENDING_SQL, (user_id, user_id))
        rows = cur.fetchall()
    if not rows:
        return []

    parsed: list[dict] = []
    for row in rows:
        raw_from = row["from_addr"] or ""
        address = normalize_address(raw_from)
        # parseaddr returns ("", address) for a bare address, so an empty display name means the
        # sender supplied none — not that parsing failed. Used rather than splitting on "<" by
        # hand, which gets quoted names containing angle brackets wrong.
        display_name, _ = parseaddr(raw_from)
        parsed.append(
            {
                "thread_id": row["thread_id"],
                "email_message_id": row["email_message_id"],
                "from_addr": address,
                "from_name": display_name.strip() or None,
                "subject": row["subject"],
                "received_at": row["received_at"],
                "_domain": _domain_of(address),
            }
        )

    suggestions = _suggested_organizations(
        conn, user_id, {row["_domain"] for row in parsed if row["_domain"]}
    )
    for row in parsed:
        organization = suggestions.get(row.pop("_domain"))
        row["suggested_organization_id"] = organization["id"] if organization else None
        row["suggested_organization_name"] = organization["name"] if organization else None
    return parsed


def _owns_thread(conn: Connection, user_id: int, thread_id: int) -> bool:
    """Return whether `thread_id` is a live thread belonging to `user_id`.

    Checked before the UPDATE rather than inferred from its ``rowcount``, because MySQL reports
    rows *changed*, not matched: re-linking a thread to the contact it already has would change
    nothing, report 0, and be indistinguishable from a thread that does not exist. Linking is an
    idempotent statement of fact, so saying it twice must succeed twice.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM email_threads WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (thread_id, user_id),
        )
        return cur.fetchone() is not None


def link_contact(conn: Connection, user_id: int, thread_id: int, contact_id: int | None) -> bool:
    """Attach a contact to a thread and its unattributed messages, or detach (acceptance #4).

    The thread is what every read actually uses — ``repositories.email_threads`` joins ``contacts``
    through ``email_threads.contact_id``, and no query anywhere selects the message-level column
    yet. Setting the thread is therefore what makes the conversation appear on the contact's page.

    Messages are filled in **only where they carry no contact of their own**, which is the whole
    conversation in the ordinary case and matters for the case that is not ordinary. A message's
    contact has an independent source of truth: ingest derives it from who actually sent or received
    that message. So a thread can legitimately hold a message attributed to someone else — a second
    tracked contact looped into an unimported thread and replying puts their own id on their reply
    while the thread stays contactless and stays in this queue. Overwriting that would record their
    message as having come from whoever Donna links, which is simply false. Filling only the blanks
    attributes the import without destroying evidence.

    The contact must already exist. Creating one is ``POST /contacts``, which routes through slice
    2's dedupe — and offering to attach an existing person rather than creating a duplicate *is*
    that dedupe, so putting a second creation path here would defeat it.

    **Detaching (``contact_id=None``) returns the thread to the pending-import queue**, which is
    the correction for having linked the wrong person. It clears the thread's contact and the
    contact on messages that were filled *by a previous link* — recognised as those whose contact
    matches the thread's — while leaving alone any message whose contact ingest derived
    independently. Undoing a link must not erase evidence the link never touched.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection, inside the caller's transaction.
    user_id : int
        The owning user.
    thread_id : int
        Thread to attach.
    contact_id : int or None
        An existing contact of this user, or ``None`` to detach.

    Returns
    -------
    bool
        ``False`` when the thread does not exist, is soft-deleted, or is not the caller's — the
        handler maps that to 404.

    Raises
    ------
    common.errors.InvalidInput
        When `contact_id` is not a live contact of this user.
    """
    # `validate_contact` passes None through, so detaching needs no special case here.
    validate_contact(conn, user_id, contact_id)
    if not _owns_thread(conn, user_id, thread_id):
        return False

    with conn.cursor() as cur:
        # Read the outgoing contact before overwriting it: on a detach it is the only way to tell
        # which messages a previous link filled from those ingest attributed independently.
        cur.execute(
            "SELECT contact_id FROM email_threads WHERE id = %s AND user_id = %s",
            (thread_id, user_id),
        )
        previous_contact_id = cur.fetchone()["contact_id"]

        cur.execute(
            "UPDATE email_threads SET contact_id = %s WHERE id = %s AND user_id = %s",
            (contact_id, thread_id, user_id),
        )

        if contact_id is None:
            # Clear only what a previous link set. A message whose contact differs from the
            # thread's came from ingest — a second tracked contact replying into this thread — and
            # undoing Donna's link must not erase a fact her link never asserted.
            cur.execute(
                "UPDATE email_messages SET contact_id = NULL "
                "WHERE thread_id = %s AND user_id = %s AND contact_id = %s",
                (thread_id, user_id, previous_contact_id),
            )
        else:
            cur.execute(
                "UPDATE email_messages SET contact_id = %s "
                "WHERE thread_id = %s AND user_id = %s AND contact_id IS NULL",
                (contact_id, thread_id, user_id),
            )
    return True


def link_opportunity(
    conn: Connection, user_id: int, thread_id: int, opportunity_id: int | None
) -> bool:
    """Attach a thread to a gig, or detach it by passing ``None``.

    This control is not a convenience. An inbound-first thread is created with
    ``opportunity_id`` NULL unconditionally — a contact having exactly one open gig is not evidence
    that a given email concerns it, and misfiling side-channel mail against the wrong gig is worse
    than leaving it unattached. The consequence is that nothing else can ever give such a thread an
    opportunity, so this is the only path.

    The thread's messages are updated with it — **all** of them, unlike :func:`link_contact`, which
    fills only the blanks. The asymmetry is deliberate and rests on where each column's truth comes
    from. A message's *contact* is independently derived at ingest from who sent or received it, so
    it can differ from the thread's and must not be overwritten. A message's *opportunity* has no
    such source: nothing may infer a gig from a message, so ingest only ever copies the thread's.
    The thread is the sole authority, which makes a blanket update correct here — and necessary on
    detach, since filling only blanks would strand messages pointing at a gig their thread has left.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection, inside the caller's transaction.
    user_id : int
        The owning user.
    thread_id : int
        Thread to attribute.
    opportunity_id : int or None
        Gig to attribute it to, or ``None`` to detach.

    Returns
    -------
    bool
        ``False`` when the thread does not exist, is soft-deleted, or is not the caller's.

    Raises
    ------
    common.errors.InvalidInput
        When `opportunity_id` is given and is not a live opportunity of this user.
    """
    validate_opportunity(conn, user_id, opportunity_id)
    if not _owns_thread(conn, user_id, thread_id):
        return False
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE email_threads SET opportunity_id = %s WHERE id = %s AND user_id = %s",
            (opportunity_id, thread_id, user_id),
        )
        cur.execute(
            "UPDATE email_messages SET opportunity_id = %s WHERE thread_id = %s AND user_id = %s",
            (opportunity_id, thread_id, user_id),
        )
    return True
