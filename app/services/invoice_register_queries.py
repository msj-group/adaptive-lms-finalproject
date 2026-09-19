"""Read queries and presentation for the Administrator Invoice Register
(Phase 5 / M09R).

Flask-independent: explicit queries and plain presentation dicts, and no
``request``, ``abort``, ``url_for`` or template. The route in
``app/blueprints/admin/invoice_register.py`` builds each row's links from the
public ids read here.

**A lookup, never a writer.** Nothing here adds, changes, flushes, locks or
commits a row. The register lists existing invoices across every Student and
links each to its existing invoice page -- and an issued invoice to its
payments and receipts page -- which stay authoritative for everything.

**Every row nests.** One join reads each invoice with its fee assignment,
Enrollment, Group, Course, Student and fee plan, so every link is built from
verified public ids of the same row; nothing the client sends becomes part of
a link.

**Amounts are the payments page's.** The total is the invoice's active lines,
added in Python ``Decimal``. For an issued invoice the paid and outstanding
amounts are M05's ``payment_balance`` over the invoice's confirmed movements,
shown only when the payments page would also call the invoice payable: its
lines are a valid set within ``MAX_INVOICE_ITEM_ROWS`` rows, its transactions
are within ``MAX_INVOICE_PAYMENT_ROWS``, and the balance is valid. Otherwise
the balance is "unavailable", never a misleading figure. Nothing is summed in
SQL. A payment reduces the invoice balance; it is never allocated to an item.

**Nothing sensitive is read.** No bank-transfer reference or date, rejection
or void reason, provider reference, idempotency key, event id, digest, audit
snapshot, receipt document or internal id reaches a row.

**Bounded, free of N+1.** A page is :data:`PAGE_SIZE` invoices, newest first,
with an exact ``COUNT``; its lines, transactions, active intents and
reconciliation flags come from one keyed query each. The payment-state
filter must classify before paging, so it reads the lines and transactions of
every issued invoice matching the other filters -- still a fixed number of
queries, through a subquery rather than a list of ids.
"""

from collections import defaultdict

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import (
    ACTIVE_PAYMENT_INTENT_STATUSES,
    MAX_INVOICE_ITEM_ROWS,
    Course,
    Enrollment,
    FeePlan,
    Group,
    Invoice,
    InvoiceItem,
    InvoiceItemStatus,
    InvoiceStatus,
    PaymentIntent,
    PaymentProviderEvent,
    PaymentTransaction,
    PaymentTransactionStatus,
    ProviderEventOutcome,
    StudentFeeAssignment,
    User,
)
from app.services.invoice_queries import STATUS_LABELS
from app.services.invoice_transactions import invoice_items_valid
from app.services.money import format_amount, sum_amounts
from app.services.payment_transactions import payment_balance, payment_rows_over_bound
from app.services.search_terms import escape_like

_DRAFT = InvoiceStatus.DRAFT.value
_ISSUED = InvoiceStatus.ISSUED.value
_CANCELLED = InvoiceStatus.CANCELLED.value
_ITEM_ACTIVE = InvoiceItemStatus.ACTIVE.value
_PENDING = PaymentTransactionStatus.PENDING.value
_RECONCILIATION = ProviderEventOutcome.RECONCILIATION_REQUIRED.value

#: Invoices per page.
PAGE_SIZE = 25
#: The longest search text read; anything longer is cut, never an error.
MAX_SEARCH_LENGTH = 100

ALL = "all"
OUTSTANDING = "outstanding"
PAID = "paid"
UNAVAILABLE = "unavailable"

#: The lifecycle filter's values, in the order the page offers them.
STATUS_FILTERS = {ALL: "All statuses", _DRAFT: "Draft", _ISSUED: "Issued",
                  _CANCELLED: "Cancelled"}
#: The payment-state filter's values; it applies to issued invoices only.
PAYMENT_FILTERS = {ALL: "Any payment state", OUTSTANDING: "Outstanding", PAID: "Paid"}

