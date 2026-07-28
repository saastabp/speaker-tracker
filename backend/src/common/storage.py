"""S3 access for application content — the single place the backend knows about object storage.

This is the first S3 code in the backend (the only other AWS clients are ``rds`` in
:mod:`common.db` and ``secretsmanager`` in :mod:`common.secrets`), so it establishes the
convention rather than consolidating existing duplication.

**One bucket, prefixed by purpose.** The bucket name arrives in ``CONTENT_BUCKET``; keys are
namespaced so a later slice extends this module instead of provisioning a second bucket and a
second access path:

===================== =========================================================================
``email/raw/``        Sent raw MIME, one object per message (``email_messages.s3_key``). The
                      exact bytes handed to SES and IMAP ``APPEND``, so a thread view can
                      reconstruct a body and list attachments — neither is a column.
``email/attachments/`` Ad-hoc composer uploads, PUT directly by the browser via a presigned URL
                      and read back here when the MIME is assembled (slice 6a acceptance #6).
``materials/``        Reusable one-sheets and speaker menus. **Not used yet** — the materials
                      library is a later slice, which will add a presigned *GET* here for
                      downloads. That function is deliberately absent until it has a caller.
===================== =========================================================================

**The bucket lives in the Api stack, not the Messaging stack.** Its only consumer is the API
Lambda, for both email attachments and (later) materials; materials are not messaging. Putting it
in Messaging would force the materials slice to reach across stacks for it, and cross-stack
references are how the sandbox CloudFront origin silently went stale on 2026-07-25.

The client is created lazily behind :func:`_client` — the seam tests monkeypatch, same as
``common.secrets._client`` — and never at import time, so an S3 outage fails a send rather than
breaking module initialization for every route.
"""

from __future__ import annotations

import os
import time
from typing import Final

import boto3

from common.logger import logger

#: Env var naming the content bucket, set by the Api stack.
CONTENT_BUCKET_ENV = "CONTENT_BUCKET"

#: Key prefixes. Callers compose keys from these rather than hardcoding strings, so the IAM
#: grants in the Api stack (which are prefix-scoped) and the code cannot drift apart.
RAW_MESSAGE_PREFIX: Final = "email/raw/"
ATTACHMENT_PREFIX: Final = "email/attachments/"
MATERIAL_PREFIX: Final = "materials/"

#: Lifetime of a presigned upload URL. Long enough for a slow connection to finish a one-sheet,
#: short enough that a leaked URL is not a durable write grant.
PRESIGNED_PUT_TTL_S = 900

_client_instance = None


def _client():
    """Return the module-cached boto3 S3 client, created on first use.

    Tests monkeypatch this function rather than boto3 itself, matching the seam pattern used by
    ``common.secrets._client`` and ``common.db.get_connection``.
    """
    global _client_instance
    if _client_instance is None:
        region = os.environ.get("AWS_REGION")
        _client_instance = boto3.client("s3", region_name=region) if region else boto3.client("s3")
    return _client_instance


def bucket_name() -> str:
    """Return the content bucket name from the environment.

    Returns
    -------
    str
        The bucket set in ``CONTENT_BUCKET``.

    Raises
    ------
    RuntimeError
        When the variable is unset — a deployment fault, not a runtime condition.
    """
    bucket = os.environ.get(CONTENT_BUCKET_ENV)
    if not bucket:
        raise RuntimeError(f"Required environment variable {CONTENT_BUCKET_ENV} is not set")
    return bucket


def raw_message_key(user_id: int, message_id: str) -> str:
    """Build the key for a message's raw MIME, sent or received.

    Keyed on the ``Message-ID`` rather than the row id, so both halves of the email path land on
    the same key without coordinating: the send path writes the bytes it hands to SES, and the
    poller writes the bytes it fetched from IMAP. That also makes the write idempotent — a message
    re-read after a ``UIDVALIDITY`` reset overwrites its own object with identical content instead
    of accumulating copies.

    Parameters
    ----------
    user_id : int
        Owning user, so one tenant's objects are prefix-separable from another's.
    message_id : str
        The RFC 5322 ``Message-ID``, brackets included as stored.

    Returns
    -------
    str
        A key under :data:`RAW_MESSAGE_PREFIX`.

    Examples
    --------
    >>> raw_message_key(7, "<abc@example.com>")
    'email/raw/7/abc@example.com.eml'
    """
    stripped = message_id.strip().lstrip("<").rstrip(">")
    return f"{RAW_MESSAGE_PREFIX}{user_id}/{stripped}.eml"


def get_object_bytes(key: str) -> bytes:
    """Fetch an object's bytes.

    Parameters
    ----------
    key : str
        Key within the content bucket.

    Returns
    -------
    bytes
        The object body.

    Raises
    ------
    botocore.exceptions.ClientError
        Propagated unchanged when the object is missing or access is denied, so the caller sees
        the real AWS error rather than an empty body standing in for a failure.
    """
    bucket = bucket_name()
    started = time.monotonic()
    response = _client().get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    logger.info(
        "S3 get bucket=%s key=%s bytes=%d duration_ms=%d",
        bucket,
        key,
        len(body),
        int((time.monotonic() - started) * 1000),
    )
    return body


def put_object(key: str, body: bytes, *, content_type: str = "application/octet-stream") -> str:
    """Store bytes and return the key.

    Parameters
    ----------
    key : str
        Key within the content bucket.
    body : bytes
        Object content.
    content_type : str, optional
        MIME type recorded on the object; defaults to ``application/octet-stream``.

    Returns
    -------
    str
        The `key` written, so a caller can store it on a row in one expression.

    Raises
    ------
    botocore.exceptions.ClientError
        Propagated unchanged on failure.
    """
    bucket = bucket_name()
    started = time.monotonic()
    _client().put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
    logger.info(
        "S3 put bucket=%s key=%s bytes=%d duration_ms=%d",
        bucket,
        key,
        len(body),
        int((time.monotonic() - started) * 1000),
    )
    return key


def presigned_put_url(
    key: str, *, content_type: str = "application/octet-stream", expires_in: int | None = None
) -> str:
    """Return a presigned URL the browser can PUT an attachment to directly.

    Attachment bytes never pass through the API (slice 6a acceptance #6): the composer uploads to
    this URL, then sends only the resulting key, and the MIME builder reads it back with
    :func:`get_object_bytes`.

    Parameters
    ----------
    key : str
        Destination key, normally under :data:`ATTACHMENT_PREFIX`.
    content_type : str, optional
        Content type the client must send. It is signed into the URL, so a client that PUTs a
        different type is rejected — the URL grants one specific upload, not arbitrary writes.
    expires_in : int or None, optional
        URL lifetime in seconds; defaults to :data:`PRESIGNED_PUT_TTL_S`.

    Returns
    -------
    str
        The presigned URL.
    """
    bucket = bucket_name()
    ttl = expires_in if expires_in is not None else PRESIGNED_PUT_TTL_S
    url = _client().generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=ttl,
    )
    # The URL embeds a signature; log the key and TTL, never the URL itself.
    logger.info("S3 presigned PUT issued bucket=%s key=%s ttl_s=%d", bucket, key, ttl)
    return url


def reset_client() -> None:
    """Clear the cached client. For tests, and for nothing else."""
    global _client_instance
    _client_instance = None
