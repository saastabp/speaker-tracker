"""Lambda entrypoint for the schema migration runner.

Invoked out-of-band (a CDK ``Trigger`` after the API stack updates, or a manual invoke) to
apply pending ``migrations/NNNN_*.sql`` files. It is a *separate* function from the API
(slice-1 sizing: 512 MB / 300s / reserved concurrency 1) for two reasons:

- **A dedicated, short-lived connection.** The migration advisory lock (``GET_LOCK``) is only
  safe because it releases when the session ends. The API's module-scope connection is reused
  across invocations and would hold the lock indefinitely, so migrations must never run on it.
  This handler opens its own connection via :func:`common.db.open_dedicated_connection` and
  closes it in ``finally``.
- **A failed migration must fail the deploy.** Any error is logged with a stack trace and
  re-raised so the CDK ``Trigger`` (and thus the deployment) fails loudly rather than leaving
  the schema half-migrated and the deploy green.
"""

from __future__ import annotations

import time
from pathlib import Path

from aws_lambda_powertools.utilities.typing import LambdaContext

from common import db
from common.logger import logger
from migrations import runner

#: The migration files ship next to the runner module in the deployment bundle.
MIGRATIONS_DIR = Path(runner.__file__).resolve().parent


@logger.inject_lambda_context(log_event=False)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """Apply all pending migrations and return a summary of what changed.

    Parameters
    ----------
    event : dict
        The invocation event. Unused — migrations are discovered from the bundled files, not
        from the event.
    context : LambdaContext
        The Lambda invocation context; ``aws_request_id`` is the correlation id.

    Returns
    -------
    dict
        ``{"applied": [...], "skipped": [...]}`` — versions applied by this run and those
        already present.

    Raises
    ------
    Exception
        Re-raises any migration failure after logging it, so the invoking deploy fails.
    """
    correlation_id = context.aws_request_id
    start = time.monotonic()
    logger.info("Migrate start correlation_id=%s migrations_dir=%s", correlation_id, MIGRATIONS_DIR)

    conn = None
    try:
        conn = db.open_dedicated_connection()
        result = runner.run_migrations(conn, MIGRATIONS_DIR)
    except Exception:
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        logger.exception(
            "Migrate failed correlation_id=%s duration_ms=%s", correlation_id, duration_ms
        )
        raise
    finally:
        # Closing releases the runner's advisory-lock session even if it raised mid-apply.
        db.close_quietly(conn)

    duration_ms = round((time.monotonic() - start) * 1000, 1)
    logger.info(
        "Migrate end correlation_id=%s status=ok duration_ms=%s applied=%s skipped=%s",
        correlation_id,
        duration_ms,
        result.applied,
        result.skipped,
    )
    return {"applied": result.applied, "skipped": result.skipped}
