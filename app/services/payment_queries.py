"""Read queries and presentation for Administrator manual payments
(Phase 5 / M05).

Flask-independent: explicit, column-projected queries returning rows or plain
presentation dicts, and no ``request``, ``abort`` or template. Route-level
404 / redirect handling belongs in ``app/blueprints/admin/payments.py``.

**Every read here is reachable only by an active Administrator.** There is no
Student, Teacher, Researcher or public read of a payment or a receipt anywhere
in M05.

**Nesting is part of every lookup.** A transaction is found only inside the
invoice in its URL, which the route has already found inside its assignment,
Enrollment and Group. Anything else is ``None``, which the route turns into a
404 without saying whether the identifier exists elsewhere.

**Internal ids stay inside the service layer.** The ``build_*`` helpers drop
them before anything reaches a template.

**Bounded, and free of N+1.** An invoice's transactions are read once, at most
one past :data:`~app.models.payment_transaction.MAX_INVOICE_PAYMENT_ROWS`; its
receipts and the accounts named on its page come from one keyed query each.
The overview is a fixed page of :data:`PAGE_SIZE` with ``LIMIT PAGE_SIZE + 1``
and no ``COUNT``, every name and receipt joined into its one query.

**Money is added in Python, never in SQL**, through
:func:`~app.services.payment_transactions.payment_balance`. No balance is
stored.
"""

from decimal import Decimal

from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import (
    MAX_INVOICE_PAYMENT_ROWS,
    Enrollment,
    Group,
    Invoice,
    PaymentAuditEventKind,
    PaymentMethod,
    PaymentTransaction,
    PaymentTransactionKind,
    PaymentTransactionStatus,
    Receipt,
    ReceiptStatus,
    StudentFeeAssignment,
    User,
)
from app.services.money import format_amount
from app.services.schedule_occurrences import to_app_local

_COLLECTION = PaymentTransactionKind.COLLECTION.value
_REVERSAL = PaymentTransactionKind.REVERSAL.value
_PENDING = PaymentTransactionStatus.PENDING.value
_CONFIRMED = PaymentTransactionStatus.CONFIRMED.value
_REJECTED = PaymentTransactionStatus.REJECTED.value
_RECEIPT_ISSUED = ReceiptStatus.ISSUED.value

#: The fixed page size of the payments overview.
PAGE_SIZE = 20

_PUBLIC_ID_MAX_LENGTH = 36

KIND_LABELS = {_COLLECTION: "Collection", _REVERSAL: "Reversal"}
METHOD_LABELS = {PaymentMethod.CASH.value: "Cash", PaymentMethod.BANK_TRANSFER.value: "Bank transfer"}
STATUS_LABELS = {_PENDING: "Pending", _CONFIRMED: "Confirmed", _REJECTED: "Rejected"}
RECEIPT_STATUS_LABELS = {_RECEIPT_ISSUED: "Issued", ReceiptStatus.VOIDED.value: "Void"}
EVENT_KIND_LABELS = {
    PaymentAuditEventKind.PAYMENT_CASH_RECORDED.value: "Cash payment recorded",
    PaymentAuditEventKind.PAYMENT_BANK_TRANSFER_RECORDED.value: "Bank transfer recorded",
    PaymentAuditEventKind.PAYMENT_BANK_TRANSFER_CONFIRMED.value: "Bank transfer confirmed",
    PaymentAuditEventKind.PAYMENT_BANK_TRANSFER_REJECTED.value: "Bank transfer rejected",
    PaymentAuditEventKind.PAYMENT_REVERSED.value: "Payment reversed",
    PaymentAuditEventKind.RECEIPT_ISSUED.value: "Receipt issued",
    PaymentAuditEventKind.RECEIPT_VOIDED.value: "Receipt voided",
}

#: The only values the overview filters accept; anything else is dropped.
STATUS_FILTERS = tuple(STATUS_LABELS)
METHOD_FILTERS = tuple(METHOD_LABELS)


