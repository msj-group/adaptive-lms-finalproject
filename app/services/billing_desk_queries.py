"""Read queries and presentation for the Administrator Billing Desk
(Phase 5 / M09).

Flask-independent: explicit queries and plain presentation dicts, and no
``request``, ``abort``, ``url_for`` or template. The route in
``app/blueprints/admin/billing_desk.py`` turns the action *keys* returned here
into links to the existing routes.

**A guide, never a writer.** Nothing here adds, changes, flushes, locks or
commits a row. The desk finds one Enrollment's fee assignment and invoice and
says which existing page is the next step; every write stays on its own route,
which re-proves everything after its locks.

**Selection is by public id.** A Group is chosen by its ``public_id``; a Student
by the Student account's ``public_id`` *within* that Group, which names at most
one Enrollment (``uq_enrollments_student_group``). A numeric id is never looked
up.

**The rules are the existing ones.** Whether a plan may be assigned is
:func:`~app.services.student_fee_assignment_queries.context_block_reason`;
whether a draft may be created is
:func:`~app.services.invoice_queries.context_draft_block_reason`; the balance is
M05's ``payment_balance`` and whether a payment may be recorded is the payments
page's own decision, which the route passes in. The desk only adds caution: it
offers no collection shortcut while a bank transfer is pending or a provider
event awaits reconciliation, because deciding those comes first.

**Payments are invoice-wide.** The registration and course subtotals are an
explanation of the invoice's lines; a payment reduces the invoice's outstanding
balance and is never allocated to an item.

**Bounded, free of N+1.** The student list is one page of
:data:`PAGE_SIZE` plus one ``COUNT`` (so the range shown is exact), and its
fee-plan and invoice states come from one keyed query each. A selected
Enrollment costs a fixed number of queries.
"""

import re

from sqlalchemy import func, or_

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    Invoice,
    InvoiceItemKind,
    InvoiceStatus,
    Level,
    PaymentTransactionStatus,
    StudentFeeAssignment,
    StudentFeeAssignmentStatus,
    User,
    UserRole,
    UserStatus,
)
from app.services.invoice_queries import STATUS_LABELS as INVOICE_STATUS_LABELS
from app.services.money import format_amount, sum_amounts
from app.services.search_terms import escape_like

_ASSIGNED = StudentFeeAssignmentStatus.ASSIGNED.value
_ASSIGNMENT_CANCELLED = StudentFeeAssignmentStatus.CANCELLED.value
_DRAFT = InvoiceStatus.DRAFT.value
_ISSUED = InvoiceStatus.ISSUED.value
_INVOICE_CANCELLED = InvoiceStatus.CANCELLED.value
_OPEN = (_DRAFT, _ISSUED)
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_GROUP_ACTIVE = AcademicStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_PENDING = PaymentTransactionStatus.PENDING.value
_REGISTRATION = InvoiceItemKind.REGISTRATION.value
_COURSE = InvoiceItemKind.COURSE.value

#: Students per page of a Group's list.
PAGE_SIZE = 20
#: The longest search text read; anything longer is cut, never an error.
MAX_SEARCH_LENGTH = 100

_PUBLIC_ID_SHAPE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

#: Stated wherever amounts are shown.
ALLOCATION_NOTE = (
    "Payments reduce the invoice balance and are not allocated to a particular fee item."
)

# ---------------------------------------------------------------------------
# The stages of one Enrollment's billing, and the actions each allows
# ---------------------------------------------------------------------------

WITHDRAWN = "withdrawn"
NO_ASSIGNMENT = "no_assignment"
ASSIGNMENT_CANCELLED = "assignment_cancelled"
NO_INVOICE = "no_invoice"
INVOICE_CANCELLED = "invoice_cancelled"
DRAFT = "draft"
OUTSTANDING = "outstanding"
BLOCKED = "blocked"
PAID = "paid"
INCONSISTENT = "inconsistent"

#: Action keys. The route maps each to an existing route; the desk itself
#: performs none of them.
ASSIGN_PLAN = "assign_plan"
CREATE_DRAFT = "create_draft"
OPEN_DRAFT = "open_draft"
RECORD_CASH = "record_cash"
RECORD_BANK = "record_bank"
OPEN_PAYMENTS = "open_payments"
OPEN_INVOICE = "open_invoice"
OPEN_INTENTS = "open_intents"
FEE_HISTORY = "fee_history"
INVOICE_HISTORY = "invoice_history"

