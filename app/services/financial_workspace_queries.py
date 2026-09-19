"""Read queries for the guided "New invoice" flow (Phase 5 / M10).

Flask-independent. Every read here is a **pre-lock preview**: it decides what
a step of the flow shows and which rows the final POST locks, never whether
the write is allowed -- the POST re-proves everything against the rows it
locked.

The flow is Student -> active Enrollment (and so Group) -> Fee Plan:

- the Student step lists **active** Student accounts, searchable and paged;
- the Enrollment step lists that Student's Enrollments with their Group,
  Course, academic chain, current fee assignment and whether it already holds
  a live open invoice, and says which may receive a new invoice;
- the plan step lists the assignable (active, activated) fee plans, and -- when
  the Enrollment already has an assigned plan that can still be invoiced --
  that plan first, to be reused.

Nothing sensitive is read; only public ids leave through the routes.
"""

from decimal import Decimal

from sqlalchemy import and_, func, or_

from app.extensions import db
from app.models import (
    MAX_ACTIVE_FEE_PLAN_ITEMS,
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    FeePlan,
    FeePlanStatus,
    Group,
    Invoice,
    InvoiceStatus,
    Level,
    StudentFeeAssignment,
    StudentFeeAssignmentStatus,
    User,
    UserRole,
    UserStatus,
)
from app.services.fee_plan_queries import active_item_summaries
from app.services.invoice_transactions import fee_plan_invoiceable
from app.services.money import format_amount
from app.services.search_terms import escape_like
from app.services.student_fee_assignment_queries import enrollment_fee_context

_STUDENT = UserRole.STUDENT.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_ACADEMIC_ACTIVE = AcademicStatus.ACTIVE.value
_ASSIGNED = StudentFeeAssignmentStatus.ASSIGNED.value
_OPEN = (InvoiceStatus.DRAFT.value, InvoiceStatus.ISSUED.value)
_PLAN_ACTIVE = FeePlanStatus.ACTIVE.value

#: Students and plans per page.
PAGE_SIZE = 25
#: The most Enrollments one Student's step lists.
ENROLLMENT_CAP = 100
MAX_SEARCH_LENGTH = 100
_PUBLIC_ID_MAX_LENGTH = 36


def _public_id_ok(value):
    return isinstance(value, str) and 0 < len(value) <= _PUBLIC_ID_MAX_LENGTH


def normalize_search(raw):
    return " ".join((raw or "").split())[:MAX_SEARCH_LENGTH].strip()


