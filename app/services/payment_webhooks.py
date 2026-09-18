"""Verified provider webhooks: the provider-event inbox and the online
collection (Phase 5 / M07).

Flask-independent -- no ``request``, ``abort``, ``flash`` or template. The
public endpoint (``app/blueprints/webhooks/routes.py``) and the Administrator's
Mock/Sandbox delivery control both hand the exact raw body and the provider's
headers to :func:`process_provider_webhook`; nothing else can create an online
collection.

**Three distinct things.** A browser-observed provider status
(``provider_succeeded``, M06) is not an event; an authenticated provider event
is stored in the inbox (:class:`~app.models.payment_provider_event.PaymentProviderEvent`)
whatever it means; and only a verified ``payment.succeeded`` event that passes
every locked check becomes a financial collection.

**Order of trust.** The signature and freshness are verified over the exact
raw bytes **before** the body is parsed; the body is then normalized strictly;
only then is anything read from the database. A delivery that fails any of
this raises :class:`WebhookRejected` and nothing is stored.

**Idempotency.** An event is identified by ``(provider, event id)``. A
re-delivery of a stored event with the same payload digest returns the stored
outcome and changes nothing; the same id with a different digest is rejected.
The unique constraint is the final defense against two deliveries racing.

**The lock chain** (no Administrator row is locked, because none acts)::

    AcademicTerm -> Level -> Course     (lock_academic_hierarchy, which owns
                                         the one deliberate reset)
    -> Group -> enrolled Student User -> Enrollment
    -> StudentFeeAssignment
    -> Invoice
    -> the target PaymentIntent
    -> the invoice's PaymentTransactions (ascending internal id)
    -> the invoice's other PaymentIntents (ascending internal id)
    -> ReceiptNumberSequence (a confirmation only; last)

It is M06's intent chain with the actor left out. The stored event is read
**after** the Invoice lock, which every event write for the intent's invoice
takes first; a new event row is inserted, not locked, and its unique id
guards the race.

**Outcomes** (:class:`~app.models.enums.ProviderEventOutcome`), decided against
the locked rows:

- ``payment.failed`` for an active intent -> ``failed``: the intent becomes
  ``provider_failed``. No payment, receipt or audit event.
- ``payment.succeeded`` for an active intent, when the event's reference,
  amount and currency are the intent's and the invoice can still be
  collected in full -> ``confirmed``: one online collection, its receipt, the
  intent ``confirmed``, the event and two system-origin audit events -- all in
  one transaction.
- a new event restating the intent's recorded outcome -> ``duplicate``; a
  failure for a cancelled intent -> ``ignored_terminal``.
- anything else that conflicts -> ``reconciliation_required``: stored and
  shown, financially inert.

Every accounting moment is the LMS's own trusted clock, read after the locks;
the provider's moment is kept only in the inbox. A transient database failure
raises :class:`WebhookRetry` after a rollback, so nothing is committed and the
provider may deliver again.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE``; tests assert the
requested lock structure only.
"""

import time
import uuid
from collections import namedtuple

from sqlalchemy.exc import IntegrityError, OperationalError

from app.extensions import db
from app.models import (
    ACTIVE_PAYMENT_INTENT_STATUSES,
    MAX_INVOICE_COLLECTIONS,
    MAX_INVOICE_ITEM_ROWS,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    Invoice,
    InvoiceStatus,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentMethod,
    PaymentProviderEvent,
    PaymentTransaction,
    PaymentTransactionKind,
    PaymentTransactionStatus,
    ProviderEventOutcome,
    Receipt,
    ReceiptStatus,
    StudentFeeAssignment,
    StudentFeeAssignmentStatus,
)
from app.services.invoice_queries import active_lines
from app.services.invoice_transactions import (
    assignment_nesting_broken,
    invoice_items_valid,
    invoice_nesting_broken,
    invoice_rows_for_snapshot,
)
from app.services.payment_audit import (
    ONLINE_CONFIRMED,
    RECEIPT_ONLINE_ISSUED,
    build_online_receipt_document,
    build_payment_snapshot,
    online_context,
    record_payment_event,
)
from app.services.payment_intent_transactions import (
    active_intents,
    intent_nesting_broken,
    lock_payment_intent_chain,
    locked_invoice_intents,
    locked_invoice_payments,
    manual_payment_freezes,
)
from app.services.payment_providers import (
    EVENT_PAYMENT_FAILED,
    EVENT_PAYMENT_SUCCEEDED,
    PaymentProviderError,
)
from app.services.payment_transactions import (
    allocate_receipt_number,
    collection_rows,
    lock_receipt_number_sequence,
    payment_balance,
    payment_rows_over_bound,
    receipt_number_taken,
)
from app.services.schedule_occurrences import to_app_local, utc_reference_now
from app.services.student_fee_assignment_transactions import (
    enrollment_nesting_broken,
    hierarchy_moved,
)