STAGE_LABELS = {
    WITHDRAWN: "Enrollment withdrawn",
    NO_ASSIGNMENT: "No fee plan assigned",
    ASSIGNMENT_CANCELLED: "Fee assignment cancelled",
    NO_INVOICE: "Fee plan assigned, no invoice yet",
    INVOICE_CANCELLED: "Invoice cancelled",
    DRAFT: "Draft invoice",
    OUTSTANDING: "Invoice issued, balance outstanding",
    BLOCKED: "Invoice issued, payment needs attention first",
    PAID: "Invoice fully paid",
    INCONSISTENT: "Records need review",
}

_STAGE_GUIDANCE = {
    WITHDRAWN: (
        "This Enrollment is withdrawn. Its fee history stays available; the desk offers no "
        "new assignment, invoice or payment."
    ),
    NO_ASSIGNMENT: "Assign a fee plan to this Enrollment first.",
    ASSIGNMENT_CANCELLED: (
        "The last fee assignment was cancelled. Review the fee history before charging this "
        "Enrollment again; the desk offers no new assignment after a cancellation."
    ),
    NO_INVOICE: "Create a draft invoice from the assigned fee plan, then review and issue it.",
    INVOICE_CANCELLED: (
        "The last invoice was cancelled. Review the invoice history before charging again; the "
        "desk offers no new invoice after a cancellation."
    ),
    DRAFT: (
        "The draft invoice can still be corrected. Open it to review its lines and issue it; "
        "payments are recorded only against an issued invoice."
    ),
    OUTSTANDING: "Record a cash payment or a bank transfer against the outstanding balance.",
    BLOCKED: "Resolve what is shown below on the payments page before recording a payment.",
    PAID: "Nothing is outstanding. The payments and receipts stay available.",
    INCONSISTENT: (
        "This Enrollment's records do not describe one current fee assignment and invoice. "
        "Review its history; the desk offers no action."
    ),
}

_PENDING_NOTE = (
    "{count} bank transfer(s) of this invoice are pending. Confirm or reject them on the "
    "payments page before recording another payment."
)
_RECONCILIATION_NOTE = (
    "A verified provider event for this invoice requires reconciliation. Review the payments "
    "and payment intents pages before recording a payment."
)

_PLAN_STATE_LABELS = {_ASSIGNED: "Assigned", _ASSIGNMENT_CANCELLED: "Cancelled"}


def public_id_ok(value):
    """Whether `value` has the exact shape of a public id."""
    return isinstance(value, str) and _PUBLIC_ID_SHAPE.fullmatch(value) is not None


def normalize_search(raw):
    """Search text with whitespace collapsed, at most
    :data:`MAX_SEARCH_LENGTH` characters."""
    text = " ".join((raw or "").split())
    return text[:MAX_SEARCH_LENGTH].strip()


# ---------------------------------------------------------------------------
# Step 1: the Group
# ---------------------------------------------------------------------------


def desk_group(public_id):
    """The Group whose ``public_id`` is exactly `public_id`, with its Course,
    Level and Academic Term, or ``None``. One query."""
    if not public_id_ok(public_id):
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
        .join(Course, Course.id == Group.course_id)
        .join(Level, Level.id == Course.level_id)
        .join(AcademicTerm, AcademicTerm.id == Group.academic_term_id)
        .filter(Group.public_id == public_id)
        .first()
    )


def build_group_view(row):
    """A Group as the desk states it. No internal id."""
    return {
        "public_id": row.public_id,
        "name": row.name,
        "is_archived": row.status != _GROUP_ACTIVE,
        "course_title": row.course_title,
        "level_name": row.level_name,
        "term_name": row.term_name,
    }


# ---------------------------------------------------------------------------
# Step 2: the Group's currently enrolled Students
# ---------------------------------------------------------------------------


def _enrolled_query(group_id, search):
    query = (
        db.session.query(
            Enrollment.id.label("enrollment_id"),
            User.public_id.label("student_public_id"),
            User.full_name,
            User.email,
            User.status.label("student_status"),
        )
        .select_from(Enrollment)
        .join(User, User.id == Enrollment.student_id)
        .filter(
            Enrollment.group_id == group_id,
            Enrollment.status == _ENROLLMENT_ACTIVE,
            User.role == _STUDENT,
        )
    )
    if search:
        # Prefix matching, exactly as the Administrator student list searches:
        # the start of the name, of any word in it, or of the email.
        escaped = escape_like(search)
        query = query.filter(
            or_(
                User.full_name.ilike(f"{escaped}%", escape="\\"),
                User.full_name.ilike(f"% {escaped}%", escape="\\"),
                User.email.ilike(f"{escaped}%", escape="\\"),
            )
        )
    return query


