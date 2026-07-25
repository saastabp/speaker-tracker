"""Pydantic contracts for per-user email signatures.

A signature is fully-styled HTML (composed in the Tiptap editor) that the email composer appends to
outgoing mail. Exactly one signature per user is the default (enforced in the repository, not the
schema); ``name`` + ``is_default`` leave room for multiple signatures later with no migration.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SignatureInput(BaseModel):
    """Writable fields for creating or replacing a signature.

    Parameters
    ----------
    name : str
        Display name for the signature (e.g. "Formal"); 1-255 characters.
    body_html : str
        The styled HTML body from the editor; required.
    is_default : bool
        Whether this is the composer's default signature. Setting it clears the default on the
        caller's other signatures (repository invariant); defaults to False.
    """

    name: str = Field(min_length=1, max_length=255)
    body_html: str = Field(min_length=1)
    is_default: bool = False


class Signature(SignatureInput):
    """A stored signature: the writable fields plus its id and audit timestamps."""

    id: int
    created_at: datetime
    updated_at: datetime
