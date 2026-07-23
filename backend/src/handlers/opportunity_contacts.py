"""Opportunity-contacts router — link, adjust, and unlink the people on a gig.

The opportunity is named by the path, the contact by the path or body. Each op returns the updated
**opportunity detail** (via ``handlers.responses.opportunity_response``) so the frontend refreshes
its linked-contact list from the response. Ownership of both the opportunity and the contact is
enforced in the repository; a duplicate link surfaces as ``Conflict``.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors
from common.db import transaction
from common.logger import logger
from handlers.context import authenticate
from handlers.params import path_int
from handlers.responses import opportunity_response
from models.opportunities import OpportunityContactInput, OpportunityContactUpdate
from repositories import opportunity_contacts as oc_repo

router = Router()


@router.post("/opportunities/<opp_id>/contacts")
def add_contact(opp_id: str) -> dict:
    """Link a contact to an opportunity; return the updated opportunity."""
    request = authenticate(router.current_event.raw_event)
    opp_id_int = path_int(opp_id, "opportunity_id")
    data = OpportunityContactInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        oc_repo.add_contact(conn, request.user_id, opp_id_int, data)
    logger.info(
        "Linked contact id=%s opportunity id=%s user_id=%s",
        data.contact_id,
        opp_id_int,
        request.user_id,
    )
    return opportunity_response(request.connection, request.user_id, opp_id_int)


@router.put("/opportunities/<opp_id>/contacts/<contact_id>")
def update_contact(opp_id: str, contact_id: str) -> dict:
    """Update a linked contact's per-gig role; return the updated opportunity."""
    request = authenticate(router.current_event.raw_event)
    opp_id_int = path_int(opp_id, "opportunity_id")
    contact_id_int = path_int(contact_id, "contact_id")
    data = OpportunityContactUpdate.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        updated = oc_repo.update_contact(conn, request.user_id, opp_id_int, contact_id_int, data)
    if not updated:
        raise errors.NotFound("opportunity contact not found")
    logger.info(
        "Updated opportunity contact opportunity id=%s contact id=%s user_id=%s",
        opp_id_int,
        contact_id_int,
        request.user_id,
    )
    return opportunity_response(request.connection, request.user_id, opp_id_int)


@router.delete("/opportunities/<opp_id>/contacts/<contact_id>")
def remove_contact(opp_id: str, contact_id: str) -> dict:
    """Unlink a contact from an opportunity; return the updated opportunity."""
    request = authenticate(router.current_event.raw_event)
    opp_id_int = path_int(opp_id, "opportunity_id")
    contact_id_int = path_int(contact_id, "contact_id")
    with transaction(request.connection) as conn:
        removed = oc_repo.remove_contact(conn, request.user_id, opp_id_int, contact_id_int)
    if not removed:
        raise errors.NotFound("opportunity contact not found")
    logger.info(
        "Unlinked contact id=%s opportunity id=%s user_id=%s",
        contact_id_int,
        opp_id_int,
        request.user_id,
    )
    return opportunity_response(request.connection, request.user_id, opp_id_int)
