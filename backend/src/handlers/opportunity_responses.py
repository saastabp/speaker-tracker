"""Opportunity-responses router — the audience-growth counters on a gig.

One route. A response counter is set to a value rather than nudged by a delta, so the ``+``/``-``
control is safe to lean on: a double-fired click lands on the same number, and there is no separate
delete because lowering a counter to zero *is* the removal.

The opportunity is named by the path and the response type is the sub-resource, which is why this is
a PUT — it addresses one named counter and re-sending the same body changes nothing. Like the notes
and contacts routers, it returns the updated **opportunity detail** so the frontend refreshes its
grid from the response.

Named ``opportunity_responses`` rather than ``responses``: ``handlers/responses.py`` is the detail-
response composition module and has nothing to do with this feature. The prefix also matches
``opportunity_notes`` and ``opportunity_contacts``, the other two children of a gig.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common.db import transaction
from common.logger import logger
from handlers.context import authenticate
from handlers.params import path_int
from handlers.responses import opportunity_response
from models.opportunity_responses import OpportunityResponseCountInput
from repositories import opportunity_responses as responses_repo

router = Router()


@router.put("/opportunities/<opp_id>/responses/<response_type>")
def set_response_count(opp_id: str, response_type: str) -> dict:
    """Set one response counter on an opportunity; return the updated opportunity.

    PUT, not POST: the path names exactly one counter and the body carries its resulting value, so
    the operation is idempotent. Ownership, the parent check and the catalog lookup all live in the
    repository.
    """
    request = authenticate(router.current_event.raw_event)
    opp_id_int = path_int(opp_id, "opportunity_id")
    data = OpportunityResponseCountInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        responses_repo.set_response_count(
            conn, request.user_id, opp_id_int, response_type, data.count
        )
    logger.info(
        "Set response count opportunity_id=%s type=%s count=%s user_id=%s",
        opp_id_int,
        response_type,
        data.count,
        request.user_id,
    )
    return opportunity_response(request.connection, request.user_id, opp_id_int)
