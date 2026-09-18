"""Server-built payment snapshots, receipt documents and the one payment /
receipt audit-event writer (Phase 5 / M05).

Flask-independent: no ``request``, ``abort``, ``flash`` or template. The
routes in ``app/blueprints/admin/payments.py`` call :func:`record_payment_event`
once per payment and receipt movement, after their locks and before their
commit, so every event and the change it describes commit or roll back
together. The invoice's own events keep their writer,
``app/services/invoice_audit.py``; both write the one
:class:`~app.models.payment_audit_event.PaymentAuditEvent` trail.

**Nothing here is submitted.** :func:`build_payment_snapshot` reads the
invoice, its active lines and its transactions -- rows the request locked or
read after the invoice lock -- and emits the issued invoice's public id,
number, status, exact total, paid and outstanding amounts, and the event's
transaction and receipt. :func:`build_receipt_document` emits the permanent
receipt content. Neither carries an internal id, card or bank data, a transfer
reference, a reason, a token or a session value, and both are proved by the
model validators before anything is stored.

**Nothing here edits or deletes an event**, and nothing ever will: the
module's only write is an insert.
"""

from decimal import Decimal

from app.extensions import db
from app.models import (
    InvoiceStatus,
    PaymentAuditEvent,
    PaymentAuditEventKind,
    PaymentMethod,
    PaymentTransactionKind,
    PaymentTransactionStatus,
    ReceiptStatus,
    UserRole,
    UserStatus,
)
from app.models.payment_audit_event import (
    PAYMENT_SNAPSHOT_SCHEMA,
    REASON_REQUIRED_KINDS,
    RECEIPT_EVENT_KINDS,
    normalize_audit_reason,
    snapshot_amount_text,
    validate_payment_snapshot,
)
from app.models.receipt import RECEIPT_SNAPSHOT_SCHEMA, receipt_moment_text, validate_receipt_snapshot
from app.services.payment_transactions import payment_balance

_K = PaymentAuditEventKind
CASH_RECORDED = _K.PAYMENT_CASH_RECORDED.value
BANK_RECORDED = _K.PAYMENT_BANK_TRANSFER_RECORDED.value
BANK_CONFIRMED = _K.PAYMENT_BANK_TRANSFER_CONFIRMED.value
BANK_REJECTED = _K.PAYMENT_BANK_TRANSFER_REJECTED.value
REVERSED = _K.PAYMENT_REVERSED.value
RECEIPT_ISSUED = _K.RECEIPT_ISSUED.value
RECEIPT_VOIDED = _K.RECEIPT_VOIDED.value

_ISSUED_INVOICE = InvoiceStatus.ISSUED.value
_ADMINISTRATOR = UserRole.ADMINISTRATOR.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_COLLECTION = PaymentTransactionKind.COLLECTION.value
_REVERSAL = PaymentTransactionKind.REVERSAL.value
_CASH = PaymentMethod.CASH.value
_BANK = PaymentMethod.BANK_TRANSFER.value
_PENDING = PaymentTransactionStatus.PENDING.value
_CONFIRMED = PaymentTransactionStatus.CONFIRMED.value
_REJECTED = PaymentTransactionStatus.REJECTED.value
_RECEIPT_ISSUED = ReceiptStatus.ISSUED.value
_RECEIPT_VOIDED = ReceiptStatus.VOIDED.value

#: For each kind: the transaction's status in the "before" snapshot (``None``
#: when it did not exist yet), its status "after", its kind, its method
#: (``None`` for any), the receipt status before and after (``None`` for
#: none), and the sign of the event's effect on the paid amount.
_SHAPES = {
    CASH_RECORDED: (None, _CONFIRMED, _COLLECTION, _CASH, None, None, 1),
    BANK_RECORDED: (None, _PENDING, _COLLECTION, _BANK, None, None, 0),
    BANK_CONFIRMED: (_PENDING, _CONFIRMED, _COLLECTION, _BANK, None, None, 1),
    BANK_REJECTED: (_PENDING, _REJECTED, _COLLECTION, _BANK, None, None, 0),
    REVERSED: (None, _CONFIRMED, _REVERSAL, None, None, None, -1),
    RECEIPT_ISSUED: (_CONFIRMED, _CONFIRMED, _COLLECTION, None, None, _RECEIPT_ISSUED, 0),
    RECEIPT_VOIDED: (_CONFIRMED, _CONFIRMED, _COLLECTION, None, _RECEIPT_ISSUED, _RECEIPT_VOIDED, 0),
}


