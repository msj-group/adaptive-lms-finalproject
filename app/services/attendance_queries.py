"""Read-only query layer and pure occurrence rules for Attendance
(Phase 4 / M07).

Flask-independent: plain functions over the ORM and over ``datetime``
values, no ``request`` / ``flash`` / ``redirect`` and no route decorators,
mirroring ``app/services/assignment_queries.py`` and
``app/services/schedule_queries.py``. **Every function here is read-only**
-- no locks, no writes, no commits.

Design rules (Part Phase 4 / M07):

- **Authorization lives in the SQL ``WHERE`` clause.** A session, a record
  or a roster row is never loaded broadly and filtered in Python
  afterwards. The Teacher reads are keyed off a Group the caller has
  already proved an active ``GroupTeacherAssignment`` to; the Student read
  is keyed off ``student_id`` *and* ``finalized_at IS NOT NULL``, so there
  is no query here that can return a row the caller may not see.
- **Every read is bounded.** Lists fetch :data:`PAGE_SIZE` ``+ 1`` rows to
  derive a non-disclosing "there is a next page" flag **without a COUNT**,
  and the two aggregates are ``GROUP BY`` queries whose result sets are
  bounded by (page size x four statuses) and by four statuses
  respectively. Nothing loads an unbounded attendance history.
- **No N+1.** Status counts for a whole page of sessions are one query
  keyed by the page's session ids, never one query per row.
- **Every row this module returns is converted to a plain presentation
  dict** before it reaches a template, so rendering attendance can never
  trigger a lazy load or an ORM-driven authorization decision. Internal
  numeric ids are used *inside* the SQL only -- for ordering tie-breaks
  and for joins -- and never placed in a dict that reaches a template.
- Ordering is always fully deterministic, tie-broken by an internal
  ``id``.

**The occurrence rules are pure and reuse M08/M09.** Nothing here invents
a second calendar: ``day_of_week`` is Monday=0..Sunday=6 exactly as
``datetime.date.weekday()`` and as ``schedules.day_of_week``, the
effective date range is inclusive at both ends, and "today" is a *local*
civil date the caller obtained once from
:func:`app.services.schedule_occurrences.app_now`. No function here reads
a wall clock, so a response can never contradict itself and tests are
deterministic.

**These helpers take no locks.** A read reflects whatever snapshot the
caller's transaction already holds. An authoritative mutation caller MUST
hold the M07 lock chain (see ``app/services/attendance_transactions.py``)
and re-run the relevant helper against the locked, current rows before
deciding. A pre-lock call is only acceptable as a friendly preview whose
result is re-verified after the lock.
"""

from sqlalchemy import func

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    Schedule,
    User,
    UserRole,
    UserStatus,
)
from app.services.assignment_queries import normalize_page  # noqa: F401  (re-exported)

_ACTIVE = AcademicStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value
_TEACHER = UserRole.TEACHER.value
_USER_ACTIVE = UserStatus.ACTIVE.value

#: Fixed page size for the Teacher session list, the Administrator review
#: list and the Student summary alike. Not configurable and never
#: client-supplied -- the explicit bound the Part requires instead of an
#: unbounded history. Every list query issues ``LIMIT PAGE_SIZE + 1``.
PAGE_SIZE = 20

#: The four statuses, in the order every page displays and totals them.
#: Declared once so a template, a form and an aggregate cannot disagree
#: about the order, and deliberately not alphabetical: it reads
#: present -> absent -> late -> excused, which is how a Teacher thinks
#: about a roster.
STATUS_ORDER = (
    AttendanceStatus.PRESENT.value,
    AttendanceStatus.ABSENT.value,
    AttendanceStatus.LATE.value,
    AttendanceStatus.EXCUSED.value,
)

STATUS_LABELS = {
    AttendanceStatus.PRESENT.value: "Present",
    AttendanceStatus.ABSENT.value: "Absent",
    AttendanceStatus.LATE.value: "Late",
    AttendanceStatus.EXCUSED.value: "Excused",
}

STATUS_VALUES = frozenset(STATUS_ORDER)

# ---------------------------------------------------------------------------
# Occurrence rules -- pure, and the ONLY place they are stated
# ---------------------------------------------------------------------------
#
# Returned as short codes rather than sentences: the Teacher-facing
# wording belongs to the Blueprint, which declares each sentence exactly
# once so the friendly pre-lock preview and the authoritative post-lock
# recheck can never explain the same rule differently.

