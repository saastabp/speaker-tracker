"""Secrets Manager access — the backend's first runtime secret (DEV-PLAN slice 6a).

Only the IMAP mailbox password lives here. Everything else the Lambda needs is either an
environment variable or an IAM-authenticated call; IMAP is username/password because WorkMail
offers nothing else, so the credential has to be fetched at runtime and never baked into the
bundle, the CDK template, or `cdk.out` (see the Messaging stack: CDK creates the secret *empty*
and the value is written once by hand).

**Module-scope caching, like `common/db.py`.** One warm Lambda container serves many requests and
a Secrets Manager round trip is ~50-100 ms; paying that per send is pure waste, so a fetched
secret is cached for the life of the container. The hazard that introduces is staleness: if the
mailbox password is rotated, a warm container keeps presenting the old one and every IMAP
operation fails identically. :func:`get_imap_credentials` therefore accepts ``refresh=True`` so an
authentication failure can force exactly one re-fetch before giving up — the difference between a
rotation causing a blip and a rotation causing an outage that lasts until the container recycles.
6b's poller (acceptance #11: a wrong password must *alarm*, not silently no-op) is the intended
caller of that path.

**Nothing is fetched at import time.** A Secrets Manager outage must fail the email send, not take
down every route in the API by breaking module initialization.

The boto3 client is created lazily behind :func:`_client`, which is the seam tests monkeypatch
(decision #1: CI and local never reach real AWS).

Naming note: this module is ``common.secrets``, not a top-level ``secrets``. ``src/`` is what sits
on ``sys.path``, so the stdlib ``secrets`` module is unaffected — unlike ``core/email.py``, which
would have shadowed stdlib ``email``.
"""

from __future__ import annotations

import json
import os
import time
from typing import NamedTuple

import boto3

from common.aws import resolve_region
from common.logger import logger

#: Env var naming the secret holding the IMAP credentials, set by the Messaging stack.
IMAP_SECRET_ENV = "IMAP_SECRET_ID"

_client_instance = None

#: Parsed secrets by secret id, cached for the life of the container. Values are credentials —
#: never log this dict, and never include it in an error message.
_cache: dict[str, dict] = {}


class ImapCredentials(NamedTuple):
    """Username and password for the WorkMail mailbox.

    Attributes
    ----------
    username : str
        Mailbox address, e.g. ``donna.king@360balancedliving.com``.
    password : str
        Mailbox password. Never logged, never included in an exception message.
    """

    username: str
    password: str


def _region() -> str:
    """Resolve the Secrets Manager region (SECRETS_REGION, else AWS_REGION)."""
    return resolve_region("SECRETS_REGION", "to read secrets")


def _client():
    """Return the module-cached boto3 Secrets Manager client, created on first use.

    Tests monkeypatch this function rather than boto3 itself, matching the seam pattern used for
    ``common.db.get_connection`` and ``common.auth.principal_from_event``.
    """
    global _client_instance
    if _client_instance is None:
        _client_instance = boto3.client("secretsmanager", region_name=_region())
    return _client_instance


def get_secret_json(secret_id: str, *, refresh: bool = False) -> dict:
    """Fetch a JSON secret by id and return it parsed, caching it for the container's lifetime.

    Parameters
    ----------
    secret_id : str
        Secret name or ARN.
    refresh : bool, optional
        Bypass the cache and re-fetch. Use after an authentication failure, when the cached value
        may predate a rotation; the fresh value replaces the cached one.

    Returns
    -------
    dict
        The parsed ``SecretString``.

    Raises
    ------
    RuntimeError
        If the secret holds no ``SecretString`` (a binary secret is not something this app writes),
        or its contents are not a JSON object. The underlying value is never included in the
        message — only the id and the shape problem.
    botocore.exceptions.ClientError
        Propagated unchanged when the fetch itself fails (missing secret, denied, throttled), so
        the caller sees the real AWS error rather than a swallowed one.
    """
    if not refresh and secret_id in _cache:
        return _cache[secret_id]

    started = time.monotonic()
    logger.info("Fetching secret secret_id=%s refresh=%s", secret_id, refresh)
    response = _client().get_secret_value(SecretId=secret_id)
    duration_ms = int((time.monotonic() - started) * 1000)

    raw = response.get("SecretString")
    if not raw:
        logger.error(
            "Secret has no SecretString secret_id=%s duration_ms=%d", secret_id, duration_ms
        )
        raise RuntimeError(f"Secret {secret_id} has no SecretString value")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        # The message deliberately omits `raw` — it is the credential.
        logger.exception("Secret is not valid JSON secret_id=%s", secret_id)
        raise RuntimeError(f"Secret {secret_id} is not valid JSON") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError(f"Secret {secret_id} must be a JSON object")

    logger.info(
        "Fetched secret secret_id=%s duration_ms=%d keys=%d", secret_id, duration_ms, len(parsed)
    )
    _cache[secret_id] = parsed
    return parsed


def get_imap_credentials(*, refresh: bool = False) -> ImapCredentials:
    """Return the IMAP mailbox credentials from the secret named by ``IMAP_SECRET_ID``.

    Parameters
    ----------
    refresh : bool, optional
        Force a re-fetch, bypassing the container cache. Pass ``True`` on a retry after an IMAP
        authentication failure, so a password rotation recovers on the next attempt instead of
        failing until the container is recycled.

    Returns
    -------
    ImapCredentials
        The ``username`` / ``password`` pair.

    Raises
    ------
    RuntimeError
        If ``IMAP_SECRET_ID`` is unset, or the secret is missing either key. Both are deployment
        faults that must fail loudly — a half-configured mailbox that silently does nothing is the
        failure mode 6b's acceptance #11 exists to prevent.
    """
    secret_id = os.environ.get(IMAP_SECRET_ENV)
    if not secret_id:
        raise RuntimeError(f"Required environment variable {IMAP_SECRET_ENV} is not set")

    secret = get_secret_json(secret_id, refresh=refresh)
    username = secret.get("username")
    password = secret.get("password")
    if not username or not password:
        missing = [k for k in ("username", "password") if not secret.get(k)]
        raise RuntimeError(f"Secret {secret_id} is missing required key(s): {', '.join(missing)}")

    return ImapCredentials(username=username, password=password)


def reset_cache() -> None:
    """Clear the cached secrets and client. For tests, and for nothing else."""
    global _client_instance
    _client_instance = None
    _cache.clear()
