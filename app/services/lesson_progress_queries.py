"""Read-only, bounded queries for Student lesson progress (Phase 4 / M13).

Flask-independent: no ``request``, ``flash`` or template here, and no lock
-- the completion write's proof lives in
``app/services/lesson_progress_transactions.py``.

**Student visibility is proved in SQL, keyed by the acting Student.** A
progress row -- and every Lesson a Student progress surface names -- is
read only through the M11 effective-visibility formula, extended with the
acting account itself::

    User.role == student AND User.status == active
      AND Enrollment(student, group).status == active
      AND AcademicTerm, Level, Course and Group all active
      AND Unit.status == active   AND Unit.group_id == that Group
      AND Lesson.status == published AND Lesson.unit_id == Unit.id

and a progress row is joined only on ``(student, group, lesson)`` with
``Unit.group_id == LessonProgress.group_id``. A withdrawn Enrollment, a
suspended account, an archived link, an unpublished Lesson and a row bound
to another Group therefore never contribute to a count, an ordering, a
destination or an empty state. Nothing is loaded broadly and filtered in
Python.

**Teacher reads are the current assignment, proved in SQL.** A Group is
found only through an active Teacher account's **active**
``GroupTeacherAssignment`` to exactly that Group; the roster is that
Group's actively enrolled, active Student accounts; and only their rows for
that Group are counted. As on the Units, Lessons, Attendance and Gradebook
pages, an archived Group or ancestor does not hide the page.

**Every query is bounded and column-projected**, and templates receive
plain dicts carrying public identifiers only -- never an internal id, never
another Student's identity on a Student surface.
"""

from collections import namedtuple

from sqlalchemy import and_, case, func, select

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Lesson,
    LessonProgress,
    LessonStatus,
    Level,
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.services.schedule_occurrences import to_app_local

#: Most rows the Student dashboard's Recently Opened list shows.
RECENTLY_OPENED_LIMIT = 5
#: Most Groups the Student dashboard's progress summary lists.
DASHBOARD_GROUP_LIMIT = 20
#: Students per Teacher Group progress page.
TEACHER_PAGE_SIZE = 20

#: The version a Student with no progress row is treated as having: one
#: that has never been completed. See ``LessonProgress``.
NO_PROGRESS_VERSION = 1

_MAX_PAGE = 10000

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value
_TEACHER = UserRole.TEACHER.value
_PUBLISHED = LessonStatus.PUBLISHED.value

#: One Student's progress on one authorized Lesson. The internal ids are
#: for the write paths only and never reach a template.
LessonProgressRef = namedtuple(
    "LessonProgressRef",
    "group_id group_public_id unit_public_id lesson_id lesson_public_id "
    "progress_id version completed_at last_opened_at",
)

#: The rows the completion lock chain must lock, from a non-locking
#: preview. Internal ids only -- never rendered.
LockTarget = namedtuple(
    "LockTarget", "term_id level_id course_id group_id group_public_id unit_id lesson_id"
)


# ---------------------------------------------------------------------------
# Shared scoping
# ---------------------------------------------------------------------------


def normalize_page(value):
    """A positive page number; anything unusable becomes page 1."""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    if page < 1 or page > _MAX_PAGE:
        return 1
    return page


def scope_to_student(query, student_id):
    """Add the acting-Student half of the visibility formula to `query`,
    which must already have ``Group`` in its FROM clause: the Group's
    Course, Level and AcademicTerm, the Student's own Enrollment in that
    Group and the Student's account, every one of them active."""
    return (
        query.join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(
            Enrollment,
            and_(Enrollment.group_id == Group.id, Enrollment.student_id == student_id),
        )
        .join(User, User.id == Enrollment.student_id)
        .filter(
            Enrollment.status == _ENROLLMENT_ACTIVE,
            User.role == _STUDENT,
            User.status == _USER_ACTIVE,
            Group.status == _ACTIVE,
            Course.status == _ACTIVE,
            Level.status == _ACTIVE,
            AcademicTerm.status == _ACTIVE,
        )
    )


def progress_join(student_id):
    """The ON clause joining the Student's own progress row for the Lesson
    and Group already in the query -- at most one row, by the UNIQUE
    constraint."""
    return and_(
        LessonProgress.lesson_id == Lesson.id,
        LessonProgress.group_id == Group.id,
        LessonProgress.student_id == student_id,
    )


