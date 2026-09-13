"""Shared locking primitives and the atomic roster capture for the
Attendance aggregate (Phase 4 / M07).

Flask-independent -- no ``abort``, ``flash``, ``redirect`` or template
rendering, exactly like ``app/services/group_transactions.py`` and
``app/services/quiz_transactions.py``. Route-level 404 / redirect / flash
handling belongs in the Blueprint modules that call this.

**This module exists so the two M07 write paths share one lock order**,
and so the Teacher surface and (should one ever be authorized) any other
caller cannot drift apart in what they take and in which sequence. The
established academic prefix is preserved and extended deterministically.

Creation::

    AcademicTerm -> Level -> Course      (lock_academic_hierarchy, which
                                          owns the one deliberate reset)
    -> Group
    -> acting Teacher + eligible Student User rows, one ascending-id set
    -> GroupTeacherAssignment
    -> Schedule
    -> active Enrollment rows, ascending internal id
    -> AttendanceSession (the occurrence's row, which normally does not
       exist yet)

Draft save and finalization::

    AcademicTerm -> Level -> Course      (same single reset)
    -> Group
    -> acting Teacher + captured Student User rows, one ascending-id set
    -> GroupTeacherAssignment
    -> Schedule
    -> AttendanceSession
    -> captured AttendanceRecord rows, ascending internal id

All involved User rows form one ascending-id lock set, never a
``Teacher first, then Students`` pair of sets. That is the project-wide
rule shared with feedback, speaking and messaging, and it matters across
different Groups: two transactions can share the same people without
sharing a Group row that would serialize them first. Every other row set
also uses ascending internal id, never display order or submitted order.
The Group lock is the same one every Group-affecting mutation in this
project already takes, so an attendance write serializes against a Group
retarget, a Schedule edit, an Enrollment change and a teacher-assignment
change rather than racing them.

**The last statement of the creation chain locates the session by
``(schedule_id, session_date)``** (``uq_attendance_sessions_schedule_date``),
which for a row that does not exist yet can only take a gap / next-key
lock. A gap lock is **not** a mutex: MySQL/InnoDB documents that gap locks
on the same gap can be held by several transactions at once and do not
block one another (see
https://dev.mysql.com/doc/refman/8.0/en/innodb-locking.html), so acquiring
one is not by itself what makes two competing creators mutually exclusive.
What actually serializes them is the chain of locks on rows that *do*
exist -- above all the Group -- taken before either request reads or
inserts anything; and ``uq_attendance_sessions_schedule_date`` remains the
final duplicate defense behind both, with the loser resolved to the
session that won rather than shown an error.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so this code runs correctly in tests
without actually locking anything. Tests can assert the *requested* lock
set and order (structural); they prove nothing about real InnoDB blocking.
"""

from app.extensions import db
from app.models import (
    AttendanceRecord,
    AttendanceSession,
    DEFAULT_ATTENDANCE_STATUS,
    Enrollment,
    EnrollmentStatus,
    GroupTeacherAssignment,
    Schedule,
    User,
    UserRole,
    UserStatus,
)
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.group_transactions import lock_group_in_open_transaction

_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value
_USER_ACTIVE = UserStatus.ACTIVE.value


class AttendanceLocks:
    """The rows one M07 lock chain returned.

    Any attribute may be ``None`` (or hold a ``None`` value): the caller
    must treat that as a business / authorization rejection, roll back,
    and 404 or redirect. It must never "keep going past" one.
    """

    __slots__ = (
        "hierarchy",
        "group",
        "teacher",
        "teacher_assignment",
        "schedule",
        "session",
        "students",
        "enrollments",
        "records",
    )

    def __init__(
        self,
        hierarchy,
        group,
        teacher,
        teacher_assignment,
        schedule,
        session=None,
        students=None,
        enrollments=None,
        records=None,
    ):
        self.hierarchy = hierarchy
        self.group = group
        self.teacher = teacher
        self.teacher_assignment = teacher_assignment
        self.schedule = schedule
        self.session = session
        self.students = students or {}
        self.enrollments = enrollments or {}
        self.records = records or {}


