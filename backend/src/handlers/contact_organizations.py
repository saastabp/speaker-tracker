"""Contact-organization affiliations router — attach/adjust/detach a contact and an org.

The contact is named by the path, the organization by the path or body. Each op returns the
**updated contact detail** (via ``handlers.responses.contact_response``) so the frontend refreshes
the affiliation list from the response. Ownership of both the contact and the org is enforced in
the repository; a duplicate affiliation surfaces as ``Conflict``.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors
from common.db import transaction
from common.logger import logger
from handlers.context import authenticate
from handlers.params import path_int
from handlers.responses import contact_response
from models.contacts import AffiliationInput, AffiliationUpdate
from repositories import contacts as contacts_repo

router = Router()


@router.post("/contacts/<contact_id>/organizations")
def add_affiliation(contact_id: str) -> dict:
    """Affiliate a contact with an organization; return the updated contact."""
    request = authenticate(router.current_event.raw_event)
    contact_id_int = path_int(contact_id, "contact_id")
    data = AffiliationInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        contacts_repo.add_affiliation(conn, request.user_id, contact_id_int, data)
    logger.info(
        "Affiliated contact id=%s organization id=%s user_id=%s",
        contact_id_int,
        data.organization_id,
        request.user_id,
    )
    return contact_response(request.connection, request.user_id, contact_id_int)


@router.put("/contacts/<contact_id>/organizations/<org_id>")
def update_affiliation(contact_id: str, org_id: str) -> dict:
    """Update an affiliation's role/primary flag; return the updated contact."""
    request = authenticate(router.current_event.raw_event)
    contact_id_int = path_int(contact_id, "contact_id")
    org_id_int = path_int(org_id, "organization_id")
    data = AffiliationUpdate.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        updated = contacts_repo.update_affiliation(
            conn, request.user_id, contact_id_int, org_id_int, data
        )
    if not updated:
        raise errors.NotFound("affiliation not found")
    logger.info(
        "Updated affiliation contact id=%s organization id=%s user_id=%s",
        contact_id_int,
        org_id_int,
        request.user_id,
    )
    return contact_response(request.connection, request.user_id, contact_id_int)


@router.delete("/contacts/<contact_id>/organizations/<org_id>")
def remove_affiliation(contact_id: str, org_id: str) -> dict:
    """Detach a contact from an organization; return the updated contact."""
    request = authenticate(router.current_event.raw_event)
    contact_id_int = path_int(contact_id, "contact_id")
    org_id_int = path_int(org_id, "organization_id")
    with transaction(request.connection) as conn:
        removed = contacts_repo.remove_affiliation(
            conn, request.user_id, contact_id_int, org_id_int
        )
    if not removed:
        raise errors.NotFound("affiliation not found")
    logger.info(
        "Removed affiliation contact id=%s organization id=%s user_id=%s",
        contact_id_int,
        org_id_int,
        request.user_id,
    )
    return contact_response(request.connection, request.user_id, contact_id_int)
