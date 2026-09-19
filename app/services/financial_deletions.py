"""Visible deletion and payment replacement (Phase 5 / M10).

Flask-independent: no ``request``, ``abort``, ``flash`` or template. The
workspace routes (``app/blueprints/admin/invoice_workspace.py`` and
``payment_workspace.py``) lock, re-prove and then call these helpers before
their single commit.

**A deletion is a tombstone, never a physical delete.** :func:`tombstone`
sets ``deleted_at``, ``deleted_by_id`` and ``deletion_reason`` together and
moves the row's version by one; the models' guards allow exactly that
transition once and refuse any later change. The row keeps everything it
recorded -- amounts, numbers, statuses, receipt documents -- and its audit
events stay where they are.

**What may be deleted.**

- An invoice that is ``draft`` or ``issued`` and not yet deleted. A cancelled
  invoice keeps its own meaning and is never deleted. While one of its payment
  intents is active nothing is deleted: the provider may still confirm it.
- Deleting an issued invoice deletes its whole **document family** in the same
  transaction: every live transaction -- whatever its kind, method or status
  -- and every live receipt, each with its own event.
- One manual collection (``cash`` or ``bank_transfer``) that is ``pending`` or
  ``confirmed`` and has no live reversal may be deleted or edited on its own,
  with its receipt. An online collection, a reversal and a rejected transfer
  are deleted only with their invoice.

**Edit = replace.** A recorded payment is never changed in place: an edit
deletes the collection and its receipt (``payment_replaced``,
``receipt_deleted``) and records a new collection in the same transaction,
with a new receipt number when it is confirmed.

**Balances in deletion events** are the invoice's before and after the whole
deletion (see ``app/services/payment_audit.py``).
"""

from app.extensions import db
from app.models import (
    Enrollment,
    Group,
    Invoice,
    InvoiceStatus,
    PaymentMethod,
    PaymentTransaction,
    PaymentTransactionKind,
    PaymentTransactionStatus,
    Receipt,
    StudentFeeAssignment,
    User,
    UserRole,
)
from app.services.invoice_audit import (
    build_invoice_deletion_snapshot,
    record_invoice_deletion_event,
)
from app.services.payment_audit import (
    PAYMENT_DELETED,
    PAYMENT_REPLACED,
    RECEIPT_DELETED,
    build_payment_deletion_snapshot,
    record_payment_deletion_event,
)
from app.services.payment_intent_transactions import active_intents

_ISSUED = InvoiceStatus.ISSUED.value
_CANCELLED = InvoiceStatus.CANCELLED.value
_COLLECTION = PaymentTransactionKind.COLLECTION.value
_REVERSAL = PaymentTransactionKind.REVERSAL.value
_MANUAL = frozenset({PaymentMethod.CASH.value, PaymentMethod.BANK_TRANSFER.value})
_PENDING = PaymentTransactionStatus.PENDING.value
_CONFIRMED = PaymentTransactionStatus.CONFIRMED.value
_STUDENT = UserRole.STUDENT.value

_PUBLIC_ID_MAX_LENGTH = 36

#: Why a document cannot be deleted or edited now, as a code; the routes own
#: the wording.
BLOCK_DELETED = "deleted"
BLOCK_CANCELLED = "cancelled"
BLOCK_INTENT_ACTIVE = "intent_active"
BLOCK_NOT_MANUAL = "not_manual"
BLOCK_NOT_OPEN = "not_open"
BLOCK_REVERSED = "reversed"
BLOCK_NOT_ISSUED = "not_issued"


def _public_id_ok(value):
    return isinstance(value, str) and 0 < len(value) <= _PUBLIC_ID_MAX_LENGTH


# ---------------------------------------------------------------------------
# Locating a document from its own public id
# ---------------------------------------------------------------------------


def _chain_columns():
    return (
        Group.public_id.label("group_public_id"),
        Enrollment.public_id.label("enrollment_public_id"),
        StudentFeeAssignment.public_id.label("assignment_public_id"),
        Invoice.public_id.label("invoice_public_id"),
    )


