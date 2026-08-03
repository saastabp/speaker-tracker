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
``materials/``        Reusable one-sheets and speaker menus (slice 9). Uploaded by presigned PUT
                      like an attachment, then read back by presigned *GET* for download and
                      preview — the caller that function was waiting for.
===================== =========================================================================

**A material is read through a presigned URL, never proxied through the API, and that is a security
property rather than a performance one.** The URL's origin is S3, so a previewed file renders in a
different origin from the SPA and cannot reach the ID token in its memory. Inlining a material into
our own DOM would undo that — the rule email bodies obey via ``SafeHtml``, for the same reason.

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
from collections.abc import Sequence
from typing import Final

from common.aws import client_for
from common.logger import logger

#: Env var naming the content bucket, set by the Api stack.
CONTENT_BUCKET_ENV = "CONTENT_BUCKET"

#: Key prefixes. Callers compose keys from these rather than hardcoding strings, so the IAM
#: grants in the Api stack (which are prefix-scoped) and the code cannot drift apart.
RAW_MESSAGE_PREFIX: Final = "email/raw/"
ATTACHMENT_PREFIX: Final = "email/attachments/"
MATERIAL_PREFIX: Final = "materials/"

#: Prefixes whose objects a caller may attach to an outgoing email: their own ad-hoc uploads and
#: their own materials. ``RAW_MESSAGE_PREFIX`` is **deliberately absent** — a stored message is not
#: an attachment, and allowing it would let a send exfiltrate mail.
ATTACHABLE_PREFIXES: Final = (ATTACHMENT_PREFIX, MATERIAL_PREFIX)

#: Lifetime of a presigned upload URL. Long enough for a slow connection to finish a one-sheet,
#: short enough that a leaked URL is not a durable write grant.
PRESIGNED_PUT_TTL_S = 900

#: Lifetime of a presigned download URL. Shorter than the upload's: a read URL is handed to the
#: browser on every listing and preview, so it leaks more easily and needs to expire sooner. Still
#: long enough to start a 25 MB download over a poor connection.
PRESIGNED_GET_TTL_S = 300

#: Largest material accepted, in bytes. Chosen against SES's 40 MB message limit rather than S3's:
#: a material exists to be attached to an email, so one too large to send is one that cannot do its
#: job. The margin covers base64 encoding (~1.37×) plus the body and signature.
MAX_MATERIAL_BYTES = 25 * 1024 * 1024

_client_instance = None


def _client():
    """Return the module-cached boto3 S3 client, created on first use.

    Tests monkeypatch this function rather than boto3 itself, matching the seam pattern used by
    ``common.secrets._client`` and ``common.db.get_connection``.
    """
    global _client_instance
    if _client_instance is None:
        _client_instance = client_for("s3")
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


def owns_key(user_id: int, key: str, prefixes: Sequence[str]) -> bool:
    """Return whether ``key`` is one of this user's objects under one of ``prefixes``.

    **A key that arrives from a client is a claim, not a capability.** Every key in this bucket is
    written as ``<prefix><user_id>/…`` by server-side code, so ownership is decidable from the
    string — and must be decided, every time, before an object is read, signed for, or attached.
    Skipping it turns any endpoint that takes a key into an arbitrary read of the whole bucket.

    Lives here rather than in a handler because two paths need the same rule: the materials library
    (its own prefix only) and the email composer (:data:`ATTACHABLE_PREFIXES`). Two copies of an
    ownership check is one copy too many.

    Parameters
    ----------
    user_id : int
        The caller.
    key : str
        The object key being claimed.
    prefixes : Sequence[str]
        Which prefixes are acceptable for this operation.

    Returns
    -------
    bool
        True iff the key sits under one of ``prefixes`` and is scoped to this user.

    Examples
    --------
    >>> owns_key(7, "materials/7/abc/one-sheet.pdf", [MATERIAL_PREFIX])
    True
    >>> owns_key(7, "materials/8/abc/theirs.pdf", [MATERIAL_PREFIX])
    False
    >>> owns_key(7, "email/raw/7/a-message.eml", ATTACHABLE_PREFIXES)
    False
    """
    return any(key.startswith(f"{prefix}{user_id}/") for prefix in prefixes)


