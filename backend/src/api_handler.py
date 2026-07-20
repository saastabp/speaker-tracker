"""Lambda entrypoint for the single API function.

AWS invokes :func:`lambda_handler`, which delegates to the Powertools resolver in
:mod:`app`. The three decorators wrap the handler because they need the ``event`` and
``context`` AWS passes:

- ``logger.inject_lambda_context`` — attaches Lambda context (``function_request_id``,
  ``cold_start``, …) and the API Gateway ``correlation_id`` to every log line for the
  request. ``log_event=False`` (set explicitly) guarantees the raw event — which carries the
  ``Authorization`` JWT — is never logged, even if ``POWERTOOLS_LOGGER_LOG_EVENT`` is set.
- ``tracer.capture_lambda_handler`` — X-Ray tracing, disabled by default via
  ``POWERTOOLS_TRACE_DISABLED=true`` (set per-env in CDK) and auto-off outside Lambda.
- ``metrics.log_metrics`` — flushes EMF metrics; ``capture_cold_start_metric`` emits a
  ``ColdStart`` metric so the app's known cold-start cost is measurable.
"""

from __future__ import annotations

import logging
import os
import time

from aws_lambda_powertools import Metrics, Tracer
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext

from app import app
from common.logger import logger

tracer = Tracer()
metrics = Metrics(
    namespace=os.environ.get("POWERTOOLS_METRICS_NAMESPACE", "SpeakerTracker"),
    service=os.environ.get("POWERTOOLS_SERVICE_NAME", "speaker-tracker"),
)


@logger.inject_lambda_context(
    correlation_id_path=correlation_paths.API_GATEWAY_HTTP, log_event=False
)
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """Resolve an API Gateway HTTP event through the Powertools router.

    Wraps ``app.resolve`` with one pairable entry/exit log. ``resolve`` returns the final
    response dict for every outcome — success, mapped domain error, 500, and unmatched-route
    404 — so the exit log always records the true status. It is logged here rather than in a
    middleware because Powertools runs exception handlers outside the middleware chain.

    Parameters
    ----------
    event : dict
        The API Gateway HTTP API v2 proxy event.
    context : LambdaContext
        The Lambda invocation context.

    Returns
    -------
    dict
        The proxy response object produced by the resolver.
    """
    start = time.monotonic()
    method = event.get("requestContext", {}).get("http", {}).get("method")
    path = event.get("rawPath")
    logger.info("Request start method=%s path=%s", method, path)
    try:
        response = app.resolve(event, context)
    except Exception:
        # resolve should convert everything to a response via the catch-all handler; reaching
        # here means an error escaped even that (e.g. a pre-routing parse failure). Log loudly
        # and re-raise — never bury a propagating failure at INFO.
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        logger.exception(
            "Request end method=%s path=%s status=unhandled duration_ms=%s",
            method,
            path,
            duration_ms,
        )
        raise
    duration_ms = round((time.monotonic() - start) * 1000, 1)
    status = response.get("statusCode")
    # Exit severity mirrors the outcome, so monitoring keys off the exit line alone.
    level = (
        logging.ERROR
        if status and status >= 500
        else logging.WARNING
        if status and status >= 400
        else logging.INFO
    )
    logger.log(
        level,
        "Request end method=%s path=%s status=%s duration_ms=%s",
        method,
        path,
        status,
        duration_ms,
    )
    return response
