"""The deterministic lock chains and post-lock facts for every payment intent
write (Phase 5 / M06).

Flask-independent -- no ``abort``, ``flash``, ``redirect`` or template
rendering. Route-level 404 / redirect / flash handling belongs in
``app/blueprints/admin/payment_intents.py``.

**Creation**::

    AcademicTerm -> Level -> Course     (lock_academic_hierarchy, which owns
                                         the one deliberate reset)
    -> Group -> enrolled Student User -> Enrollment
    -> acting Administrator User
    -> StudentFeeAssignment
    -> Invoice
    -> the invoice's PaymentTransactions (ascending internal id)
    -> the invoice's PaymentIntents (ascending internal id)

**Cancellation and Mock-result synchronization**::

    ... -> Invoice -> the target PaymentIntent
    -> the invoice's PaymentTransactions (ascending internal id)
    -> the invoice's other PaymentIntents (ascending internal id)

The prefix through the Invoice is Phase 5 / M04's chain
(:func:`~app.services.invoice_transactions.lock_invoice_chain`), unchanged --
including its approved Student -> Enrollment -> Administrator order, which
M03, M04 and M05 share -- so **the Invoice lock serializes every payment
intent write against every invoice-content and manual-payment write**. A line
change or cancellation that took the lock first is seen by the intent write,
and an intent that took it first freezes the invoice before the line change
re-reads it.

- The id-only reads choosing which transactions and intents to lock run
  **after** the Invoice lock. Every insert or status change of either takes
  that lock first, so neither set can change between the read and the row
  locks.
- The provider is called only after every lock is held and every rule has
  been re-proved, so its answer is judged against current rows.

**Nothing here authorizes anything.** Every value may come back ``None`` and
the helpers only report what the locked rows say; the caller rolls back and
404s or redirects.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no REPEATABLE
READ snapshot isolation. Tests assert the *requested* lock set and order
(structural); they prove nothing about real InnoDB blocking.
"""

from app.extensions import db
from app.models import (
    ACTIVE_PAYMENT_INTENT_STATUSES,
    MAX_INVOICE_PAYMENT_INTENTS,
    PaymentIntent,
    PaymentTransaction,
    PaymentTransactionStatus,
)
from app.services.invoice_transactions import lock_invoice_chain
from app.services.payment_transactions import invoice_payment_ids

#: The manual-payment statuses that freeze an invoice -- and, in M06, refuse
#: a new payment intent. A rejected transfer alone does neither.
_FREEZING_PAYMENT_STATUSES = (
    PaymentTransactionStatus.PENDING.value,
    PaymentTransactionStatus.CONFIRMED.value,
)


class PaymentIntentLocks:
    """The rows one M06 lock chain returned.

    ``chain`` is the M04 :class:`~app.services.invoice_transactions.InvoiceLocks`
    through the Invoice; ``payments`` and ``intents`` map an internal id to a
    locked row (``intents`` excluding ``intent``), or to ``None`` when the id
    no longer names a row. ``__slots__``-ed, so a typo raises instead of
    reading ``None``.
    """

    __slots__ = ("chain", "intent", "payments", "intents")

    def __init__(self, chain, intent=None, payments=None, intents=None):
        self.chain = chain
        self.intent = intent
        self.payments = {} if payments is None else payments
        self.intents = {} if intents is None else intents


def _lock_by_id(model, row_id):
    if row_id is None:
        return None
    return model.query.filter_by(id=row_id).with_for_update().first()


def _lock_rows(model, ids):
    return {
        row_id: model.query.filter_by(id=row_id).with_for_update().first()
        for row_id in sorted({row_id for row_id in ids if row_id is not None})
    }


def invoice_payment_intent_ids(invoice_id):
    """The internal ids of `invoice_id`'s payment intents, ascending, at most
    one past :data:`~app.models.payment_intent.MAX_INVOICE_PAYMENT_INTENTS`."""
    return [
        row.id
        for row in db.session.query(PaymentIntent.id)
        .filter(PaymentIntent.invoice_id == invoice_id)
        .order_by(PaymentIntent.id.asc())
        .limit(MAX_INVOICE_PAYMENT_INTENTS + 1)
        .all()
    ]