def presigned_get_url(
    key: str, *, download_as: str | None = None, expires_in: int | None = None
) -> str:
    """Return a presigned URL the browser can read an object from directly.

    The counterpart to :func:`presigned_put_url`, and the reason the materials library can preview
    and download without the bytes passing back through the API.

    **This URL's origin is the security property, not an implementation detail.** It points at S3,
    so a previewed material renders in a different origin from the SPA and cannot reach the ID
    token held in the app's memory. A material must never be inlined into our own DOM instead —
    that is the same rule email bodies obey via ``SafeHtml``.

    Parameters
    ----------
    key : str
        Object key, normally under :data:`MATERIAL_PREFIX`.
    download_as : str or None, optional
        Filename to force a download under, via ``Content-Disposition: attachment``. ``None`` lets
        the browser display the object inline, which is what a preview wants.
    expires_in : int or None, optional
        URL lifetime in seconds; defaults to :data:`PRESIGNED_GET_TTL_S`.

    Returns
    -------
    str
        The presigned URL.
    """
    bucket = bucket_name()
    ttl = expires_in if expires_in is not None else PRESIGNED_GET_TTL_S
    params: dict = {"Bucket": bucket, "Key": key}
    if download_as:
        # Quoted so a filename containing a comma or a quote cannot break out of the header.
        escaped = download_as.replace('"', "")
        params["ResponseContentDisposition"] = f'attachment; filename="{escaped}"'
    url = _client().generate_presigned_url("get_object", Params=params, ExpiresIn=ttl)
    # The URL embeds a signature; log the key and TTL, never the URL itself.
    logger.info("S3 presigned GET issued bucket=%s key=%s ttl_s=%d", bucket, key, ttl)
    return url


def head_object(key: str) -> tuple[int, str]:
    """Return an object's ``(size_bytes, content_type)`` without fetching its body.

    Used to record a material's size and type from **S3 rather than from the client**. The browser
    uploads straight to a presigned URL, so a client-reported size is an unverified claim — and it
    is the number the upload cap is enforced against. A HEAD costs one request and makes both
    facts true by construction.

    Parameters
    ----------
    key : str
        Object key.

    Returns
    -------
    tuple of (int, str)
        Size in bytes, and the stored content type (``application/octet-stream`` when S3 has none).

    Raises
    ------
    botocore.exceptions.ClientError
        Propagated unchanged when the object is missing — which is the signal that a claimed upload
        never actually happened.
    """
    response = _client().head_object(Bucket=bucket_name(), Key=key)
    return int(response["ContentLength"]), response.get("ContentType") or "application/octet-stream"


def delete_object_best_effort(key: str) -> bool:
    """Delete an object, returning whether it went; a failure is logged, never raised.

    For objects nothing points at any more — the file a material used to hold after it has been
    replaced. It is called *after* the row already points at the new key, so a failure here leaks
    storage but breaks nothing, and failing the user's request over it would turn a successful
    replacement into an error.

    Logged at WARNING rather than swallowed, so an S3 permission problem that starts orphaning
    every superseded upload is visible to monitoring instead of silently accumulating.

    Parameters
    ----------
    key : str
        Object key to remove.

    Returns
    -------
    bool
        True when the delete call succeeded.
    """
    try:
        _client().delete_object(Bucket=bucket_name(), Key=key)
    except Exception:
        logger.warning(
            "Could not delete superseded object key=%s; it is now orphaned", key, exc_info=True
        )
        return False
    logger.info("S3 delete key=%s", key)
    return True


def reset_client() -> None:
    """Clear the cached client. For tests, and for nothing else."""
    global _client_instance
    _client_instance = None