_ACTIVE = frozenset(ACTIVE_PAYMENT_INTENT_STATUSES)
_INTENT_FAILED = PaymentIntentStatus.PROVIDER_FAILED.value
_INTENT_CANCELLED = PaymentIntentStatus.CANCELLED.value
_INTENT_CONFIRMED = PaymentIntentStatus.CONFIRMED.value
_ISSUED = InvoiceStatus.ISSUED.value
_ASSIGNED = StudentFeeAssignmentStatus.ASSIGNED.value
_ENROLLED = EnrollmentStatus.ACTIVE.value
_COLLECTION = PaymentTransactionKind.COLLECTION.value
_ONLINE = PaymentMethod.ONLINE.value
_CONFIRMED_PAYMENT = PaymentTransactionStatus.CONFIRMED.value
_RECEIPT_ISSUED = ReceiptStatus.ISSUED.value

CONFIRMED = ProviderEventOutcome.CONFIRMED.value
FAILED = ProviderEventOutcome.FAILED.value
DUPLICATE = ProviderEventOutcome.DUPLICATE.value
IGNORED_TERMINAL = ProviderEventOutcome.IGNORED_TERMINAL.value
RECONCILIATION_REQUIRED = ProviderEventOutcome.RECONCILIATION_REQUIRED.value


class WebhookRejected(Exception):
    """The delivery is not authentic, fresh, well-formed, known, or it reuses
    a stored event id with a different body. Nothing was stored. The message
    is internal; the endpoint answers generically."""


class WebhookRetry(Exception):
    """A transient failure -- a lost race, a lock timeout, a year that turned
    while waiting. Everything was rolled back and nothing was committed; the
    provider may deliver the same event again."""


#: What processing one delivery did. ``redelivered`` is ``True`` when the event
#: was already stored and nothing changed; ``receipt_number`` is set for a
#: confirmation only.
WebhookResult = namedtuple("WebhookResult", "outcome event_public_id receipt_number redelivered")


def _trusted_now():
    """The LMS's own naive-UTC whole-second moment. Never the provider's."""
    return utc_reference_now().replace(microsecond=0)


def webhook_intent_context(provider_name, provider_reference):
    """One row locating the intent a verified event names, with every internal
    id its lock chain needs -- or ``None`` when no intent of `provider_name`
    holds `provider_reference`. One query; a pre-lock read that only decides
    which rows to lock."""
    return (
        db.session.query(
            PaymentIntent.id.label("intent_id"),
            PaymentIntent.public_id.label("intent_public_id"),
            Invoice.id.label("invoice_id"),
            Invoice.public_id.label("invoice_public_id"),
            StudentFeeAssignment.id.label("assignment_id"),
            StudentFeeAssignment.public_id.label("assignment_public_id"),
            Enrollment.id.label("enrollment_id"),
            Enrollment.student_id.label("student_id"),
            Group.public_id.label("group_public_id"),
            Group.academic_term_id.label("academic_term_id"),
            Group.course_id.label("course_id"),
            Course.level_id.label("level_id"),
        )
        .select_from(PaymentIntent)
        .join(Invoice, Invoice.id == PaymentIntent.invoice_id)
        .join(StudentFeeAssignment, StudentFeeAssignment.id == Invoice.student_fee_assignment_id)
        .join(Enrollment, Enrollment.id == StudentFeeAssignment.enrollment_id)
        .join(Group, Group.id == Enrollment.group_id)
        .join(Course, Course.id == Group.course_id)
        .join(AcademicTerm, AcademicTerm.id == Group.academic_term_id)
        .filter(
            PaymentIntent.provider == provider_name,
            PaymentIntent.provider_reference == provider_reference,
        )
        .first()
    )


