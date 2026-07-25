"""Signatures router — the user's styled email signatures.

``GET /signatures`` lists them; ``GET /signatures/default`` returns the composer's default (or
null); ``POST`` creates, ``PUT /{id}`` full-replaces, ``DELETE /{id}`` soft-deletes. A single
default per user is enforced in the repository.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors
from common.db import transaction
from common.logger import logger
from handlers.context import authenticate
from handlers.params import path_int
from models.signatures import Signature, SignatureInput
from repositories import signatures as signatures_repo

router = Router()


@router.get("/signatures")
def list_signatures() -> dict:
    """Return the caller's signatures, default first."""
    request = authenticate(router.current_event.raw_event)
    rows = signatures_repo.list_signatures(request.connection, request.user_id)
    return {"signatures": [Signature(**row).model_dump(mode="json") for row in rows]}


@router.get("/signatures/default")
def get_default_signature() -> dict:
    """Return the caller's default signature, or ``{"signature": null}`` when none is set."""
    request = authenticate(router.current_event.raw_event)
    row = signatures_repo.get_default_signature(request.connection, request.user_id)
    return {"signature": Signature(**row).model_dump(mode="json") if row else None}


@router.post("/signatures")
def create_signature() -> dict:
    """Create a signature and return its detail."""
    request = authenticate(router.current_event.raw_event)
    data = SignatureInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        sig_id = signatures_repo.create_signature(conn, request.user_id, data)
    logger.info(
        "Created signature id=%s is_default=%s user_id=%s",
        sig_id,
        data.is_default,
        request.user_id,
    )
    row = signatures_repo.get_signature(request.connection, request.user_id, sig_id)
    return Signature(**row).model_dump(mode="json")


@router.put("/signatures/<sig_id>")
def update_signature(sig_id: str) -> dict:
    """Full-replace a signature and return its detail."""
    request = authenticate(router.current_event.raw_event)
    sig_id_int = path_int(sig_id)
    data = SignatureInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        updated = signatures_repo.update_signature(conn, request.user_id, sig_id_int, data)
    if not updated:
        raise errors.NotFound("signature not found")
    logger.info(
        "Updated signature id=%s is_default=%s user_id=%s",
        sig_id_int,
        data.is_default,
        request.user_id,
    )
    row = signatures_repo.get_signature(request.connection, request.user_id, sig_id_int)
    return Signature(**row).model_dump(mode="json")


@router.delete("/signatures/<sig_id>")
def delete_signature(sig_id: str) -> dict:
    """Soft-delete a signature."""
    request = authenticate(router.current_event.raw_event)
    sig_id_int = path_int(sig_id)
    with transaction(request.connection) as conn:
        deleted = signatures_repo.soft_delete_signature(conn, request.user_id, sig_id_int)
    if not deleted:
        raise errors.NotFound("signature not found")
    logger.info("Deleted signature id=%s user_id=%s", sig_id_int, request.user_id)
    return {"deleted": True}
