"""Shared fixtures for the Phase 5 / M03 student fee assignment test modules.

Kept in one module, like ``tests/fee_plan_fixtures.py`` (whose account, plan
and token helpers it reuses), so the model, route, transaction and migration
suites build the same academic chain, Enrollments, plans and assignments.

Row helpers write straight into the tables, so a test about (say) a cancelled
assignment is not also a test of the cancellation route. The route helpers at
the bottom drive the application's own write path and read every signed token
out of the page the server rendered.

Every stored moment is a whole-second 2026 UTC instant earlier than the real
clock, so the timestamp CHECKs hold when a route later writes "now" on top of
a fixture row, and a fixture row never looks newer than a freshly rendered
form.
"""

from datetime import date, datetime

import tests.fee_plan_fixtures as plans
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
    Level,
    StudentFeeAssignment,
    StudentFeeAssignmentStatus,
    UserRole,
    UserStatus,
)

PW = plans.PW
STATE_FIELD = plans.STATE_FIELD
STALE_TEXT = plans.STALE_TEXT
INTEGRITY_TEXT = plans.INTEGRITY_TEXT

ASSIGNED = StudentFeeAssignmentStatus.ASSIGNED.value
CANCELLED = StudentFeeAssignmentStatus.CANCELLED.value
ACTIVE = AcademicStatus.ACTIVE.value
ARCHIVED = AcademicStatus.ARCHIVED.value
ENROLLED = EnrollmentStatus.ACTIVE.value
WITHDRAWN = EnrollmentStatus.WITHDRAWN.value

ASSIGNED_AT = datetime(2026, 5, 10, 9, 0, 0)
CANCELLED_AT = datetime(2026, 5, 11, 9, 0, 0)

ASSIGNED_OK_TEXT = "Fee plan assigned to this enrollment"
CANCELLED_OK_TEXT = "Fee assignment cancelled"
ALREADY_ASSIGNED_TEXT = "already has an assigned fee plan"
ALREADY_CANCELLED_TEXT = "already cancelled"
WITHDRAWAL_BLOCKED_TEXT = "Cancel the fee assignment explicitly"
PLAN_UNAVAILABLE_TEXT = "not available for assignment"
ITEMS_INVALID_TEXT = "does not have a valid set of items"
ENROLLMENT_INACTIVE_TEXT = "This enrollment is withdrawn"
STUDENT_INACTIVE_TEXT = "account is not active"
ACADEMIC_INACTIVE_TEXT = "is archived, so a fee plan cannot be assigned"

MISSING = "00000000-0000-0000-0000-000000000000"

login_as = plans.login_as
fresh_identity = plans.fresh_identity
admin = plans.admin
user = plans.user
state_in = plans.state_in
page = plans.page

_SEQUENCE = {"n": 0}


def _next():
    _SEQUENCE["n"] += 1
    return _SEQUENCE["n"]


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def members_url(gp):
    return f"/admin/groups/{gp}/members"


def history_url(gp, ep):
    return f"/admin/groups/{gp}/enrollments/{ep}/fee-assignments"


def choices_url(gp, ep):
    return f"/admin/groups/{gp}/enrollments/{ep}/fee-plans"


def assign_url(gp, ep, pp):
    return f"/admin/groups/{gp}/enrollments/{ep}/fee-plans/{pp}/assign"


def cancel_url(gp, ep, ap):
    return f"/admin/groups/{gp}/enrollments/{ep}/fee-assignments/{ap}/cancel"


def withdraw_url(gp, ep):
    return f"/admin/groups/{gp}/enrollments/{ep}/withdraw"


def reactivate_url(gp, ep):
    return f"/admin/groups/{gp}/enrollments/{ep}/reactivate"


# ---------------------------------------------------------------------------
# Rows, written directly
# ---------------------------------------------------------------------------