def stored_provider_event(provider_name, event_id):
    """The stored event ``(provider, event id)``, or ``None``."""
    return PaymentProviderEvent.query.filter(
        PaymentProviderEvent.provider == provider_name,
        PaymentProviderEvent.provider_event_id == event_id,
    ).first()


def _redelivery(stored, event):
    """The result of a delivery of an event already stored: the stored outcome
    when the body is the same, a rejection when it is not. Changes nothing."""
    same = stored.payload_digest == event.payload_digest
    result = WebhookResult(stored.outcome, stored.public_id, None, True)
    db.session.rollback()
    if not same:
        raise WebhookRejected("an event id was reused with a different body")
    return result


def _corresponds(provider_name, intent, event):
    return (
        intent.provider == provider_name
        and intent.provider_reference == event.provider_reference
        and intent.amount == event.amount
        and intent.currency_code == event.currency_code
    )


def _context_current(chain, context):
    """Whether the locked Enrollment, Student and assignment still form the
    context the intent was created in, and the assignment is still the
    Enrollment's current charge: ``assigned``, of an active Enrollment."""
    if (
        enrollment_nesting_broken(chain, context.group_public_id, context.enrollment_id)
        or assignment_nesting_broken(chain, context.assignment_id, context.assignment_public_id)
        or invoice_nesting_broken(chain, context.invoice_id, context.invoice_public_id)
    ):
        return False
    return chain.assignment.status == _ASSIGNED and chain.enrollment.status == _ENROLLED


def _collectable(chain, context, intent, item_rows, payments, intents):
    """Whether `intent`'s exact amount can be recorded now as the whole
    outstanding balance of its invoice -- every rule re-proved against the
    locked rows. ``(True, balance)`` or ``(False, None)``."""
    invoice = chain.invoice
    active = active_lines(item_rows)
    if (
        not _context_current(chain, context)
        or invoice.status != _ISSUED
        or len(item_rows) > MAX_INVOICE_ITEM_ROWS
        or not invoice_items_valid(active)
        or payment_rows_over_bound(payments)
        or manual_payment_freezes(payments)
        or len(collection_rows(payments)) >= MAX_INVOICE_COLLECTIONS
        or [row.id for row in active_intents(intents)] != [intent.id]
    ):
        return False, None
    balance = payment_balance(active, payments)
    if balance is None or balance.outstanding != intent.amount:
        return False, None
    return True, balance


def _decide(provider_name, event, chain, context, intent, item_rows, payments, intents):
    """The outcome of one verified event against the locked rows."""
    corresponds = _corresponds(provider_name, intent, event)
    status = intent.status
    if event.event_type == EVENT_PAYMENT_FAILED:
        if not corresponds:
            return RECONCILIATION_REQUIRED
        if status in _ACTIVE:
            return FAILED
        if status == _INTENT_FAILED:
            return DUPLICATE
        if status == _INTENT_CANCELLED:
            return IGNORED_TERMINAL
        return RECONCILIATION_REQUIRED
    if event.event_type != EVENT_PAYMENT_SUCCEEDED or not corresponds:
        return RECONCILIATION_REQUIRED
    if status == _INTENT_CONFIRMED:
        return DUPLICATE
    if status not in _ACTIVE:
        return RECONCILIATION_REQUIRED
    collectable, _balance = _collectable(chain, context, intent, item_rows, payments, intents)
    return CONFIRMED if collectable else RECONCILIATION_REQUIRED


