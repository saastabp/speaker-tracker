"""Pydantic contracts for follow-up reminders.

A follow-up is a *future, actionable* reminder against a contact, an opportunity, or both — which
is what separates it from the two resources it resembles: ``outreaches`` records touches that
already happened, and ``opportunity_notes`` records dated commentary. Only a follow-up can be
marked done, and only a follow-up makes the app reach out to Donna rather than the other way round
(``0010_followups.sql``, DEV-PLAN slice 7).

The wire contract follows the project's Option-A rule — entities by id, catalog vocabularies by
short_name. There are no catalogs here, so every field is a literal or an entity id.

Four decisions are expressed in the shapes below:

**Both links are individually optional but not both absent.** :class:`FollowUpInput` mirrors the
``ck_follow_ups_target`` CHECK so an unreachable follow-up is rejected as a 400 rather than
surfacing as a pymysql ``IntegrityError`` through the catch-all as a 500 (acceptance #5). The
database constraint remains the real guarantee; this is the polite layer in front of it.

**Links are set at create and are not patchable.** :class:`FollowUpPatch` deliberately omits
``contact_id`` / ``opportunity_id``: re-linking would mean re-validating the target CHECK on every
edit, and slice 7 has no use case for moving a reminder between parents.

**Marking done is a patch field, not a dedicated endpoint.** Slice 3 gave status and payment their
own routes, but decision 3 (settled 2026-07-29) is that complete/uncomplete is cancel-and-recreate
*exactly like a date edit* — so it is modelled as the same operation, and one code path calls
``core.follow_ups.reconcile`` for every mutation. Two paths would eventually disagree, and the way
they would disagree is a completed follow-up whose schedule survived and emails Donna about
something she has already finished.

**``completed`` is a bool, not a timestamp.** The server stamps ``completed_at``; a client does not
get to say when something was finished. ``completed_at IS NULL`` is the pending state — there is no
``status`` column (DATABASE.md, overriding DESIGN.md §4).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator


class FollowUpInput(BaseModel):
    """A follow-up reminder, for create.

    Parameters
    ----------
    due_date : date
        The calendar day the reminder is for. A DATE, never a relative "in N days" (DESIGN.md §7);
        the *time* it fires is not client-controlled — ``core.follow_ups.REMINDER_HOUR`` applies
        07:00 in the user's own timezone when the schedule is built.
    note : str
        Free-form text; it becomes the body of the reminder email, so it is required and non-empty.
    contact_id : int or None
        The contact this reminder is about. ``None`` for a gig-level reminder that names no person
        ("chase the Hanalei contract").
    opportunity_id : int or None
        The gig this reminder is about. ``None`` for a person-level reminder that belongs to no gig
        ("check in with Kalei").
    remind_by_email : bool
        Whether to actually email. ``False`` makes the row dashboard-only and **no schedule is
        created for it**. Defaults to ``True``: having explicitly asked for a reminder, the default
        is that it reminds you. This does not contradict the composer's opt-in rider (see
        :class:`FollowUpRider`), which answers the different question of whether a follow-up gets
        created at all.

    Raises
    ------
    ValueError
        If neither ``contact_id`` nor ``opportunity_id`` is set.

    Examples
    --------
    >>> gig = FollowUpInput(due_date=date(2026, 8, 1), note="Chase contract", opportunity_id=7)
    >>> gig.contact_id is None and gig.remind_by_email
    True
    >>> try:  # neither link: a 400 here rather than a 500 from the CHECK
    ...     FollowUpInput(due_date=date(2026, 8, 1), note="Chase contract")
    ... except ValueError:
    ...     print("rejected")
    rejected
    """

    due_date: date
    note: str = Field(min_length=1)
    contact_id: int | None = None
    opportunity_id: int | None = None
    remind_by_email: bool = True

    @model_validator(mode="after")
    def _check_target(self) -> FollowUpInput:
        """Reject a follow-up attached to neither a contact nor an opportunity."""
        if self.contact_id is None and self.opportunity_id is None:
            raise ValueError("a follow-up needs a contact_id, an opportunity_id, or both")
        return self


class FollowUpPatch(BaseModel):
    """A partial edit to an existing follow-up.

    Every field is optional and ``None`` means **unchanged**. No patchable field is nullable in the
    database, so the usual "omitted versus explicitly null" ambiguity has no consequence here — a
    client sending ``{"note": null}`` gets the same result as omitting it.

    Any change to ``due_date`` or ``note`` alters what the reminder email renders and therefore
    forces a schedule replace; ``remind_by_email`` and ``completed`` decide whether a schedule
    should exist at all. All four are handled uniformly by ``core.follow_ups.reconcile`` — the
    handler does not special-case which one moved.

    Parameters
    ----------
    due_date : date or None
        New calendar due date.
    note : str or None
        New note text; non-empty when provided.
    remind_by_email : bool or None
        Turn the email reminder on or off, leaving the row itself in place.
    completed : bool or None
        ``True`` marks the follow-up done (the server stamps ``completed_at`` and cancels the
        schedule, acceptance #7); ``False`` reopens it and recreates the schedule.
    """

    due_date: date | None = None
    note: str | None = Field(default=None, min_length=1)
    remind_by_email: bool | None = None
    completed: bool | None = None


class FollowUpRider(BaseModel):
    """An opt-in request to create a follow-up alongside some other action.

    Attached to the email-send and outreach-log inputs so Donna can log a touch and set the next
    reminder in one step. **Opt-in and off by default** — the field it hangs from defaults to
    ``None``, and sending an email must never silently schedule anything (DESIGN.md §7, acceptance
    #6). Present-but-empty is not a thing: a rider exists only when a due date was chosen.

    The links are not carried here — they are inherited from the parent action (the contact the
    email went to, the opportunity it was attributed to), which is the whole point of the rider.

    Parameters
    ----------
    due_date : date
        The calendar day for the follow-up being created.
    note : str or None
        Note for the follow-up. ``None`` lets the caller derive one from the parent action's
        context rather than making Donna retype it.
    """

    due_date: date
    note: str | None = None


class FollowUpSummary(BaseModel):
    """A follow-up as returned to clients, for list and single-item responses.

    ``contact_name`` and ``opportunity_title`` are denormalized for display so a list render needs
    no follow-up lookups; either may be ``None``, and by ``ck_follow_ups_target`` never both.

    Whether the row is *pending* is ``completed_at is None`` — no separate flag is sent, because a
    derived boolean beside the timestamp is the same two-columns-one-fact drift the schema
    deliberately avoids. Whether it is *overdue* is likewise not sent: it depends on the viewer's
    today, and the SPA already knows the user's timezone.
    """

    id: int
    due_date: date
    note: str
    contact_id: int | None
    contact_name: str | None
    opportunity_id: int | None
    opportunity_title: str | None
    remind_by_email: bool
    completed_at: datetime | None
    created_at: datetime