def build_payment_snapshot(invoice, active_items, payments, payment=None, receipt=None):
    """The canonical payment snapshot of `invoice` as it now is.

    `active_items` are its active lines; `payments` are **all** of its
    transactions (flush a new one first), which the balance and a reversal's
    link are read from; `payment` and `receipt` are the event's subjects.
    Raises ``ValueError`` when the records do not describe a balance.
    """
    balance = payment_balance(active_items, payments)
    if balance is None:
        raise ValueError("The invoice's payment records do not describe a balance")
    entry = None
    if payment is not None:
        reversal_of = None
        if payment.reversal_of_payment_transaction_id is not None:
            public_ids = {row.id: row.public_id for row in payments}
            reversal_of = public_ids.get(payment.reversal_of_payment_transaction_id)
            if reversal_of is None:
                raise ValueError("A reversal's collection is not among the invoice's payments")
        entry = {
            "public_id": payment.public_id,
            "kind": payment.kind,
            "method": payment.method,
            "status": payment.status,
            "amount": snapshot_amount_text(payment.amount),
            "reversal_of_public_id": reversal_of,
        }
    snapshot = {
        "schema": PAYMENT_SNAPSHOT_SCHEMA,
        "invoice_public_id": invoice.public_id,
        "invoice_number": invoice.invoice_number,
        "invoice_status": invoice.status,
        "currency_code": invoice.currency_code,
        "invoice_total": snapshot_amount_text(balance.total),
        "paid_amount": snapshot_amount_text(balance.paid),
        "outstanding_amount": snapshot_amount_text(balance.outstanding),
        "payment": entry,
        "receipt": None
        if receipt is None
        else {
            "public_id": receipt.public_id,
            "receipt_number": receipt.receipt_number,
            "status": receipt.status,
        },
    }
    return validate_payment_snapshot(snapshot)


def build_receipt_document(
    *,
    receipt_public_id,
    receipt_number,
    payment,
    invoice,
    assignment_public_id,
    confirmed_by_name,
    student_name,
    group_name,
    course_title,
    academic_term_name,
):
    """The canonical permanent content of one confirmed collection's receipt,
    from rows the request locked. Raises ``ValueError`` for anything else."""
    if payment.kind != _COLLECTION or payment.status != _CONFIRMED or payment.confirmed_at is None:
        raise ValueError("Only a confirmed collection receives a receipt")
    document = {
        "schema": RECEIPT_SNAPSHOT_SCHEMA,
        "receipt_public_id": receipt_public_id,
        "receipt_number": receipt_number,
        "payment_public_id": payment.public_id,
        "invoice_public_id": invoice.public_id,
        "invoice_number": invoice.invoice_number,
        "student_fee_assignment_public_id": assignment_public_id,
        "method": payment.method,
        "amount": snapshot_amount_text(payment.amount),
        "currency_code": payment.currency_code,
        "confirmed_at": receipt_moment_text(payment.confirmed_at),
        "confirmed_by_name": confirmed_by_name,
        "student_name": student_name,
        "group_name": group_name,
        "course_title": course_title,
        "academic_term_name": academic_term_name,
    }
    return validate_receipt_snapshot(document)


def _payment_facts(entry):
    return None if entry is None else (entry["public_id"], entry["kind"], entry["method"], entry["amount"])


def _receipt_status(entry):
    return None if entry is None else entry["status"]