def _public_id_ok(value):
    return bool(value) and len(value) <= _PUBLIC_ID_MAX_LENGTH


def _local(tz_name, moment):
    return None if moment is None else to_app_local(tz_name, moment)


def normalize_payment_status_filter(value):
    """One of :data:`STATUS_FILTERS`, or ``None`` for "any status"."""
    value = (value or "").strip()
    return value if value in STATUS_FILTERS else None


def normalize_payment_method_filter(value):
    """One of :data:`METHOD_FILTERS`, or ``None`` for "any method"."""
    value = (value or "").strip()
    return value if value in METHOD_FILTERS else None


# ---------------------------------------------------------------------------
# One invoice's payments
# ---------------------------------------------------------------------------


def invoice_payment_rows(invoice_id):
    """Every transaction of `invoice_id`, ascending internal id, at most one
    past :data:`~app.models.payment_transaction.MAX_INVOICE_PAYMENT_ROWS`. A
    pre-lock read: it decides what a page shows and which rows a write locks,
    never whether a write is allowed."""
    return (
        PaymentTransaction.query.filter(PaymentTransaction.invoice_id == invoice_id)
        .order_by(PaymentTransaction.id.asc())
        .limit(MAX_INVOICE_PAYMENT_ROWS + 1)
        .all()
    )


def invoice_payment(invoice_id, payment_public_id):
    """One transaction by ``public_id`` **inside** `invoice_id`, or ``None``."""
    if not _public_id_ok(payment_public_id):
        return None
    return PaymentTransaction.query.filter(
        PaymentTransaction.invoice_id == invoice_id,
        PaymentTransaction.public_id == payment_public_id,
    ).first()


def receipts_by_payment(payment_ids):
    """``{payment_transaction_id: Receipt}`` for `payment_ids`, from one query."""
    ids = [row_id for row_id in payment_ids if row_id is not None]
    if not ids:
        return {}
    rows = Receipt.query.filter(Receipt.payment_transaction_id.in_(ids)).all()
    return {row.payment_transaction_id: row for row in rows}


def receipt_for_payment(payment_id):
    """The receipt of `payment_id`, or ``None``."""
    return Receipt.query.filter(Receipt.payment_transaction_id == payment_id).first()


def account_names(user_ids):
    """``{user_id: full_name}`` for `user_ids`, from one query."""
    ids = {user_id for user_id in user_ids if user_id is not None}
    if not ids:
        return {}
    return dict(db.session.query(User.id, User.full_name).filter(User.id.in_(ids)).all())


def history_account_ids(rows, receipts):
    """Every account a payment page names."""
    ids = []
    for row in rows:
        ids += [row.recorded_by_id, row.confirmed_by_id, row.rejected_by_id]
    for receipt in receipts.values():
        ids += [receipt.issued_by_id, receipt.voided_by_id]
    return ids


def build_balance_view(balance):
    """Display text of a :class:`~app.services.payment_transactions.PaymentBalance`,
    or ``None`` when the records do not describe one."""
    if balance is None:
        return None
    return {
        "total_text": format_amount(balance.total),
        "paid_text": format_amount(balance.paid),
        "outstanding_text": format_amount(balance.outstanding),
        "is_settled": balance.outstanding == 0,
    }


def _receipt_view(receipt, names, tz_name):
    if receipt is None:
        return None
    return {
        "public_id": receipt.public_id,
        "receipt_number": receipt.receipt_number,
        "status": receipt.status,
        "status_label": RECEIPT_STATUS_LABELS.get(receipt.status, receipt.status),
        "is_issued": receipt.status == _RECEIPT_ISSUED,
        "voided_local": _local(tz_name, receipt.voided_at),
        "voided_by_name": names.get(receipt.voided_by_id),
        "void_reason": receipt.void_reason,
    }