def enrolled_students_page(group_id, search, page):
    """``(rows, total, page)``: one page of the Group's Students with an
    active Enrollment, by name then Enrollment, the exact number matching, and
    the page shown (page 1 when `page` is past the end). Two queries."""
    query = _enrolled_query(group_id, search)
    total = query.order_by(None).with_entities(func.count(Enrollment.id)).scalar()
    pages = max(1, -(-total // PAGE_SIZE))
    if page > pages:
        page = 1
    rows = (
        query.order_by(User.full_name.asc(), Enrollment.id.asc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )
    return rows, total, page


def list_states(enrollment_ids):
    """``{enrollment_id: (fee plan label, invoice label)}`` for one page of the
    list: whether each Enrollment has an assigned (or only a cancelled) fee
    plan, and the status of the assigned plan's open invoice. One keyed query
    each; no amount is read."""
    ids = list(enrollment_ids)
    if not ids:
        return {}
    plans = {}
    for enrollment_id, status in (
        db.session.query(StudentFeeAssignment.enrollment_id, StudentFeeAssignment.status)
        .filter(StudentFeeAssignment.enrollment_id.in_(ids))
        .all()
    ):
        if plans.get(enrollment_id) != _ASSIGNED:
            plans[enrollment_id] = status
    invoices = dict(
        db.session.query(StudentFeeAssignment.enrollment_id, Invoice.status)
        .join(Invoice, Invoice.student_fee_assignment_id == StudentFeeAssignment.id)
        .filter(
            StudentFeeAssignment.enrollment_id.in_(ids),
            StudentFeeAssignment.status == _ASSIGNED,
            Invoice.status.in_(_OPEN),
        )
        .all()
    )
    return {
        enrollment_id: (
            _PLAN_STATE_LABELS.get(plans.get(enrollment_id), "None"),
            INVOICE_STATUS_LABELS.get(invoices.get(enrollment_id), "—"),
        )
        for enrollment_id in ids
    }


def build_student_list_view(rows, states):
    """The page's Students. Public ids only."""
    return [
        {
            "student_public_id": row.student_public_id,
            "full_name": row.full_name,
            "email": row.email,
            "is_suspended": row.student_status != _USER_ACTIVE,
            "plan_label": states.get(row.enrollment_id, ("None", "—"))[0],
            "invoice_label": states.get(row.enrollment_id, ("None", "—"))[1],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Step 3: one Student's Enrollment in the Group
# ---------------------------------------------------------------------------


def student_enrollment(group_id, student_public_id):
    """``(enrollment_public_id, enrollment_status)`` of the Student-role
    account `student_public_id` in the Group, or ``None``. One query."""
    if not public_id_ok(student_public_id):
        return None
    row = (
        db.session.query(Enrollment.public_id, Enrollment.status)
        .join(User, User.id == Enrollment.student_id)
        .filter(
            Enrollment.group_id == group_id,
            User.public_id == student_public_id,
            User.role == _STUDENT,
        )
        .first()
    )
    return None if row is None else (row.public_id, row.status)


def enrollment_assignments(enrollment_id):
    """``(assigned rows, latest row)``: the Enrollment's ``assigned`` fee
    assignments (at most two are read -- two means the records need review)
    and its most recent assignment of any status. Two queries."""
    assigned = (
        db.session.query(StudentFeeAssignment.id, StudentFeeAssignment.public_id)
        .filter(
            StudentFeeAssignment.enrollment_id == enrollment_id,
            StudentFeeAssignment.status == _ASSIGNED,
        )
        .order_by(StudentFeeAssignment.id.asc())
        .limit(2)
        .all()
    )
    latest = (
        db.session.query(
            StudentFeeAssignment.id, StudentFeeAssignment.public_id, StudentFeeAssignment.status
        )
        .filter(StudentFeeAssignment.enrollment_id == enrollment_id)
        .order_by(StudentFeeAssignment.id.desc())
        .first()
    )
    return assigned, latest


def assignment_invoices(assignment_id):
    """``(open invoices, latest invoice)``: the assignment's ``draft`` or
    ``issued`` invoices (at most two are read) and its most recent invoice of
    any status. Two queries."""
    open_invoices = (
        Invoice.query.filter(
            Invoice.student_fee_assignment_id == assignment_id, Invoice.status.in_(_OPEN)
        )
        .order_by(Invoice.id.asc())
        .limit(2)
        .all()
    )
    latest = (
        db.session.query(Invoice.public_id, Invoice.status, Invoice.invoice_number)
        .filter(Invoice.student_fee_assignment_id == assignment_id)
        .order_by(Invoice.id.desc())
        .first()
    )
    return open_invoices, latest


def kind_subtotals(active_lines):
    """``{registration, course}`` exact subtotals of an invoice's active
    lines -- an explanation of the invoice, never an allocation."""
    return {
        kind: sum_amounts([line.amount for line in active_lines if line.kind == kind])
        for kind in (_REGISTRATION, _COURSE)
    }


def pending_transfer_count(rows):
    return sum(1 for row in rows if row.status == _PENDING)


# ---------------------------------------------------------------------------
# The summary
# ---------------------------------------------------------------------------


def issued_stage(balance, payable_block, record_block, pending, reconciliation):
    """The stage of an issued invoice and the sentences explaining it.

    `payable_block` and `record_block` are the payments page's own reasons --
    the invoice cannot take a payment at all (invalid lines or balance), and
    no collection may be recorded now -- each ``None`` when there is none. An
    invalid invoice is never shown as paid; a settled valid balance is paid;
    any other block, a pending transfer or a reconciliation stops the
    collection shortcuts.
    """
    notes = []
    if pending:
        notes.append(_PENDING_NOTE.format(count=pending))
    if reconciliation:
        notes.append(_RECONCILIATION_NOTE)
    if payable_block is not None:
        return BLOCKED, [payable_block] + notes
    if balance.outstanding == 0:
        return PAID, notes
    if record_block is not None:
        return BLOCKED, [record_block] + notes
    if notes:
        return BLOCKED, notes
    return OUTSTANDING, []


def stage_actions(stage, can_assign=False, can_draft=False, has_intents=False):
    """The action keys the desk links for `stage`, in display order. Only
    the next step for the current state is ever offered."""
    if stage == NO_ASSIGNMENT:
        return [ASSIGN_PLAN] if can_assign else []
    if stage == NO_INVOICE:
        return [CREATE_DRAFT] if can_draft else []
    if stage == DRAFT:
        return [OPEN_DRAFT]
    if stage == OUTSTANDING:
        return [RECORD_CASH, RECORD_BANK, OPEN_PAYMENTS, OPEN_INVOICE]
    if stage == BLOCKED:
        return [OPEN_PAYMENTS] + ([OPEN_INTENTS] if has_intents else []) + [OPEN_INVOICE]
    if stage == PAID:
        return [OPEN_PAYMENTS, OPEN_INVOICE]
    return []


def build_money_view(balance, subtotals, currency_code):
    """Exact total, paid and outstanding amounts and the item subtotals, as
    display text; ``None`` amounts when the records give no valid balance."""
    return {
        "currency_code": currency_code,
        "balance_valid": balance is not None,
        "total_text": None if balance is None else format_amount(balance.total),
        "paid_text": None if balance is None else format_amount(balance.paid),
        "outstanding_text": None if balance is None else format_amount(balance.outstanding),
        "registration_text": format_amount(subtotals[_REGISTRATION]),
        "course_text": format_amount(subtotals[_COURSE]),
        "allocation_note": ALLOCATION_NOTE,
    }


def build_summary(context, stage, notes, actions, history, plan=None, invoice=None,
                  money=None, block_text=None):
    """The selected Enrollment as the desk shows it. No internal id, bank
    reference, provider identifier or audit snapshot."""
    return {
        "student_name": context.student_name,
        "student_email": context.student_email,
        "student_status": context.student_status,
        "group_name": context.group_name,
        "course_title": context.course_title,
        "level_name": context.level_name,
        "term_name": context.term_name,
        "enrollment_status": context.enrollment_status,
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "guidance": block_text or _STAGE_GUIDANCE[stage],
        "notes": list(notes),
        "actions": list(actions),
        "history": list(history),
        "plan": plan,
        "invoice": invoice,
        "money": money,
    }
