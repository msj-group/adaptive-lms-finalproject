"""Read queries and presentation for Student Accounts (Phase 5 / M10).

Flask-independent: explicit queries and plain presentation dicts; the routes
in ``app/blueprints/admin/student_accounts.py`` build every link from the
public ids read here.

**A Student's financial status is computed, never stored.** It is derived on
every request from the Student's live invoices, their active lines and their
live transactions -- deleted documents count for nothing -- and is written
to no User, Enrollment, Group or other row. It never changes an account,
Enrollment, Group membership or academic status. The five labels:

- ``No financial obligation`` -- no live draft or issued invoice;
- ``Draft invoice`` -- live drafts only, nothing issued yet;
- ``Amount due`` -- a live issued invoice has a positive outstanding balance;
- ``Settled`` -- live issued invoices exist and every one is fully paid;
- ``Needs review`` -- a live issued invoice's lines or payments do not
  describe a valid balance. No balance is shown then, never a misleading one.

Balances are M05's ``payment_balance`` over each issued invoice, read with the
Invoice Register's own facts (valid lines within ``MAX_INVOICE_ITEM_ROWS``,
transactions within ``MAX_INVOICE_PAYMENT_ROWS``). Money is added in Python
``Decimal``, never in SQL. A payment reduces its invoice's balance and is never
allocated to an item.

**Nothing sensitive is read**: no bank reference, provider identifier, webhook
data, idempotency key, audit snapshot or internal id reaches a template.

**Bounded, free of N+1.** The list is one ``COUNT``, one page of Students and
two keyed queries for their invoices' facts. The Financial Record is a fixed
set of queries, each capped (:data:`RECORD_INVOICE_CAP`,
:data:`RECORD_PAYMENT_CAP`) with a truncation note.
"""

from collections import defaultdict

from sqlalchemy import func, or_

from app.extensions import db
from app.models import (
    Course,
    Enrollment,
    FeePlan,
    Group,
    Invoice,
    InvoiceStatus,
    PaymentMethod,
    PaymentTransaction,
    PaymentTransactionKind,
    PaymentTransactionStatus,
    Receipt,
    ReceiptStatus,
    StudentFeeAssignment,
    User,
    UserRole,
    UserStatus,
)
from app.services.invoice_queries import STATUS_LABELS as INVOICE_STATUS_LABELS
from app.services.invoice_register_queries import (
    OUTSTANDING,
    UNAVAILABLE,
    invoice_money,
    money_facts,
)
from app.services.money import format_amount, sum_amounts
from app.services.payment_queries import KIND_LABELS, METHOD_LABELS, RECEIPT_STATUS_LABELS
from app.services.payment_queries import STATUS_LABELS as PAYMENT_STATUS_LABELS
from app.services.schedule_occurrences import to_app_local
from app.services.search_terms import escape_like

_STUDENT = UserRole.STUDENT.value
_DRAFT = InvoiceStatus.DRAFT.value
_ISSUED = InvoiceStatus.ISSUED.value
_OPEN = (_DRAFT, _ISSUED)
_PENDING = PaymentTransactionStatus.PENDING.value

#: Students per page.
PAGE_SIZE = 25
#: The longest search text read; anything longer is cut, never an error.
MAX_SEARCH_LENGTH = 100
#: The most invoices and transactions one Financial Record lists.
RECORD_INVOICE_CAP = 200
RECORD_PAYMENT_CAP = 500

NO_OBLIGATION = "no_obligation"
DRAFT_INVOICE = "draft_invoice"
AMOUNT_DUE = "amount_due"
SETTLED = "settled"
NEEDS_REVIEW = "needs_review"

FINANCIAL_STATUS_LABELS = {
    NO_OBLIGATION: "No financial obligation",
    DRAFT_INVOICE: "Draft invoice",
    AMOUNT_DUE: "Amount due",
    SETTLED: "Settled",
    NEEDS_REVIEW: "Needs review",
}
ACCOUNT_STATUS_LABELS = {
    UserStatus.ACTIVE.value: "Active",
    UserStatus.SUSPENDED.value: "Suspended",
}


def _local(tz_name, moment):
    return None if moment is None else to_app_local(tz_name, moment)


def normalize_search(raw):
    """Whitespace collapsed, cut to :data:`MAX_SEARCH_LENGTH`."""
    return " ".join((raw or "").split())[:MAX_SEARCH_LENGTH].strip()


# ---------------------------------------------------------------------------
# The computed status
# ---------------------------------------------------------------------------


def financial_status(invoices, facts):
    """``(code, has_open, has_amount_due, outstanding)`` for one Student from
    `invoices` -- rows with ``id`` and ``status`` -- and the register's
    `facts`. `has_amount_due` and `outstanding` are ``None`` when a balance
    needs review. Cancelled invoices describe no obligation."""
    lines, movements = facts
    open_rows = [row for row in invoices if row.status in _OPEN]
    issued = [row for row in open_rows if row.status == _ISSUED]
    states, balances = [], []
    for row in issued:
        _total, balance, state = invoice_money(
            row.status, lines.get(row.id, []), movements.get(row.id, [])
        )
        states.append(state)
        if balance is not None:
            balances.append(balance)
    if UNAVAILABLE in states:
        return NEEDS_REVIEW, bool(open_rows), None, None
    outstanding = sum_amounts([balance.outstanding for balance in balances])
    if OUTSTANDING in states:
        return AMOUNT_DUE, True, True, outstanding
    if issued:
        return SETTLED, True, False, outstanding
    if open_rows:
        return DRAFT_INVOICE, True, False, outstanding
    return NO_OBLIGATION, False, False, outstanding


