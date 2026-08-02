"""Canonical Powertools ``Logger`` for the backend.

One configured parent logger (structured JSON, service name) is created here and imported
where a module-level logger is convenient. Submodules that want their own child instance
use ``Logger(child=True)`` — Powertools resolves the child to this parent by matching the
service name, so they inherit its configuration without re-declaring it.

The correlation id is injected at the API handler via
``@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)``
— never with ``log_event=True``, which would log the raw event carrying the JWT.
"""

from __future__ import annotations

import os
import time

from aws_lambda_powertools import Logger

#: Tags every log line. Powertools also reads POWERTOOLS_SERVICE_NAME directly; this default
#: gives a meaningful name locally and in tests where that env var is unset.
SERVICE_NAME = os.environ.get("POWERTOOLS_SERVICE_NAME", "speaker-tracker")

logger = Logger(service=SERVICE_NAME)


def elapsed_ms(start: float) -> float:
    """Return milliseconds since ``start``, for the ``duration_ms`` of an exit log line.

    Lives here because ``duration_ms`` is a property of the log contract, not of any one handler.
    Four handlers had grown their own copy and they had already diverged — two truncated to ``int``
    and two rounded to one decimal — so the same field arrived as an integer on some lines and a
    decimal on others, which defeats aggregating over it in a log query.

    Parameters
    ----------
    start : float
        A :func:`time.monotonic` reading taken at handler entry. Monotonic rather than wall clock
        so a clock adjustment mid-request cannot produce a negative or wildly wrong duration.

    Returns
    -------
    float
        Milliseconds elapsed, rounded to one decimal place.

    Examples
    --------
    >>> start = time.monotonic()
    >>> logger.info("Request end duration_ms=%s", elapsed_ms(start))
    """
    return round((time.monotonic() - start) * 1000, 1)
