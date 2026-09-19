"""Read queries and presentation for Deleted Records (Phase 5 / M10).

Flask-independent. **Read-only**: nothing here adds, changes, flushes, locks
or commits a row, and there is no restore.

A deleted invoice, payment transaction or receipt is a tombstone: its row is
kept with ``deleted_at``, ``deleted_by_id`` and ``deletion_reason``, and it
counts for nothing in any live list, balance or report. This page lists them,
newest deletion first, and shows each one's safe identifying facts: its type,
number or label, Student, related invoice, deletion moment, the deleting
Administrator and the reason.

**Nothing sensitive is read.** No bank-transfer reference or date, rejection
or void reason, provider reference, intent, webhook data, idempotency key,
audit snapshot, receipt document field beyond its number, or internal id
reaches a template.

**Bounded, free of N+1.** The list is one ``UNION ALL`` counted once and paged
once (:data:`PAGE_SIZE`), then one keyed query per document type on the page.
A tombstone's detail is a fixed number of queries, each capped.
"""

from sqlalchemy import String, func, literal, or_, select, union_all
from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import (
    Course,
    Enrollment,
    FeePlan,
    Group,
    Invoice,
    InvoiceItem,
    InvoiceItemStatus,
    PaymentAuditEvent,
    PaymentAuditEventKind,
    PaymentTransaction,
    Receipt,
    StudentFeeAssignment,
    User,
)
from app.services.invoice_queries import EVENT_KIND_LABELS as INVOICE_EVENT_LABELS
from app.services.invoice_queries import STATUS_LABELS as INVOICE_STATUS_LABELS
from app.services.money import format_amount, sum_amounts
from app.services.payment_queries import EVENT_KIND_LABELS as PAYMENT_EVENT_LABELS
from app.services.payment_queries import (
    KIND_LABELS,
    METHOD_LABELS,
    RECEIPT_STATUS_LABELS,
)
from app.services.payment_queries import STATUS_LABELS as PAYMENT_STATUS_LABELS
from app.services.schedule_occurrences import to_app_local
from app.services.search_terms import escape_like

INVOICE = "invoice"
PAYMENT = "payment"
RECEIPT = "receipt"

#: The document-type filter, in the order the page offers it.
TYPE_FILTERS = {"all": "All documents", INVOICE: "Invoices", PAYMENT: "Payments",
                RECEIPT: "Receipts"}
TYPE_LABELS = {INVOICE: "Invoice", PAYMENT: "Payment", RECEIPT: "Receipt"}

#: Deleted records per page.
PAGE_SIZE = 25
MAX_SEARCH_LENGTH = 100
#: The most lines, transactions and events one tombstone page shows.
DETAIL_CAP = 200

_ITEM_ACTIVE = InvoiceItemStatus.ACTIVE.value
_REPLACED = PaymentAuditEventKind.PAYMENT_REPLACED.value
_EVENT_LABELS = {**INVOICE_EVENT_LABELS, **PAYMENT_EVENT_LABELS}


def _local(tz_name, moment):
    return None if moment is None else to_app_local(tz_name, moment)


def normalize_filters(raw_type, raw_search):
    """``(type, search)``: an unknown type is ``all``; the search has its
    whitespace collapsed and is cut to :data:`MAX_SEARCH_LENGTH`."""
    kind = (raw_type or "").strip()
    kind = kind if kind in TYPE_FILTERS else "all"
    search = " ".join((raw_search or "").split())[:MAX_SEARCH_LENGTH].strip()
    return kind, search


def _invoice_chain(query, student):
    return (
        query.join(StudentFeeAssignment, StudentFeeAssignment.id == Invoice.student_fee_assignment_id)
        .join(Enrollment, Enrollment.id == StudentFeeAssignment.enrollment_id)
        .join(student, student.id == Enrollment.student_id)
    )


def _searched(query, search, student, *columns):
    if not search:
        return query
    pattern = f"%{escape_like(search)}%"
    return query.where(
        or_(
            student.full_name.ilike(pattern, escape="\\"),
            student.email.ilike(pattern, escape="\\"),
            *(column.ilike(pattern, escape="\\") for column in columns),
        )
    )


