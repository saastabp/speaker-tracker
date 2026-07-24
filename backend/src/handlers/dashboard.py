"""Dashboard router — one composite read for the home screen.

``GET /dashboard`` returns actual-vs-target tiles, funnel ratio counts, the money rollup, the stale
list, and the needs-attention list in a single response (DEV-PLAN slice 5). All aggregation lives in
the repository; this is a pure read (no transaction).
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from handlers.context import authenticate
from models.dashboard import Dashboard
from repositories import dashboard as dashboard_repo

router = Router()


@router.get("/dashboard")
def get_dashboard() -> dict:
    """Return the composite dashboard payload for the caller."""
    request = authenticate(router.current_event.raw_event)
    payload = dashboard_repo.build_dashboard(request.connection, request.user_id)
    return Dashboard(**payload).model_dump(mode="json")