def active_students_page(search, page):
    """``(rows, total, page)``: active Student accounts by name, then id."""
    query = db.session.query(User.id, User.public_id, User.full_name, User.email).filter(
        User.role == _STUDENT, User.status == _USER_ACTIVE
    )
    if search:
        pattern = f"%{escape_like(search)}%"
        query = query.filter(
            or_(
                User.full_name.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
            )
        )
    total = query.order_by(None).with_entities(func.count(User.id)).scalar()
    page = page if (page - 1) * PAGE_SIZE < total else 1
    rows = (
        query.order_by(User.full_name.asc(), User.id.asc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )
    return rows, total, page


def active_student(student_public_id):
    """One **active** Student account by ``public_id``, or ``None``."""
    if not _public_id_ok(student_public_id):
        return None
    return User.query.filter(
        User.public_id == student_public_id, User.role == _STUDENT, User.status == _USER_ACTIVE
    ).first()


def student_enrollments(student_id):
    """``(rows, truncated)``: the Student's Enrollments, newest first, each
    with its Group, Course, academic chain and its current ``assigned`` fee
    assignment and plan, if any. One query."""
    rows = (
        db.session.query(
            Enrollment.id.label("enrollment_id"),
            Enrollment.public_id.label("enrollment_public_id"),
            Enrollment.status.label("enrollment_status"),
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            Group.code.label("group_code"),
            Group.status.label("group_status"),
            Course.title.label("course_title"),
            Course.status.label("course_status"),
            Level.status.label("level_status"),
            AcademicTerm.name.label("term_name"),
            AcademicTerm.status.label("term_status"),
            StudentFeeAssignment.id.label("assignment_id"),
            StudentFeeAssignment.public_id.label("assignment_public_id"),
            FeePlan.public_id.label("plan_public_id"),
            FeePlan.name.label("plan_name"),
        )
        .select_from(Enrollment)
        .join(Group, Group.id == Enrollment.group_id)
        .join(Course, Course.id == Group.course_id)
        .join(Level, Level.id == Course.level_id)
        .join(AcademicTerm, AcademicTerm.id == Group.academic_term_id)
        .outerjoin(
            StudentFeeAssignment,
            and_(
                StudentFeeAssignment.enrollment_id == Enrollment.id,
                StudentFeeAssignment.status == _ASSIGNED,
            ),
        )
        .outerjoin(FeePlan, FeePlan.id == StudentFeeAssignment.fee_plan_id)
        .filter(Enrollment.student_id == student_id)
        .order_by(Enrollment.id.desc())
        .limit(ENROLLMENT_CAP + 1)
        .all()
    )
    return rows[:ENROLLMENT_CAP], len(rows) > ENROLLMENT_CAP


def assignments_with_live_open_invoice(assignment_ids):
    """``{assignment_id: invoice public id}`` for the assignments among
    `assignment_ids` that hold a live draft or issued invoice. One query."""
    ids = [row_id for row_id in assignment_ids if row_id is not None]
    if not ids:
        return {}
    rows = (
        db.session.query(Invoice.student_fee_assignment_id, Invoice.public_id)
        .filter(
            Invoice.student_fee_assignment_id.in_(ids),
            Invoice.status.in_(_OPEN),
            Invoice.deleted_at.is_(None),
        )
        .all()
    )
    return {assignment_id: public_id for assignment_id, public_id in rows}


def enrollment_is_invoiceable(row):
    """Whether an Enrollment row's own chain may receive a new invoice: the
    Enrollment and its Group, Course, Level and Academic Term active."""
    return row.enrollment_status == _ENROLLMENT_ACTIVE and all(
        status == _ACADEMIC_ACTIVE
        for status in (row.group_status, row.course_status, row.level_status, row.term_status)
    )


def workspace_context(student_public_id, enrollment_public_id):
    """The M03 Enrollment context of `enrollment_public_id` -- only when it
    belongs to the active Student `student_public_id` -- or ``None``. Two
    queries; the client's pairing of the two ids is never trusted."""
    if not _public_id_ok(student_public_id) or not _public_id_ok(enrollment_public_id):
        return None
    group_public_id = (
        db.session.query(Group.public_id)
        .join(Enrollment, Enrollment.group_id == Group.id)
        .join(User, User.id == Enrollment.student_id)
        .filter(
            Enrollment.public_id == enrollment_public_id,
            User.public_id == student_public_id,
            User.role == _STUDENT,
        )
        .scalar()
    )
    if group_public_id is None:
        return None
    return enrollment_fee_context(group_public_id, enrollment_public_id)


def current_assignment(enrollment_id):
    """The Enrollment's ``assigned`` fee assignment, or ``None``."""
    return StudentFeeAssignment.query.filter(
        StudentFeeAssignment.enrollment_id == enrollment_id,
        StudentFeeAssignment.status == _ASSIGNED,
    ).first()


def assignable_plans(page):
    """``(rows, has_next)`` for one page of active, activated fee plans,
    newest first."""
    rows = (
        db.session.query(FeePlan.id, FeePlan.public_id, FeePlan.name, FeePlan.currency_code)
        .filter(FeePlan.status == _PLAN_ACTIVE, FeePlan.first_activated_at.isnot(None))
        .order_by(FeePlan.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


def plan_choice_view(rows):
    """The plan choices with their active item count and exact total; a plan
    whose item count is outside the valid range is shown, not selectable.
    One keyed query."""
    summaries = active_item_summaries([row.id for row in rows])
    view = []
    for row in rows:
        count, total = summaries.get(row.id, (0, Decimal(0)))
        view.append(
            {
                "public_id": row.public_id,
                "name": row.name,
                "currency_code": row.currency_code,
                "item_count": count,
                "total_text": format_amount(total),
                "selectable": 1 <= count <= MAX_ACTIVE_FEE_PLAN_ITEMS,
            }
        )
    return view


def chosen_plan(plan_public_id, reused_plan_id=None):
    """The plan the flow may use: an active, activated plan, or -- the only
    exception -- the plan the Enrollment's current assignment already uses,
    while it can still be invoiced (an archived plan keeps invoicing an
    existing assignment, exactly as on the assignment's own page)."""
    if not _public_id_ok(plan_public_id):
        return None
    plan = FeePlan.query.filter(FeePlan.public_id == plan_public_id).first()
    if plan is None:
        return None
    if plan.id == reused_plan_id and fee_plan_invoiceable(plan):
        return plan
    if plan.status == _PLAN_ACTIVE and plan.first_activated_at is not None:
        return plan
    return None
