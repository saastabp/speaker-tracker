"""Pydantic contracts for the message-template library.

A template carries two orthogonal axes as separate catalog references (DATABASE.md §5): ``kind``
(``message_template_kinds`` — the purpose/audience, e.g. cold pitch vs power-partner intro) and
``channel`` (``outreach_channels`` — how it is sent, dm / email). ``body`` holds merge fields like
``[Name]`` resolved client-side for the copy-to-clipboard flow (acceptance #3); ``subject`` is
present for email templates and null for DM templates.

Shared templates (``user_id IS NULL``) are reference content editable in place; **Duplicate** writes
a personal copy (acceptance #4). Responses expose ``is_shared`` rather than ``user_id`` so the SPA
can show the edit-in-place vs Duplicate affordances without seeing owner ids.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MessageTemplateInput(BaseModel):
    """Writable template fields, for create and full-replace update.

    Parameters
    ----------
    kind : str
        ``message_template_kinds`` catalog short_name (the purpose/audience).
    channel : str
        ``outreach_channels`` catalog short_name (how it is sent).
    name : str
        Display name; 1-255 characters.
    subject : str or None
        Email subject line; null for DM templates.
    body : str
        Template body with merge fields (e.g. ``[Name]``); required.
    """

    kind: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=255)
    subject: str | None = Field(default=None, max_length=255)
    body: str = Field(min_length=1)


class MessageTemplateSummary(MessageTemplateInput):
    """A template, for responses. ``is_shared`` is True for a reference row (``user_id`` NULL)."""

    id: int
    is_shared: bool
    created_at: datetime
    updated_at: datetime
