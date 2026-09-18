"""The deterministic lock chains, exact balance and receipt numbering for every
manual payment write (Phase 5 / M05).

Flask-independent -- no ``abort``, ``flash``, ``redirect`` or template
rendering. Route-level 404 / redirect / flash handling belongs in
``app/blueprints/admin/payments.py``.

**Cash collection and bank transfer recording**::

    AcademicTerm -> Level -> Course     (lock_academic_hierarchy, which owns
                                         the one deliberate reset)
    -> Group -> enrolled Student User -> Enrollment
    -> acting Administrator User
    -> StudentFeeAssignment
    -> Invoice
    -> the invoice's PaymentTransactions (ascending internal id)
    -> ReceiptNumberSequence (cash only; see below)

**Bank transfer confirmation**::

    ... -> Invoice -> the target PaymentTransaction
    -> the invoice's other PaymentTransactions (ascending internal id)
    -> ReceiptNumberSequence

**Bank transfer rejection**::

    ... -> Invoice -> the target PaymentTransaction

**Full reversal**::

    ... -> Invoice -> the original PaymentTransaction
    -> the invoice's other PaymentTransactions (ascending internal id)
    -> the original's Receipt

The prefix through the Invoice is Phase 5 / M04's chain
(:func:`~app.services.invoice_transactions.lock_invoice_chain`), so **the
Invoice lock serializes every payment write against every invoice-content
write** -- a line change or cancellation that took the lock first is seen by
the payment, and a payment that took it first freezes the invoice before the
line change re-reads it.

A cash collection is confirmed -- and so receives its receipt -- in the same
request that records it, so its chain also ends with the year's
ReceiptNumberSequence, exactly where confirmation takes it: last.

- The id-only read choosing which transactions to lock, and the receipt of a
  reversal, run **after** the Invoice lock. Every insert or status change of a
  transaction or receipt takes that lock first, so the set cannot change
  between the read and the row locks.
- A rejection locks only its target; the other rows it needs for its audit
  snapshot are read after the Invoice lock, which every change to them takes
  first.

**Nothing here authorizes anything.** Every value may come back ``None`` and
the helpers only report what the locked rows say; the caller rolls back and
404s or redirects.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no REPEATABLE
READ snapshot isolation. Tests assert the *requested* lock set and order
(structural); they prove nothing about real InnoDB blocking.
"""

from collections import namedtuple
from decimal import Inexact, localcontext

from app.extensions import db
from app.models import (
    MAX_INVOICE_PAYMENT_ROWS,
    MAX_RECEIPT_SEQUENCE_NUMBER,
    PaymentTransaction,
    PaymentTransactionKind,
    PaymentTransactionStatus,
    Receipt,
    ReceiptNumberSequence,
)
from app.models.receipt_number_sequence import format_receipt_number
from app.services.invoice_transactions import lock_invoice_chain
from app.services.money import sum_amounts

_COLLECTION = PaymentTransactionKind.COLLECTION.value
_REVERSAL = PaymentTransactionKind.REVERSAL.value
_PENDING = PaymentTransactionStatus.PENDING.value
_CONFIRMED = PaymentTransactionStatus.CONFIRMED.value

#: The statuses that freeze an invoice's lines and cancellation.
_FREEZING = (_PENDING, _CONFIRMED)

#: An invoice's exact balance: the total of its active lines, the net amount
#: paid, and what is still outstanding -- all ``Decimal``.
PaymentBalance = namedtuple("PaymentBalance", "total paid outstanding")


class PaymentLocks:
    """The rows one M05 lock chain returned.

    ``chain`` is the M04 :class:`~app.services.invoice_transactions.InvoiceLocks`
    through the Invoice; ``payments`` maps an internal id to a locked
    transaction other than ``payment`` (or ``None`` when the id no longer
    names a row). ``__slots__``-ed, so a typo raises instead of reading
    ``None``.
    """

    __slots__ = ("chain", "payment", "payments", "receipt")

    def __init__(self, chain, payment=None, payments=None, receipt=None):
        self.chain = chain
        self.payment = payment
        self.payments = {} if payments is None else payments
        self.receipt = receipt