def _selects(kind, search):
    """One ``SELECT type, id, deleted_at`` per document type the filter
    keeps."""
    selects = []
    if kind in ("all", INVOICE):
        student = aliased(User)
        query = _invoice_chain(
            select(
                literal(INVOICE, String()).label("doc_type"),
                Invoice.id.label("row_id"),
                Invoice.deleted_at.label("deleted_at"),
            ).select_from(Invoice),
            student,
        ).where(Invoice.deleted_at.isnot(None))
        selects.append(_searched(query, search, student, Invoice.invoice_number))
    if kind in ("all", PAYMENT):
        student = aliased(User)
        query = _invoice_chain(
            select(
                literal(PAYMENT, String()).label("doc_type"),
                PaymentTransaction.id.label("row_id"),
                PaymentTransaction.deleted_at.label("deleted_at"),
            )
            .select_from(PaymentTransaction)
            .join(Invoice, Invoice.id == PaymentTransaction.invoice_id),
            student,
        ).where(PaymentTransaction.deleted_at.isnot(None))
        selects.append(_searched(query, search, student, Invoice.invoice_number))
    if kind in ("all", RECEIPT):
        student = aliased(User)
        query = _invoice_chain(
            select(
                literal(RECEIPT, String()).label("doc_type"),
                Receipt.id.label("row_id"),
                Receipt.deleted_at.label("deleted_at"),
            )
            .select_from(Receipt)
            .join(PaymentTransaction, PaymentTransaction.id == Receipt.payment_transaction_id)
            .join(Invoice, Invoice.id == PaymentTransaction.invoice_id),
            student,
        ).where(Receipt.deleted_at.isnot(None))
        selects.append(
            _searched(query, search, student, Invoice.invoice_number, Receipt.receipt_number)
        )
    return selects