def record_payment_event(
    *, invoice, actor, kind, payment, receipt, before_snapshot, after_snapshot, reason, moment
):
    """Add the one :class:`PaymentAuditEvent` for a payment or receipt change
    already applied in this transaction, or raise ``ValueError`` and add
    nothing.

    `actor` is the **locked** acting account, re-proved here as an active
    Administrator. `invoice` is the locked, issued invoice, whose version the
    event records and does not move. `payment` is the event's stored
    transaction of that invoice; `receipt` is the stored receipt of that
    transaction for a receipt event and ``None`` otherwise. The reason must be
    present, normalized text exactly when the kind requires one. Both
    snapshots must describe the invoice, show the transition the kind means --
    including its exact effect on the paid amount -- and the "after" snapshot
    must describe the rows as they now are.
    """
    if actor is None or actor.role != _ADMINISTRATOR or actor.status != _USER_ACTIVE:
        raise ValueError("A payment audit event needs an active acting Administrator")
    if kind not in _SHAPES:
        raise ValueError(f"Unknown payment audit event kind: {kind}")
    if invoice is None or invoice.id is None or invoice.status != _ISSUED_INVOICE:
        raise ValueError("A payment audit event needs a stored, issued invoice")
    if payment is None or payment.id is None or payment.invoice_id != invoice.id:
        raise ValueError("A payment audit event needs a stored transaction of the invoice")
    if kind in RECEIPT_EVENT_KINDS:
        if receipt is None or receipt.id is None or receipt.payment_transaction_id != payment.id:
            raise ValueError("A receipt event needs the stored receipt of its transaction")
    elif receipt is not None:
        raise ValueError("A payment event names no receipt")

    if kind in REASON_REQUIRED_KINDS:
        normalized, error = normalize_audit_reason(reason)
        if error is not None or normalized != reason:
            raise ValueError("This payment change needs a normalized, non-empty reason")
    elif reason is not None:
        raise ValueError("This payment change carries no reason")

    if before_snapshot is None or after_snapshot is None:
        raise ValueError("A payment audit event has both snapshots")
    before = validate_payment_snapshot(before_snapshot)
    after = validate_payment_snapshot(after_snapshot)
    for snapshot in (before, after):
        if (snapshot["invoice_public_id"], snapshot["invoice_number"]) != (
            invoice.public_id,
            invoice.invoice_number,
        ):
            raise ValueError("A payment snapshot does not describe the invoice")
    if before == after:
        raise ValueError("A payment audit event records a change")

    before_status, after_status, payment_kind, method, receipt_before, receipt_after, effect = (
        _SHAPES[kind]
    )
    now = after["payment"]
    if now is None or (now["public_id"], now["kind"], now["method"], now["status"], now["amount"]) != (
        payment.public_id,
        payment.kind,
        payment.method,
        payment.status,
        snapshot_amount_text(payment.amount),
    ):
        raise ValueError("The after snapshot does not describe the transaction as it now is")
    if now["status"] != after_status or now["kind"] != payment_kind or (
        method is not None and now["method"] != method
    ):
        raise ValueError("The transaction is not in the state this event records")
    earlier = before["payment"]
    if before_status is None:
        if earlier is not None:
            raise ValueError("The before snapshot shows a transaction that did not exist yet")
    elif (
        earlier is None
        or earlier["status"] != before_status
        or _payment_facts(earlier) != _payment_facts(now)
    ):
        raise ValueError("The before snapshot does not describe the same transaction")

    if (_receipt_status(before["receipt"]), _receipt_status(after["receipt"])) != (
        receipt_before,
        receipt_after,
    ):
        raise ValueError("The snapshots do not show the receipt change this event records")
    if receipt is not None:
        for entry, expected_status in ((before["receipt"], receipt_before), (after["receipt"], None)):
            if entry is not None and (entry["public_id"], entry["receipt_number"]) != (
                receipt.public_id,
                receipt.receipt_number,
            ):
                raise ValueError("A snapshot names another receipt")
        if after["receipt"]["status"] != receipt.status:
            raise ValueError("The after snapshot does not describe the receipt as it now is")

    if after["invoice_total"] != before["invoice_total"] or Decimal(after["paid_amount"]) - Decimal(
        before["paid_amount"]
    ) != effect * Decimal(now["amount"]):
        raise ValueError("The snapshots do not show this event's effect on the balance")

    event = PaymentAuditEvent(
        invoice_id=invoice.id,
        actor_id=actor.id,
        kind=kind,
        occurred_at=moment,
        invoice_version_before=invoice.version,
        invoice_version_after=invoice.version,
        reason=reason,
        before_snapshot=before,
        after_snapshot=after,
        payment_transaction_id=payment.id,
        receipt_id=None if receipt is None else receipt.id,
    )
    db.session.add(event)
    return event
