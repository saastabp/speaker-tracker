"""Talks router — CRUD for the reusable offers Donna pitches.

Talks carry no catalog references or nested data, so each op is a straight read/write; writes run
inside a ``transaction`` on the reused connection and re-read via ``handlers.responses`` so the
response reflects committed state.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors
from common.db import transaction
from common.logger import logger
from handlers.context import authenticate
from handlers.params import path_int
from handlers.responses import talk_response
from models.talks import TalkInput, TalkSummary
from repositories import talks as talks_repo

router = Router()


@router.get("/talks")
def list_talks() -> dict:
    """Return the caller's talks as list summaries."""
    request = authenticate(router.current_event.raw_event)
    rows = talks_repo.list_talks(request.connection, request.user_id)
    summaries = [TalkSummary(**row) for row in rows]
    return {"talks": [s.model_dump(mode="json") for s in summaries]}


@router.post("/talks")
def create_talk() -> dict:
    """Create a talk and return its detail."""
    request = authenticate(router.current_event.raw_event)
    data = TalkInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        talk_id = talks_repo.create_talk(conn, request.user_id, data)
    logger.info("Created talk id=%s user_id=%s", talk_id, request.user_id)
    return talk_response(request.connection, request.user_id, talk_id)


@router.get("/talks/<talk_id>")
def get_talk(talk_id: str) -> dict:
    """Return one talk's detail."""
    request = authenticate(router.current_event.raw_event)
    return talk_response(request.connection, request.user_id, path_int(talk_id))


@router.put("/talks/<talk_id>")
def update_talk(talk_id: str) -> dict:
    """Full-replace a talk and return its updated detail."""
    request = authenticate(router.current_event.raw_event)
    talk_id_int = path_int(talk_id)
    data = TalkInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        updated = talks_repo.update_talk(conn, request.user_id, talk_id_int, data)
    if not updated:
        raise errors.NotFound("talk not found")
    logger.info("Updated talk id=%s user_id=%s", talk_id_int, request.user_id)
    return talk_response(request.connection, request.user_id, talk_id_int)


@router.delete("/talks/<talk_id>")
def delete_talk(talk_id: str) -> dict:
    """Soft-delete a talk."""
    request = authenticate(router.current_event.raw_event)
    talk_id_int = path_int(talk_id)
    with transaction(request.connection) as conn:
        deleted = talks_repo.soft_delete_talk(conn, request.user_id, talk_id_int)
    if not deleted:
        raise errors.NotFound("talk not found")
    logger.info("Deleted talk id=%s user_id=%s", talk_id_int, request.user_id)
    return {"deleted": True}