def build_payment_history_view(rows, receipts, names, tz_name="UTC"):
    """Presentation dicts for one invoice's transactions, newest first. No
    internal id survives.

    A collection shows whether it has been reversed; a reversal names the
    collection it reverses and the reason recorded on that collection's voided
    receipt.
    """
    by_id = {row.id: row for row in rows}
    reversed_ids = {
        row.reversal_of_payment_transaction_id for row in rows if row.kind == _REVERSAL
    }
    view = []
    for row in sorted(rows, key=lambda item: item.id, reverse=True):
        original = by_id.get(row.reversal_of_payment_transaction_id)
        original_receipt = receipts.get(row.reversal_of_payment_transaction_id)
        view.append(
            {
                "public_id": row.public_id,
                "kind": row.kind,
                "kind_label": KIND_LABELS.get(row.kind, row.kind),
                "method_label": METHOD_LABELS.get(row.method, row.method),
                "status": row.status,
                "status_label": STATUS_LABELS.get(row.status, row.status),
                "amount_text": format_amount(row.amount),
                "is_pending": row.status == _PENDING,
                "is_confirmed_collection": row.kind == _COLLECTION and row.status == _CONFIRMED,
                "is_reversed": row.id in reversed_ids,
                "bank_transfer_reference": row.bank_transfer_reference,
                "bank_transfer_date": row.bank_transfer_date,
                "recorded_local": _local(tz_name, row.recorded_at),
                "recorded_by_name": names.get(row.recorded_by_id),
                "confirmed_local": _local(tz_name, row.confirmed_at),
                "confirmed_by_name": names.get(row.confirmed_by_id),
                "rejected_local": _local(tz_name, row.rejected_at),
                "rejected_by_name": names.get(row.rejected_by_id),
                "rejection_reason": row.rejection_reason,
                "reverses_recorded_local": None
                if original is None
                else _local(tz_name, original.recorded_at),
                "reversal_reason": None if original_receipt is None else original_receipt.void_reason,
                "receipt": _receipt_view(receipts.get(row.id), names, tz_name),
            }
        )
    return view


def build_payment_detail_view(row, receipt, names, tz_name="UTC"):
    """One transaction, as a confirmation, rejection or reversal page shows
    it. No internal id survives."""
    return build_payment_history_view([row], {row.id: receipt} if receipt else {}, names, tz_name)[0]


# ---------------------------------------------------------------------------
# The payments overview
# ---------------------------------------------------------------------------


