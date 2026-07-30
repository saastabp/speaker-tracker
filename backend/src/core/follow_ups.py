"""Follow-up reminder scheduling rules — pure domain logic, no AWS, no SQL, no clock.

A follow-up row is the *record*; an EventBridge schedule is the *reminder*. This module owns the
rule connecting them, and nothing else: given a row's fields it answers "what schedule should exist
for this, if any", and given a before/after pair it answers "what call does that require". The
boto3 calls themselves live in ``common/scheduler.py``; the handler is the composition root.

Three decisions from DEV-PLAN slice 7 are realized here (all settled with Brian 2026-07-29):

**The reminder fires at 07:00 in the user's own timezone** (:data:`REMINDER_HOUR`).
``follow_ups.due_date`` is a DATE and carries no time, so an hour has to be chosen somewhere.
EventBridge Scheduler accepts ``at(2026-08-01T07:00:00)`` alongside a separate
``ScheduleExpressionTimezone``, which means the local wall-clock time we want *is* the expression —
there is no UTC arithmetic anywhere in this codebase, which is where this class of bug normally
lives. The hour is applied when the schedule is built and never stored, so moving it is a code
change rather than a data migration.

**The payload is frozen at schedule-creation time and ``followup_notify`` never reads the
database** (the deterministic ``followup-<id>`` naming in DATABASE.md is what buys that: no
read-back, no stored schedule id). The consequence is the recreate rule below, and it is the reason
:class:`ReminderSchedule` carries the rendered strings rather than just the row id.

**Whether a schedule should exist at all is a policy question with three independent ways to answer
no** — see :func:`wants_reminder`. Marking a follow-up done is one of them (acceptance #7): because
the notify Lambda never re-checks the row, a completed follow-up whose schedule survived would email
Donna about something she has already finished, which is the worst failure this slice can produce.

The recreate rule
-----------------
Any change to a field the reminder email renders must replace the schedule, not just the date —
acceptance #2 names the date alone, but the note and the contact/opportunity labels are equally
baked into the frozen payload. Rather than enumerate those field names (a list that has to be
maintained in lockstep with the notify template, and that will silently rot the first time a line is
added to the email), the rule is expressed as **inequality of the desired schedule itself**:
:class:`ReminderSchedule` is frozen, so ``before != after`` is exactly "something the reminder
depends on changed". A field that is not in the payload cannot be rendered; a field that is, is
automatically recreate-forcing.

Because the schedule name is a pure function of the row id, "cancel and recreate" collapses to a
single idempotent replace (:data:`PUT`) — one operation, and no window in which the schedule is
missing. That is a stronger form of acceptance #2's "only one email fires", not a weaker one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

#: Local hour at which a follow-up reminder email fires, in the user's own timezone.
#:
#: 07:00 lands before the working day starts; a reminder arriving at 00:01 reads as an alert rather
#: than a prompt. Applied when the schedule expression is built, never persisted.
REMINDER_HOUR = 7

#: Reconcile action: create or replace the schedule with the desired state.
PUT = "put"

#: Reconcile action: cancel the schedule. Cancelling one that never existed, or that has already
#: fired and self-deleted, is harmless (acceptance #3) — which is what makes it safe to issue this
#: without first proving a schedule is there.
DELETE = "delete"

#: Reconcile action: the reminder is unaffected by this edit; make no scheduler call.
NOOP = "noop"


@dataclass(frozen=True)
class ReminderSchedule:
    """The complete desired state of one follow-up's reminder schedule.

    Every field is either an input to the EventBridge schedule itself or a value the reminder email
    renders, and the payload is frozen at creation time because ``followup_notify`` never reads the
    database. Those two facts are why equality of this object is the recreate rule (see the module
    docstring): if two desired states are equal, the reminder that would fire is byte-for-byte the
    same one, so replacing it would be a no-op.

    Attributes
    ----------
    follow_up_id : int
        The ``follow_ups.id`` this reminder belongs to. Also what the deterministic schedule name
        ``followup-<id>`` is derived from, in ``common/scheduler.py``.
    expression : str
        An EventBridge Scheduler one-time expression, ``at(YYYY-MM-DDTHH:MM:SS)``.
    timezone : str
        IANA name for ``ScheduleExpressionTimezone`` — the zone ``expression`` is read in.
    to_address : str
        Recipient of the reminder: ``users.email``, Cognito's copy.
    note : str
        The follow-up's note; the body of the reminder.
    due_date : date
        The calendar day the follow-up is for, as rendered in the email.
    contact_name : str or None
        Display name of the linked contact, or ``None`` for a gig-level reminder that names no
        person.
    opportunity_title : str or None
        Title of the linked opportunity, or ``None`` for a person-level reminder that belongs to no
        gig. At least one of this and ``contact_name`` is present for any row that satisfies
        ``ck_follow_ups_target``.
    """

    follow_up_id: int
    expression: str
    timezone: str
    to_address: str
    note: str
    due_date: date
    contact_name: str | None
    opportunity_title: str | None

    def payload(self) -> dict:
        """Return the JSON-ready dict carried as the schedule's ``Input``.

        This **is** the contract between the handler that creates a schedule and
        ``handlers/followup_notify``, which renders the email and never reads the database — so it
        is defined once here rather than hand-built at both ends, where the two copies would
        eventually disagree about a key name and produce a reminder with a blank line in it.

        ``expression`` and ``timezone`` are absent on purpose: they tell EventBridge *when* to fire,
        which the email itself has no use for. ``due_date`` is serialized as an ISO string because
        the payload is JSON on the wire and a ``date`` is not.

        Returns
        -------
        dict
            Keys ``follow_up_id``, ``to_address``, ``note``, ``due_date`` (ISO ``YYYY-MM-DD``),
            ``contact_name``, ``opportunity_title``.

        Examples
        --------
        >>> from datetime import date
        >>> ReminderSchedule(7, "at(2026-08-01T07:00:00)", "Pacific/Honolulu",
        ...                  "d@example.com", "Chase contract", date(2026, 8, 1),
        ...                  None, "Wellness Wheel").payload()["due_date"]
        '2026-08-01'
        """
        return {
            "follow_up_id": self.follow_up_id,
            "to_address": self.to_address,
            "note": self.note,
            "due_date": self.due_date.isoformat(),
            "contact_name": self.contact_name,
            "opportunity_title": self.opportunity_title,
        }


def fires_at(due_date: date) -> datetime:
    """Return the local wall-clock instant a follow-up due on ``due_date`` fires.

    Parameters
    ----------
    due_date : date
        The follow-up's calendar due date.

    Returns
    -------
    datetime
        A naive datetime at :data:`REMINDER_HOUR` on ``due_date``. Naive on purpose: it is a local
        wall-clock time, and the zone travels separately as ``ScheduleExpressionTimezone``.
        Attaching a tzinfo here would invite exactly the UTC conversion this design avoids.

    Examples
    --------
    >>> from datetime import date
    >>> fires_at(date(2026, 8, 1))
    datetime.datetime(2026, 8, 1, 7, 0)
    """
    return datetime(due_date.year, due_date.month, due_date.day, REMINDER_HOUR)


def schedule_expression(due_date: date) -> str:
    """Return the EventBridge Scheduler one-time expression for a follow-up due on ``due_date``.

    Parameters
    ----------
    due_date : date
        The follow-up's calendar due date.

    Returns
    -------
    str
        ``at(YYYY-MM-DDTHH:MM:SS)``. Deliberately carries no ``Z`` and no UTC offset — EventBridge
        reads it in the zone given by ``ScheduleExpressionTimezone``, so a reminder set for Donna's
        Tuesday fires on Donna's Tuesday regardless of where the schedule is evaluated.

    Examples
    --------
    >>> from datetime import date
    >>> schedule_expression(date(2026, 8, 1))
    'at(2026-08-01T07:00:00)'
    """
    return f"at({fires_at(due_date).isoformat(timespec='seconds')})"


def fires_in_past(due_date: date, now_local: datetime) -> bool:
    """Return whether the fire instant for ``due_date`` has already gone by.

    A follow-up created for *today* after 07:00 — a realistic case, since Donna is looking at the
    app when she creates it — would otherwise produce a one-time schedule pointing into the past.

    Parameters
    ----------
    due_date : date
        The follow-up's calendar due date.
    now_local : datetime
        The current time in the user's timezone, naive (the caller strips tzinfo, as in
        ``core/periods.py``). Passing this in rather than reading a clock is what keeps this module
        pure and unit-testable.

    Returns
    -------
    bool
        ``True`` when :func:`fires_at` is at or before ``now_local``.

    Examples
    --------
    >>> from datetime import date, datetime
    >>> fires_in_past(date(2026, 8, 1), datetime(2026, 8, 1, 10, 0))
    True
    >>> fires_in_past(date(2026, 8, 2), datetime(2026, 8, 1, 10, 0))
    False
    """
    return fires_at(due_date) <= now_local


def wants_reminder(
    remind_by_email: bool, completed_at: datetime | None, deleted_at: datetime | None
) -> bool:
    """Return whether a follow-up row is one that should have a live reminder schedule.

    Three independent ways to answer no, each from a different acceptance criterion:

    - ``remind_by_email`` false makes the row dashboard-only; no schedule is ever created for it.
      (This does not contradict the composer's opt-in rider, which decides whether a follow-up is
      created at all — a different question from whether an explicitly created one emails.)
    - ``completed_at`` set means done, and done must cancel (acceptance #7).
    - ``deleted_at`` set means deleted, and deleted must cancel (acceptance #3).

    Clock-free by design: this is the policy gate only. Whether the fire instant has already passed
    is :func:`fires_in_past`, and the two are combined in :func:`desired_schedule`.

    Parameters
    ----------
    remind_by_email : bool
        The row's ``remind_by_email`` flag.
    completed_at : datetime or None
        The row's ``completed_at``. ``None`` is the pending state — there is no ``status`` column;
        this column *is* the done-state (DATABASE.md, overriding DESIGN.md §4).
    deleted_at : datetime or None
        The row's soft-delete marker.

    Returns
    -------
    bool
        ``True`` only when the row is pending, undeleted, and asks to be emailed.

    Examples
    --------
    >>> wants_reminder(True, None, None)
    True
    >>> from datetime import datetime
    >>> wants_reminder(True, datetime(2026, 7, 30, 9, 0), None)
    False
    >>> wants_reminder(False, None, None)
    False
    """
    return remind_by_email and completed_at is None and deleted_at is None


def desired_schedule(
    *,
    follow_up_id: int,
    due_date: date,
    note: str,
    remind_by_email: bool,
    completed_at: datetime | None,
    deleted_at: datetime | None,
    to_address: str,
    timezone: str,
    now_local: datetime,
    contact_name: str | None = None,
    opportunity_title: str | None = None,
) -> ReminderSchedule | None:
    """Return the schedule a follow-up row should have, or ``None`` if it should have none.

    ``None`` is returned when the row is not one that reminds (:func:`wants_reminder`) **or** when
    its fire instant has already gone by (:func:`fires_in_past`). The second case is deliberately
    silent rather than an error: a follow-up created for today at 10:00 is still a real follow-up
    and still belongs on the Dashboard's due list — it just has no future moment left to email
    about, and emailing Donna the instant she types something is noise, not a reminder.

    Parameters
    ----------
    follow_up_id : int
        The ``follow_ups.id``.
    due_date : date
        The row's calendar due date.
    note : str
        The row's note — the body of the reminder.
    remind_by_email : bool
        The row's ``remind_by_email`` flag.
    completed_at : datetime or None
        The row's ``completed_at``; ``None`` is pending.
    deleted_at : datetime or None
        The row's soft-delete marker.
    to_address : str
        Recipient address (``users.email``).
    timezone : str
        The user's IANA timezone, already validated by ``common/tz.py``.
    now_local : datetime
        Current time in that timezone, naive.
    contact_name : str or None, optional
        Display name of the linked contact, if any.
    opportunity_title : str or None, optional
        Title of the linked opportunity, if any.

    Returns
    -------
    ReminderSchedule or None
        The desired state, or ``None`` when no schedule should exist.

    Examples
    --------
    >>> from datetime import date, datetime
    >>> s = desired_schedule(
    ...     follow_up_id=42,
    ...     due_date=date(2026, 8, 1),
    ...     note="Chase the Hanalei contract",
    ...     remind_by_email=True,
    ...     completed_at=None,
    ...     deleted_at=None,
    ...     to_address="donna@example.com",
    ...     timezone="Pacific/Honolulu",
    ...     now_local=datetime(2026, 7, 30, 9, 0),
    ... )
    >>> s.expression
    'at(2026-08-01T07:00:00)'
    >>> desired_schedule(
    ...     follow_up_id=42,
    ...     due_date=date(2026, 7, 30),
    ...     note="Chase the Hanalei contract",
    ...     remind_by_email=True,
    ...     completed_at=None,
    ...     deleted_at=None,
    ...     to_address="donna@example.com",
    ...     timezone="Pacific/Honolulu",
    ...     now_local=datetime(2026, 7, 30, 9, 0),
    ... ) is None
    True
    """
    if not wants_reminder(remind_by_email, completed_at, deleted_at):
        return None
    if fires_in_past(due_date, now_local):
        return None
    return ReminderSchedule(
        follow_up_id=follow_up_id,
        expression=schedule_expression(due_date),
        timezone=timezone,
        to_address=to_address,
        note=note,
        due_date=due_date,
        contact_name=contact_name,
        opportunity_title=opportunity_title,
    )


def reconcile(before: ReminderSchedule | None, after: ReminderSchedule | None) -> str:
    """Return the scheduler action that moves a follow-up from ``before`` to ``after``.

    Intended for **edits** — creating a follow-up needs no before/after, just
    :func:`desired_schedule` and a :data:`PUT` if it returns anything.

    ``after is None`` yields :data:`DELETE` unconditionally, without consulting ``before``. That is
    not redundancy: ``before`` is itself computed against the current clock, so a follow-up whose
    fire time has passed evaluates to ``None`` even though a schedule may still exist for it — and
    comparing the two would then skip a cancel that was needed. Cancelling a schedule that never
    existed or has already fired is explicitly harmless (acceptance #3), so the asymmetry costs at
    most a wasted API call and closes the one gap that would let a live schedule survive a
    completion.

    Parameters
    ----------
    before : ReminderSchedule or None
        Desired state computed from the row as it was.
    after : ReminderSchedule or None
        Desired state computed from the row as it now is, using the **same** ``now_local``.

    Returns
    -------
    str
        :data:`DELETE` when no schedule should exist, :data:`NOOP` when the desired state is
        unchanged, else :data:`PUT`.

    Examples
    --------
    >>> reconcile(None, None)
    'delete'
    >>> from datetime import date
    >>> s = ReminderSchedule(1, "at(2026-08-01T07:00:00)", "Pacific/Honolulu",
    ...                      "d@example.com", "Call", date(2026, 8, 1), "Kalei", None)
    >>> reconcile(s, s)
    'noop'
    >>> import dataclasses
    >>> reconcile(s, dataclasses.replace(s, note="Call twice"))
    'put'
    """
    if after is None:
        return DELETE
    if before == after:
        return NOOP
    return PUT
