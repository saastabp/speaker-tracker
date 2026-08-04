"""Outreaches router — the outbound touch journal and the unified contact timeline.

An outreach is a first-class resource created flat (``POST /outreaches``) with both its links —
the required ``contact_id`` and the optional ``opportunity_id`` — in the body, since a gig and a
venue are filter axes over one journal rather than parents (DEV-PLAN slice 4). Reads are
contact-scoped: ``GET /contacts/<id>/outreaches`` lists that contact's touches, and
``GET /contacts/<id>/timeline`` interleaves them with the contact's gig notes and status events
(acceptance #5). Ownership and reference validation live in the repository.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors
from common.db import transaction
from common.logger import logger
from handlers.context import authenticate
from handlers.follow_ups import create_rider_follow_up, schedule_new_follow_up
from handlers.params import path_int
from models.outreach import OutreachInput, OutreachPatch, OutreachSummary
from models.timeline import TimelineItem
from repositories import outreaches as outreaches_repo
from repositories import timeline as timeline_repo

router = Router()


@router.post("/outreaches")
def create_outreach() -> dict:
    """Log an outbound touch; return the created outreach with its resolved kind.

    The ``kind`` is inferred server-side when omitted (``initial`` for the first touch to the
    contact, ``correspondence`` after) and echoed back resolved, so the client never re-derives it.

    An opt-in ``follow_up`` rider schedules a reminder alongside the touch. It is created **in the
    same transaction** — a touch and the reminder to chase it should land together — and its
    schedule is built after the commit, so a scheduler outage costs the email and not the touch.
    """
    request = authenticate(router.current_event.raw_event)
    data = OutreachInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        outreach_id = outreaches_repo.create_outreach(conn, request.user_id, data)
        follow_up_id = create_rider_follow_up(
            conn,
            request.user_id,
            data.follow_up,
            contact_id=data.contact_id,
            opportunity_id=data.opportunity_id,
            fallback_note="Follow up on this touch.",
        )
    logger.info(
        "Logged outreach id=%s contact_id=%s follow_up_id=%s user_id=%s",
        outreach_id,
        data.contact_id,
        follow_up_id,
        request.user_id,
    )
    if follow_up_id is not None:
        schedule_new_follow_up(request, follow_up_id)
    row = outreaches_repo.get_outreach(request.connection, request.user_id, outreach_id)
    return OutreachSummary(**row).model_dump(mode="json")


@router.get("/contacts/<contact_id>/outreaches")
def list_contact_outreaches(contact_id: str) -> dict:
    """Return a contact's outbound touches, newest first."""
    request = authenticate(router.current_event.raw_event)
    rows = outreaches_repo.list_outreaches_for_contact(
        request.connection, request.user_id, path_int(contact_id, "contact_id")
    )
    return {"outreaches": [OutreachSummary(**row).model_dump(mode="json") for row in rows]}


@router.get("/contacts/<contact_id>/timeline")
def get_contact_timeline(contact_id: str) -> dict:
    """Return a contact's unified timeline (outreaches + notes + status events), newest first."""
    request = authenticate(router.current_event.raw_event)
    rows = timeline_repo.contact_timeline(
        request.connection, request.user_id, path_int(contact_id, "contact_id")
    )
    return {"timeline": [TimelineItem(**row).model_dump(mode="json") for row in rows]}


@router.patch("/outreaches/<outreach_id>")
def patch_outreach(outreach_id: str) -> dict:
    """Edit a logged touch — channel, kind, gig attribution, note or date.

    Not the contact: who a touch went to is what the row is, and moving it between timelines would
    re-open the kind inference that ran at create (``models.outreach.OutreachPatch``). No follow-up
    rider either — the rider belongs to the moment a touch is logged, not to a later correction.
    """
    request = authenticate(router.current_event.raw_event)
    outreach_id_int = path_int(outreach_id, "outreach_id")
    data = OutreachPatch.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        matched = outreaches_repo.patch_outreach(conn, request.user_id, outreach_id_int, data)
    if not matched:
        raise errors.NotFound("outreach not found")
    logger.info(
        "Patched outreach id=%s fields=%s user_id=%s",
        outreach_id_int,
        sorted(data.model_fields_set),
        request.user_id,
    )
    row = outreaches_repo.get_outreach(request.connection, request.user_id, outreach_id_int)
    return OutreachSummary(**row).model_dump(mode="json")


@router.delete("/outreaches/<outreach_id>")
def delete_outreach(outreach_id: str) -> dict:
    """Soft-delete a mis-logged outreach."""
    request = authenticate(router.current_event.raw_event)
    outreach_id_int = path_int(outreach_id, "outreach_id")
    with transaction(request.connection) as conn:
        deleted = outreaches_repo.soft_delete_outreach(conn, request.user_id, outreach_id_int)
    if not deleted:
        raise errors.NotFound("outreach not found")
    logger.info("Deleted outreach id=%s user_id=%s", outreach_id_int, request.user_id)
    return {"deleted": True}
