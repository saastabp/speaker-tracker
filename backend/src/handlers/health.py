"""Health-check router — unauthenticated and I/O-free.

``/health`` deliberately touches no database (``ARCHITECTURE.md`` §3, "no authorizer"), so a
cold or unavailable RDS never makes uptime checks or CloudFront origin health flap. It stays
unauthenticated so external monitors can hit it without a token.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

router = Router()


@router.get("/health")
def health() -> dict:
    """Return a static liveness payload.

    Returns
    -------
    dict
        ``{"status": "ok"}`` — bare JSON, no wrapper (``ARCHITECTURE.md`` §2 envelope).
    """
    return {"status": "ok"}
