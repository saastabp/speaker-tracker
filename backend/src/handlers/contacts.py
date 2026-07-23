"""Contacts router — CRUD plus the add-contact dedupe search.

`GET /contacts?q=` is the dedupe search: the frontend runs it before creating a contact so an
existing person is offered for a new affiliation instead of duplicated. Detail responses (with
affiliations) come from ``handlers.responses.contact_response``, shared with the affiliations
router so neither route module imports the other.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors
from common.db import transaction
from common.logger import logger
from handlers.context import authenticate
from handlers.params import path_int
from handlers.responses import contact_response
from models.contacts import ContactInput, ContactSummary
from repositories import contacts as contacts_repo

router = Router()


@router.get("/contacts")
def list_contacts() -> dict:
    """Return the caller's contacts; ``?q=`` filters by name/email for the dedupe search."""
    request = authenticate(router.current_event.raw_event)
    query = (router.current_event.query_string_parameters or {}).get("q")
    rows = contacts_repo.list_contacts(request.connection, request.user_id, query)
    summaries = [ContactSummary(**row) for row in rows]
    return {"contacts": [s.model_dump(mode="json") for s in summaries]}


@router.post("/contacts")
def create_contact() -> dict:
    """Create a contact and return its detail."""
    request = authenticate(router.current_event.raw_event)
    data = ContactInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        contact_id = contacts_repo.create_contact(conn, request.user_id, data)
    logger.info("Created contact id=%s user_id=%s", contact_id, request.user_id)
    return contact_response(request.connection, request.user_id, contact_id)


@router.get("/contacts/<contact_id>")
def get_contact(contact_id: str) -> dict:
    """Return one contact's detail."""
    request = authenticate(router.current_event.raw_event)
    return contact_response(request.connection, request.user_id, path_int(contact_id))


@router.put("/contacts/<contact_id>")
def update_contact(contact_id: str) -> dict:
    """Full-replace a contact and return its updated detail."""
    request = authenticate(router.current_event.raw_event)
    contact_id_int = path_int(contact_id)
    data = ContactInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        updated = contacts_repo.update_contact(conn, request.user_id, contact_id_int, data)
    if not updated:
        raise errors.NotFound("contact not found")
    logger.info("Updated contact id=%s user_id=%s", contact_id_int, request.user_id)
    return contact_response(request.connection, request.user_id, contact_id_int)


@router.delete("/contacts/<contact_id>")
def delete_contact(contact_id: str) -> dict:
    """Soft-delete a contact."""
    request = authenticate(router.current_event.raw_event)
    contact_id_int = path_int(contact_id)
    with transaction(request.connection) as conn:
        deleted = contacts_repo.soft_delete_contact(conn, request.user_id, contact_id_int)
    if not deleted:
        raise errors.NotFound("contact not found")
    logger.info("Deleted contact id=%s user_id=%s", contact_id_int, request.user_id)
    return {"deleted": True}