def _status_view(code, has_open, has_amount_due, outstanding, currency):
    return {
        "code": code,
        "label": FINANCIAL_STATUS_LABELS[code],
        "has_open_invoice": has_open,
        "has_amount_due": has_amount_due,
        "outstanding_text": None if outstanding is None else format_amount(outstanding),
        "currency_code": currency,
    }


def _student_invoice_query():
    return (
        db.session.query(
            Invoice.id,
            Invoice.status,
            Enrollment.student_id.label("student_id"),
        )
        .select_from(Invoice)
        .join(StudentFeeAssignment, StudentFeeAssignment.id == Invoice.student_fee_assignment_id)
        .join(Enrollment, Enrollment.id == StudentFeeAssignment.enrollment_id)
        .filter(Invoice.deleted_at.is_(None))
    )


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


def students_page(search, page):
    """``(rows, total, page)``: one page of Student accounts -- every status
    -- by name, then id, with the exact number matching the literal,
    case-insensitive search over name and email. A past-the-end page is
    page 1."""
    query = db.session.query(
        User.id, User.public_id, User.full_name, User.email, User.status
    ).filter(User.role == _STUDENT)
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


def open_invoice_facts(student_ids):
    """``({student_id: [invoice rows]}, facts)`` -- every live draft or issued
    invoice of `student_ids` and their lines and live transactions. Three
    queries."""
    ids = list(student_ids)
    if not ids:
        return {}, ({}, {})
    rows = (
        _student_invoice_query()
        .filter(Enrollment.student_id.in_(ids), Invoice.status.in_(_OPEN))
        .order_by(Invoice.id.asc())
        .all()
    )
    by_student = defaultdict(list)
    for row in rows:
        by_student[row.student_id].append(row)
    invoice_ids = [row.id for row in rows]
    facts = money_facts(lambda column: column.in_(invoice_ids)) if invoice_ids else ({}, {})
    return by_student, facts


def build_accounts_view(rows, by_student, facts, currency):
    """The list's rows: name, email and the computed financial facts only."""
    view = []
    for row in rows:
        code, has_open, has_due, outstanding = financial_status(by_student.get(row.id, []), facts)
        view.append(
            {
                "public_id": row.public_id,
                "full_name": row.full_name,
                "email": row.email,
                "account_status_label": ACCOUNT_STATUS_LABELS.get(row.status, row.status),
                **_status_view(code, has_open, has_due, outstanding, currency),
            }
        )
    return view


# ---------------------------------------------------------------------------
# One Student's Financial Record
# ---------------------------------------------------------------------------


def account_student(student_public_id):
    """One Student-role account by ``public_id``, or ``None``."""
    if not student_public_id or len(student_public_id) > 36:
        return None
    return User.query.filter(User.public_id == student_public_id, User.role == _STUDENT).first()


def record_invoices(student_id):
    """``(rows, truncated)``: every live invoice of the Student, across all of
    their Enrollments and Groups and in every status, newest first, capped at
    :data:`RECORD_INVOICE_CAP`. One query."""
    rows = (
        db.session.query(
            Invoice.id,
            Invoice.public_id,
            Invoice.status,
            Invoice.invoice_number,
            Invoice.currency_code,
            Invoice.created_at,
            Invoice.issued_at,
            StudentFeeAssignment.public_id.label("assignment_public_id"),
            Enrollment.public_id.label("enrollment_public_id"),
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            Course.title.label("course_title"),
            FeePlan.name.label("plan_name"),
        )
        .select_from(Invoice)
        .join(StudentFeeAssignment, StudentFeeAssignment.id == Invoice.student_fee_assignment_id)
        .join(Enrollment, Enrollment.id == StudentFeeAssignment.enrollment_id)
        .join(Group, Group.id == Enrollment.group_id)
        .join(Course, Course.id == Group.course_id)
        .join(FeePlan, FeePlan.id == StudentFeeAssignment.fee_plan_id)
        .filter(Enrollment.student_id == student_id, Invoice.deleted_at.is_(None))
        .order_by(Invoice.id.desc())
        .limit(RECORD_INVOICE_CAP + 1)
        .all()
    )
    return rows[:RECORD_INVOICE_CAP], len(rows) > RECORD_INVOICE_CAP


