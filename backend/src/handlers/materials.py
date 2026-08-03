"""Materials router — the reusable file library behind Talks & Materials and the composer.

Bytes never pass through the API in either direction. Upload is a presigned PUT straight to S3, and
download and preview are presigned GETs straight back — the same shape the composer's ad-hoc
attachments already use, for the same reason: a 25 MB file through Lambda is cost and latency for
nothing.

**A key is not a capability, and this module is where that is enforced.** The browser tells us which
key it uploaded to, and an unchecked key would be an open door: naming
``email/raw/<someone-else>/…`` would register another user's object as your material and hand you a
presigned URL for it a moment later. Every key arriving from a client is therefore checked against
the caller's own ``materials/<user_id>/`` prefix before anything is stored or signed.

**Size and type are read from S3, never taken from the request.** The upload does not pass through
here, so a client-reported size is an unverified claim — and it is the number the cap is enforced
against. A HEAD after upload makes both facts true by construction.
"""

from __future__ import annotations

import uuid

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors, storage
from common.db import transaction
from common.logger import logger
from handlers.context import authenticate
from handlers.params import path_int
from models.materials import (
    MaterialFileReplacement,
    MaterialInput,
    MaterialSummary,
    MaterialUpdate,
    MaterialUploadRequest,
)
from repositories import materials as materials_repo

router = Router()


def _own_key_prefix(user_id: int) -> str:
    """Return the only prefix this user's materials may live under."""
    return f"{storage.MATERIAL_PREFIX}{user_id}/"


def _validate_own_key(user_id: int, s3_key: str) -> str:
    """Return ``s3_key`` if it belongs to this user, else refuse.

    The check that stops a client naming somebody else's object. Shares
    :func:`common.storage.owns_key` with the email composer, which enforces the same rule over a
    wider set of prefixes — one definition of "is this yours", not two.

    Rejected as InvalidInput rather than NotFound: the caller supplied a key that is not theirs to
    use, which is a bad request, and distinguishing "exists but not yours" from "does not exist"
    would leak whether it exists.
    """
    if not storage.owns_key(user_id, s3_key, [storage.MATERIAL_PREFIX]):
        logger.warning(
            "Rejected a material key outside the caller's prefix user_id=%s key=%s", user_id, s3_key
        )
        raise errors.InvalidInput("s3_key is not one of your uploads")
    return s3_key


def _verified_object(user_id: int, s3_key: str) -> tuple[str, int]:
    """Return an uploaded object's ``(content_type, size_bytes)``, enforcing the size cap.

    Raises
    ------
    common.errors.InvalidInput
        When the key is not the caller's, when no object is there — a claimed upload that never
        happened — or when the object is larger than :data:`storage.MAX_MATERIAL_BYTES`.
    """
    _validate_own_key(user_id, s3_key)
    try:
        size_bytes, content_type = storage.head_object(s3_key)
    except Exception:
        # The row must not be written for an object that is not there; it would list a material
        # whose download 404s, which looks like data loss rather than a failed upload.
        logger.warning("No uploaded object at key=%s for user_id=%s", s3_key, user_id)
        raise errors.InvalidInput("no uploaded file found for that key") from None
    if size_bytes > storage.MAX_MATERIAL_BYTES:
        # Cleaned up rather than left to linger: nothing will ever reference it, and it is the one
        # object we know for certain is garbage.
        storage.delete_object_best_effort(s3_key)
        limit_mb = storage.MAX_MATERIAL_BYTES // (1024 * 1024)
        raise errors.InvalidInput(f"file is larger than the {limit_mb} MB limit")
    return content_type, size_bytes


@router.post("/materials/upload-url")
def create_material_upload() -> dict:
    """Issue a presigned PUT so the browser uploads material bytes directly to S3.

    The key is **server-generated** and user-scoped; accepting a client-supplied one would let a
    caller write under another user's prefix. A UUID segment keeps two uploads of the same filename
    apart, which is also what makes replacing a material a new key rather than an overwrite.
    """
    request = authenticate(router.current_event.raw_event)
    data = MaterialUploadRequest.model_validate(router.current_event.json_body or {})
    key = f"{_own_key_prefix(request.user_id)}{uuid.uuid4().hex}/{data.filename}"
    url = storage.presigned_put_url(key, content_type=data.content_type)
    logger.info(
        "Issued material upload key=%s user_id=%s content_type=%s",
        key,
        request.user_id,
        data.content_type,
    )
    return {"upload_url": url, "s3_key": key, "content_type": data.content_type}