def _lock_by_id(model, row_id):
    if row_id is None:
        return None
    return model.query.filter_by(id=row_id).with_for_update().first()


def _lock_rows(model, ids):
    return {
        row_id: model.query.filter_by(id=row_id).with_for_update().first()
        for row_id in sorted({row_id for row_id in ids if row_id is not None})
    }


def invoice_payment_ids(invoice_id):
    """The internal ids of `invoice_id`'s transactions, ascending, at most one
    past :data:`~app.models.payment_transaction.MAX_INVOICE_PAYMENT_ROWS`."""
    return [
        row.id
        for row in db.session.query(PaymentTransaction.id)
        .filter(PaymentTransaction.invoice_id == invoice_id)
        .order_by(PaymentTransaction.id.asc())
        .limit(MAX_INVOICE_PAYMENT_ROWS + 1)
        .all()
    ]


def lock_payment_chain(
    group_public_id,
    term_id,
    level_id,
    course_id,
    student_id,
    enrollment_id,
    actor_id,
    assignment_id,
    invoice_id,
    payment_id=None,
    include_invoice_payments=False,
    include_receipt=False,
):
    """Take the M05 lock order in one open transaction. Returns a
    :class:`PaymentLocks`.

    Every id except `group_public_id` is **internal**, discovered by a
    non-locking read of the URL's public ids before this call; everything is
    re-proved against the locked rows afterwards. `payment_id` locks the
    target transaction right after the Invoice; `include_invoice_payments`
    then locks the invoice's other transactions; `include_receipt` finally
    locks the target's receipt.
    """
    chain = lock_invoice_chain(
        group_public_id,
        term_id,
        level_id,
        course_id,
        student_id,
        enrollment_id,
        actor_id,
        assignment_id,
        invoice_id=invoice_id,
    )
    invoice = chain.invoice
    if invoice is None:
        return PaymentLocks(chain)
    payment = _lock_by_id(PaymentTransaction, payment_id)
    payments = {}
    if include_invoice_payments:
        payments = _lock_rows(
            PaymentTransaction,
            (row_id for row_id in invoice_payment_ids(invoice.id) if row_id != payment_id),
        )
    receipt = None
    if include_receipt and payment is not None:
        receipt_id = (
            db.session.query(Receipt.id)
            .filter(Receipt.payment_transaction_id == payment.id)
            .scalar()
        )
        receipt = _lock_by_id(Receipt, receipt_id)
    return PaymentLocks(chain, payment=payment, payments=payments, receipt=receipt)


# ---------------------------------------------------------------------------
# Re-proving facts against the locked rows
# ---------------------------------------------------------------------------


def payment_nesting_broken(locks, payment_id, payment_public_id):
    """``True`` unless the locked transaction is the URL's and still belongs to
    the locked invoice."""
    invoice, payment = locks.chain.invoice, locks.payment
    return (
        invoice is None
        or payment is None
        or payment.id != payment_id
        or payment.public_id != payment_public_id
        or payment.invoice_id != invoice.id
    )


def locked_invoice_payments(locks):
    """Every locked transaction -- the target included -- that still belongs
    to the locked invoice, ascending internal id."""
    invoice = locks.chain.invoice
    if invoice is None:
        return []
    rows = dict(locks.payments)
    if locks.payment is not None:
        rows[locks.payment.id] = locks.payment
    return [
        row for _row_id, row in sorted(rows.items()) if row is not None and row.invoice_id == invoice.id
    ]


def current_invoice_payments(invoice):
    """Every transaction of `invoice`, ascending internal id, read **after**
    the invoice lock without locking them (at most one past the bound). Rows
    this transaction already locked come back as the same objects."""
    return (
        PaymentTransaction.query.filter(PaymentTransaction.invoice_id == invoice.id)
        .order_by(PaymentTransaction.id.asc())
        .limit(MAX_INVOICE_PAYMENT_ROWS + 1)
        .all()
    )


def payment_rows_over_bound(rows):
    """Whether `rows` is the one-past-the-bound read: the invoice holds more
    transactions than any page, balance or token may describe."""
    return len(rows) > MAX_INVOICE_PAYMENT_ROWS


