"""Read queries and presentation for Administrator fee assignments by
Enrollment (Phase 5 / M03).

Flask-independent: explicit, column-projected queries returning rows or
plain presentation dicts, and no ``request``, ``abort`` or template.
Route-level 404 / redirect handling belongs in
``app/blueprints/admin/fee_assignments.py``.

**Every read here is reachable only by an active Administrator.** There is no
Student, Teacher, Researcher or public read of a fee assignment anywhere in
M03, and nothing here is called from one -- except
:func:`enrollment_has_assigned_fee_plan`, which the Administrator's Enrollment
withdrawal route asks after its locks.

**Nesting is part of every lookup.** An Enrollment is found only through the
Group in its URL and only when it references a Student-role account, exactly
as the Manage Members routes find one; an assignment is found only inside its
own Enrollment. Anything else is ``None``, which the route turns into a 404.

**Internal ids stay inside the service layer.** Rows carry them so a route can
lock and a page can fetch totals in one keyed query; the ``build_*`` helpers
drop them before anything reaches a template.

**Bounded, and free of N+1.** Both lists are a fixed page of
:data:`PAGE_SIZE` with ``LIMIT PAGE_SIZE + 1`` and no ``COUNT``; a page's
item counts and exact totals come from **one** query keyed by its plan ids,
and each page costs a fixed number of queries whatever it holds.

**Money is added in Python, never in SQL**, through
:func:`~app.services.fee_plan_queries.active_item_summaries` and
:func:`~app.services.money.sum_amounts`. A total is shown for information and
never stored: the assigned plan is frozen, so its definition cannot move.
"""

from decimal import Decimal

from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    FeePlan,
    FeePlanItem,
    FeePlanItemStatus,
    FeePlanStatus,
    Group,
    Level,
    StudentFeeAssignment,
    StudentFeeAssignmentStatus,
    User,
    UserRole,
    UserStatus,
)
from app.models.fee_plan_item import MAX_ACTIVE_FEE_PLAN_ITEMS
from app.services.fee_plan_queries import KIND_LABELS
from app.services.fee_plan_queries import STATUS_LABELS as PLAN_STATUS_LABELS
from app.services.money import format_amount, sum_amounts
from app.services.schedule_occurrences import to_app_local

_ASSIGNED = StudentFeeAssignmentStatus.ASSIGNED.value
_CANCELLED = StudentFeeAssignmentStatus.CANCELLED.value
_PLAN_ACTIVE = FeePlanStatus.ACTIVE.value
_ITEM_ACTIVE = FeePlanItemStatus.ACTIVE.value
_ACADEMIC_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value

#: The fixed page size of both lists. Declared here: each feature owns its
#: own bounds.
PAGE_SIZE = 20

_PUBLIC_ID_MAX_LENGTH = 36

STATUS_LABELS = {
    _ASSIGNED: "Assigned",
    _CANCELLED: "Cancelled",
}

#: Why an Enrollment cannot receive a new assignment, as a code. The route
#: owns the wording.
BLOCK_ENROLLMENT_INACTIVE = "enrollment_inactive"
BLOCK_STUDENT_INACTIVE = "student_inactive"
BLOCK_ACADEMIC_INACTIVE = "academic_inactive"
BLOCK_ALREADY_ASSIGNED = "already_assigned"


def _public_id_ok(value):
    return bool(value) and len(value) <= _PUBLIC_ID_MAX_LENGTH


def _local(tz_name, moment):
    return None if moment is None else to_app_local(tz_name, moment)


def _page(query, page):
    rows = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1).all()
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


# ---------------------------------------------------------------------------
# The Enrollment in the URL
# ---------------------------------------------------------------------------


