"""Follow-ups router — scheduled reminders, and the EventBridge schedules behind them.

Four flat routes (``/follow-ups``), with the contact and opportunity links carried in the body and
offered as query filters rather than as nested paths. A reminder belongs to a person, a gig, or
both, so neither is its parent — the same "filter axes over one journal" shape ``outreaches`` uses,
and it means the Follow-ups page, the contact panel and the opportunity panel are all one route
instead of three (each additional path being another chance to miss a CDK ``ROUTES`` entry, which
is the slice-2 gateway gap).

This module is the **composition root for the reminder**: it is the only place that knows both a
follow-up row and an EventBridge schedule exist. ``core.follow_ups`` decides what schedule a row
*should* have, ``common.scheduler`` performs the AWS call, and neither knows about the other.

**The database commits before the scheduler is touched, on every mutating route.** If the scheduler
call then fails, the follow-up still exists and still shows on the Dashboard — only the email is
lost, and ``common.scheduler`` has already logged it at WARNING. The opposite order would risk an
orphan schedule emailing Donna about a follow-up that no longer exists, which is worse: a missed
reminder is silent, a phantom reminder is actively wrong.

Editing reads the row **before and after** the write and asks ``core.follow_ups.reconcile`` what
changed, with the same ``now_local`` for both — so "which field changes force a schedule replace" is
answered by comparing desired states, never by inspecting which key the client happened to send.
"""

from __future__ import annotations

from aws_lambda_powertools.event_handler.api_gateway import Router

from common import errors, scheduler
from common.db import db_now_local, transaction
from common.logger import logger
from core.follow_ups import DELETE, PUT, desired_schedule, reconcile
from handlers.context import AuthenticatedRequest, authenticate
from handlers.params import path_int
from models.follow_ups import FollowUpInput, FollowUpPatch, FollowUpRider, FollowUpSummary
from repositories import follow_ups as follow_ups_repo

router = Router()


def _desired(request: AuthenticatedRequest, row: dict | None):
    """Return the schedule a summary row should have, or None when it should have none.

    Translates a repository row into ``core.follow_ups``' arguments — the layer boundary that keeps
    ``core`` free of SQL and ``common.scheduler`` free of ``core``. The recipient is the JWT's email
    claim rather than a ``users.email`` read: it is the same Cognito value and always current, since
    ``repositories.users`` deliberately never refreshes that column after the first insert.

    ``row`` may be ``None`` (a deleted or missing follow-up), which is simply "no schedule".
    """
    if row is None:
        return None
    return desired_schedule(
        follow_up_id=row["id"],
        due_date=row["due_date"],
        note=row["note"],
        remind_by_email=bool(row["remind_by_email"]),
        completed_at=row["completed_at"],
        deleted_at=None,  # a soft-deleted row never reads back; absence is handled above
        to_address=request.principal.email,
        timezone=request.timezone,
        now_local=db_now_local(request.connection),
        contact_name=row["contact_name"],
        opportunity_title=row["opportunity_title"],
    )


def _apply(action: str, follow_up_id: int, schedule) -> None:
    """Perform the scheduler call ``reconcile`` asked for, logging the decision either way.

    The decision is logged even when it is "do nothing", because a reminder that quietly failed to
    change is indistinguishable from one that was never meant to — and this is the one place that
    distinction is still visible.
    """
    if action == PUT and schedule is not None:
        scheduler.put_schedule(
            follow_up_id=follow_up_id,
            expression=schedule.expression,
            timezone=schedule.timezone,
            payload=schedule.payload(),
        )
    elif action == DELETE:
        scheduler.delete_schedule(follow_up_id=follow_up_id)
    logger.info("Reminder schedule action=%s follow_up_id=%s", action, follow_up_id)


def create_rider_follow_up(
    conn,
    user_id: int,
    rider: FollowUpRider | None,
    *,
    contact_id: int | None,
    opportunity_id: int | None,
    fallback_note: str,
) -> int | None:
    """Create the follow-up an opt-in rider asked for, or return None when there is no rider.

    Shared by the outreach and email paths so "logging a touch also sets a reminder" is one rule
    rather than two that drift. **Call inside the caller's transaction** — the reminder and the
    thing it rides on should land together or not at all — then schedule with
    :func:`schedule_new_follow_up` after the commit.

    ``rider is None`` **is** the off state, and returning ``None`` for it is what makes acceptance
    #6 structural: there is no branch that could accidentally create a follow-up for a send that
    did not ask for one.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection inside the caller's transaction.
    user_id : int
        The owning user.
    rider : models.follow_ups.FollowUpRider or None
        The opt-in request. ``None`` means the switch was off.
    contact_id, opportunity_id : int or None
        Inherited from the parent action — the contact the email went to, the gig it was
        attributed to. The rider carries no links of its own; that is the point of it.
    fallback_note : str
        Used when the rider carries no note, derived from the parent action's context (an email's
        subject, a contact's name) so Donna is not asked to retype what the app already knows.

    Returns
    -------
    int or None
        The new follow-up's id, or ``None`` when there was no rider.
    """
    if rider is None:
        return None
    return follow_ups_repo.create_follow_up(
        conn,
        user_id,
        FollowUpInput(
            due_date=rider.due_date,
            note=(rider.note or fallback_note).strip(),
            contact_id=contact_id,
            opportunity_id=opportunity_id,
        ),
    )


