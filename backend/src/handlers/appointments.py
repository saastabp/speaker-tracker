"""Appointments router — the logged meetings behind the Dashboard's "Coming up" card.

Four flat routes under ``/appointments``, with the contact link carried in the body and offered as
a query filter rather than as a nested path. That is the shape ``follow_ups`` uses, and it means the
Appointments page and the contact panel are served by one route instead of two — each extra path
being another chance to miss a CDK ``ROUTES`` entry, which is the slice-2 gateway gap.

**This is a logging feature, not a calendar.** Compare ``handlers/follow_ups.py``, which is the
composition root for an EventBridge schedule and has to order its database write against an AWS
call on every route. There is deliberately none of that here: nothing is scheduled, invited, synced
or emailed, so every route is a plain transaction and the absence is the feature's boundary.

Ownership and reference validation live in the repository, as everywhere else.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors
from common.db import transaction
from common.logger import logger
from handlers.context import authenticate
from handlers.params import path_int, query_choice
from models.appointments import AppointmentInput, AppointmentPatch, AppointmentSummary
from repositories import appointments as appointments_repo

router = Router()

#: The scope vocabulary the list route accepts, mirroring ``models.appointments.AppointmentScope``.
_SCOPES = ("upcoming", "past", "all")


@router.post("/appointments")
def create_appointment() -> dict:
    """Log an appointment; return the created row."""
    request = authenticate(router.current_event.raw_event)
    data = AppointmentInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        appointment_id = appointments_repo.create_appointment(conn, request.user_id, data)
    logger.info(
        "Created appointment id=%s contact_id=%s scheduled_at=%s user_id=%s",
        appointment_id,
        data.contact_id,
        data.scheduled_at,
        request.user_id,
    )
    row = appointments_repo.get_appointment(request.connection, request.user_id, appointment_id)
    return AppointmentSummary(**row).model_dump(mode="json")


@router.get("/appointments")
def list_appointments() -> dict:
    """Return the caller's appointments, optionally narrowed by ``scope`` and ``contact_id``.

    ``scope`` is ``upcoming`` / ``past`` / ``all`` and defaults to ``all`` — an unfiltered call
    means everything, and it is the page's toggle that chooses a half. The Dashboard asks for
    ``upcoming`` through its own repository call rather than this route.
    """
    request = authenticate(router.current_event.raw_event)
    params = router.current_event.query_string_parameters or {}
    contact_id = params.get("contact_id")
    rows = appointments_repo.list_appointments(
        request.connection,
        request.user_id,
        scope=query_choice(params.get("scope"), "scope", _SCOPES, "all"),
        contact_id=path_int(contact_id, "contact_id") if contact_id else None,
    )
    return {"appointments": [AppointmentSummary(**row).model_dump(mode="json") for row in rows]}


@router.patch("/appointments/<appointment_id>")
def patch_appointment(appointment_id: str) -> dict:
    """Edit an appointment — person, title, time or details — and return the updated row."""
    request = authenticate(router.current_event.raw_event)
    appointment_id_int = path_int(appointment_id, "appointment_id")
    data = AppointmentPatch.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        matched = appointments_repo.patch_appointment(
            conn, request.user_id, appointment_id_int, data
        )
    if not matched:
        raise errors.NotFound("appointment not found")
    logger.info(
        "Patched appointment id=%s fields=%s user_id=%s",
        appointment_id_int,
        sorted(data.model_fields_set),
        request.user_id,
    )
    row = appointments_repo.get_appointment(request.connection, request.user_id, appointment_id_int)
    return AppointmentSummary(**row).model_dump(mode="json")


@router.delete("/appointments/<appointment_id>")
def delete_appointment(appointment_id: str) -> dict:
    """Soft-delete an appointment."""
    request = authenticate(router.current_event.raw_event)
    appointment_id_int = path_int(appointment_id, "appointment_id")
    with transaction(request.connection) as conn:
        deleted = appointments_repo.soft_delete_appointment(
            conn, request.user_id, appointment_id_int
        )
    if not deleted:
        raise errors.NotFound("appointment not found")
    logger.info("Deleted appointment id=%s user_id=%s", appointment_id_int, request.user_id)
    return {"deleted": True}
