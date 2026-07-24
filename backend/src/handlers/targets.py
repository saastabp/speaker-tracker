"""Targets router — the user's activity goals.

``GET /targets`` lists every set target; ``PUT /targets`` upserts one goal keyed on
(target_type, cadence) — free-form, so any (type × cadence) can carry a goal; ``DELETE
/targets/{target_type}/{cadence}`` unsets one. Ownership and catalog validation live in the
repository.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors
from common.db import transaction
from common.logger import logger
from handlers.context import authenticate
from models.targets import Target, TargetInput
from repositories import targets as targets_repo

router = Router()


@router.get("/targets")
def list_targets() -> dict:
    """Return the caller's targets."""
    request = authenticate(router.current_event.raw_event)
    rows = targets_repo.list_targets(request.connection, request.user_id)
    return {"targets": [Target(**row).model_dump(mode="json") for row in rows]}


@router.put("/targets")
def put_target() -> dict:
    """Upsert a goal for a (target_type, cadence); returns the stored target."""
    request = authenticate(router.current_event.raw_event)
    data = TargetInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        targets_repo.upsert_target(conn, request.user_id, data)
    logger.info(
        "Upserted target type=%s cadence=%s user_id=%s",
        data.target_type,
        data.cadence,
        request.user_id,
    )
    return Target(**data.model_dump()).model_dump(mode="json")


@router.delete("/targets/<target_type>/<cadence>")
def delete_target(target_type: str, cadence: str) -> dict:
    """Unset a target."""
    request = authenticate(router.current_event.raw_event)
    with transaction(request.connection) as conn:
        deleted = targets_repo.delete_target(conn, request.user_id, target_type, cadence)
    if not deleted:
        raise errors.NotFound("target not found")
    logger.info(
        "Deleted target type=%s cadence=%s user_id=%s", target_type, cadence, request.user_id
    )
    return {"deleted": True}