#: The Schedule is archived, so it no longer produces occurrences.
OCCURRENCE_SCHEDULE_ARCHIVED = "schedule_archived"
#: The Schedule belongs to a different Group than the one in the URL.
OCCURRENCE_WRONG_GROUP = "wrong_group"
#: The chosen date is not the Schedule's weekday.
OCCURRENCE_WRONG_WEEKDAY = "wrong_weekday"
#: The chosen date falls outside the Schedule's inclusive effective range.
OCCURRENCE_OUTSIDE_RANGE = "outside_range"
#: The chosen date is in the future in the center timezone.
OCCURRENCE_FUTURE = "future"


def occurrence_rejection(schedule, group_id, session_date, today_local):
    """``None`` when `session_date` is a real, already-reached occurrence
    of `schedule` for `group_id`, else the code saying which rule failed.

    The five rules, and nothing else:

    1. the Schedule belongs to **exactly** this Group -- a cross-table
       condition no foreign key can express, so it is proved here and
       re-proved against the locked rows before the insert;
    2. the Schedule is active -- an archived slot no longer produces
       meetings, though sessions already recorded from it stay readable
       forever;
    3. the date's weekday equals ``schedule.day_of_week``;
    4. the date lies inside ``[effective_start_date, effective_end_date]``,
       inclusive at both ends;
    5. the date is not **after** `today_local`. Today itself is allowed:
       a Teacher records attendance during or right after the class, and
       requiring the meeting to have *ended* would make the common case
       impossible. Nothing here compares wall-clock times, so a session
       may legitimately be opened before that day's slot has finished.

    `today_local` is the **local civil date** in ``APP_TIMEZONE``, passed
    in by the caller, which obtains it once per request from
    :func:`app.services.schedule_occurrences.app_now`. Comparing a local
    civil date against a UTC one would let a Teacher near midnight be told
    that today is tomorrow.
    """
    if schedule is None or schedule.group_id != group_id:
        return OCCURRENCE_WRONG_GROUP
    if schedule.status != _ACTIVE:
        return OCCURRENCE_SCHEDULE_ARCHIVED
    if session_date.weekday() != schedule.day_of_week:
        return OCCURRENCE_WRONG_WEEKDAY
    if not (schedule.effective_start_date <= session_date <= schedule.effective_end_date):
        return OCCURRENCE_OUTSIDE_RANGE
    if session_date > today_local:
        return OCCURRENCE_FUTURE
    return None


# ---------------------------------------------------------------------------
# Nested lookups -- SQL-scoped, never "trust the public id"
# ---------------------------------------------------------------------------


def active_schedules_for_group(group_id):
    """Every **active** Schedule of one Group, ascending by
    ``(day_of_week, start_time, id)``.

    The create page offers exactly these and nothing else; an archived
    slot is not an option, and a slot belonging to another Group is not
    reachable because the query is keyed by ``group_id``.
    """
    return (
        Schedule.query.filter(
            Schedule.group_id == group_id, Schedule.status == _ACTIVE
        )
        .order_by(Schedule.day_of_week.asc(), Schedule.start_time.asc(), Schedule.id.asc())
        .all()
    )


def schedule_for_group(group_id, schedule_public_id):
    """One Schedule by its own ``public_id``, constrained to `group_id`,
    or ``None``. A Schedule public id valid only for another Group
    resolves to nothing here -- the nested-IDOR protection every other
    surface in this project uses. Status is deliberately **not** filtered:
    the caller decides what an archived slot means for what it is doing.
    """
    return Schedule.query.filter_by(
        public_id=schedule_public_id, group_id=group_id
    ).first()


def session_for_group(group_id, session_public_id):
    """One AttendanceSession by its own ``public_id``, constrained to
    `group_id`, or ``None``."""
    return AttendanceSession.query.filter_by(
        public_id=session_public_id, group_id=group_id
    ).first()


def session_for_occurrence(schedule_id, session_date):
    """The existing session for one scheduled occurrence, or ``None``.

    The exact shape of ``uq_attendance_sessions_schedule_date``. Used as a
    friendly pre-lock preview and, after an ``IntegrityError``, to resolve
    a duplicate create to the session that actually won.
    """
    return AttendanceSession.query.filter_by(
        schedule_id=schedule_id, session_date=session_date
    ).first()