def _event_row(provider_name, event, intent, outcome, received_at, moment, public_id=None):
    return PaymentProviderEvent(
        public_id=public_id or str(uuid.uuid4()),
        provider=provider_name,
        provider_event_id=event.event_id,
        payment_intent_id=intent.id,
        event_type=event.event_type,
        currency_code=event.currency_code,
        amount=event.amount,
        provider_occurred_at=event.occurred_at,
        received_at=received_at,
        processed_at=moment,
        payload_digest=event.payload_digest,
        outcome=outcome,
        payment_transaction_id=None,
        created_at=moment,
    )


def _close_intent(intent, status, moment):
    """Move a locked, active `intent` to a terminal `status`, once. The
    browser-observed result, if any, is kept exactly."""
    intent.version = intent.version + 1
    intent.status = status
    intent.terminal_at = moment
    intent.updated_at = moment


def _center_year(tz_name, moment):
    return to_app_local(tz_name, moment).year


def _confirm(
    provider_name, event, locks, context, item_rows, payments, received_at, tz_name, clock
):
    """Record the online collection, its receipt, the event, the intent's
    confirmation and both system-origin audit events. The caller commits.
    Returns ``(outcome, event_public_id, receipt_number)``."""
    chain, intent = locks.chain, locks.intent
    invoice = chain.invoice
    active = active_lines(item_rows)
    event_public_id = str(uuid.uuid4())
    online = online_context(intent.public_id, event_public_id)

    provisional = clock()
    year = _center_year(tz_name, provisional)
    before = build_payment_snapshot(invoice, active, payments, online=online)
    sequence = lock_receipt_number_sequence(year, provisional)
    moment = clock()
    if _center_year(tz_name, moment) != year:
        raise WebhookRetry("the calendar year turned while the sequence lock was awaited")
    number = allocate_receipt_number(sequence, moment)
    if number is None:
        # The year's receipt numbers are exhausted: nothing can be issued, so
        # the event is kept for reconciliation and no money is recorded.
        db.session.add(
            _event_row(provider_name, event, intent, RECONCILIATION_REQUIRED, received_at, moment,
                       event_public_id)
        )
        return RECONCILIATION_REQUIRED, event_public_id, None
    if receipt_number_taken(number):
        raise WebhookRetry("the allocated receipt number is already held")

    payment = PaymentTransaction(
        public_id=str(uuid.uuid4()),
        invoice_id=invoice.id,
        kind=_COLLECTION,
        method=_ONLINE,
        status=_CONFIRMED_PAYMENT,
        currency_code=intent.currency_code,
        amount=intent.amount,
        bank_transfer_reference=None,
        bank_transfer_date=None,
        recorded_at=moment,
        recorded_by_id=None,
        confirmed_at=moment,
        confirmed_by_id=None,
        rejected_at=None,
        rejected_by_id=None,
        rejection_reason=None,
        reversal_of_payment_transaction_id=None,
        payment_intent_id=intent.id,
        version=1,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(payment)
    db.session.flush()
    stored = _event_row(provider_name, event, intent, CONFIRMED, received_at, moment,
                        event_public_id)
    stored.payment_transaction_id = payment.id
    db.session.add(stored)
    everything = payments + [payment]
    after_payment = build_payment_snapshot(invoice, active, everything, payment=payment,
                                           online=online)
    record_payment_event(
        invoice=invoice,
        actor=None,
        kind=ONLINE_CONFIRMED,
        payment=payment,
        receipt=None,
        before_snapshot=before,
        after_snapshot=after_payment,
        reason=None,
        moment=moment,
        online=online,
    )
    hierarchy = chain.hierarchy
    receipt_public_id = str(uuid.uuid4())
    receipt = Receipt(
        public_id=receipt_public_id,
        payment_transaction_id=payment.id,
        receipt_number=number,
        status=_RECEIPT_ISSUED,
        issued_at=moment,
        issued_by_id=None,
        voided_at=None,
        voided_by_id=None,
        void_reason=None,
        snapshot=build_online_receipt_document(
            receipt_public_id=receipt_public_id,
            receipt_number=number,
            payment=payment,
            invoice=invoice,
            assignment_public_id=context.assignment_public_id,
            intent_public_id=intent.public_id,
            provider_event_public_id=event_public_id,
            student_name=chain.student.full_name,
            group_name=chain.group.name,
            course_title=hierarchy.course(context.course_id).title,
            academic_term_name=hierarchy.term(context.academic_term_id).name,
        ),
        version=1,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(receipt)
    db.session.flush()
    record_payment_event(
        invoice=invoice,
        actor=None,
        kind=RECEIPT_ONLINE_ISSUED,
        payment=payment,
        receipt=receipt,
        before_snapshot=after_payment,
        after_snapshot=build_payment_snapshot(
            invoice, active, everything, payment=payment, receipt=receipt, online=online
        ),
        reason=None,
        moment=moment,
        online=online,
    )
    _close_intent(intent, _INTENT_CONFIRMED, moment)
    return CONFIRMED, event_public_id, number


def process_provider_webhook(provider, raw_body, headers, *, tz_name, clock=_trusted_now,
                             epoch=time.time):
    """Verify, record and apply one provider webhook delivery. Returns a
    :data:`WebhookResult` after a **committed** result; raises
    :class:`WebhookRejected` (nothing stored) or :class:`WebhookRetry`
    (everything rolled back).

    `raw_body` is the exact request body as ``bytes``; `headers` a plain
    mapping of the provider's header names. `tz_name` is the center's time
    zone, which the receipt year is read in; `clock` and `epoch` are the LMS's
    own clocks, injectable by tests.
    """
    received_at = clock()
    try:
        verified = provider.verify_webhook(raw_body, headers, now=int(epoch()))
        event = provider.normalize_event(verified)
    except PaymentProviderError:
        raise WebhookRejected("the delivery is not an authentic, fresh, exact event") from None
    name = provider.name

    try:
        context = webhook_intent_context(name, event.provider_reference)
        if context is None:
            db.session.rollback()
            raise WebhookRejected("no payment intent holds the event's reference")
        stored = stored_provider_event(name, event.event_id)
        if stored is not None:
            return _redelivery(stored, event)

        locks = lock_payment_intent_chain(
            context.group_public_id,
            context.academic_term_id,
            context.level_id,
            context.course_id,
            context.student_id,
            context.enrollment_id,
            None,
            context.assignment_id,
            context.invoice_id,
            intent_id=context.intent_id,
        )
        chain = locks.chain
        if intent_nesting_broken(locks, context.intent_id, context.intent_public_id) or (
            hierarchy_moved(chain, context.academic_term_id, context.level_id, context.course_id)
        ):
            raise WebhookRetry("the intent's context moved while the locks were awaited")
        stored = stored_provider_event(name, event.event_id)
        if stored is not None:
            return _redelivery(stored, event)

        intent = locks.intent
        payments = locked_invoice_payments(locks)
        intents = locked_invoice_intents(locks)
        item_rows = invoice_rows_for_snapshot(chain.invoice)
        outcome = _decide(name, event, chain, context, intent, item_rows, payments, intents)
        receipt_number = None
        if outcome == CONFIRMED:
            outcome, event_public_id, receipt_number = _confirm(
                name, event, locks, context, item_rows, payments, received_at, tz_name, clock
            )
        else:
            moment = clock()
            stored = _event_row(name, event, intent, outcome, received_at, moment)
            event_public_id = stored.public_id
            if outcome == FAILED:
                _close_intent(intent, _INTENT_FAILED, moment)
            db.session.add(stored)
        db.session.commit()
    except WebhookRejected:
        raise
    except WebhookRetry:
        db.session.rollback()
        raise
    except (IntegrityError, OperationalError):
        db.session.rollback()
        raise WebhookRetry("a database conflict interrupted the delivery") from None
    except Exception:
        db.session.rollback()
        raise
    return WebhookResult(outcome, event_public_id, receipt_number, False)