@router.get("/materials")
def list_materials() -> dict:
    """Return the caller's materials; ``?talk_id=`` scopes to one talk."""
    request = authenticate(router.current_event.raw_event)
    params = router.current_event.query_string_parameters or {}
    raw_talk_id = params.get("talk_id")
    talk_id = path_int(raw_talk_id, "talk_id") if raw_talk_id else None
    rows = materials_repo.list_materials(request.connection, request.user_id, talk_id=talk_id)
    return {"materials": [MaterialSummary(**row).model_dump(mode="json") for row in rows]}


@router.post("/materials")
def create_material() -> dict:
    """Register an uploaded object as a material, recording its verified size and type."""
    request = authenticate(router.current_event.raw_event)
    data = MaterialInput.model_validate(router.current_event.json_body or {})
    content_type, size_bytes = _verified_object(request.user_id, data.s3_key)
    with transaction(request.connection) as conn:
        material_id = materials_repo.create_material(
            conn, request.user_id, data, content_type=content_type, size_bytes=size_bytes
        )
    logger.info(
        "Created material id=%s user_id=%s size_bytes=%s content_type=%s",
        material_id,
        request.user_id,
        size_bytes,
        content_type,
    )
    row = materials_repo.get_material(request.connection, request.user_id, material_id)
    return MaterialSummary(**row).model_dump(mode="json")


@router.put("/materials/<material_id>")
def update_material(material_id: str) -> dict:
    """Rename a material or move it between talks. The file is replaced separately."""
    request = authenticate(router.current_event.raw_event)
    mid = path_int(material_id, "material_id")
    data = MaterialUpdate.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        updated = materials_repo.update_material(conn, request.user_id, mid, data)
    if not updated:
        raise errors.NotFound("material not found")
    logger.info("Updated material id=%s user_id=%s", mid, request.user_id)
    row = materials_repo.get_material(request.connection, request.user_id, mid)
    return MaterialSummary(**row).model_dump(mode="json")


@router.put("/materials/<material_id>/file")
def replace_material_file(material_id: str) -> dict:
    """Point an existing material at newly uploaded bytes, keeping its id, name and talk.

    Overwriting a one-sheet is the ordinary way this library stays current, and it is safe:
    attaching a material copies its bytes into the message, so every email already sent keeps the
    version it went out with.

    The superseded object is deleted **after** the row points at the new one, best-effort — a
    failed cleanup leaks storage but must not fail a replacement that already succeeded.
    """
    request = authenticate(router.current_event.raw_event)
    mid = path_int(material_id, "material_id")
    data = MaterialFileReplacement.model_validate(router.current_event.json_body or {})
    content_type, size_bytes = _verified_object(request.user_id, data.s3_key)
    with transaction(request.connection) as conn:
        superseded = materials_repo.replace_material_file(
            conn,
            request.user_id,
            mid,
            s3_key=data.s3_key,
            content_type=content_type,
            size_bytes=size_bytes,
        )
    if superseded is None:
        raise errors.NotFound("material not found")
    logger.info(
        "Replaced material file id=%s user_id=%s size_bytes=%s superseded_key=%s",
        mid,
        request.user_id,
        size_bytes,
        superseded,
    )
    storage.delete_object_best_effort(superseded)
    row = materials_repo.get_material(request.connection, request.user_id, mid)
    return MaterialSummary(**row).model_dump(mode="json")


@router.get("/materials/<material_id>/url")
def material_url(material_id: str) -> dict:
    """Return a short-lived presigned URL for one material.

    ``?disposition=attachment`` forces a download under the material's name; the default is inline,
    which is what a preview needs.

    The URL points at S3 rather than at us, and that is the security property: a previewed file
    renders in a different origin from the SPA and cannot reach the ID token in its memory. It must
    never be fetched and inlined into our own DOM instead.
    """
    request = authenticate(router.current_event.raw_event)
    mid = path_int(material_id, "material_id")
    row = materials_repo.get_material(request.connection, request.user_id, mid)
    if row is None:
        raise errors.NotFound("material not found")
    params = router.current_event.query_string_parameters or {}
    as_download = params.get("disposition") == "attachment"
    url = storage.presigned_get_url(row["s3_key"], download_as=row["name"] if as_download else None)
    logger.info(
        "Issued material URL id=%s user_id=%s disposition=%s",
        mid,
        request.user_id,
        "attachment" if as_download else "inline",
    )
    return {"url": url, "expires_in": storage.PRESIGNED_GET_TTL_S}


@router.delete("/materials/<material_id>")
def delete_material(material_id: str) -> dict:
    """Remove a material from the library. Soft — the object stays so undelete remains possible."""
    request = authenticate(router.current_event.raw_event)
    mid = path_int(material_id, "material_id")
    with transaction(request.connection) as conn:
        deleted = materials_repo.soft_delete_material(conn, request.user_id, mid)
    if not deleted:
        raise errors.NotFound("material not found")
    logger.info("Deleted material id=%s user_id=%s", mid, request.user_id)
    return {"deleted": True}
