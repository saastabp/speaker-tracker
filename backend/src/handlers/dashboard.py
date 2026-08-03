"""Dashboard router — one composite read for the home screen.

``GET /dashboard`` returns actual-vs-target tiles, funnel ratio counts, the money rollup, the stale
list, and the needs-attention list in a single response (DEV-PLAN slice 5). All aggregation lives in
the repository; this is a pure read (no transaction).
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from handlers.context import authenticate
from handlers.params import query_date
from models.dashboard import Dashboard
from repositories import dashboard as dashboard_repo

router = Router()


@router.get("/dashboard")
def get_dashboard() -> dict:
    """Return the composite dashboard payload for the caller.

    ``?week_of=YYYY-MM-DD`` anchors the target tiles to the week containing that date; any day in
    the week works, and the response echoes the resolved ``[start, end)``. Absent, the tiles report
    on the current week, which is the pre-slice-10 behaviour.
    """
    request = authenticate(router.current_event.raw_event)
    params = router.current_event.query_string_parameters or {}
    week_of = query_date(params.get("week_of"), "week_of")
    payload = dashboard_repo.build_dashboard(request.connection, request.user_id, week_of)
    return Dashboard(**payload).model_dump(mode="json")