def _lock_prefix(
    group_public_id,
    term_id,
    level_id,
    course_id,
    teacher_id,
    schedule_id,
    student_ids=(),
):
    """The shared prefix both M07 chains take, in one open transaction,
    with the single deliberate reset owned by
    :func:`~app.services.academic_hierarchy_transactions.lock_academic_hierarchy`.

    Returns ``(hierarchy, group, teacher, teacher_assignment, schedule,
    students)``. The acting Teacher and all previewed Students are locked
    as one ascending-id User set before any relationship or domain row.
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    student_ids = sorted({uid for uid in student_ids if uid is not None})
    users = _lock_user_rows((teacher_id, *student_ids))
    teacher = users.get(teacher_id)
    students = {student_id: users.get(student_id) for student_id in student_ids}
    teacher_assignment = None
    if group is not None:
        teacher_assignment = (
            GroupTeacherAssignment.query.filter_by(
                group_id=group.id, teacher_id=teacher_id
            )
            .with_for_update()
            .first()
        )
    schedule = None
    if schedule_id is not None:
        schedule = Schedule.query.filter_by(id=schedule_id).with_for_update().first()
    return hierarchy, group, teacher, teacher_assignment, schedule, students


def _lock_user_rows(user_ids):
    """Lock the given User rows ``FOR UPDATE``, ascending internal id."""
    rows = {}
    for user_id in sorted({uid for uid in user_ids if uid is not None}):
        rows[user_id] = User.query.filter_by(id=user_id).with_for_update().first()
    return rows


def lock_creation_chain(
    group_public_id,
    term_id,
    level_id,
    course_id,
    teacher_id,
    schedule_id,
    session_date,
    student_ids=(),
    enrollment_ids=(),
):
    """Take the full M07 **creation** lock order in one open transaction.

    `student_ids` / `enrollment_ids` come from the non-locking preview
    :func:`app.services.attendance_queries.eligible_roster_rows`; they only
    decide *which* rows to lock. The caller must re-apply every
    eligibility condition to the **locked** rows returned here and must
    treat a ``None`` -- a vanished User, a vanished Enrollment, a missing
    Group, Teacher, assignment or Schedule -- as a rejection.
    """
    hierarchy, group, teacher, teacher_assignment, schedule, students = _lock_prefix(
        group_public_id,
        term_id,
        level_id,
        course_id,
        teacher_id,
        schedule_id,
        student_ids,
    )
    enrollments = {}
    for enrollment_id in sorted({e for e in enrollment_ids if e is not None}):
        enrollments[enrollment_id] = (
            Enrollment.query.filter_by(id=enrollment_id).with_for_update().first()
        )
    session = None
    if schedule_id is not None and session_date is not None:
        session = (
            AttendanceSession.query.filter_by(
                schedule_id=schedule_id, session_date=session_date
            )
            .with_for_update()
            .first()
        )
    return AttendanceLocks(
        hierarchy,
        group,
        teacher,
        teacher_assignment,
        schedule,
        session=session,
        students=students,
        enrollments=enrollments,
    )


def lock_session_chain(
    group_public_id,
    term_id,
    level_id,
    course_id,
    teacher_id,
    schedule_id,
    session_id,
    student_ids=(),
    record_ids=(),
):
    """Take the full M07 **draft save / finalization** lock order in one
    open transaction.

    All acting/captured User rows are locked first as one ascending-id set.
    The AttendanceSession then remains the serialization point for its own
    aggregate, so two co-teachers saving the same roster serialize on it;
    captured record rows follow it in ascending internal id.

    Returns an :class:`AttendanceLocks`. Any ``None`` is a rejection.
    """
    hierarchy, group, teacher, teacher_assignment, schedule, students = _lock_prefix(
        group_public_id,
        term_id,
        level_id,
        course_id,
        teacher_id,
        schedule_id,
        student_ids,
    )
    session = None
    if session_id is not None:
        session = (
            AttendanceSession.query.filter_by(id=session_id).with_for_update().first()
        )
    records = {}
    for record_id in sorted({r for r in record_ids if r is not None}):
        records[record_id] = (
            AttendanceRecord.query.filter_by(id=record_id).with_for_update().first()
        )
    return AttendanceLocks(
        hierarchy,
        group,
        teacher,
        teacher_assignment,
        schedule,
        session=session,
        students=students,
        records=records,
    )


def eligible_locked_student_ids(locks, group_id):
    """The internal User ids that are **still** eligible according to the
    rows this chain actually locked, ascending.

    Re-applies, against locked current data, exactly the four conditions
    :func:`app.services.attendance_queries.eligible_roster_rows` applied to
    the preview: an ``active`` Enrollment, in this exact Group, whose User
    exists with role ``student`` and an ``active`` account. A row that
    stopped satisfying any of them between the preview and its own lock is
    simply dropped rather than silently captured -- and a row that *became*
    eligible in that window was never in the preview, so it is not
    captured either. Both directions are deliberate: the roster is
    whatever the locked rows say at the instant of capture, and the
    Teacher is shown the count that was actually written.
    """
    eligible = []
    for enrollment in locks.enrollments.values():
        if enrollment is None:
            continue
        if enrollment.group_id != group_id or enrollment.status != _ENROLLMENT_ACTIVE:
            continue
        student = locks.students.get(enrollment.student_id)
        if student is None:
            continue
        if student.role != _STUDENT or student.status != _USER_ACTIVE:
            continue
        eligible.append(student.id)
    return sorted(set(eligible))


def create_session_with_roster(
    group_id, schedule, session_date, student_ids, moment
):
    """Insert one AttendanceSession **and** its complete roster of records
    in the caller's already-open, already-locked transaction, and return
    the session.

    Everything the session freezes is copied here, once: the Schedule's
    ``start_time``, ``end_time`` and ``location``, plus the local civil
    ``session_date`` the caller validated. Later Schedule edits or
    archiving never revisit them.

    Every record is created with :data:`DEFAULT_ATTENDANCE_STATUS`
    (``absent``) and ``version`` 1, and both timestamps on every row are
    the **same** authoritative post-lock whole-second `moment` the session
    itself carries -- so a captured roster can never look as though its
    rows were written at different times.

    **Atomic by construction**: the session and every record are added to
    one transaction and the caller commits once. A failure leaves nothing
    behind -- never a session with a partial roster. Nothing here
    authorizes anything, validates the occurrence, or commits; the caller
    has already proved all of that against the locked rows.
    """
    session = AttendanceSession(
        group_id=group_id,
        schedule_id=schedule.id,
        session_date=session_date,
        start_time=schedule.start_time,
        end_time=schedule.end_time,
        location=schedule.location,
        version=1,
        finalized_at=None,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(session)
    db.session.flush()
    for student_id in sorted(set(student_ids)):
        db.session.add(
            AttendanceRecord(
                attendance_session_id=session.id,
                student_id=student_id,
                status=DEFAULT_ATTENDANCE_STATUS,
                note=None,
                version=1,
                created_at=moment,
                updated_at=moment,
            )
        )
    return session