ALLOCATION_NOTE = (
    "Payments reduce an invoice's balance and are not allocated to a particular fee item."
)


def normalize_filters(raw_status, raw_payment, raw_search):
    """``(status, payment, search)``: an unknown status or payment state is
    ``all``, exactly as the other Administrator lists drop an unknown filter.
    A payment state describes issued invoices only, so with a ``draft`` or
    ``cancelled`` status it is ``all``. The search has its whitespace collapsed
    and is cut to :data:`MAX_SEARCH_LENGTH`."""
    status = (raw_status or "").strip()
    status = status if status in STATUS_FILTERS else ALL
    payment = (raw_payment or "").strip()
    payment = payment if payment in PAYMENT_FILTERS else ALL
    if status in (_DRAFT, _CANCELLED):
        payment = ALL
    search = " ".join((raw_search or "").split())[:MAX_SEARCH_LENGTH].strip()
    return status, payment, search


def _register_query(status, search):
    """Invoices joined to everything a row shows, filtered by lifecycle and
    search, unordered."""
    student = aliased(User)
    query = (
        db.session.query(
            Invoice.id,
            Invoice.public_id,
            Invoice.status,
            Invoice.invoice_number,
            Invoice.currency_code,
            StudentFeeAssignment.public_id.label("assignment_public_id"),
            Enrollment.public_id.label("enrollment_public_id"),
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            Course.title.label("course_title"),
            student.full_name.label("student_name"),
            FeePlan.name.label("plan_name"),
        )
        .select_from(Invoice)
        .join(StudentFeeAssignment, StudentFeeAssignment.id == Invoice.student_fee_assignment_id)
        .join(Enrollment, Enrollment.id == StudentFeeAssignment.enrollment_id)
        .join(Group, Group.id == Enrollment.group_id)
        .join(Course, Course.id == Group.course_id)
        .join(student, student.id == Enrollment.student_id)
        .join(FeePlan, FeePlan.id == StudentFeeAssignment.fee_plan_id)
    )
    if status != ALL:
        query = query.filter(Invoice.status == status)
    if search:
        pattern = f"%{escape_like(search)}%"
        query = query.filter(
            or_(
                Invoice.invoice_number.ilike(pattern, escape="\\"),
                student.full_name.ilike(pattern, escape="\\"),
                student.email.ilike(pattern, escape="\\"),
                Group.name.ilike(pattern, escape="\\"),
                Group.code.ilike(pattern, escape="\\"),
            )
        )
    return query


def _money_facts(scope):
    """``({invoice_id: [line rows]}, {invoice_id: [transaction rows]})`` for
    the invoices `scope` selects -- a criterion on ``Invoice.id``. Two queries.
    Only the columns the balance and the line validity read are selected."""
    lines, movements = defaultdict(list), defaultdict(list)
    for row in (
        db.session.query(
            InvoiceItem.invoice_id,
            InvoiceItem.kind,
            InvoiceItem.label,
            InvoiceItem.amount,
            InvoiceItem.status,
        )
        .filter(scope(InvoiceItem.invoice_id))
        .order_by(InvoiceItem.invoice_id.asc(), InvoiceItem.id.asc())
        .all()
    ):
        lines[row.invoice_id].append(row)
    for row in (
        db.session.query(
            PaymentTransaction.invoice_id,
            PaymentTransaction.kind,
            PaymentTransaction.status,
            PaymentTransaction.amount,
        )
        .filter(scope(PaymentTransaction.invoice_id))
        .order_by(PaymentTransaction.invoice_id.asc(), PaymentTransaction.id.asc())
        .all()
    ):
        movements[row.invoice_id].append(row)
    return lines, movements


