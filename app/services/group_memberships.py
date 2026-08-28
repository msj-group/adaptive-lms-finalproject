"""Query-only helpers shared between the Student Enrollment and Group
Teacher Assignment write paths.

Deliberately independent of Flask: no `request`, `flash`, `redirect`, or
template rendering here, and no route decorators -- these are plain
functions over the ORM so both `app/blueprints/admin/enrollments.py` and
`app/blueprints/admin/group_members.py` (and their forms) can import them
without depending on each other, avoiding a circular import between the
two blueprint modules.
"""

from sqlalchemy import func
from sqlalchemy.orm import aliased, joinedload

from app.extensions import db
from app.models import (
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    User,
    UserRole,
    UserStatus,
)


def active_student_enrollment_count(group_id):
    """Count only ACTIVE Enrollment rows for a Group whose referenced User
    is actually a Student -- a single SQL COUNT, never a Python len() over
    every row. A corrupted ACTIVE Enrollment that somehow references a
    Teacher/Administrator/Researcher (the FK cannot prevent that) must not
    inflate this count, since it was never a valid Student seat to begin
    with. A suspended Student's still-ACTIVE Enrollment, by contrast,
    keeps occupying its seat -- suspension is an account-level state, not
    an Enrollment-level one, and withdrawing is a separate, explicit
    action an administrator must take.
    """
    return (
        db.session.query(func.count(Enrollment.id))
        .join(User, Enrollment.student_id == User.id)
        .filter(
            Enrollment.group_id == group_id,
            Enrollment.status == EnrollmentStatus.ACTIVE.value,
            User.role == UserRole.STUDENT.value,
        )
        .scalar()
    )


def eligible_active_teacher_count(group_id, exclude_assignment_id=None):
    """Count assignments that are simultaneously ACTIVE, reference a User
    with role=teacher, AND an ACTIVE Teacher account -- the three
    conditions that together make an assignment "eligible" for both the
    at-least-one-teacher-before-enrolling rule and the
    do-not-remove-the-last-teacher rule. A suspended Teacher's
    still-ACTIVE assignment, or a corrupted assignment referencing a
    non-Teacher, both fail this and so never count.
    """
    query = (
        db.session.query(func.count(GroupTeacherAssignment.id))
        .join(User, GroupTeacherAssignment.teacher_id == User.id)
        .filter(
            GroupTeacherAssignment.group_id == group_id,
            GroupTeacherAssignment.status == GroupTeacherAssignmentStatus.ACTIVE.value,
            User.role == UserRole.TEACHER.value,
            User.status == UserStatus.ACTIVE.value,
        )
    )
    if exclude_assignment_id is not None:
        query = query.filter(GroupTeacherAssignment.id != exclude_assignment_id)
    return query.scalar()


def group_has_membership_history(group_id):
    """Whether *any* Enrollment or GroupTeacherAssignment row has ever
    existed for this Group -- deliberately regardless of status (active
    or withdrawn/removed) and regardless of whether the referenced User's
    role is still valid.

    Unlike `active_student_enrollment_count`/`eligible_active_teacher_count`
    above, this must NOT filter on status or role: a withdrawn Enrollment,
    a removed GroupTeacherAssignment, or even a corrupted row referencing
    a non-Student/non-Teacher User (the FK cannot prevent that) all still
    represent real relationship history that was created under this
    Group's Course/AcademicTerm identity at the time. Used to decide
    whether that identity may still be changed -- see the Group edit
    route -- not whether a row currently counts toward capacity or
    teacher eligibility, which is a different, stricter question answered
    by the two functions above.
    """
    has_enrollment = db.session.query(Enrollment.id).filter_by(group_id=group_id).first() is not None
    has_assignment = (
        db.session.query(GroupTeacherAssignment.id).filter_by(group_id=group_id).first() is not None
    )
    return has_enrollment or has_assignment


def conflicting_active_enrollment(student_id, target_group_id):
    """Return an existing ACTIVE Enrollment for `student_id` in some
    *other* Group that shares both `course_id` and `academic_term_id`
    with the target Group, or None if there is no such conflict.

    A Student may legitimately hold active Enrollments in several Groups
    at once (different Courses, or the same Course in different
    Academic Terms) -- only two *active* memberships in the *same*
    Course *and* the same Academic Term are disallowed, since that would
    mean the Student is simultaneously enrolled twice in what is
    conceptually one offering. Compares against the target Group's own
    course_id/academic_term_id via a single self-join query (no separate
    round-trip to fetch the target Group first), and eager-loads the
    conflicting Enrollment's Group so a caller can build a message that
    names it (e.g. "already enrolled in Group 'X'") without an extra
    query.
    """
    TargetGroup = aliased(Group)
    ConflictGroup = aliased(Group)
    return (
        Enrollment.query.join(ConflictGroup, Enrollment.group_id == ConflictGroup.id)
        .join(TargetGroup, TargetGroup.id == target_group_id)
        .options(joinedload(Enrollment.group))
        .filter(
            Enrollment.student_id == student_id,
            Enrollment.status == EnrollmentStatus.ACTIVE.value,
            Enrollment.group_id != target_group_id,
            ConflictGroup.course_id == TargetGroup.course_id,
            ConflictGroup.academic_term_id == TargetGroup.academic_term_id,
        )
        .first()
    )