def progress_ref(group_id, group_public_id, unit_public_id, lesson_id, lesson_public_id,
                 progress_id, version, completed_at, last_opened_at):
    """A :class:`LessonProgressRef`; a missing row reads as never
    completed, never opened, at :data:`NO_PROGRESS_VERSION`."""
    if progress_id is None:
        version, completed_at, last_opened_at = NO_PROGRESS_VERSION, None, None
    return LessonProgressRef(
        group_id, group_public_id, unit_public_id, lesson_id, lesson_public_id,
        progress_id, version, completed_at, last_opened_at,
    )


def _local(tz_name, moment):
    return to_app_local(tz_name, moment) if moment is not None else None


# ---------------------------------------------------------------------------
# One Lesson
# ---------------------------------------------------------------------------


def student_lesson_progress(student_id, group_public_id, unit_public_id, lesson_public_id):
    """The Student's :class:`LessonProgressRef` for a Lesson they may open
    right now, else ``None`` -- identical for a malformed, unknown, draft,
    archived, cross-Group or mismatched identifier and for ended access.

    One query. Used by the completion POST as its authorized preview; the
    transaction proves everything again under locks.
    """
    query = (
        db.session.query(
            Group.id,
            Group.public_id,
            Unit.public_id,
            Lesson.id,
            Lesson.public_id,
            LessonProgress.id,
            LessonProgress.version,
            LessonProgress.completed_at,
            LessonProgress.last_opened_at,
        )
        .select_from(Lesson)
        .join(Unit, Lesson.unit_id == Unit.id)
        .join(Group, Unit.group_id == Group.id)
    )
    row = (
        scope_to_student(query, student_id)
        .outerjoin(LessonProgress, progress_join(student_id))
        .filter(
            Lesson.public_id == lesson_public_id,
            Lesson.status == _PUBLISHED,
            Unit.public_id == unit_public_id,
            Unit.status == _ACTIVE,
            Group.public_id == group_public_id,
        )
        .first()
    )
    return progress_ref(*row) if row is not None else None


def build_lesson_progress_view(progress, tz_name):
    """The template-safe completion state of one Lesson."""
    return {
        "is_completed": progress.completed_at is not None,
        "completed_local": _local(tz_name, progress.completed_at),
    }