def invoice_money(status, lines, movements):
    """``(total, balance, state)`` of one invoice from its rows: the exact
    total of its active lines and, for an issued invoice, M05's balance and
    its payment state -- ``outstanding``, ``paid`` or ``unavailable`` when the
    payments page would not call it payable. Draft and cancelled invoices have
    no balance and no state."""
    active = [line for line in lines if line.status == _ITEM_ACTIVE]
    total = sum_amounts([line.amount for line in active])
    if status != _ISSUED:
        return total, None, None
    balance = (
        None
        if len(lines) > MAX_INVOICE_ITEM_ROWS
        or not invoice_items_valid(active)
        or payment_rows_over_bound(movements)
        else payment_balance(active, movements)
    )
    if balance is None:
        return total, None, UNAVAILABLE
    return total, balance, OUTSTANDING if balance.outstanding > 0 else PAID


def register_page(status, payment, search, page):
    """``(rows, facts, total, page)``: one page of invoices matching the
    filters, newest first (``id DESC``); the lines and transactions of the
    page's invoices; the exact number matching; and the page shown (page 1
    when `page` is past the end)."""
    query = _register_query(status, search)
    if payment != ALL:
        # The payment state is a computed balance, so it is decided for every
        # matching issued invoice before paging; none is dropped.
        issued = query.filter(Invoice.status == _ISSUED)
        matching = issued.with_entities(Invoice.id).subquery()
        lines, movements = _money_facts(lambda column: column.in_(select(matching.c.id)))
        ids = [
            row.id
            for row in issued.with_entities(Invoice.id, Invoice.status)
            .order_by(Invoice.id.desc())
            .all()
            if invoice_money(row.status, lines[row.id], movements[row.id])[2] == payment
        ]
        total = len(ids)
        page = page if (page - 1) * PAGE_SIZE < total else 1
        shown = ids[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        rows = (
            query.filter(Invoice.id.in_(shown)).order_by(Invoice.id.desc()).all()
            if shown
            else []
        )
        return rows, (lines, movements), total, page
    total = query.order_by(None).with_entities(func.count(Invoice.id)).scalar()
    page = page if (page - 1) * PAGE_SIZE < total else 1
    rows = query.order_by(Invoice.id.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    ids = [row.id for row in rows]
    facts = _money_facts(lambda column: column.in_(ids)) if ids else ({}, {})
    return rows, facts, total, page


def attention(invoice_ids):
    """``({invoice_id with an active intent}, {invoice_id awaiting
    reconciliation})`` for one page. One query each; no identifier is read."""
    ids = list(invoice_ids)
    if not ids:
        return set(), set()
    active = {
        row[0]
        for row in db.session.query(PaymentIntent.invoice_id)
        .filter(
            PaymentIntent.invoice_id.in_(ids),
            PaymentIntent.status.in_(ACTIVE_PAYMENT_INTENT_STATUSES),
        )
        .distinct()
        .all()
    }
    reconciliation = {
        row[0]
        for row in db.session.query(PaymentIntent.invoice_id)
        .join(PaymentProviderEvent, PaymentProviderEvent.payment_intent_id == PaymentIntent.id)
        .filter(
            PaymentIntent.invoice_id.in_(ids),
            PaymentProviderEvent.outcome == _RECONCILIATION,
        )
        .distinct()
        .all()
    }
    return active, reconciliation


def build_register_view(rows, facts, active_intents, reconciliation):
    """The page's rows. Public ids, labels, exact amounts as text and plain
    attention notes only."""
    lines, movements = facts
    view = []
    for row in rows:
        total, balance, state = invoice_money(
            row.status, lines.get(row.id, []), movements.get(row.id, [])
        )
        notes = []
        if row.status == _ISSUED:
            pending = sum(1 for item in movements.get(row.id, []) if item.status == _PENDING)
            if pending:
                notes.append("Bank transfer pending")
            if row.id in active_intents:
                notes.append("Online payment in progress")
            if row.id in reconciliation:
                notes.append("Reconciliation required")
        view.append(
            {
                "public_id": row.public_id,
                "number": row.invoice_number,
                "status": row.status,
                "status_label": STATUS_LABELS.get(row.status, row.status),
                "is_issued": row.status == _ISSUED,
                "student_name": row.student_name,
                "group_name": row.group_name,
                "course_title": row.course_title,
                "plan_name": row.plan_name,
                "currency_code": row.currency_code,
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