def teacher_is_actively_assigned(teacher_id, group_id):
    """A scalar ``EXISTS``: does this Teacher hold an **active**
    assignment to this exact Group right now?"""
    clause = GroupTeacherAssignment.query.filter_by(
        group_id=group_id, teacher_id=teacher_id, status=_ASSIGNMENT_ACTIVE
    ).exists()
    return bool(db.session.query(clause).scalar())


# ---------------------------------------------------------------------------
# Roster capture -- the eligibility rule, stated once
# ---------------------------------------------------------------------------


def eligible_roster_rows(group_id):
    """``[(user_id, enrollment_id)]`` for every Student who is an eligible
    active member of `group_id` **right now**, ascending by ``user_id``.

    "Eligible" is all four of:

    - an ``active`` Enrollment,
    - in **exactly** this Group,
    - whose referenced User has role ``student``,
    - and whose account status is ``active``.

    The ``users`` join is not redundant with the Enrollment row: a foreign
    key into ``users`` proves the row exists, never that it is still a
    Student or still active -- the project rule already applied to
    Enrollment and GroupTeacherAssignment everywhere else.

    This is a **non-locking preview** that only discovers which ids to
    lock. The creation path re-locks every User row (ascending internal
    id) and every Enrollment row (ascending internal id) and re-applies
    exactly these four conditions to the locked rows before inserting a
    single record, so a membership change committed in the meantime can
    never slip into -- or out of -- a captured roster.
    """
    return [
        (row.user_id, row.enrollment_id)
        for row in db.session.query(
            User.id.label("user_id"), Enrollment.id.label("enrollment_id")
        )
        .join(Enrollment, Enrollment.student_id == User.id)
        .filter(
            Enrollment.group_id == group_id,
            Enrollment.status == _ENROLLMENT_ACTIVE,
            User.role == _STUDENT,
            User.status == _USER_ACTIVE,
        )
        .order_by(User.id.asc())
        .all()
    ]


# ---------------------------------------------------------------------------
# Bounded aggregates
# ---------------------------------------------------------------------------


def status_counts_for_sessions(session_ids):
    """``{session_id: {status: count}}`` for the given sessions, in **one**
    ``GROUP BY`` query.

    Asking per row would be an N+1 on a page that already knows every id
    it cares about. The result set is bounded by
    ``len(session_ids) x 4``, and ``session_ids`` is itself one page.
    An empty input issues no query at all.
    """
    ids = [sid for sid in session_ids if sid is not None]
    if not ids:
        return {}
    counts = {}
    rows = (
        db.session.query(
            AttendanceRecord.attendance_session_id,
            AttendanceRecord.status,
            func.count(AttendanceRecord.id),
        )
        .filter(AttendanceRecord.attendance_session_id.in_(ids))
        .group_by(AttendanceRecord.attendance_session_id, AttendanceRecord.status)
        .all()
    )
    for session_id, status, total in rows:
        counts.setdefault(session_id, {})[status] = total
    return counts


def ordered_counts(counts_for_session):
    """One session's counts as an ordered list of presentation dicts, with
    an explicit zero for a status nobody was marked with -- so the four
    columns are always present and a missing one can never be misread as
    a missing *record*."""
    counts = counts_for_session or {}
    return [
        {
            "status": status,
            "label": STATUS_LABELS[status],
            "count": counts.get(status, 0),
        }
        for status in STATUS_ORDER
    ]


# ---------------------------------------------------------------------------
# Teacher reads
# ---------------------------------------------------------------------------


