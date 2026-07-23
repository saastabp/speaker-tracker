"""Opportunities router — the pipeline board, detail, and the journaled lifecycle transitions.

Reads return the flat board payload (``GET /opportunities`` with ``?closed=`` and ``?status=``
filters) the SPA buckets by ``current_status``, plus per-gig detail. The lifecycle moves through
three dedicated endpoints so the status journal and ``closed_at`` stay owned by the repository:
``PATCH /{id}/status`` (one status event per real move), ``PATCH /{id}/payment`` (recompute
``closed_at``), and ``POST /{id}/close`` (terminal event + reason note). ``GET /funnel`` serves the
server-owned board columns. Every write re-reads via ``handlers.responses.opportunity_response`` so
the response reflects committed state.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors
from common.db import transaction
from common.logger import logger
from core.funnel import Stage, build_funnel
from handlers.context import authenticate
from handlers.params import path_int
from handlers.responses import opportunity_response
from models.funnel import FunnelStage
from models.opportunities import (
    OpportunityCloseInput,
    OpportunityInput,
    OpportunityPaymentPatch,
    OpportunityStatusPatch,
    OpportunitySummary,
)
from repositories import catalogs as catalogs_repo
from repositories import opportunities as opps_repo
from repositories.opportunities import StatusPatchResult

router = Router()

#: Query-string values that mean "true" for the ?closed= board/History filter.
_TRUTHY = {"1", "true", "yes"}


@router.get("/funnel")
def get_funnel() -> dict:
    """Return the server-owned board columns in display order (acceptance #9)."""
    request = authenticate(router.current_event.raw_event)
    rows = catalogs_repo.list_opportunity_statuses(request.connection)
    stages = build_funnel(
        Stage(
            short_name=row["short_name"],
            label=row["description"],
            sort_order=row["sort_order"],
            is_terminal=bool(row["is_terminal"]),
        )
        for row in rows
    )
    columns = [
        FunnelStage(
            short_name=stage.short_name,
            label=stage.label,
            sort_order=stage.sort_order,
            is_terminal=stage.is_terminal,
        )
        for stage in stages
    ]
    return {"stages": [c.model_dump(mode="json") for c in columns]}


@router.get("/opportunities")
def list_opportunities() -> dict:
    """Return the caller's opportunities as flat board / History cards.

    ``?closed=true`` returns History, ``?closed=false`` the active board, omitted returns both;
    ``?status=<short_name>`` filters to one stage.
    """
    request = authenticate(router.current_event.raw_event)
    params = router.current_event.query_string_parameters or {}
    closed_raw = params.get("closed")
    closed = None if closed_raw is None else closed_raw.lower() in _TRUTHY
    status = params.get("status")
    rows = opps_repo.list_opportunities(
        request.connection, request.user_id, closed=closed, status=status
    )
    summaries = [OpportunitySummary(**row) for row in rows]
    return {"opportunities": [s.model_dump(mode="json") for s in summaries]}


@router.post("/opportunities")
def create_opportunity() -> dict:
    """Create an opportunity (in ``researching``) and return its detail."""
    request = authenticate(router.current_event.raw_event)
    data = OpportunityInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        opp_id = opps_repo.create_opportunity(conn, request.user_id, data)
    logger.info("Created opportunity id=%s user_id=%s", opp_id, request.user_id)
    return opportunity_response(request.connection, request.user_id, opp_id)


@router.get("/opportunities/<opp_id>")
def get_opportunity(opp_id: str) -> dict:
    """Return one opportunity's detail."""
    request = authenticate(router.current_event.raw_event)
    return opportunity_response(request.connection, request.user_id, path_int(opp_id))


@router.put("/opportunities/<opp_id>")
def update_opportunity(opp_id: str) -> dict:
    """Full-replace an opportunity's descriptive fields and return its detail."""
    request = authenticate(router.current_event.raw_event)
    opp_id_int = path_int(opp_id)
    data = OpportunityInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        updated = opps_repo.update_opportunity(conn, request.user_id, opp_id_int, data)
    if not updated:
        raise errors.NotFound("opportunity not found")
    logger.info("Updated opportunity id=%s user_id=%s", opp_id_int, request.user_id)
    return opportunity_response(request.connection, request.user_id, opp_id_int)


@router.delete("/opportunities/<opp_id>")
def delete_opportunity(opp_id: str) -> dict:
    """Soft-delete an opportunity."""
    request = authenticate(router.current_event.raw_event)
    opp_id_int = path_int(opp_id)
    with transaction(request.connection) as conn:
        deleted = opps_repo.soft_delete_opportunity(conn, request.user_id, opp_id_int)
    if not deleted:
        raise errors.NotFound("opportunity not found")
    logger.info("Deleted opportunity id=%s user_id=%s", opp_id_int, request.user_id)
    return {"deleted": True}


@router.patch("/opportunities/<opp_id>/status")
def patch_status(opp_id: str) -> dict:
    """Move an opportunity to a new board stage and return its detail.

    A move to the current stage is a no-op (no status event); both the no-op and a real move
    return the current detail at 200. Cancelled / lost are rejected here — use the close endpoint.
    """
    request = authenticate(router.current_event.raw_event)
    opp_id_int = path_int(opp_id)
    data = OpportunityStatusPatch.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        result = opps_repo.patch_status(conn, request.user_id, opp_id_int, data.status)
    if result is StatusPatchResult.NOT_FOUND:
        raise errors.NotFound("opportunity not found")
    logger.info(
        "Patched status opportunity id=%s status=%s result=%s user_id=%s",
        opp_id_int,
        data.status,
        result.value,
        request.user_id,
    )
    return opportunity_response(request.connection, request.user_id, opp_id_int)


@router.patch("/opportunities/<opp_id>/payment")
def patch_payment(opp_id: str) -> dict:
    """Update an opportunity's payment state (recomputing ``closed_at``) and return its detail."""
    request = authenticate(router.current_event.raw_event)
    opp_id_int = path_int(opp_id)
    data = OpportunityPaymentPatch.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        updated = opps_repo.patch_payment(
            conn, request.user_id, opp_id_int, data.payment_status, data.paid_on
        )
    if not updated:
        raise errors.NotFound("opportunity not found")
    logger.info(
        "Patched payment opportunity id=%s payment_status=%s user_id=%s",
        opp_id_int,
        data.payment_status,
        request.user_id,
    )
    return opportunity_response(request.connection, request.user_id, opp_id_int)


@router.post("/opportunities/<opp_id>/close")
def close_opportunity(opp_id: str) -> dict:
    """Close an opportunity (cancelled / lost) with a reason and return its detail."""
    request = authenticate(router.current_event.raw_event)
    opp_id_int = path_int(opp_id)
    data = OpportunityCloseInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        closed = opps_repo.close(conn, request.user_id, opp_id_int, data.status, data.reason)
    if not closed:
        raise errors.NotFound("opportunity not found")
    logger.info(
        "Closed opportunity id=%s status=%s user_id=%s",
        opp_id_int,
        data.status,
        request.user_id,
    )
    return opportunity_response(request.connection, request.user_id, opp_id_int)
