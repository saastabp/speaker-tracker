"""Shared AWS environment resolution.

Small on purpose: it holds the pieces that were genuinely identical across ``common`` modules, not
everything that looked similar.

Each service keeps its own ``_client()`` accessor — that function is the seam tests monkeypatch, and
collapsing the four into one factory would cost each module the docstring on its public
``reset_client`` for the sake of about a dozen lines. What *was* identical, word for word, is the
client construction in ``storage`` and ``scheduler`` and the region rule in ``db`` and ``secrets``;
those live here.
"""

from __future__ import annotations

import os
from typing import Any

import boto3

#: The region every Lambda gets from the runtime; the per-subsystem overrides fall back to it.
_DEFAULT_REGION_ENV = "AWS_REGION"


def client_for(service: str) -> Any:
    """Create a boto3 client in the runtime's region, letting boto3 resolve it when unset.

    For the services that simply live wherever the Lambda does. ``region_name`` is *omitted* rather
    than passed as ``None`` when ``AWS_REGION`` is unset, so boto3 falls back to its own resolution
    chain (config file, instance metadata) instead of being told "no region" — which is what makes
    this work under local tooling that never sets the variable.

    Services that need a say in their region do not use this: ``mail`` pins SES to a region it is
    actually available in, and ``secrets`` treats a missing region as fatal. See
    :func:`resolve_region`.

    Parameters
    ----------
    service : str
        The boto3 service name, e.g. ``"s3"``. Always a module literal, never request input.

    Returns
    -------
    Any
        A boto3 client. Untyped because botocore generates client classes at runtime.

    Examples
    --------
    >>> _client_instance = client_for("s3")
    """
    region = os.environ.get(_DEFAULT_REGION_ENV)
    return boto3.client(service, region_name=region) if region else boto3.client(service)


def resolve_region(override_env: str, purpose: str) -> str:
    """Resolve an AWS region from a subsystem's override variable, else the runtime's.

    The override exists because a subsystem can legitimately live in a different region from the
    Lambda — SES in particular is not available in every region — so each reads its own variable
    first. The *rule* is the same everywhere, though, and it had been written out once per
    subsystem with only the variable name and the message differing.

    Parameters
    ----------
    override_env : str
        Subsystem-specific variable checked first, e.g. ``"DB_REGION"``.
    purpose : str
        What the region is needed for, used in the error message ("for RDS IAM auth").

    Returns
    -------
    str
        The resolved region name.

    Raises
    ------
    RuntimeError
        When neither the override nor ``AWS_REGION`` is set. Raised rather than defaulted: a
        guessed region fails later, further from the cause, and against the wrong account's
        resources.

    Examples
    --------
    >>> resolve_region("DB_REGION", "for RDS IAM auth")
    'us-west-2'
    """
    region = os.environ.get(override_env) or os.environ.get(_DEFAULT_REGION_ENV)
    if not region:
        raise RuntimeError(f"{override_env} or {_DEFAULT_REGION_ENV} must be set {purpose}")
    return region