def deleted_page(kind, search, page):
    """``(entries, total, page)``: one page of deleted documents, newest
    deletion first, with the exact number matching. Five queries at most."""
    combined = union_all(*_selects(kind, search)).subquery()
    total = db.session.execute(select(func.count()).select_from(combined)).scalar()
    page = page if (page - 1) * PAGE_SIZE < total else 1
    keys = db.session.execute(
        select(combined.c.doc_type, combined.c.row_id)
        .order_by(combined.c.deleted_at.desc(), combined.c.doc_type.asc(), combined.c.row_id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    ids = {INVOICE: [], PAYMENT: [], RECEIPT: []}
    for doc_type, row_id in keys:
        ids[doc_type].append(row_id)
    details = {
        INVOICE: {row.id: row for row in _invoice_rows(ids[INVOICE])},
        PAYMENT: {row.id: row for row in _payment_rows(ids[PAYMENT])},
        RECEIPT: {row.id: row for row in _receipt_rows(ids[RECEIPT])},
    }
    return [(doc_type, details[doc_type][row_id]) for doc_type, row_id in keys], total, page


def _student_columns(student, deleter):
    return (
        student.full_name.label("student_name"),
        student.email.label("student_email"),
        deleter.full_name.label("deleted_by_name"),
    )


def _invoice_rows(ids, public_id=None):
    student, deleter = aliased(User), aliased(User)
    query = _invoice_chain(
        db.session.query(
            Invoice.id,
            Invoice.public_id,
            Invoice.status,
            Invoice.invoice_number,
            Invoice.currency_code,
            Invoice.created_at,
            Invoice.issued_at,
            Invoice.deleted_at,
            Invoice.deletion_reason,
            Invoice.invoice_number.label("related_invoice_number"),
            Group.name.label("group_name"),
            Course.title.label("course_title"),
            FeePlan.name.label("plan_name"),
            *_student_columns(student, deleter),
        ).select_from(Invoice),
        student,
    )
    query = (
        query.join(Group, Group.id == Enrollment.group_id)
        .join(Course, Course.id == Group.course_id)
        .join(FeePlan, FeePlan.id == StudentFeeAssignment.fee_plan_id)
        .join(deleter, deleter.id == Invoice.deleted_by_id)
        .filter(Invoice.deleted_at.isnot(None))
    )
    if public_id is not None:
        return query.filter(Invoice.public_id == public_id).first()
    return query.filter(Invoice.id.in_(ids)).all() if ids else []


def _payment_rows(ids, public_id=None, invoice_id=None):
    student, deleter = aliased(User), aliased(User)
    query = _invoice_chain(
        db.session.query(
            PaymentTransaction.id,
            PaymentTransaction.public_id,
            PaymentTransaction.kind,
            PaymentTransaction.method,
            PaymentTransaction.status,
            PaymentTransaction.amount,
            PaymentTransaction.currency_code,
            PaymentTransaction.recorded_at,
            PaymentTransaction.deleted_at,
            PaymentTransaction.deletion_reason,
            Invoice.id.label("invoice_id"),
            Invoice.public_id.label("invoice_public_id"),
            Invoice.invoice_number.label("related_invoice_number"),
            Invoice.deleted_at.label("invoice_deleted_at"),
            Group.name.label("group_name"),
            *_student_columns(student, deleter),
        )
        .select_from(PaymentTransaction)
        .join(Invoice, Invoice.id == PaymentTransaction.invoice_id),
        student,
    )
    query = (
        query.join(Group, Group.id == Enrollment.group_id)
        .join(deleter, deleter.id == PaymentTransaction.deleted_by_id)
        .filter(PaymentTransaction.deleted_at.isnot(None))
    )
    if public_id is not None:
        return query.filter(PaymentTransaction.public_id == public_id).first()
    if invoice_id is not None:
        return (
            query.filter(PaymentTransaction.invoice_id == invoice_id)
            .order_by(PaymentTransaction.id.asc())
            .limit(DETAIL_CAP)
            .all()
        )
    return query.filter(PaymentTransaction.id.in_(ids)).all() if ids else []


def _receipt_rows(ids, public_id=None, payment_ids=None):
    student, deleter = aliased(User), aliased(User)
    query = _invoice_chain(
        db.session.query(
            Receipt.id,
            Receipt.public_id,
            Receipt.receipt_number,
            Receipt.status,
            Receipt.issued_at,
            Receipt.deleted_at,
            Receipt.deletion_reason,
            Receipt.payment_transaction_id,
            PaymentTransaction.public_id.label("payment_public_id"),
            PaymentTransaction.method,
            PaymentTransaction.amount,
            PaymentTransaction.currency_code,
            PaymentTransaction.deleted_at.label("payment_deleted_at"),
            Invoice.public_id.label("invoice_public_id"),
            Invoice.invoice_number.label("related_invoice_number"),
            Group.name.label("group_name"),
            *_student_columns(student, deleter),
        )
        .select_from(Receipt)
        .join(PaymentTransaction, PaymentTransaction.id == Receipt.payment_transaction_id)
        .join(Invoice, Invoice.id == PaymentTransaction.invoice_id),
        student,
    )
    query = (
        query.join(Group, Group.id == Enrollment.group_id)
        .join(deleter, deleter.id == Receipt.deleted_by_id)
        .filter(Receipt.deleted_at.isnot(None))
    )
    if public_id is not None:
        return query.filter(Receipt.public_id == public_id).first()
    if payment_ids is not None:
        return (
            query.filter(Receipt.payment_transaction_id.in_(payment_ids)).all()
            if payment_ids
            else []
        )
    return query.filter(Receipt.id.in_(ids)).all() if ids else []


def _label(doc_type, row):
    if doc_type == INVOICE:
        return row.invoice_number or "Draft invoice"
    if doc_type == RECEIPT:
        return row.receipt_number
    return (
        f"{METHOD_LABELS.get(row.method, row.method)} {KIND_LABELS.get(row.kind, row.kind).lower()}"
        f" of {format_amount(row.amount)} {row.currency_code}"
    )


def _entry(doc_type, row, tz_name):
    return {
        "doc_type": doc_type,
        "type_label": TYPE_LABELS[doc_type],
        "public_id": row.public_id,
        "label": _label(doc_type, row),
        "student_name": row.student_name,
        "student_email": row.student_email,
        "invoice_number": row.related_invoice_number,
        "group_name": row.group_name,
        "deleted_local": _local(tz_name, row.deleted_at),
        "deleted_by_name": row.deleted_by_name,
        "deletion_reason": row.deletion_reason,
    }


def build_deleted_view(entries, tz_name="UTC"):
    """The list's rows: safe identifying facts only."""
    return [_entry(doc_type, row, tz_name) for doc_type, row in entries]


# ---------------------------------------------------------------------------
# One tombstone
# ---------------------------------------------------------------------------


def _events(invoice_id, tz_name, payment_id=None, receipt_id=None):
    """The document's audit events -- kind, moment, actor and reason only;
    never a snapshot -- oldest first, capped."""
    actor = aliased(User)
    query = (
        db.session.query(
            PaymentAuditEvent.kind,
            PaymentAuditEvent.occurred_at,
            PaymentAuditEvent.reason,
            actor.full_name.label("actor_name"),
        )
        .select_from(PaymentAuditEvent)
        .outerjoin(actor, actor.id == PaymentAuditEvent.actor_id)
        .filter(PaymentAuditEvent.invoice_id == invoice_id)
    )
    if payment_id is not None:
        query = query.filter(PaymentAuditEvent.payment_transaction_id == payment_id)
    if receipt_id is not None:
        query = query.filter(PaymentAuditEvent.receipt_id == receipt_id)
    rows = query.order_by(PaymentAuditEvent.id.asc()).limit(DETAIL_CAP).all()
    return [
        {
            "kind_label": _EVENT_LABELS.get(row.kind, row.kind),
            "occurred_local": _local(tz_name, row.occurred_at),
            "actor_name": row.actor_name or "The verified provider webhook",
            "reason": row.reason,
        }
        for row in rows
    ]


def _payment_view(row, receipt, tz_name):
    return {
        **_entry(PAYMENT, row, tz_name),
        "kind_label": KIND_LABELS.get(row.kind, row.kind),
        "method_label": METHOD_LABELS.get(row.method, row.method),
        "status_label": PAYMENT_STATUS_LABELS.get(row.status, row.status),
        "amount_text": format_amount(row.amount),
        "currency_code": row.currency_code,
        "recorded_local": _local(tz_name, row.recorded_at),
        "receipt_number": None if receipt is None else receipt.receipt_number,
        "receipt_public_id": None if receipt is None else receipt.public_id,
    }


def deleted_invoice_detail(public_id, tz_name="UTC"):
    """A deleted invoice's tombstone: its facts, its lines as they were, the
    transactions and receipts deleted with it, and its event trail -- or
    ``None``."""
    row = _invoice_rows(None, public_id=public_id)
    if row is None:
        return None
    lines = (
        InvoiceItem.query.filter(InvoiceItem.invoice_id == row.id)
        .order_by(InvoiceItem.id.asc())
        .limit(DETAIL_CAP)
        .all()
    )
    active = [line for line in lines if line.status == _ITEM_ACTIVE]
    payments = _payment_rows(None, invoice_id=row.id)
    receipts = {
        receipt.payment_transaction_id: receipt
        for receipt in _receipt_rows(None, payment_ids=[payment.id for payment in payments])
    }
    return {
        **_entry(INVOICE, row, tz_name),
        "status_label": INVOICE_STATUS_LABELS.get(row.status, row.status),
        "course_title": row.course_title,
        "plan_name": row.plan_name,
        "currency_code": row.currency_code,
        "created_local": _local(tz_name, row.created_at),
        "issued_local": _local(tz_name, row.issued_at),
        "lines": [
            {"label": line.label, "amount_text": format_amount(line.amount)} for line in active
        ],
        "total_text": format_amount(sum_amounts([line.amount for line in active])),
        "payments": [
            _payment_view(payment, receipts.get(payment.id), tz_name) for payment in payments
        ],
        "events": _events(row.id, tz_name),
    }


def replacement_of(payment_id):
    """The live collection an edit recorded in place of `payment_id`, as
    ``(public_id, amount, recorded_at)``, or ``None``. Read from the
    ``payment_replaced`` event's own record of the replacement's public id;
    nothing else of the event is shown."""
    event = (
        db.session.query(PaymentAuditEvent.after_snapshot)
        .filter(
            PaymentAuditEvent.payment_transaction_id == payment_id,
            PaymentAuditEvent.kind == _REPLACED,
        )
        .first()
    )
    if event is None:
        return None
    replaced_by = (event.after_snapshot.get("payment") or {}).get("replaced_by_public_id")
    if not replaced_by:
        return None
    return (
        db.session.query(
            PaymentTransaction.public_id, PaymentTransaction.amount, PaymentTransaction.recorded_at
        )
        .filter(
            PaymentTransaction.public_id == replaced_by, PaymentTransaction.deleted_at.is_(None)
        )
        .first()
    )


def deleted_payment_detail(public_id, tz_name="UTC"):
    """A deleted transaction's tombstone, or ``None``."""
    row = _payment_rows(None, public_id=public_id)
    if row is None:
        return None
    receipt = next(iter(_receipt_rows(None, payment_ids=[row.id])), None)
    replacement = replacement_of(row.id)
    return {
        **_payment_view(row, receipt, tz_name),
        "invoice_public_id": row.invoice_public_id,
        "invoice_deleted": row.invoice_deleted_at is not None,
        "replacement": None
        if replacement is None
        else {
            "amount_text": format_amount(replacement.amount),
            "recorded_local": _local(tz_name, replacement.recorded_at),
        },
        "events": _events(row.invoice_id, tz_name, payment_id=row.id),
    }


def deleted_receipt_detail(public_id, tz_name="UTC"):
    """A deleted receipt's tombstone, or ``None``."""
    row = _receipt_rows(None, public_id=public_id)
    if row is None:
        return None
    invoice_id = (
        db.session.query(PaymentTransaction.invoice_id)
        .filter(PaymentTransaction.id == row.payment_transaction_id)
        .scalar()
    )
    return {
        **_entry(RECEIPT, row, tz_name),
        "receipt_number": row.receipt_number,
        "status_label": RECEIPT_STATUS_LABELS.get(row.status, row.status),
        "issued_local": _local(tz_name, row.issued_at),
        "method_label": METHOD_LABELS.get(row.method, row.method),
        "amount_text": format_amount(row.amount),
        "currency_code": row.currency_code,
        "payment_public_id": row.payment_public_id,
        "invoice_public_id": row.invoice_public_id,
        "events": _events(invoice_id, tz_name, receipt_id=row.id),
    }