def _chain_query(*columns):
    return (
        db.session.query(*_chain_columns(), *columns)
        .select_from(Invoice)
        .join(StudentFeeAssignment, StudentFeeAssignment.id == Invoice.student_fee_assignment_id)
        .join(Enrollment, Enrollment.id == StudentFeeAssignment.enrollment_id)
        .join(Group, Group.id == Enrollment.group_id)
        .join(User, User.id == Enrollment.student_id)
        .filter(User.role == _STUDENT, Invoice.deleted_at.is_(None))
    )


def invoice_locator(invoice_public_id):
    """The public ids of the Group, Enrollment and assignment a **live**
    invoice nests in, or ``None``. One query; the route then re-reads the
    invoice through the existing nested lookups, which re-prove the chain."""
    if not _public_id_ok(invoice_public_id):
        return None
    return _chain_query().filter(Invoice.public_id == invoice_public_id).first()


def payment_locator(payment_public_id):
    """:func:`invoice_locator` for a **live** transaction of a live invoice,
    with the transaction's own public id."""
    if not _public_id_ok(payment_public_id):
        return None
    return (
        _chain_query(PaymentTransaction.public_id.label("payment_public_id"))
        .join(PaymentTransaction, PaymentTransaction.invoice_id == Invoice.id)
        .filter(
            PaymentTransaction.public_id == payment_public_id,
            PaymentTransaction.deleted_at.is_(None),
        )
        .first()
    )


# ---------------------------------------------------------------------------
# What may be deleted or edited
# ---------------------------------------------------------------------------


def invoice_delete_block(invoice, intents):
    """The ``BLOCK_*`` code refusing `invoice`'s deletion, or ``None``.
    `intents` are the invoice's payment intents."""
    if invoice.deleted_at is not None:
        return BLOCK_DELETED
    if invoice.status == _CANCELLED:
        return BLOCK_CANCELLED
    if active_intents(intents):
        return BLOCK_INTENT_ACTIVE
    return None


def reversed_ids(rows):
    """The ids of the collections a live reversal among `rows` reverses."""
    return {
        row.reversal_of_payment_transaction_id
        for row in rows
        if row.kind == _REVERSAL and row.deleted_at is None
    }


def payment_change_block(invoice, payment, rows, intents):
    """The ``BLOCK_*`` code refusing an edit or deletion of `payment` on its
    own, or ``None``. `rows` are the invoice's live transactions and
    `intents` its payment intents."""
    if invoice.deleted_at is not None or payment.deleted_at is not None:
        return BLOCK_DELETED
    if invoice.status != _ISSUED:
        return BLOCK_NOT_ISSUED
    if payment.kind != _COLLECTION or payment.method not in _MANUAL:
        return BLOCK_NOT_MANUAL
    if payment.status not in (_PENDING, _CONFIRMED):
        return BLOCK_NOT_OPEN
    if payment.id in reversed_ids(rows):
        return BLOCK_REVERSED
    if active_intents(intents):
        return BLOCK_INTENT_ACTIVE
    return None


# ---------------------------------------------------------------------------
# Locks and rows
# ---------------------------------------------------------------------------


def lock_live_receipts(payment_ids):
    """``{payment_transaction_id: Receipt}`` -- the live receipts of
    `payment_ids`, locked in ascending internal id **after** those
    transactions' own locks. Every receipt insert or change takes its
    invoice's lock first, so the set read here is current."""
    ids = sorted({row_id for row_id in payment_ids if row_id is not None})
    if not ids:
        return {}
    receipt_ids = sorted(
        row.id
        for row in db.session.query(Receipt.id)
        .filter(Receipt.payment_transaction_id.in_(ids), Receipt.deleted_at.is_(None))
        .all()
    )
    receipts = {}
    for receipt_id in receipt_ids:
        receipt = Receipt.query.filter_by(id=receipt_id).with_for_update().first()
        if receipt is not None and receipt.deleted_at is None:
            receipts[receipt.payment_transaction_id] = receipt
    return receipts


