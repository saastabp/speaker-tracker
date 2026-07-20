"""API composition root — the single Powertools resolver serving every route.

One ``APIGatewayHttpResolver`` serves all API routes via included ``Router`` modules
(``ARCHITECTURE.md`` §2); ~20 per-route functions would each pay the 2-6s cold RDS handshake.

Exception handlers register on ``app``, never on a ``Router`` — router-level propagation
through ``include_router`` is version-dependent. A single catch-all delegates to
``common.http``, whose ordered ``isinstance`` map decides the status, so correctness does not
depend on Powertools' exception-handler MRO precedence.

Entry/exit logging is **not** a middleware: Powertools runs exception handlers *outside* the
global middleware chain, so a middleware never observes the mapped error status (or unmatched
routes at all). Instead ``api_handler.lambda_handler`` wraps ``app.resolve``, which returns the
final response dict for every outcome — success, mapped domain error, 500, and 404.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response

from common.http import response_for_exception
from handlers import health

app = APIGatewayHttpResolver()
app.include_router(health.router)


@app.exception_handler(Exception)
def handle_exception(exc: Exception) -> Response:
    """Map any uncaught exception to the single JSON error envelope.

    Parameters
    ----------
    exc : Exception
        The exception raised anywhere in request handling.

    Returns
    -------
    aws_lambda_powertools.event_handler.Response
        The ``{"error": ...}`` envelope with the status from :mod:`common.http`.
    """
    return response_for_exception(exc)
