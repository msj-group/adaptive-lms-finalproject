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
    AcademicStatus,
    Enrollment,
    EnrollmentMembership,
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
    Group's Course/AcademicTerm identity at the time.

    This is the **membership-only** history helper and it stays that way:
    it never looks at `Schedule` or `Unit`. The Group identity-freeze
    decision (`app/blueprints/admin/groups.py`) is made by
    `_group_identity_frozen`, which ORs this with
    `app.services.schedule_queries.group_has_schedule_history` (M08) and
    `app.services.unit_queries.group_has_unit_history` (M10) -- a Schedule
    or Unit row freezes identity on the same principle, but each check
    lives in its own helper so this one keeps its precise membership
    meaning for every other caller. Neither helper answers whether a row
    currently counts toward capacity or teacher eligibility, which is a
    different, stricter question answered by the two functions above.
    """
    has_enrollment = db.session.query(Enrollment.id).filter_by(group_id=group_id).first() is not None
    has_assignment = (
        db.session.query(GroupTeacherAssignment.id).filter_by(group_id=group_id).first() is not None
    )
    has_membership = db.session.query(EnrollmentMembership.id).filter_by(group_id=group_id).first() is not None
    return has_enrollment or has_assignment or has_membership


def conflicting_active_enrollment(student_id, target_group_id):
    """Return an existing ACTIVE Enrollment for `student_id` in some
    *other, currently ACTIVE* Group that shares both `course_id` and
    `academic_term_id` with the target Group, or None if there is no such
    conflict.

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

    Part M07C3: an *archived* conflicting Group no longer participates.
    Its roster is a frozen historical closure record, not an operational
    seat -- so an archived Group's active Enrollment rows never block a
    new/reactivated enrollment, nor a Group reactivation, in another
    (active) Group for the same Course + Academic Term. Only the
    conflicting Group's own status is added here; every other conflict
    dimension is unchanged.
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
            ConflictGroup.status == AcademicStatus.ACTIVE.value,
            ConflictGroup.course_id == TargetGroup.course_id,
            ConflictGroup.academic_term_id == TargetGroup.academic_term_id,
        )
        .first()
    )


def active_student_enrollment_rows(group_id):
    """`(enrollment_id, student_id)` tuples, ascending by `enrollment_id`,
    for every ACTIVE Enrollment of `group_id` whose referenced User has
    the Student role -- the same "valid active Student seat" definition
    as `active_student_enrollment_count`.

    Part M07C3: the Group-reactivation guard uses this to (a) lock each
    Student User row and each Enrollment row deterministically and
    (b) re-check the per-Student cross-Group conflict. It is a
    non-locking preview read used only to discover the candidate ids;
    the caller re-locks and re-checks.
    """
    return [
        (row.id, row.student_id)
        for row in db.session.query(Enrollment.id, Enrollment.student_id)
        .join(User, Enrollment.student_id == User.id)
        .filter(
            Enrollment.group_id == group_id,
            Enrollment.status == EnrollmentStatus.ACTIVE.value,
            User.role == UserRole.STUDENT.value,
        )
        .order_by(Enrollment.id)
        .all()
    ]


def teacher_assignment_rows(group_id):
    """`(assignment_id, teacher_id)` tuples, ascending by `assignment_id`,
    for *every* GroupTeacherAssignment of `group_id` -- any status, any
    referenced role. A removed or corrupted row is still a row the
    Group-reactivation guard must lock deterministically before judging
    teacher eligibility. Non-locking preview read; the caller re-locks.
    """
    return [
        (row.id, row.teacher_id)
        for row in db.session.query(GroupTeacherAssignment.id, GroupTeacherAssignment.teacher_id)
        .filter(GroupTeacherAssignment.group_id == group_id)
        .order_by(GroupTeacherAssignment.id)
        .all()
    ]