def lock_payment_intent_chain(
    group_public_id,
    term_id,
    level_id,
    course_id,
    student_id,
    enrollment_id,
    actor_id,
    assignment_id,
    invoice_id,
    intent_id=None,
):
    """Take the M06 lock order in one open transaction. Returns a
    :class:`PaymentIntentLocks`.

    Every id except `group_public_id` is **internal**, discovered by a
    non-locking read of the URL's public ids before this call; everything is
    re-proved against the locked rows afterwards. `intent_id` locks the target
    intent right after the Invoice; the invoice's transactions and then its
    other intents follow, each in ascending internal id.
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
        return PaymentIntentLocks(chain)
    intent = _lock_by_id(PaymentIntent, intent_id)
    payments = _lock_rows(PaymentTransaction, invoice_payment_ids(invoice.id))
    intents = _lock_rows(
        PaymentIntent,
        (row_id for row_id in invoice_payment_intent_ids(invoice.id) if row_id != intent_id),
    )
    return PaymentIntentLocks(chain, intent=intent, payments=payments, intents=intents)


# ---------------------------------------------------------------------------
# Re-proving facts against the locked rows
# ---------------------------------------------------------------------------


def intent_nesting_broken(locks, intent_id, intent_public_id):
    """``True`` unless the locked intent is the URL's and still belongs to the
    locked invoice."""
    invoice, intent = locks.chain.invoice, locks.intent
    return (
        invoice is None
        or intent is None
        or intent.id != intent_id
        or intent.public_id != intent_public_id
        or intent.invoice_id != invoice.id
    )


def locked_invoice_payments(locks):
    """Every locked manual transaction that still belongs to the locked
    invoice, ascending internal id."""
    invoice = locks.chain.invoice
    if invoice is None:
        return []
    return [
        row
        for _row_id, row in sorted(locks.payments.items())
        if row is not None and row.invoice_id == invoice.id
    ]


def locked_invoice_intents(locks):
    """Every locked payment intent -- the target included -- that still
    belongs to the locked invoice, ascending internal id."""
    invoice = locks.chain.invoice
    if invoice is None:
        return []
    rows = dict(locks.intents)
    if locks.intent is not None:
        rows[locks.intent.id] = locks.intent
    return [
        row for _row_id, row in sorted(rows.items()) if row is not None and row.invoice_id == invoice.id
    ]


def intent_rows_over_bound(rows):
    """Whether `rows` is the one-past-the-bound read: the invoice holds more
    payment intents than any page or token may describe."""
    return len(rows) > MAX_INVOICE_PAYMENT_INTENTS


def active_intents(rows):
    """The ``pending`` and ``provider_succeeded`` intents among `rows`."""
    return [row for row in rows if row.status in ACTIVE_PAYMENT_INTENT_STATUSES]


def manual_payment_freezes(rows):
    """Whether any of an invoice's manual transactions `rows` is ``pending``
    or ``confirmed`` -- M05's freeze, which M06 also reads as "this invoice is
    being paid manually, so no online intent may start"."""
    return any(row.status in _FREEZING_PAYMENT_STATUSES for row in rows)


def invoice_payment_intent_frozen(invoice_id):
    """Whether `invoice_id` holds an active (``pending`` or
    ``provider_succeeded``) payment intent, which freezes its lines and its
    cancellation exactly as M05's pending or confirmed payments do. A failed or
    cancelled intent alone does not.

    An invoice-content write asks this after its Invoice lock: every intent
    insert and status change takes that lock first, so the answer is current.
    """
    query = db.session.query(PaymentIntent.id).filter(
        PaymentIntent.invoice_id == invoice_id,
        PaymentIntent.status.in_(ACTIVE_PAYMENT_INTENT_STATUSES),
    )
    return bool(db.session.query(query.exists()).scalar())


def invoice_intent_by_idempotency_key(invoice_id, idempotency_key):
    """The intent of `invoice_id` created under `idempotency_key`, or
    ``None``. ``uq_payment_intents_idempotency_key`` is the final defense."""
    return PaymentIntent.query.filter(
        PaymentIntent.invoice_id == invoice_id,
        PaymentIntent.idempotency_key == idempotency_key,
    ).first()


def idempotency_key_taken(idempotency_key):
    """Whether any intent -- of any invoice -- holds `idempotency_key`."""
    query = db.session.query(PaymentIntent.id).filter(
        PaymentIntent.idempotency_key == idempotency_key
    )
    return bool(db.session.query(query.exists()).scalar())


def provider_reference_taken(provider_reference):
    """Whether any intent already holds `provider_reference`.
    ``uq_payment_intents_provider_reference`` is the final defense."""
    query = db.session.query(PaymentIntent.id).filter(
        PaymentIntent.provider_reference == provider_reference
    )
    return bool(db.session.query(query.exists()).scalar())
