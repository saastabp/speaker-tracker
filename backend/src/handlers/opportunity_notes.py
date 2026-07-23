"""Opportunity-notes router — add and remove dated notes on a gig.

The opportunity is named by the path. Each op returns the updated **opportunity detail** (via
``handlers.responses.opportunity_response``) so the frontend refreshes its notes list from the
response. Ownership is enforced in the repository.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors
from common.db import transaction
from common.logger import logger
from handlers.context import authenticate
from handlers.params import path_int
from handlers.responses import opportunity_response
from models.opportunities import OpportunityNoteInput
from repositories import opportunity_notes as notes_repo

router = Router()


@router.post("/opportunities/<opp_id>/notes")
def add_note(opp_id: str) -> dict:
    """Add a dated note to an opportunity; return the updated opportunity."""
    request = authenticate(router.current_event.raw_event)
    opp_id_int = path_int(opp_id, "opportunity_id")
    data = OpportunityNoteInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        note_id = notes_repo.add_note(conn, request.user_id, opp_id_int, data)
    logger.info(
        "Added note id=%s opportunity id=%s user_id=%s", note_id, opp_id_int, request.user_id
    )
    return opportunity_response(request.connection, request.user_id, opp_id_int)


@router.delete("/opportunities/<opp_id>/notes/<note_id>")
def delete_note(opp_id: str, note_id: str) -> dict:
    """Soft-delete a note; return the updated opportunity."""
    request = authenticate(router.current_event.raw_event)
    opp_id_int = path_int(opp_id, "opportunity_id")
    note_id_int = path_int(note_id, "note_id")
    with transaction(request.connection) as conn:
        deleted = notes_repo.soft_delete_note(conn, request.user_id, opp_id_int, note_id_int)
    if not deleted:
        raise errors.NotFound("note not found")
    logger.info(
        "Deleted note id=%s opportunity id=%s user_id=%s", note_id_int, opp_id_int, request.user_id
    )
    return opportunity_response(request.connection, request.user_id, opp_id_int)