def tombstone(row, actor_id, reason, moment):
    """Mark `row` -- an invoice, transaction or receipt -- deleted: the three
    tombstone columns together, the version moved by one. The version is read
    first: reading it from an expired row would autoflush a half-applied
    deletion, which the guards refuse."""
    version = row.version
    row.deleted_at = moment
    row.deleted_by_id = actor_id
    row.deletion_reason = reason
    row.version = version + 1
    row.updated_at = moment


# ---------------------------------------------------------------------------
# The deletions
# ---------------------------------------------------------------------------


def delete_payments(
    *, invoice, actor, active_items, payments, targets, receipts, reason, moment, replacement=None
):
    """Tombstone `targets` -- live transactions of the locked, issued
    `invoice` -- and their live `receipts` (``{payment id: Receipt}``), and
    write each one's deletion event. `payments` are the invoice's live
    transactions read under the invoice lock (the targets included); the
    events' balances are the invoice's before and after this whole deletion.
    With `replacement` -- a stored, live collection an edit recorded -- the
    single target's event is ``payment_replaced`` and names it.

    Raises ``ValueError`` (or ``IntegrityError`` on flush) and leaves the
    caller to roll back; nothing is committed here.
    """
    if replacement is not None and len(targets) != 1:
        raise ValueError("An edit replaces exactly one collection")
    replaced_by = None if replacement is None else replacement.public_id
    rows = list(payments)
    before = {}
    for payment in targets:
        receipt = receipts.get(payment.id)
        before[payment.id] = (
            build_payment_deletion_snapshot(invoice, active_items, rows, payment),
            None
            if receipt is None
            else build_payment_deletion_snapshot(invoice, active_items, rows, payment, receipt),
        )
    for payment in targets:
        receipt = receipts.get(payment.id)
        if receipt is not None:
            tombstone(receipt, actor.id, reason, moment)
        tombstone(payment, actor.id, reason, moment)
    db.session.flush()
    for payment in targets:
        payment_before, receipt_before = before[payment.id]
        receipt = receipts.get(payment.id)
        if receipt is not None:
            record_payment_deletion_event(
                invoice=invoice,
                actor=actor,
                kind=RECEIPT_DELETED,
                payment=payment,
                receipt=receipt,
                before_snapshot=receipt_before,
                after_snapshot=build_payment_deletion_snapshot(
                    invoice, active_items, rows, payment, receipt, replaced_by
                ),
                reason=reason,
                moment=moment,
                replacement=replacement,
            )
        record_payment_deletion_event(
            invoice=invoice,
            actor=actor,
            kind=PAYMENT_DELETED if replacement is None else PAYMENT_REPLACED,
            payment=payment,
            receipt=None,
            before_snapshot=payment_before,
            after_snapshot=build_payment_deletion_snapshot(
                invoice, active_items, rows, payment, None, replaced_by
            ),
            reason=reason,
            moment=moment,
            replacement=replacement,
        )


def family_targets(payments):
    """Every live transaction of an invoice, newest first -- the order a
    family deletion records them in."""
    return sorted(
        (row for row in payments if row.deleted_at is None), key=lambda row: row.id, reverse=True
    )


def delete_invoice(*, invoice, actor, assignment_public_id, items, reason, moment):
    """Tombstone the locked, live, draft or issued `invoice` and write its
    ``invoice_deleted`` event. `items` are all of its lines, read after the
    invoice lock. Its transactions and receipts, if any, must already have
    been deleted with :func:`delete_payments` in this transaction."""
    before = build_invoice_deletion_snapshot(invoice, assignment_public_id, items)
    version_before = invoice.version
    tombstone(invoice, actor.id, reason, moment)
    db.session.flush()
    record_invoice_deletion_event(
        invoice=invoice,
        actor=actor,
        version_before=version_before,
        before_snapshot=before,
        after_snapshot=build_invoice_deletion_snapshot(invoice, assignment_public_id, items),
        reason=reason,
        moment=moment,
    )