def group(status=ACTIVE, term_status=ACTIVE, level_status=ACTIVE, course_status=ACTIVE,
          name=None, capacity=30):
    """A Group under its own Academic Term, Level and Course."""
    n = _next()
    term = AcademicTerm(name=f"Term {n}", start_date=date(2020, 1, 1),
                        end_date=date(2099, 1, 1), status=term_status)
    level = Level(name=f"Level {n}", display_order=n, status=level_status)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title=f"Course {n}", level_id=level.id, display_order=0, status=course_status)
    db.session.add(course)
    db.session.commit()
    row = Group(academic_term_id=term.id, course_id=course.id, name=name or f"Group {n}",
                capacity=capacity, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def teacher_for(owning_group):
    """An eligible active Teacher assignment, which Enrollment reactivation
    requires."""
    teacher = user(f"teacher{_next()}@example.com", UserRole.TEACHER.value)
    db.session.add(GroupTeacherAssignment(group_id=owning_group.id, teacher_id=teacher.id,
                                          status=GroupTeacherAssignmentStatus.ACTIVE.value))
    db.session.commit()
    return teacher


def student(email=None, status=UserStatus.ACTIVE.value, name=None):
    return user(email or f"student{_next()}@example.com", UserRole.STUDENT.value,
                status=status, name=name)


def enrollment(owning_group=None, enrolled=None, status=ENROLLED):
    owning_group = owning_group or group()
    enrolled = enrolled or student()
    row = Enrollment(student_id=enrolled.id, group_id=owning_group.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


DEFAULT_ITEMS = (("registration", "Registration", "50.000"), ("course", "Course", "1200.500"))


def active_plan(creator, name=None, items=DEFAULT_ITEMS, version=2):
    """An active, frozen plan (version 2: created, then activated)."""
    owner = plans.plan(creator, name=name, status=plans.ACTIVE, version=version)
    for kind, label, amount in items:
        plans.item(owner, label=label, amount=amount, kind=kind)
    return owner


def assignment(owning_enrollment, fee_plan, actor, status=ASSIGNED, version=None,
               assigned_at=ASSIGNED_AT):
    cancelled = status == CANCELLED
    row = StudentFeeAssignment(
        enrollment_id=owning_enrollment.id,
        fee_plan_id=fee_plan.id,
        status=status,
        assigned_at=assigned_at,
        assigned_by_id=actor.id,
        cancelled_at=CANCELLED_AT if cancelled else None,
        cancelled_by_id=actor.id if cancelled else None,
        version=version or (2 if cancelled else 1),
        created_at=assigned_at,
        updated_at=CANCELLED_AT if cancelled else assigned_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def world(app, admin_email="admin@example.com"):
    """An active Administrator, one active Enrollment in a fully active
    chain, and one active two-item plan. Plain scalars only."""
    with app.app_context():
        actor = admin(admin_email)
        row = enrollment(enrolled=student(name="Student One"))
        fee_plan = active_plan(actor, name="Standard plan")
        owning_group = db.session.get(Group, row.group_id)
        course = db.session.get(Course, owning_group.course_id)
        return {
            "gp": owning_group.public_id,
            "ep": row.public_id,
            "pp": fee_plan.public_id,
            "admin_id": actor.id,
            "admin_public_id": actor.public_id,
            "student_id": row.student_id,
            "student_public_id": db.session.get(plans.User, row.student_id).public_id,
            "enrollment_id": row.id,
            "group_id": owning_group.id,
            "course_id": course.id,
            "level_id": course.level_id,
            "term_id": owning_group.academic_term_id,
            "plan_id": fee_plan.id,
        }


def stored_assignments(ep):
    db.session.expire_all()
    owner = Enrollment.query.filter_by(public_id=ep).one()
    return (
        StudentFeeAssignment.query.filter_by(enrollment_id=owner.id)
        .order_by(StudentFeeAssignment.id)
        .all()
    )


def snapshot(rows):
    return [
        (row.public_id, row.enrollment_id, row.fee_plan_id, row.status, row.version,
         row.assigned_at, row.assigned_by_id, row.cancelled_at, row.cancelled_by_id,
         row.created_at, row.updated_at)
        for row in rows
    ]


def assignments_snapshot(app, ep):
    with app.app_context():
        return snapshot(stored_assignments(ep))


# ---------------------------------------------------------------------------
# Route-driven helpers
# ---------------------------------------------------------------------------


def assign_token(client, gp, ep, pp):
    return state_in(page(client, assign_url(gp, ep, pp)), assign_url(gp, ep, pp))


def assign(client, gp, ep, pp, token=None):
    if token is None:
        token = assign_token(client, gp, ep, pp)
    return client.post(assign_url(gp, ep, pp), data={STATE_FIELD: token})


def cancel_token(client, gp, ep, ap):
    return state_in(page(client, history_url(gp, ep)), cancel_url(gp, ep, ap))


def cancel(client, gp, ep, ap, token=None):
    if token is None:
        token = cancel_token(client, gp, ep, ap)
    return client.post(cancel_url(gp, ep, ap), data={STATE_FIELD: token})


def withdraw(client, gp, ep):
    return client.post(withdraw_url(gp, ep))


def reactivate(client, gp, ep):
    return client.post(reactivate_url(gp, ep))


def followed(client, response):
    """The page a redirect lands on, which shows the flashed message."""
    return page(client, response.headers["Location"])