def schedule_new_follow_up(request: AuthenticatedRequest, follow_up_id: int) -> None:
    """Create the EventBridge schedule for a freshly created follow-up.

    No before-state to reconcile against, so this is the create half of :func:`reconcile` on its
    own. Call **after** the transaction commits: a scheduler failure then loses the reminder email
    but keeps the row, where the reverse ordering could leave a schedule with no row behind it.
    """
    row = follow_ups_repo.get_follow_up(request.connection, request.user_id, follow_up_id)
    schedule = _desired(request, row)
    _apply(PUT if schedule is not None else "none", follow_up_id, schedule)


@router.post("/follow-ups")
def create_follow_up() -> dict:
    """Create a follow-up and schedule its reminder; return the created row.

    A row whose ``remind_by_email`` is false, or whose 07:00 has already passed today, is created
    normally but gets no schedule — it is a Dashboard-only reminder. That is not an error and not
    logged as one.
    """
    request = authenticate(router.current_event.raw_event)
    data = FollowUpInput.model_validate(router.current_event.json_body or {})
    with transaction(request.connection) as conn:
        follow_up_id = follow_ups_repo.create_follow_up(conn, request.user_id, data)
    logger.info(
        "Created follow_up id=%s contact_id=%s opportunity_id=%s user_id=%s",
        follow_up_id,
        data.contact_id,
        data.opportunity_id,
        request.user_id,
    )
    schedule_new_follow_up(request, follow_up_id)
    row = follow_ups_repo.get_follow_up(request.connection, request.user_id, follow_up_id)
    return FollowUpSummary(**row).model_dump(mode="json")


@router.get("/follow-ups")
def list_follow_ups() -> dict:
    """Return the caller's follow-ups, soonest first.

    Optional ``contact_id`` / ``opportunity_id`` narrow to one parent's reminders (ANDed when both
    are given). ``organization_id`` is different in kind: it has no column of its own and instead
    matches reminders on any of that venue's gigs **or** any contact affiliated with it, which is
    what a venue page means by "our follow-ups". ``pending_only=true`` drops completed rows. The
    unfiltered call is the Follow-ups page, which shows completed history too.
    """
    request = authenticate(router.current_event.raw_event)
    params = router.current_event.query_string_parameters or {}
    contact_id = params.get("contact_id")
    opportunity_id = params.get("opportunity_id")
    organization_id = params.get("organization_id")
    rows = follow_ups_repo.list_follow_ups(
        request.connection,
        request.user_id,
        contact_id=path_int(contact_id, "contact_id") if contact_id else None,
        opportunity_id=path_int(opportunity_id, "opportunity_id") if opportunity_id else None,
        organization_id=(path_int(organization_id, "organization_id") if organization_id else None),
        pending_only=str(params.get("pending_only", "")).lower() == "true",
    )
    return {"follow_ups": [FollowUpSummary(**row).model_dump(mode="json") for row in rows]}


@router.patch("/follow-ups/<follow_up_id>")
def patch_follow_up(follow_up_id: str) -> dict:
    """Edit a follow-up — date, note, email flag, or done-state — and reconcile its schedule.

    Marking done is this route with ``{"completed": true}``, not a separate endpoint: completion
    cancels the schedule exactly as a date change replaces it, and routing both through one code
    path is what guarantees a completed follow-up can never keep emailing (acceptance #7).
    """
    request = authenticate(router.current_event.raw_event)
    follow_up_id_int = path_int(follow_up_id, "follow_up_id")
    data = FollowUpPatch.model_validate(router.current_event.json_body or {})

    before = _desired(
        request,
        follow_ups_repo.get_follow_up(request.connection, request.user_id, follow_up_id_int),
    )
    with transaction(request.connection) as conn:
        matched = follow_ups_repo.patch_follow_up(conn, request.user_id, follow_up_id_int, data)
    if not matched:
        raise errors.NotFound("follow-up not found")
    logger.info("Patched follow_up id=%s user_id=%s", follow_up_id_int, request.user_id)

    row = follow_ups_repo.get_follow_up(request.connection, request.user_id, follow_up_id_int)
    after = _desired(request, row)
    _apply(reconcile(before, after), follow_up_id_int, after)
    return FollowUpSummary(**row).model_dump(mode="json")


@router.delete("/follow-ups/<follow_up_id>")
def delete_follow_up(follow_up_id: str) -> dict:
    """Soft-delete a follow-up and cancel its reminder (acceptance #3).

    The cancel is unconditional: it does not matter whether a schedule exists, whether it already
    fired, or whether the row ever had ``remind_by_email`` set. Cancelling nothing is harmless, and
    checking first would be a read-back this design deliberately does without.
    """
    request = authenticate(router.current_event.raw_event)
    follow_up_id_int = path_int(follow_up_id, "follow_up_id")
    with transaction(request.connection) as conn:
        deleted = follow_ups_repo.soft_delete_follow_up(conn, request.user_id, follow_up_id_int)
    if not deleted:
        raise errors.NotFound("follow-up not found")
    logger.info("Deleted follow_up id=%s user_id=%s", follow_up_id_int, request.user_id)
    _apply(DELETE, follow_up_id_int, None)
    return {"deleted": True}
