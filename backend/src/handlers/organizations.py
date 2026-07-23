"""Organizations router — CRUD for venues, podcasts, and expos.

Each write runs inside a ``transaction`` on the reused connection; the response is re-read (via
``handlers.responses``) so it always reflects committed state, including the computed
``research_ready`` and affiliated contacts. Uniqueness (a live org name) and FK validity are
enforced in the repository and surface as ``Conflict``/``InvalidInput`` through ``common/http.py``.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors
from common.db import transaction
from common.logger import logger
from core.research import is_research_ready
from handlers.context import authenticate
from handlers.params import path_int
from handlers.responses import organization_response
from models.organizations import OrganizationInput, OrganizationSummary
from repositories import organizations as orgs_repo

router = Router()


@router.get("/organizations")
def list_organizations() -> dict:
    """Return the caller's organizations as list summaries (with research-ready state)."""
    request = authenticate(router.current_event.raw_event)
    rows = orgs_repo.list_organizations(request.connection, request.user_id)
    summaries = [
        OrganizationSummary(
            id=row["id"],
            organization_type_id=row["organization_type_id"],
            name=row["name"],
            location=row["location"],
            why_it_fits=row["why_it_fits"],
            contact_count=row["contact_count"],
            research_ready=is_research_ready(
                row["what_it_is"], row["why_it_fits"], row["how_to_approach"], row["contact_count"]
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]
    return {"organizations": [s.model_dump(mode="json") for s in summaries]}


@router.post("/organizations")
def create_organization() -> dict:
    """Create an organization and return its detail."""
    request = authenticate(router.current_event.raw_event)
    data = OrganizationInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        org_id = orgs_repo.create_organization(conn, request.user_id, data)
    logger.info("Created organization id=%s user_id=%s", org_id, request.user_id)
    return organization_response(request.connection, request.user_id, org_id)


@router.get("/organizations/<org_id>")
def get_organization(org_id: str) -> dict:
    """Return one organization's detail."""
    request = authenticate(router.current_event.raw_event)
    return organization_response(request.connection, request.user_id, path_int(org_id))


@router.put("/organizations/<org_id>")
def update_organization(org_id: str) -> dict:
    """Full-replace an organization and return its updated detail."""
    request = authenticate(router.current_event.raw_event)
    org_id_int = path_int(org_id)
    data = OrganizationInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        updated = orgs_repo.update_organization(conn, request.user_id, org_id_int, data)
    if not updated:
        raise errors.NotFound("organization not found")
    logger.info("Updated organization id=%s user_id=%s", org_id_int, request.user_id)
    return organization_response(request.connection, request.user_id, org_id_int)


@router.delete("/organizations/<org_id>")
def delete_organization(org_id: str) -> dict:
    """Soft-delete an organization."""
    request = authenticate(router.current_event.raw_event)
    org_id_int = path_int(org_id)
    with transaction(request.connection) as conn:
        deleted = orgs_repo.soft_delete_organization(conn, request.user_id, org_id_int)
    if not deleted:
        raise errors.NotFound("organization not found")
    logger.info("Deleted organization id=%s user_id=%s", org_id_int, request.user_id)
    return {"deleted": True}