def lock_target(group_public_id, unit_public_id, lesson_public_id):
    """The :class:`LockTarget` naming the rows a completion write must lock
    for this Lesson, else ``None``. **Not an authorization decision**: it
    only names rows through their nested ownership, and everything is
    proved again after the locks."""
    row = (
        db.session.query(
            Group.academic_term_id,
            Course.level_id,
            Group.course_id,
            Group.id,
            Group.public_id,
            Unit.id,
            Lesson.id,
        )
        .select_from(Lesson)
        .join(Unit, Lesson.unit_id == Unit.id)
        .join(Group, Unit.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .filter(
            Lesson.public_id == lesson_public_id,
            Unit.public_id == unit_public_id,
            Group.public_id == group_public_id,
        )
        .first()
    )
    return LockTarget(*row) if row is not None else None


# ---------------------------------------------------------------------------
# Student dashboard
# ---------------------------------------------------------------------------


def student_group_progress(student_id, limit=DASHBOARD_GROUP_LIMIT):
    """``(groups, truncated)`` -- at most `limit` Groups whose Lessons the
    Student may open right now, ordered by name then internal id, each with
    its visible Lesson total and the Student's completed count among those
    Lessons.

    One query: two grouped aggregates joined onto the authorized Groups, so
    the cost does not grow with the number of Groups or Lessons shown.
    """
    enrolled_group_ids = select(Enrollment.group_id).where(
        Enrollment.student_id == student_id,
        Enrollment.status == _ENROLLMENT_ACTIVE,
    )
    totals = (
        db.session.query(
            Unit.group_id.label("group_id"),
            func.count(Lesson.id).label("lesson_total"),
        )
        .select_from(Unit)
        .join(Lesson, Lesson.unit_id == Unit.id)
        .filter(
            Unit.group_id.in_(enrolled_group_ids),
            Unit.status == _ACTIVE,
            Lesson.status == _PUBLISHED,
        )
        .group_by(Unit.group_id)
        .subquery()
    )
    completed = (
        db.session.query(
            LessonProgress.group_id.label("group_id"),
            func.count(LessonProgress.id).label("completed_total"),
        )
        .select_from(LessonProgress)
        .join(Lesson, Lesson.id == LessonProgress.lesson_id)
        .join(Unit, and_(Unit.id == Lesson.unit_id, Unit.group_id == LessonProgress.group_id))
        .filter(
            LessonProgress.student_id == student_id,
            LessonProgress.completed_at.isnot(None),
            Unit.status == _ACTIVE,
            Lesson.status == _PUBLISHED,
        )
        .group_by(LessonProgress.group_id)
        .subquery()
    )
    query = db.session.query(
        Group.public_id,
        Group.name,
        Course.title,
        Level.name,
        func.coalesce(totals.c.lesson_total, 0),
        func.coalesce(completed.c.completed_total, 0),
    ).select_from(Group)
    rows = (
        scope_to_student(query, student_id)
        .outerjoin(totals, totals.c.group_id == Group.id)
        .outerjoin(completed, completed.c.group_id == Group.id)
        .order_by(Group.name, Group.id)
        .limit(limit + 1)
        .all()
    )
    groups = [
        {
            "group_public_id": row[0],
            "group_name": row[1],
            "course_title": row[2],
            "level_name": row[3],
            "lesson_total": int(row[4] or 0),
            "completed": int(row[5] or 0),
        }
        for row in rows[:limit]
    ]
    return groups, len(rows) > limit


def continue_learning(student_id):
    """The one Lesson the Student should continue, else ``None``.

    Among the incomplete Lessons the Student may open right now:

    1. the most recently opened one (``last_opened_at`` newest first);
    2. otherwise the earliest one in teaching order -- Group name, Group
       id, Unit ``display_order``, Unit id, Lesson ``display_order``,
       Lesson id.

    Both rules are one ``ORDER BY``: never-opened Lessons sort after every
    opened one, and every tie is broken by the same teaching order, so the
    answer is deterministic. One query; ``None`` means there is no such
    Lesson, and the page says so rather than guessing a destination.
    """
    query = (
        db.session.query(
            Lesson.public_id,
            Lesson.title,
            Unit.public_id,
            Unit.title,
            Group.public_id,
            Group.name,
            Course.title,
            LessonProgress.last_opened_at,
        )
        .select_from(Lesson)
        .join(Unit, Lesson.unit_id == Unit.id)
        .join(Group, Unit.group_id == Group.id)
    )
    row = (
        scope_to_student(query, student_id)
        .outerjoin(LessonProgress, progress_join(student_id))
        .filter(
            Lesson.status == _PUBLISHED,
            Unit.status == _ACTIVE,
            LessonProgress.completed_at.is_(None),
        )
        .order_by(
            case((LessonProgress.last_opened_at.is_(None), 1), else_=0),
            LessonProgress.last_opened_at.desc(),
            Group.name,
            Group.id,
            Unit.display_order,
            Unit.id,
            Lesson.display_order,
            Lesson.id,
        )
        .first()
    )
    if row is None:
        return None
    return {
        "lesson_public_id": row[0],
        "lesson_title": row[1],
        "unit_public_id": row[2],
        "unit_title": row[3],
        "group_public_id": row[4],
        "group_name": row[5],
        "course_title": row[6],
        "resume": row[7] is not None,
    }


def recently_opened(student_id, limit=RECENTLY_OPENED_LIMIT):
    """At most `limit` Lessons the Student may open right now, most
    recently opened first (``last_opened_at`` then row id, both
    descending). Rows of Lessons that are no longer visible are skipped by
    the query itself, never counted towards the limit. One query."""
    query = (
        db.session.query(
            Lesson.public_id,
            Lesson.title,
            Unit.public_id,
            Unit.title,
            Group.public_id,
            Group.name,
            LessonProgress.last_opened_at,
            LessonProgress.completed_at,
        )
        .select_from(LessonProgress)
        .join(Lesson, Lesson.id == LessonProgress.lesson_id)
        .join(Unit, and_(Unit.id == Lesson.unit_id, Unit.group_id == LessonProgress.group_id))
        .join(Group, Group.id == LessonProgress.group_id)
    )
    return (
        scope_to_student(query, student_id)
        .filter(
            LessonProgress.student_id == student_id,
            LessonProgress.last_opened_at.isnot(None),
            Lesson.status == _PUBLISHED,
            Unit.status == _ACTIVE,
        )
        .order_by(LessonProgress.last_opened_at.desc(), LessonProgress.id.desc())
        .limit(limit)
        .all()
    )


def build_recent_view(rows, tz_name):
    return [
        {
            "lesson_public_id": row[0],
            "lesson_title": row[1],
            "unit_public_id": row[2],
            "unit_title": row[3],
            "group_public_id": row[4],
            "group_name": row[5],
            "opened_local": _local(tz_name, row[6]),
            "is_completed": row[7] is not None,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Teacher Group view
# ---------------------------------------------------------------------------


def teacher_progress_group(teacher_id, group_public_id):
    """``{id, public_id, name, course_title, level_name, term_name,
    operational}`` for a Group `teacher_id` is actively assigned to right
    now with an active Teacher account, else ``None`` -- identical for
    every reason access is missing. The Group and its ancestors may be
    archived; ``operational`` says whether they all are active."""
    row = (
        db.session.query(
            Group.id,
            Group.public_id,
            Group.name,
            Course.title,
            Level.name,
            AcademicTerm.name,
            Group.status,
            Course.status,
            Level.status,
            AcademicTerm.status,
        )
        .select_from(Group)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(GroupTeacherAssignment, GroupTeacherAssignment.group_id == Group.id)
        .join(User, User.id == GroupTeacherAssignment.teacher_id)
        .filter(
            Group.public_id == group_public_id,
            GroupTeacherAssignment.teacher_id == teacher_id,
            GroupTeacherAssignment.status == _ASSIGNMENT_ACTIVE,
            User.role == _TEACHER,
            User.status == _USER_ACTIVE,
        )
        .first()
    )
    if row is None:
        return None
    return {
        "id": row[0],
        "public_id": row[1],
        "name": row[2],
        "course_title": row[3],
        "level_name": row[4],
        "term_name": row[5],
        "operational": all(status == _ACTIVE for status in row[6:10]),
    }


def public_group(group):
    """The template-safe form of a Group dict: no internal id."""
    return {key: value for key, value in group.items() if key != "id"}


def group_lesson_total(group_id):
    """How many Lessons of the Group its Students could open: published
    Lessons in active Units. One COUNT."""
    return int(
        db.session.query(func.count(Lesson.id))
        .select_from(Lesson)
        .join(Unit, Lesson.unit_id == Unit.id)
        .filter(
            Unit.group_id == group_id,
            Unit.status == _ACTIVE,
            Lesson.status == _PUBLISHED,
        )
        .scalar()
        or 0
    )


def roster_page(group_id, page):
    """``(rows, has_next)`` -- one page of ``(student_id, full_name)`` for
    the Group's actively enrolled, active Student accounts, ordered by name
    then internal id, reading ``TEACHER_PAGE_SIZE + 1`` rows so "is there
    another page" costs no COUNT."""
    rows = (
        db.session.query(User.id, User.full_name)
        .select_from(Enrollment)
        .join(User, User.id == Enrollment.student_id)
        .filter(
            Enrollment.group_id == group_id,
            Enrollment.status == _ENROLLMENT_ACTIVE,
            User.role == _STUDENT,
            User.status == _USER_ACTIVE,
        )
        .order_by(User.full_name, User.id)
        .offset((page - 1) * TEACHER_PAGE_SIZE)
        .limit(TEACHER_PAGE_SIZE + 1)
        .all()
    )
    return rows[:TEACHER_PAGE_SIZE], len(rows) > TEACHER_PAGE_SIZE


def roster_progress(group_id, student_ids):
    """``{student_id: (completed, last_completed_at, last_opened_at)}`` for
    the given Students' rows **in this Group**, counting only Lessons its
    Students could open (published, in an active Unit of this Group). One
    grouped query over one page of Students."""
    if not student_ids:
        return {}
    rows = (
        db.session.query(
            LessonProgress.student_id,
            func.sum(case((LessonProgress.completed_at.isnot(None), 1), else_=0)),
            func.max(LessonProgress.completed_at),
            func.max(LessonProgress.last_opened_at),
        )
        .select_from(LessonProgress)
        .join(Lesson, Lesson.id == LessonProgress.lesson_id)
        .join(Unit, and_(Unit.id == Lesson.unit_id, Unit.group_id == LessonProgress.group_id))
        .filter(
            LessonProgress.group_id == group_id,
            LessonProgress.student_id.in_(list(student_ids)),
            Unit.status == _ACTIVE,
            Lesson.status == _PUBLISHED,
        )
        .group_by(LessonProgress.student_id)
        .all()
    )
    return {row[0]: (int(row[1] or 0), row[2], row[3]) for row in rows}


def build_roster_view(rows, progress, tz_name):
    """Template rows for one roster page: a name and three progress
    values, never an internal id."""
    view = []
    for student_id, full_name in rows:
        completed, last_completed_at, last_opened_at = progress.get(student_id, (0, None, None))
        view.append(
            {
                "name": full_name,
                "completed": completed,
                "last_completed_local": _local(tz_name, last_completed_at),
                "last_opened_local": _local(tz_name, last_opened_at),
            }
        )
    return view
