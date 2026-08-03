"""Pydantic contracts for materials — the reusable file library.

A material is a file the user keeps to send repeatedly: a one-sheet, a speaker menu, a headshot
pack. The bytes live in S3; this contract carries the row that indexes them.

**The client never states a material's size or type.** It uploads to a presigned URL and then names
the key; the server reads the size and content type back from S3 (``storage.head_object``). A
browser-reported size is an unverified claim, and it is the number the upload cap is enforced
against — so :class:`MaterialInput` deliberately has nowhere to put one.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MaterialInput(BaseModel):
    """Register an already-uploaded object as a material.

    Parameters
    ----------
    name : str
        Display name, normally the original filename; 1-255 characters.
    s3_key : str
        Key the browser PUT to, as issued by ``POST /materials/upload-url``. Validated against the
        caller's own prefix server-side — a key is not a capability.
    talk_id : int or None
        Optional talk this material belongs to. ``None`` is a general material, which is what a
        one-sheet or a headshot pack usually is.
    """

    name: str = Field(min_length=1, max_length=255)
    s3_key: str = Field(min_length=1, max_length=512)
    talk_id: int | None = None


class MaterialUpdate(BaseModel):
    """Rename a material or move it between talks. The file is replaced separately.

    Metadata and bytes are separate operations because they fail differently: a rename is one
    statement, while a replacement is an upload that can be abandoned half-done. Splitting them
    keeps a failed upload from also losing the name.
    """

    name: str = Field(min_length=1, max_length=255)
    talk_id: int | None = None


class MaterialFileReplacement(BaseModel):
    """Point an existing material at newly uploaded bytes.

    Overwriting a one-sheet in place is the normal way this library is kept current — the file
    keeps its name, its talk, and its id, and everything that already used it is unaffected.
    **Attaching a material copies its bytes into the message**, so a sent email carries its own
    copy and does not change when the library does.

    The key is new rather than reused: an upload that fails half-way would otherwise leave the row
    pointing at a truncated object. Size and type are re-read from S3, never taken from the client.
    """

    s3_key: str = Field(min_length=1, max_length=512)


class MaterialSummary(BaseModel):
    """One row in the materials list, and in the composer's attachment picker."""

    id: int
    talk_id: int | None
    name: str
    s3_key: str
    content_type: str
    size_bytes: int
    sort_order: int
    created_at: datetime
    updated_at: datetime


class MaterialUploadRequest(BaseModel):
    """Ask for a presigned PUT so the browser can upload bytes directly to S3."""

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream", max_length=255)
