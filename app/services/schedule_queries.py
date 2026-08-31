"""Query-only helpers and pure date arithmetic for recurring Group
schedules (M08).

Deliberately independent of Flask: no ``request``, ``flash``,
``redirect``, or template rendering here, and no route decorators --
plain functions over the ORM and over ``datetime`` values, mirroring
``app/services/group_memberships.py`` and
``app/services/academic_lifecycle.py`` so any Blueprint can import them.

**These helpers take no locks.** A read here reflects whatever snapshot
the caller's transaction already holds. An authoritative mutation caller
MUST first hold the ``AcademicTerm -> Level -> Course -> Group ->
Schedule`` lock chain (``app/services/academic_hierarchy_transactions.py``
-> ``app/services/group_transactions.py`` ->
``app/services/schedule_transactions.py``) and re-run the relevant helper
against that locked, current data before deciding. A pre-lock call is
only acceptable for a friendly preview whose result is re-verified after
the lock.

Recurrence convention: ``day_of_week`` is an integer, Monday=0 ..
Sunday=6 -- the same numbering as ``datetime.date.weekday()``. Overnight
slots are out of scope, so every slot has ``start_time < end_time`` on
the same civil day.
"""

from datetime import timedelta

from sqlalchemy import or_

from app.extensions import db
from app.models import AcademicStatus, Group, Schedule


def first_weekday_on_or_after(day_of_week, from_date):
    """The first calendar date ``>= from_date`` whose weekday is
    ``day_of_week`` (Monday=0 .. Sunday=6)."""
    offset = (day_of_week - from_date.weekday()) % 7
    return from_date + timedelta(days=offset)


def range_contains_weekday(day_of_week, range_start, range_end):
    """True if the inclusive date range ``[range_start, range_end]``
    contains at least one actual occurrence of ``day_of_week``. An empty
    range (``range_start > range_end``) contains nothing."""
    if range_start > range_end:
        return False
    return first_weekday_on_or_after(day_of_week, range_start) <= range_end


def term_contains_range(term_start, term_end, effective_start, effective_end):
    """True if ``[effective_start, effective_end]`` lies entirely within
    ``[term_start, term_end]`` (inclusive at both ends)."""
    return term_start <= effective_start and effective_end <= term_end


def times_overlap(start_a, end_a, start_b, end_b):
    """Half-open interval overlap test for ``[start_a, end_a)`` vs
    ``[start_b, end_b)``: ``start_a < end_b and end_a > start_b``.
    Adjacent intervals (``end_a == start_b``) do **not** overlap."""
    return start_a < end_b and end_a > start_b


def effective_ranges_share_weekday(day_of_week, start_a, end_a, start_b, end_b):
    """True if the two inclusive effective-date ranges share at least one
    actual calendar date whose weekday is ``day_of_week`` -- i.e. their
    date-range intersection is non-empty *and* contains an occurrence of
    that weekday. Non-overlapping effective periods, and periods that
    overlap only on other weekdays, both return False."""
    overlap_start = max(start_a, start_b)
    overlap_end = min(end_a, end_b)
    return range_contains_weekday(day_of_week, overlap_start, overlap_end)


def conflicting_active_schedule(
    group_id,
    day_of_week,
    start_time,
    end_time,
    effective_start_date,
    effective_end_date,
    exclude_schedule_id=None,
):
    """Return the first **active** Schedule in ``group_id`` that
    operationally conflicts with the given slot, or ``None``.

    Two active slots in the same Group conflict only when ALL of:

    - the weekday is equal;
    - the half-open time intervals overlap
      (``start < other_end and end > other_start``) -- adjacent times are
      allowed;
    - their effective date ranges share at least one actual calendar date
      of that weekday.

    Archived rows never participate. The exact-slot ``UniqueConstraint``
    on ``schedules`` is a separate, final defense against byte-for-byte
    duplicates; this cross-row overlap rule is the authoritative
    application logic and must be rechecked after locking.

    The weekday + time-overlap filter runs in SQL; the
    shared-weekday-date test runs in Python over the (small, group-scoped)
    candidate set.
    """
    candidates = Schedule.query.filter(
        Schedule.group_id == group_id,
        Schedule.status == AcademicStatus.ACTIVE.value,
        Schedule.day_of_week == day_of_week,
        Schedule.start_time < end_time,
        Schedule.end_time > start_time,
    )
    if exclude_schedule_id is not None:
        candidates = candidates.filter(Schedule.id != exclude_schedule_id)
    for other in candidates.order_by(Schedule.id).all():
        if effective_ranges_share_weekday(
            day_of_week,
            effective_start_date,
            effective_end_date,
            other.effective_start_date,
            other.effective_end_date,
        ):
            return other
    return None


def group_has_schedule_history(group_id):
    """True if **any** Schedule row -- active or archived -- exists for
    this Group.

    A Schedule row freezes the Group's academic identity
    (``academic_term_id`` / ``course_id``) exactly like
    Enrollment/GroupTeacherAssignment history does -- see
    ``app/services/group_memberships.group_has_membership_history``. Every
    Schedule range was authored against this Group's Term, so retargeting
    the Group afterwards would silently reinterpret it.
    """
    return db.session.query(Schedule.id).filter_by(group_id=group_id).first() is not None


def term_schedule_range_outside(term_id, new_start, new_end):
    """Return the first Schedule belonging to ``term_id`` (through its
    Group) whose effective range is **not** fully contained within
    ``[new_start, new_end]``, or ``None``.

    Deliberately unfiltered by status and by the Group's status: an
    archived Schedule, and a Schedule under an archived Group, are still
    historical rows whose range must stay inside the Term. Used by the
    AcademicTerm date-edit guard -- a Term's dates may change only if
    every existing Schedule effective range in that Term still fits.
    """
    return (
        Schedule.query.join(Group, Schedule.group_id == Group.id)
        .filter(
            Group.academic_term_id == term_id,
            or_(
                Schedule.effective_start_date < new_start,
                Schedule.effective_end_date > new_end,
            ),
        )
        .order_by(Schedule.id)
        .first()
    )
