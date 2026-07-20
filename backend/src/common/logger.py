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

from aws_lambda_powertools import Logger

#: Tags every log line. Powertools also reads POWERTOOLS_SERVICE_NAME directly; this default
#: gives a meaningful name locally and in tests where that env var is unset.
SERVICE_NAME = os.environ.get("POWERTOOLS_SERVICE_NAME", "speaker-tracker")

logger = Logger(service=SERVICE_NAME)
