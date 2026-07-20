"""Authentication — resolve the calling principal from the request, per environment.

**Prod** runs behind a Cognito JWT authorizer at the API gateway; the gateway verifies the
token and forwards the claims in ``event["requestContext"]["authorizer"]["jwt"]["claims"]``.
**Sandbox** omits the authorizer entirely and ``AUTH_MODE=dev`` injects a fixed ``dev``
principal, so the same handlers run without Cognito.

**Import-time guard.** ``AUTH_MODE=dev`` is only legal when ``ENV_TYPE=sandbox``. A prod
Lambda mistakenly deployed with ``AUTH_MODE=dev`` would otherwise accept anonymous traffic
against Donna's CRM, so it fails at **cold start** instead (``DEV-PLAN.md`` acceptance #6).
Both env vars default to their production-safe value, so a *missing* ``ENV_TYPE`` still trips
the guard rather than silently allowing dev auth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from common import errors

#: Production-safe defaults: unset means cognito auth in a prod environment, never dev.
_AUTH_MODE = os.environ.get("AUTH_MODE", "cognito")
_ENV_TYPE = os.environ.get("ENV_TYPE", "prod")

if _AUTH_MODE == "dev" and _ENV_TYPE != "sandbox":
    raise RuntimeError("AUTH_MODE=dev is only allowed when ENV_TYPE=sandbox")

#: The fixed sandbox principal. Its ``users`` row owns no records (see seed_sandbox_user).
DEV_USER_SUB = "dev"
DEV_USER_EMAIL = "dev@speaker-tracker.local"


@dataclass(frozen=True)
class Principal:
    """The authenticated caller.

    Parameters
    ----------
    sub : str
        The Cognito subject — the stable unique identifier used as the ``users`` key.
    email : str
        The caller's email, or an empty string if the token carried none.
    """

    sub: str
    email: str


def principal_from_event(event: dict) -> Principal:
    """Resolve the calling principal from an API Gateway HTTP event.

    In ``dev`` mode returns the fixed sandbox principal without inspecting the event. In
    ``cognito`` mode reads the gateway-verified JWT claims; the Lambda only runs after the
    authorizer accepts the token, so absent claims indicate a misconfiguration.

    Parameters
    ----------
    event : dict
        An API Gateway HTTP API v2 event.

    Returns
    -------
    Principal
        The authenticated caller.

    Raises
    ------
    common.errors.Unauthorized
        In ``cognito`` mode when no ``sub`` claim is present (maps to HTTP 401).
    RuntimeError
        In ``cognito`` mode when ``sub`` is present but ``email`` is absent — almost
        always the access token sent in place of the ID token (maps to HTTP 500, not
        401, so it cannot loop the sign-in redirect).
    """
    if _AUTH_MODE == "dev":
        return Principal(sub=DEV_USER_SUB, email=DEV_USER_EMAIL)

    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    sub = claims.get("sub")
    if not sub:
        raise errors.Unauthorized("no authenticated principal")
    email = claims.get("email")
    if not email:
        # sub present but no email almost always means the ACCESS token was sent instead
        # of the ID token (access tokens omit email) — ARCHITECTURE §6.1. Fail as 500, not
        # 401: re-auth can't fix it, so 401 would loop useApi()->signinRedirect().
        raise RuntimeError(
            f"authenticated token for sub={sub} has no email claim; "
            "expected the Cognito ID token (access tokens omit email)"
        )
    return Principal(sub=sub, email=email)