def record_payments(student_id):
    """``(rows, truncated)``: every live transaction of the Student's live
    invoices with its live receipt, oldest first (``recorded_at``, then
    ``id``), capped at :data:`RECORD_PAYMENT_CAP`. One query; no bank
    reference, provider or intent value is selected."""
    rows = (
        db.session.query(
            PaymentTransaction.public_id,
            PaymentTransaction.kind,
            PaymentTransaction.method,
            PaymentTransaction.status,
            PaymentTransaction.amount,
            PaymentTransaction.currency_code,
            PaymentTransaction.recorded_at,
            Invoice.public_id.label("invoice_public_id"),
            Invoice.invoice_number,
            StudentFeeAssignment.public_id.label("assignment_public_id"),
            Enrollment.public_id.label("enrollment_public_id"),
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            Receipt.public_id.label("receipt_public_id"),
            Receipt.receipt_number,
            Receipt.status.label("receipt_status"),
        )
        .select_from(PaymentTransaction)
        .join(Invoice, Invoice.id == PaymentTransaction.invoice_id)
        .join(StudentFeeAssignment, StudentFeeAssignment.id == Invoice.student_fee_assignment_id)
        .join(Enrollment, Enrollment.id == StudentFeeAssignment.enrollment_id)
        .join(Group, Group.id == Enrollment.group_id)
        .outerjoin(
            Receipt,
            (Receipt.payment_transaction_id == PaymentTransaction.id)
            & Receipt.deleted_at.is_(None),
        )
        .filter(
            Enrollment.student_id == student_id,
            Invoice.deleted_at.is_(None),
            PaymentTransaction.deleted_at.is_(None),
        )
        .order_by(PaymentTransaction.recorded_at.asc(), PaymentTransaction.id.asc())
        .limit(RECORD_PAYMENT_CAP + 1)
        .all()
    )
    return rows[:RECORD_PAYMENT_CAP], len(rows) > RECORD_PAYMENT_CAP


def build_record_invoices_view(rows, facts, tz_name="UTC"):
    """The record's invoices: number, status, Group, total and -- for an
    issued invoice with a valid balance -- paid and outstanding."""
    lines, movements = facts
    view = []
    for row in rows:
        total, balance, state = invoice_money(
            row.status, lines.get(row.id, []), movements.get(row.id, [])
        )
        notes = []
        if row.status == _ISSUED and any(
            item.status == _PENDING for item in movements.get(row.id, [])
        ):
            notes.append("Bank transfer pending")
        view.append(
            {
                "public_id": row.public_id,
                "number": row.invoice_number,
                "status": row.status,
                "status_label": INVOICE_STATUS_LABELS.get(row.status, row.status),
                "group_name": row.group_name,
                "course_title": row.course_title,
                "plan_name": row.plan_name,
                "currency_code": row.currency_code,
                "created_local": _local(tz_name, row.created_at),
                "total_text": format_amount(total),
                "payment_state": state,
                "paid_text": None if balance is None else format_amount(balance.paid),
                "outstanding_text": None if balance is None else format_amount(balance.outstanding),
                "notes": notes,
                "group_public_id": row.group_public_id,
                "enrollment_public_id": row.enrollment_public_id,
                "assignment_public_id": row.assignment_public_id,
            }
        )
    return view


def build_record_payments_view(rows, tz_name="UTC"):
    """The record's movements, oldest first. Public ids and labels only."""
    return [
        {
            "public_id": row.public_id,
            "recorded_local": _local(tz_name, row.recorded_at),
            "kind_label": KIND_LABELS.get(row.kind, row.kind),
            "method_label": METHOD_LABELS.get(row.method, row.method),
            "status": row.status,
            "status_label": PAYMENT_STATUS_LABELS.get(row.status, row.status),
            "is_reversal": row.kind == PaymentTransactionKind.REVERSAL.value,
            "is_online": row.method == PaymentMethod.ONLINE.value,
            "amount_text": format_amount(row.amount),
            "currency_code": row.currency_code,
            "invoice_number": row.invoice_number,
            "group_name": row.group_name,
            "receipt_number": row.receipt_number,
            "receipt_public_id": row.receipt_public_id,
            "receipt_status_label": None
            if row.receipt_status is None
            else RECEIPT_STATUS_LABELS.get(row.receipt_status, row.receipt_status),
            "receipt_is_void": row.receipt_status == ReceiptStatus.VOIDED.value,
            "invoice_public_id": row.invoice_public_id,
            "group_public_id": row.group_public_id,
            "enrollment_public_id": row.enrollment_public_id,
            "assignment_public_id": row.assignment_public_id,
        }
        for row in rows
    ]


def record_status(invoice_rows, facts, currency):
    """The Financial Record's computed status over **all** of the Student's
    live invoices (the record's own rows are capped; the status is not)."""
    code, has_open, has_due, outstanding = financial_status(invoice_rows, facts)
    return _status_view(code, has_open, has_due, outstanding, currency)


def student_status_facts(student_id):
    """``(invoice rows, facts)`` for the status of one Student: every live
    draft or issued invoice and its facts. Three queries."""
    by_student, facts = open_invoice_facts([student_id])
    return by_student.get(student_id, []), facts


def record_invoice_facts(invoice_rows):
    """The register facts of the record's own invoice rows. Two queries."""
    ids = [row.id for row in invoice_rows]
    return money_facts(lambda column: column.in_(ids)) if ids else ({}, {})
