"""Shared AWS environment resolution.

Small on purpose: it holds the pieces that were genuinely identical across ``common`` modules, not
everything that looked similar. The per-service boto3 client accessors stay in their own modules —
see :func:`resolve_region` for why the region rule did not.
"""

from __future__ import annotations

import os

#: The region every Lambda gets from the runtime; the per-subsystem overrides fall back to it.
_DEFAULT_REGION_ENV = "AWS_REGION"


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