def enrollment_fee_context(group_public_id, enrollment_public_id):
    """One row describing the Enrollment, its Student and its academic
    chain, or ``None`` when the Group does not exist, the Enrollment is not
    in it, or the Enrollment does not reference a Student-role account.

    One query. A pre-lock read: it decides which rows a write locks and what
    a page shows, never whether a write is allowed.
    """
    if not _public_id_ok(group_public_id) or not _public_id_ok(enrollment_public_id):
        return None
    return (
        db.session.query(
            Group.id.label("group_id"),
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            Group.code.label("group_code"),
            Group.status.label("group_status"),
            AcademicTerm.id.label("academic_term_id"),
            AcademicTerm.name.label("term_name"),
            AcademicTerm.status.label("term_status"),
            Course.id.label("course_id"),
            Course.title.label("course_title"),
            Course.status.label("course_status"),
            Level.id.label("level_id"),
            Level.name.label("level_name"),
            Level.status.label("level_status"),
            Enrollment.id.label("enrollment_id"),
            Enrollment.public_id.label("enrollment_public_id"),
            Enrollment.status.label("enrollment_status"),
            User.id.label("student_id"),
            User.full_name.label("student_name"),
            User.email.label("student_email"),
            User.status.label("student_status"),
        )
        .select_from(Enrollment)
        .join(Group, Group.id == Enrollment.group_id)
        .join(Course, Course.id == Group.course_id)
        .join(Level, Level.id == Course.level_id)
        .join(AcademicTerm, AcademicTerm.id == Group.academic_term_id)
        .join(User, User.id == Enrollment.student_id)
        .filter(
            Group.public_id == group_public_id,
            Enrollment.public_id == enrollment_public_id,
            User.role == _STUDENT,
        )
        .first()
    )


def build_enrollment_context_view(context):
    """The facts every M03 page shows about its Enrollment. No internal id."""
    return {
        "group_public_id": context.group_public_id,
        "group_name": context.group_name,
        "group_code": context.group_code,
        "group_status": context.group_status,
        "course_title": context.course_title,
        "course_status": context.course_status,
        "level_name": context.level_name,
        "level_status": context.level_status,
        "term_name": context.term_name,
        "term_status": context.term_status,
        "enrollment_public_id": context.enrollment_public_id,
        "enrollment_status": context.enrollment_status,
        "student_name": context.student_name,
        "student_email": context.student_email,
        "student_status": context.student_status,
    }


def enrollment_has_assigned_fee_plan(enrollment_id):
    """Whether `enrollment_id` currently has an ``assigned`` fee plan.

    Enrollment withdrawal asks this after its Group -> Student -> Enrollment
    locks: every assignment insert and cancellation takes that same
    Enrollment lock first, so the answer is current.
    """
    query = db.session.query(StudentFeeAssignment.id).filter(
        StudentFeeAssignment.enrollment_id == enrollment_id,
        StudentFeeAssignment.status == _ASSIGNED,
    )
    return bool(db.session.query(query.exists()).scalar())


def assignment_block_reason(
    enrollment_status, student_status, academic_statuses, has_assigned_plan
):
    """The first reason an Enrollment cannot receive a new assignment, as a
    ``BLOCK_*`` code, or ``None``.

    `academic_statuses` are the Group, Course, Level and Academic Term
    statuses. Used for the friendly preview and, with the locked rows'
    values, for the authoritative decision.
    """
    if enrollment_status != _ENROLLMENT_ACTIVE:
        return BLOCK_ENROLLMENT_INACTIVE
    if student_status != _USER_ACTIVE:
        return BLOCK_STUDENT_INACTIVE
    if any(status != _ACADEMIC_ACTIVE for status in academic_statuses):
        return BLOCK_ACADEMIC_INACTIVE
    if has_assigned_plan:
        return BLOCK_ALREADY_ASSIGNED
    return None


def context_block_reason(context):
    """:func:`assignment_block_reason` for a pre-lock context row."""
    return assignment_block_reason(
        context.enrollment_status,
        context.student_status,
        (context.group_status, context.course_status, context.level_status, context.term_status),
        enrollment_has_assigned_fee_plan(context.enrollment_id),
    )


# ---------------------------------------------------------------------------
# One Enrollment's history
# ---------------------------------------------------------------------------


def fee_assignment_history_page(enrollment_id, page):
    """``(rows, has_next)`` for one page of the Enrollment's assignments,
    newest first (``id DESC``). One query: the plan, the assigning account and
    the cancelling account are joined in."""
    assigner = aliased(User)
    canceller = aliased(User)
    query = (
        db.session.query(
            StudentFeeAssignment.public_id,
            StudentFeeAssignment.status,
            StudentFeeAssignment.version,
            StudentFeeAssignment.fee_plan_id,
            StudentFeeAssignment.assigned_at,
            StudentFeeAssignment.cancelled_at,
            FeePlan.public_id.label("plan_public_id"),
            FeePlan.name.label("plan_name"),
            FeePlan.status.label("plan_status"),
            FeePlan.currency_code.label("currency_code"),
            assigner.full_name.label("assigned_by_name"),
            canceller.full_name.label("cancelled_by_name"),
        )
        .select_from(StudentFeeAssignment)
        .join(FeePlan, FeePlan.id == StudentFeeAssignment.fee_plan_id)
        .join(assigner, assigner.id == StudentFeeAssignment.assigned_by_id)
        .outerjoin(canceller, canceller.id == StudentFeeAssignment.cancelled_by_id)
        .filter(StudentFeeAssignment.enrollment_id == enrollment_id)
        .order_by(StudentFeeAssignment.id.desc())
    )
    return _page(query, page)


