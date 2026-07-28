"""Pending-import router — the triage queue the poller fills and Donna empties.

Three routes, all owner-scoped through :func:`handlers.context.authenticate`:

- ``GET /emails/imports`` — threads awaiting import. Its length **is** the badge count; there is
  no separate count endpoint, because the queue holds a handful of rows and a second query could
  disagree with the first.
- ``PUT /emails/threads/{id}/contact`` — attach an existing contact (DEV-PLAN slice 6b #4).
- ``PUT /emails/threads/{id}/opportunity`` — attach a gig, or detach with ``null``.

**Both links are ``PUT``, not ``POST``, and that is a claim about behaviour rather than taste.**
The sibling thread routes (``/read``, ``/close``, ``/reopen``) are ``POST`` because they are verbs
whose second application is a no-op *failure* — closing a closed thread 404s. These two set a
property, and setting it to the value it already has succeeds; ``repositories.email_imports``
checks ownership with a ``SELECT`` rather than reading the ``UPDATE``'s ``rowcount`` precisely so
that a repeated link is not mistaken for a missing thread. Idempotent set is what ``PUT`` means.

**Neither route creates anything.** A contact is created by ``POST /contacts``, which routes
through slice 2's dedupe, and the frontend's import flow opens that form prefilled from the ``From``
header. Offering to attach an existing person rather than creating a duplicate *is* the dedupe, so
a second creation path here would defeat it.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors
from common.db import transaction
from common.logger import logger
from handlers.context import authenticate
from handlers.params import path_int
from models.email_inbound import LinkContactInput, LinkOpportunityInput, PendingImportSummary
from repositories import email_imports as imports_repo

router = Router()


@router.get("/emails/imports")
def list_pending_imports() -> dict:
    """Return threads awaiting import, newest first.

    Each row carries the sender split into address and display name, and the organization suggested
    by matching the sender's domain against ``organizations.email_domain`` — which is what prefills
    the venue on the Add Contact form.
    """
    request = authenticate(router.current_event.raw_event)
    rows = imports_repo.list_pending_imports(request.connection, request.user_id)
    return {
        "imports": [PendingImportSummary(**row).model_dump(mode="json") for row in rows],
    }


@router.put("/emails/threads/<thread_id>/contact")
def link_contact(thread_id: str) -> dict:
    """Attach an existing contact to a thread and its unattributed messages.

    Messages already carrying a contact of their own are left alone: that value is derived at
    ingest from who actually sent the message, so a second tracked contact who replied into this
    thread keeps their own attribution rather than being rewritten as the person Donna links.
    """
    request = authenticate(router.current_event.raw_event)
    thread_row_id = path_int(thread_id)
    data = LinkContactInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        linked = imports_repo.link_contact(conn, request.user_id, thread_row_id, data.contact_id)
    if not linked:
        raise errors.NotFound("thread not found")
    logger.info(
        "Linked thread id=%s to contact_id=%s user_id=%s",
        thread_row_id,
        data.contact_id,
        request.user_id,
    )
    return {"thread_id": thread_row_id, "contact_id": data.contact_id}


@router.put("/emails/threads/<thread_id>/opportunity")
def link_opportunity(thread_id: str) -> dict:
    """Attach a thread to a gig, or detach it by sending ``{"opportunity_id": null}``.

    This is the only way an inbound-first thread ever reaches an opportunity. Nothing infers one:
    a contact having exactly one open gig is not evidence that a given email concerns it, and
    misfiling side-channel mail against the wrong gig is worse than leaving it unattached.
    """
    request = authenticate(router.current_event.raw_event)
    thread_row_id = path_int(thread_id)
    data = LinkOpportunityInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        linked = imports_repo.link_opportunity(
            conn, request.user_id, thread_row_id, data.opportunity_id
        )
    if not linked:
        raise errors.NotFound("thread not found")
    logger.info(
        "Linked thread id=%s to opportunity_id=%s user_id=%s",
        thread_row_id,
        data.opportunity_id,
        request.user_id,
    )
    return {"thread_id": thread_row_id, "opportunity_id": data.opportunity_id}