def collection_rows(rows):
    return [row for row in rows if row.kind == _COLLECTION]


def reversal_of(rows, original_id):
    """The reversal among `rows` of the collection `original_id`, or ``None``."""
    return next(
        (
            row
            for row in rows
            if row.kind == _REVERSAL and row.reversal_of_payment_transaction_id == original_id
        ),
        None,
    )


def payment_balance(active_items, payments):
    """The exact :class:`PaymentBalance` of an invoice, or ``None`` when its
    records do not describe one.

    ``outstanding = total of active lines - confirmed collections + confirmed
    reversals``, added and subtracted in Python ``Decimal`` with ``Inexact``
    trapped; pending and rejected rows count for nothing. A negative paid or
    outstanding amount cannot come from the application's own writes and is
    reported as ``None`` rather than shown or acted on.
    """
    with localcontext() as context:
        context.traps[Inexact] = True
        total = sum_amounts([item.amount for item in active_items])
        collected = sum_amounts(
            [row.amount for row in payments if row.kind == _COLLECTION and row.status == _CONFIRMED]
        )
        reversed_amount = sum_amounts(
            [row.amount for row in payments if row.kind == _REVERSAL and row.status == _CONFIRMED]
        )
        paid = collected - reversed_amount
        outstanding = total - paid
    if paid < 0 or outstanding < 0:
        return None
    return PaymentBalance(total, paid, outstanding)


def invoice_payment_frozen(invoice_id):
    """Whether `invoice_id` holds a ``pending`` or ``confirmed`` transaction,
    which freezes its lines and its cancellation. A rejected transfer alone
    does not.

    An invoice-content write asks this after its Invoice lock: every payment
    insert and status change takes that lock first, so the answer is current.
    """
    query = db.session.query(PaymentTransaction.id).filter(
        PaymentTransaction.invoice_id == invoice_id, PaymentTransaction.status.in_(_FREEZING)
    )
    return bool(db.session.query(query.exists()).scalar())


# ---------------------------------------------------------------------------
# Receipt numbering
# ---------------------------------------------------------------------------


def lock_receipt_number_sequence(calendar_year, moment):
    """The locked sequence row of `calendar_year`, created (at 0) if the year
    has none -- the last lock of a confirmation.

    Found by a plain read and locked by primary key, never by a locking read
    that matches nothing (InnoDB gap locks would let two first receipts of a
    year deadlock). Two first confirmations that both insert the row meet the
    unique ``calendar_year``; the flush raises ``IntegrityError``, which the
    caller rolls back and reports generically. Nothing is consumed either way.
    """
    sequence_id = (
        db.session.query(ReceiptNumberSequence.id)
        .filter(ReceiptNumberSequence.calendar_year == calendar_year)
        .scalar()
    )
    if sequence_id is None:
        row = ReceiptNumberSequence(
            calendar_year=calendar_year, last_number=0, created_at=moment, updated_at=moment
        )
        db.session.add(row)
        db.session.flush()
        sequence_id = row.id
    return _lock_by_id(ReceiptNumberSequence, sequence_id)


def allocate_receipt_number(sequence, moment):
    """Increment the **locked** `sequence` exactly once and return the new
    ``RCT-YYYY-NNNNNN``, or ``None`` -- changing nothing -- when the year's
    six-digit capacity is exhausted."""
    if sequence is None or sequence.last_number >= MAX_RECEIPT_SEQUENCE_NUMBER:
        return None
    sequence.last_number = sequence.last_number + 1
    sequence.updated_at = moment
    return format_receipt_number(sequence.calendar_year, sequence.last_number)


def receipt_number_taken(number):
    """Whether any receipt already holds `number`. ``uq_receipts_receipt_number``
    is the final defense."""
    query = db.session.query(Receipt.id).filter(Receipt.receipt_number == number)
    return bool(db.session.query(query.exists()).scalar())


def payment_has_receipt(payment_id):
    """Whether `payment_id` already has a receipt.
    ``uq_receipts_payment_transaction_id`` is the final defense."""
    query = db.session.query(Receipt.id).filter(Receipt.payment_transaction_id == payment_id)
    return bool(db.session.query(query.exists()).scalar())