def build_history_view(rows, summaries, tz_name="UTC"):
    """Presentation dicts for one history page. No internal id survives."""
    view = []
    for row in rows:
        count, total = summaries.get(row.fee_plan_id, (0, Decimal(0)))
        view.append(
            {
                "public_id": row.public_id,
                "status": row.status,
                "status_label": STATUS_LABELS.get(row.status, row.status),
                "version": row.version,
                "plan_public_id": row.plan_public_id,
                "plan_name": row.plan_name,
                "plan_status": row.plan_status,
                "plan_status_label": PLAN_STATUS_LABELS.get(row.plan_status, row.plan_status),
                "currency_code": row.currency_code,
                "item_count": count,
                "total_text": format_amount(total),
                "assigned_local": _local(tz_name, row.assigned_at),
                "assigned_by_name": row.assigned_by_name,
                "cancelled_local": _local(tz_name, row.cancelled_at),
                "cancelled_by_name": row.cancelled_by_name,
            }
        )
    return view


def enrollment_fee_assignment(enrollment_id, assignment_public_id):
    """One assignment by ``public_id`` **inside** `enrollment_id`, or
    ``None``. An assignment of another Enrollment is not found."""
    if not _public_id_ok(assignment_public_id):
        return None
    return StudentFeeAssignment.query.filter(
        StudentFeeAssignment.enrollment_id == enrollment_id,
        StudentFeeAssignment.public_id == assignment_public_id,
    ).first()


# ---------------------------------------------------------------------------
# Choosing and confirming a plan
# ---------------------------------------------------------------------------


def _assignable_plan_filter(query):
    return query.filter(FeePlan.status == _PLAN_ACTIVE, FeePlan.first_activated_at.isnot(None))


def assignable_plans_page(page):
    """``(rows, has_next)`` for one page of **active** plans, newest first --
    the order and index the catalogue itself uses. Drafts and archived plans
    are never listed."""
    query = _assignable_plan_filter(
        db.session.query(FeePlan.id, FeePlan.public_id, FeePlan.name, FeePlan.currency_code)
    ).order_by(FeePlan.id.desc())
    return _page(query, page)


def build_plan_choice_view(rows, summaries):
    """Presentation dicts for one page of plan choices. A plan whose active
    item count is outside the valid range is shown but not selectable; the
    confirmation page proves the full item rule. No internal id survives."""
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


def assignable_fee_plan(public_id):
    """One **active**, activated plan by ``public_id``, or ``None`` -- a draft,
    an archived plan and an unknown id are all simply not found."""
    if not _public_id_ok(public_id):
        return None
    return _assignable_plan_filter(FeePlan.query.filter(FeePlan.public_id == public_id)).first()


def fee_plan_active_items(plan_id):
    """`plan_id`'s active items, ascending internal id."""
    return (
        FeePlanItem.query.filter(
            FeePlanItem.fee_plan_id == plan_id, FeePlanItem.status == _ITEM_ACTIVE
        )
        .order_by(FeePlanItem.id.asc())
        .all()
    )


def build_plan_confirmation_view(plan, items):
    """The plan exactly as it would be assigned: its items and their exact
    total. No internal id survives."""
    return {
        "public_id": plan.public_id,
        "name": plan.name,
        "description": plan.description,
        "currency_code": plan.currency_code,
        #: Not "items": Jinja resolves ``plan.items`` to the dict method.
        "lines": [
            {
                "kind_label": KIND_LABELS.get(item.kind, item.kind),
                "label": item.label,
                "amount_text": format_amount(item.amount),
            }
            for item in items
        ],
        "item_count": len(items),
        "total_text": format_amount(sum_amounts([item.amount for item in items])),
    }
