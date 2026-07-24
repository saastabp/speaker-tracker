"""Message-templates router — the reusable outreach copy library.

Templates are visible to the caller when shared (``user_id IS NULL``) or personally owned. Shared
reference rows are editable in place; **Duplicate** forks one into a personal copy (acceptance #4).
Merge fields in the body are resolved client-side for the copy-to-clipboard flow, so the server
just stores and serves the text. Visibility, edit-in-place, and duplicate semantics live in the
repository; this router is the HTTP surface.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors
from common.db import transaction
from common.logger import logger
from handlers.context import authenticate
from handlers.params import path_int
from models.message_templates import MessageTemplateInput, MessageTemplateSummary
from repositories import message_templates as templates_repo

router = Router()


def _template_response(conn, user_id: int, template_id: int) -> dict:
    """Return one visible template serialized, or raise NotFound."""
    row = templates_repo.get_message_template(conn, user_id, template_id)
    if row is None:
        raise errors.NotFound("message template not found")
    return MessageTemplateSummary(**row).model_dump(mode="json")


@router.get("/templates")
def list_templates() -> dict:
    """Return every template visible to the caller (own + shared), shared first."""
    request = authenticate(router.current_event.raw_event)
    rows = templates_repo.list_message_templates(request.connection, request.user_id)
    return {"templates": [MessageTemplateSummary(**row).model_dump(mode="json") for row in rows]}


@router.post("/templates")
def create_template() -> dict:
    """Create a personal template and return it."""
    request = authenticate(router.current_event.raw_event)
    data = MessageTemplateInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        template_id = templates_repo.create_message_template(conn, request.user_id, data)
    logger.info("Created message template id=%s user_id=%s", template_id, request.user_id)
    return _template_response(request.connection, request.user_id, template_id)


@router.get("/templates/<template_id>")
def get_template(template_id: str) -> dict:
    """Return one visible template."""
    request = authenticate(router.current_event.raw_event)
    return _template_response(
        request.connection, request.user_id, path_int(template_id, "template_id")
    )


@router.put("/templates/<template_id>")
def update_template(template_id: str) -> dict:
    """Full-replace a visible template (including a shared row, in place) and return it."""
    request = authenticate(router.current_event.raw_event)
    template_id_int = path_int(template_id, "template_id")
    data = MessageTemplateInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        updated = templates_repo.update_message_template(
            conn, request.user_id, template_id_int, data
        )
    if not updated:
        raise errors.NotFound("message template not found")
    logger.info("Updated message template id=%s user_id=%s", template_id_int, request.user_id)
    return _template_response(request.connection, request.user_id, template_id_int)


@router.post("/templates/<template_id>/duplicate")
def duplicate_template(template_id: str) -> dict:
    """Fork a visible template into a personal copy and return the copy (acceptance #4)."""
    request = authenticate(router.current_event.raw_event)
    source_id = path_int(template_id, "template_id")
    with transaction(request.connection) as conn:
        new_id = templates_repo.duplicate_message_template(conn, request.user_id, source_id)
    logger.info(
        "Duplicated message template source_id=%s new_id=%s user_id=%s",
        source_id,
        new_id,
        request.user_id,
    )
    return _template_response(request.connection, request.user_id, new_id)


@router.delete("/templates/<template_id>")
def delete_template(template_id: str) -> dict:
    """Soft-delete one of the caller's own templates (shared rows are protected)."""
    request = authenticate(router.current_event.raw_event)
    template_id_int = path_int(template_id, "template_id")
    with transaction(request.connection) as conn:
        deleted = templates_repo.soft_delete_message_template(
            conn, request.user_id, template_id_int
        )
    if not deleted:
        raise errors.NotFound("message template not found")
    logger.info("Deleted message template id=%s user_id=%s", template_id_int, request.user_id)
    return {"deleted": True}