def payments_overview_page(page, status=None, method=None):
    """``(rows, has_next)`` for one page of every transaction, newest first
    (``id DESC``). `status` and `method` must already be normalized. One
    query: the invoice chain, the Student, the recording account and any
    receipt are joined in."""
    recorder = aliased(User)
    student = aliased(User)
    query = (
        db.session.query(
            PaymentTransaction.public_id,
            PaymentTransaction.kind,
            PaymentTransaction.method,
            PaymentTransaction.status,
            PaymentTransaction.amount,
            PaymentTransaction.currency_code,
            PaymentTransaction.recorded_at,
            recorder.full_name.label("recorded_by_name"),
            Invoice.public_id.label("invoice_public_id"),
            Invoice.invoice_number,
            StudentFeeAssignment.public_id.label("assignment_public_id"),
            Enrollment.public_id.label("enrollment_public_id"),
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            student.full_name.label("student_name"),
            Receipt.public_id.label("receipt_public_id"),
            Receipt.receipt_number,
            Receipt.status.label("receipt_status"),
        )
        .select_from(PaymentTransaction)
        .join(Invoice, Invoice.id == PaymentTransaction.invoice_id)
        .join(StudentFeeAssignment, StudentFeeAssignment.id == Invoice.student_fee_assignment_id)
        .join(Enrollment, Enrollment.id == StudentFeeAssignment.enrollment_id)
        .join(Group, Group.id == Enrollment.group_id)
        .join(student, student.id == Enrollment.student_id)
        .join(recorder, recorder.id == PaymentTransaction.recorded_by_id)
        .outerjoin(Receipt, Receipt.payment_transaction_id == PaymentTransaction.id)
    )
    if status is not None:
        query = query.filter(PaymentTransaction.status == status)
    if method is not None:
        query = query.filter(PaymentTransaction.method == method)
    rows = (
        query.order_by(PaymentTransaction.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


def build_overview_view(rows, tz_name="UTC"):
    """Presentation dicts for one overview page. Public ids only."""
    return [
        {
            "public_id": row.public_id,
            "kind_label": KIND_LABELS.get(row.kind, row.kind),
            "method_label": METHOD_LABELS.get(row.method, row.method),
            "status": row.status,
            "status_label": STATUS_LABELS.get(row.status, row.status),
            "amount_text": format_amount(row.amount),
            "currency_code": row.currency_code,
            "recorded_local": _local(tz_name, row.recorded_at),
            "recorded_by_name": row.recorded_by_name,
            "invoice_public_id": row.invoice_public_id,
            "invoice_number": row.invoice_number,
            "assignment_public_id": row.assignment_public_id,
            "enrollment_public_id": row.enrollment_public_id,
            "group_public_id": row.group_public_id,
            "group_name": row.group_name,
            "student_name": row.student_name,
            "receipt_public_id": row.receipt_public_id,
            "receipt_number": row.receipt_number,
            "receipt_status_label": None
            if row.receipt_status is None
            else RECEIPT_STATUS_LABELS.get(row.receipt_status, row.receipt_status),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Payment events on the invoice timeline
# ---------------------------------------------------------------------------


def _amount_text(text):
    return format_amount(Decimal(text))


def describe_payment_event(kind, before, after):
    """Plain sentences saying what one payment or receipt event changed,
    computed from its two server-built snapshots."""
    currency = after["currency_code"]
    payment = after["payment"]
    changes = []
    if payment is not None:
        amount = f"{_amount_text(payment['amount'])} {currency}"
        method = METHOD_LABELS.get(payment["method"], payment["method"]).lower()
        if kind == PaymentAuditEventKind.PAYMENT_CASH_RECORDED.value:
            changes.append(f"Cash payment of {amount} recorded and confirmed.")
        elif kind == PaymentAuditEventKind.PAYMENT_BANK_TRANSFER_RECORDED.value:
            changes.append(f"Bank transfer of {amount} recorded as pending.")
        elif kind == PaymentAuditEventKind.PAYMENT_BANK_TRANSFER_CONFIRMED.value:
            changes.append(f"Bank transfer of {amount} confirmed.")
        elif kind == PaymentAuditEventKind.PAYMENT_BANK_TRANSFER_REJECTED.value:
            changes.append(f"Bank transfer of {amount} rejected.")
        elif kind == PaymentAuditEventKind.PAYMENT_REVERSED.value:
            changes.append(f"The {method} payment of {amount} was reversed in full.")
    receipt, earlier = after["receipt"], before["receipt"]
    if receipt is not None and (earlier is None or earlier["status"] != receipt["status"]):
        label = RECEIPT_STATUS_LABELS.get(receipt["status"], receipt["status"]).lower()
        changes.append(
            f"Receipt {receipt['receipt_number']} issued."
            if earlier is None
            else f"Receipt {receipt['receipt_number']} is now {label}."
        )
    if before["paid_amount"] != after["paid_amount"]:
        changes.append(
            f"Paid changed from {_amount_text(before['paid_amount'])} to "
            f"{_amount_text(after['paid_amount'])} {currency}; outstanding is now "
            f"{_amount_text(after['outstanding_amount'])} {currency}."
        )
    return changes


def build_payment_event_state(after):
    """The recorded balance after one payment or receipt event."""
    return {
        "total_text": _amount_text(after["invoice_total"]),
        "paid_text": _amount_text(after["paid_amount"]),
        "outstanding_text": _amount_text(after["outstanding_amount"]),
    }