def teacher_group_cards(teacher_id):
    """Every Group this Teacher is **actively assigned** to, as plain
    dicts, ascending by ``(group name, group id)``.

    Bounded by how many Groups one Teacher is assigned to, which is a
    small operational number rather than a history that grows over time;
    it is the same set the Teacher dashboard already renders. Archived
    Groups and archived ancestors are deliberately **included**: reading
    attendance is historical, and this overview is how a Teacher reaches
    it.
    """
    rows = (
        db.session.query(
            Group.public_id,
            Group.name,
            Group.status,
            Course.title,
            Level.name,
            AcademicTerm.name,
            AcademicTerm.status,
            Level.status,
            Course.status,
        )
        .select_from(GroupTeacherAssignment)
        .join(Group, GroupTeacherAssignment.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .filter(
            GroupTeacherAssignment.teacher_id == teacher_id,
            GroupTeacherAssignment.status == _ASSIGNMENT_ACTIVE,
        )
        .order_by(Group.name.asc(), Group.id.asc())
        .all()
    )
    cards = []
    for (
        group_public_id,
        group_name,
        group_status,
        course_title,
        level_name,
        term_name,
        term_status,
        level_status,
        course_status,
    ) in rows:
        archived = [
            label
            for label, status in (
                ("academic term", term_status),
                ("level", level_status),
                ("course", course_status),
                ("group", group_status),
            )
            if status != _ACTIVE
        ]
        cards.append(
            {
                "group_public_id": group_public_id,
                "group_name": group_name,
                "course_title": course_title,
                "level_name": level_name,
                "term_name": term_name,
                "operational": not archived,
                "archived_labels": archived,
            }
        )
    return cards


def _session_page_query(page):
    """The shared ``(offset, limit)`` for a session list page."""
    return (page - 1) * PAGE_SIZE, PAGE_SIZE + 1


def teacher_sessions_page(group_id, page):
    """``(rows, has_next)`` -- one bounded page of a Group's attendance
    sessions, newest local session date first.

    Deterministic SQL ordering (``session_date DESC, id DESC``) and
    ``LIMIT PAGE_SIZE + 1`` for the has-next flag, with **no** ``COUNT``:
    an exact total would be both an unbounded scan and a disclosure of how
    much history exists beyond the page.
    """
    offset, limit = _session_page_query(page)
    rows = (
        db.session.query(
            AttendanceSession.id,
            AttendanceSession.public_id,
            AttendanceSession.session_date,
            AttendanceSession.start_time,
            AttendanceSession.end_time,
            AttendanceSession.location,
            AttendanceSession.finalized_at,
            AttendanceSession.version,
            Schedule.day_of_week,
        )
        .join(Schedule, AttendanceSession.schedule_id == Schedule.id)
        .filter(AttendanceSession.group_id == group_id)
        .order_by(AttendanceSession.session_date.desc(), AttendanceSession.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


def build_session_list_view(rows, counts_by_session):
    """Plain presentation dicts for a page of sessions.

    The internal ``id`` is used **only** to look up this page's already
    fetched status counts and is then dropped -- it never reaches a
    template.
    """
    return [
        {
            "public_id": row.public_id,
            "session_date": row.session_date,
            "start_time": row.start_time,
            "end_time": row.end_time,
            "location": row.location,
            "day_of_week": row.day_of_week,
            "is_finalized": row.finalized_at is not None,
            "finalized_at": row.finalized_at,
            "counts": ordered_counts(counts_by_session.get(row.id)),
        }
        for row in rows
    ]


def session_records(session_id):
    """Every captured record of one session, with its Student's name,
    ordered deterministically by ``(full name, record id)``.

    Bounded by the captured roster of a single Group meeting. The Student
    name is joined once here rather than lazy-loaded per row.

    **The Student's current role, account status and Enrollment are
    deliberately not filtered.** They were all required at capture time,
    against the locked rows; afterwards the record is history, and a
    withdrawal, a suspension, a re-assignment or even a role change must
    never make a recorded roster look incomplete -- which is also what
    keeps the finalization roster-integrity check honest.
    """
    return (
        db.session.query(
            AttendanceRecord.id,
            AttendanceRecord.public_id,
            AttendanceRecord.status,
            AttendanceRecord.note,
            AttendanceRecord.version,
            User.full_name,
        )
        .join(User, AttendanceRecord.student_id == User.id)
        .filter(AttendanceRecord.attendance_session_id == session_id)
        .order_by(User.full_name.asc(), AttendanceRecord.id.asc())
        .all()
    )


def build_record_view(rows):
    """Plain presentation dicts for a session's records.

    Used only by the two **staff** surfaces -- the Teacher pages and the
    Administrator review -- both of which may read the private ``note``.
    The Student summary deliberately does not go through here at all: it
    has its own query and its own builder
    (:func:`build_student_attendance_view`), which never fetch a note, a
    version or another Student's row in the first place. Keeping the two
    apart is what makes "a Student cannot be shown a note" a property of
    the query rather than of a flag somebody could pass wrongly.
    """
    return [
        {
            "public_id": row.public_id,
            "student_name": row.full_name,
            "status": row.status,
            "status_label": STATUS_LABELS.get(row.status, row.status),
            "version": row.version,
            "note": row.note,
        }
        for row in rows
    ]


def captured_record_ids(session_id):
    """Every captured record's internal id for one session, **ascending**
    -- the exact order the draft-save and finalization paths must lock
    them in. Non-locking preview; the caller re-locks each row."""
    return [
        row.id
        for row in db.session.query(AttendanceRecord.id)
        .filter(AttendanceRecord.attendance_session_id == session_id)
        .order_by(AttendanceRecord.id.asc())
        .all()
    ]


def captured_student_ids(session_id):
    """Every captured Student's internal User id for one session,
    **ascending** -- the order the User rows must be locked in, before the
    records. Non-locking preview; the caller re-locks each row."""
    return [
        row.student_id
        for row in db.session.query(AttendanceRecord.student_id)
        .filter(AttendanceRecord.attendance_session_id == session_id)
        .order_by(AttendanceRecord.student_id.asc())
        .all()
    ]


# ---------------------------------------------------------------------------
# Administrator review -- read only, center-wide
# ---------------------------------------------------------------------------


def admin_sessions_page(page, group_id=None, session_date=None, finalized=None):
    """``(rows, has_next)`` -- one bounded page of attendance sessions
    across the whole center, newest local session date first.

    Every filter is optional and already **validated by the caller** into
    a known shape: an internal Group id resolved from a submitted *public*
    id, a real ``date``, or ``True`` / ``False`` / ``None`` for finalized
    / draft / either. Nothing raw from the query string reaches SQL, and
    an unrecognised filter value is dropped by the caller rather than
    guessed at here.

    Deterministic ordering (``session_date DESC, id DESC``) and
    ``LIMIT PAGE_SIZE + 1``; no ``COUNT``.
    """
    offset, limit = _session_page_query(page)
    query = (
        db.session.query(
            AttendanceSession.id,
            AttendanceSession.public_id,
            AttendanceSession.session_date,
            AttendanceSession.start_time,
            AttendanceSession.end_time,
            AttendanceSession.location,
            AttendanceSession.finalized_at,
            Schedule.day_of_week,
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            Course.title.label("course_title"),
            Level.name.label("level_name"),
            AcademicTerm.name.label("term_name"),
        )
        .select_from(AttendanceSession)
        .join(Schedule, AttendanceSession.schedule_id == Schedule.id)
        .join(Group, AttendanceSession.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
    )
    if group_id is not None:
        query = query.filter(AttendanceSession.group_id == group_id)
    if session_date is not None:
        query = query.filter(AttendanceSession.session_date == session_date)
    if finalized is True:
        query = query.filter(AttendanceSession.finalized_at.isnot(None))
    elif finalized is False:
        query = query.filter(AttendanceSession.finalized_at.is_(None))
    rows = (
        query.order_by(
            AttendanceSession.session_date.desc(), AttendanceSession.id.desc()
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


def build_admin_session_list_view(rows, counts_by_session):
    return [
        {
            "public_id": row.public_id,
            "group_public_id": row.group_public_id,
            "group_name": row.group_name,
            "course_title": row.course_title,
            "level_name": row.level_name,
            "term_name": row.term_name,
            "session_date": row.session_date,
            "start_time": row.start_time,
            "end_time": row.end_time,
            "location": row.location,
            "day_of_week": row.day_of_week,
            "is_finalized": row.finalized_at is not None,
            "finalized_at": row.finalized_at,
            "counts": ordered_counts(counts_by_session.get(row.id)),
        }
        for row in rows
    ]


def group_by_public_id(group_public_id):
    """One Group by ``public_id`` with the ancestors the Administrator
    pages display, or ``None``."""
    if not group_public_id:
        return None
    return (
        db.session.query(
            Group.id,
            Group.public_id,
            Group.name,
            Group.status,
            Course.title.label("course_title"),
            Level.name.label("level_name"),
            AcademicTerm.name.label("term_name"),
        )
        .select_from(Group)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .filter(Group.public_id == group_public_id)
        .first()
    )


def session_schedule_weekday(session_id):
    """The originating Schedule's weekday for one session, or ``None``.

    A one-column read used by the detail pages, which already hold the
    session's own immutable date/time snapshot and need only the weekday
    label to name the slot it came from.
    """
    row = (
        db.session.query(Schedule.day_of_week)
        .select_from(AttendanceSession)
        .join(Schedule, AttendanceSession.schedule_id == Schedule.id)
        .filter(AttendanceSession.id == session_id)
        .first()
    )
    return None if row is None else row.day_of_week


# ---------------------------------------------------------------------------
# Student summary -- the Student's OWN finalized records, and nothing else
# ---------------------------------------------------------------------------


def _student_finalized_base(student_id):
    """The one visibility formula every Student attendance read applies::

        the acting User is that Student, with role 'student' and an
        active account
        AND AttendanceRecord.student_id == that Student
        AND the record's session is FINALIZED

    Deliberately **not** included: the Enrollment, the Group and the
    academic ancestors need not still be active, and the Enrollment need
    not still exist as ``active`` at all -- a Student who has withdrawn
    keeps reading their own finalized history, which is the whole point of
    capturing it. Equally deliberately, a **draft** session is invisible:
    it is a Teacher's work in progress, not a statement about anybody yet.
    """
    return (
        db.session.query(
            AttendanceRecord.id.label("record_id"),
            AttendanceRecord.status,
            AttendanceSession.session_date,
            AttendanceSession.start_time,
            AttendanceSession.end_time,
            AttendanceSession.location,
            Group.name.label("group_name"),
            Course.title.label("course_title"),
        )
        .select_from(AttendanceRecord)
        .join(
            AttendanceSession,
            AttendanceRecord.attendance_session_id == AttendanceSession.id,
        )
        .join(Group, AttendanceSession.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(User, AttendanceRecord.student_id == User.id)
        .filter(
            AttendanceRecord.student_id == student_id,
            User.id == student_id,
            User.role == _STUDENT,
            User.status == _USER_ACTIVE,
            AttendanceSession.finalized_at.isnot(None),
        )
    )


def student_attendance_page(student_id, page):
    """``(rows, has_next)`` -- one bounded page of this Student's own
    finalized attendance, newest session date first.

    ``session_date DESC, record id DESC`` and ``LIMIT PAGE_SIZE + 1``; no
    ``COUNT``. Nothing here can return another Student's row: the
    ``WHERE`` clause names ``student_id`` twice -- once on the record and
    once on the joined account whose role and status are re-proved.
    """
    offset, limit = _session_page_query(page)
    rows = (
        _student_finalized_base(student_id)
        .order_by(AttendanceSession.session_date.desc(), AttendanceRecord.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


def build_student_attendance_view(rows):
    """Plain presentation dicts for the Student summary.

    **No note, no other Student, no Teacher, no Group roster and no
    internal id** is placed in these dicts -- not hidden by the template,
    simply never fetched into the row set and never put here.
    """
    return [
        {
            "group_name": row.group_name,
            "course_title": row.course_title,
            "session_date": row.session_date,
            "start_time": row.start_time,
            "end_time": row.end_time,
            "location": row.location,
            "status": row.status,
            "status_label": STATUS_LABELS.get(row.status, row.status),
        }
        for row in rows
    ]


def student_status_totals(student_id):
    """This Student's own finalized-record count per status, as an ordered
    list of presentation dicts plus the overall total.

    One ``GROUP BY`` query, scoped to a single ``student_id`` (the column
    ``ix_attendance_records_student_id`` leads with) and to finalized
    sessions only -- exactly the same visibility formula as the page
    above, so the totals can never describe rows the list does not show.
    Its result set is bounded to at most four rows.

    **Counts only.** There is deliberately no percentage, no rate, no
    "attendance score" and no center-wide comparison: any of those would
    read as a grade, Grades are an undecided module, and a percentage over
    a partially recorded term would be actively misleading.
    """
    rows = (
        db.session.query(AttendanceRecord.status, func.count(AttendanceRecord.id))
        .select_from(AttendanceRecord)
        .join(
            AttendanceSession,
            AttendanceRecord.attendance_session_id == AttendanceSession.id,
        )
        .join(User, AttendanceRecord.student_id == User.id)
        .filter(
            AttendanceRecord.student_id == student_id,
            User.role == _STUDENT,
            User.status == _USER_ACTIVE,
            AttendanceSession.finalized_at.isnot(None),
        )
        .group_by(AttendanceRecord.status)
        .all()
    )
    counts = {status: total for status, total in rows}
    return (
        [
            {
                "status": status,
                "label": STATUS_LABELS[status],
                "count": counts.get(status, 0),
            }
            for status in STATUS_ORDER
        ],
        sum(counts.values()),
    )
