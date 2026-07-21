"""Catalogs router — the vocabularies the SPA loads once after sign-in.

``GET /catalogs`` is authenticated but not user-scoped: the reference data is identical for
every caller. Authentication (including the first-request ``users`` upsert) is handled by the
shared :func:`common.auth.authenticate` step, so this handler is a pure read.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common.logger import logger
from handlers.context import authenticate
from repositories import catalogs as catalogs_repo

router = Router()


@router.get("/catalogs")
def get_catalogs() -> dict:
    """Return all catalog vocabularies as a bare JSON object keyed by table name.

    Returns
    -------
    dict
        The serialized :class:`models.catalogs.Catalogs` (no envelope — ``ARCHITECTURE.md`` §2).
    """
    request = authenticate(router.current_event.raw_event)
    logger.info("Catalogs requested user_id=%s", request.user_id)
    return catalogs_repo.fetch_catalogs(request.connection).model_dump()
