"""Read queries and presentation for Administrator payment intents
(Phase 5 / M06) and their verified provider events (M07).

Flask-independent: explicit queries returning rows or plain presentation
dicts, and no ``request``, ``abort`` or template. Route-level 404 / redirect
handling belongs in ``app/blueprints/admin/payment_intents.py``.

**Every read here is reachable only by an active Administrator.** There is no
Student, Teacher, Researcher or public read of a payment intent anywhere.

**Nesting is part of every lookup.** An intent is found only inside the
invoice in its URL, which the route has already found inside its assignment,
Enrollment and Group. Anything else is ``None``, which the route turns into a
404 without saying whether the identifier exists elsewhere.

**Internal ids and idempotency keys stay inside the service layer.** The
``build_*`` helpers drop them before anything reaches a template.

**Bounded, and free of N+1.** An invoice's intents are read once for its state
(at most one past
:data:`~app.models.payment_intent.MAX_INVOICE_PAYMENT_INTENTS`), its history is
a page of :data:`PAGE_SIZE` with ``LIMIT PAGE_SIZE + 1`` and no ``COUNT``, and
the accounts a page names come from one keyed query. The overview is one query
per page with every name joined in. An intent's provider events are its newest
:data:`EVENT_PAGE_SIZE` (``LIMIT`` one past it); which intents await
reconciliation is one keyed query.

**Provider events are shown safely.** Their type, amount, outcome and the
moments are shown; their provider event id, payload digest and provider
reference never reach a template, and no raw body, signature or secret exists
to show.
"""

from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import (
    ACTIVE_PAYMENT_INTENT_STATUSES,
    MAX_INVOICE_PAYMENT_INTENTS,
    Enrollment,
    Group,
    Invoice,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentProviderEvent,
    PaymentTransaction,
    ProviderEventOutcome,
    ProviderEventType,
    Receipt,
    StudentFeeAssignment,
    User,
)
from app.services.money import format_amount
from app.services.schedule_occurrences import to_app_local

_PENDING = PaymentIntentStatus.PENDING.value
_SUCCEEDED = PaymentIntentStatus.PROVIDER_SUCCEEDED.value
_FAILED = PaymentIntentStatus.PROVIDER_FAILED.value
_CANCELLED = PaymentIntentStatus.CANCELLED.value
_CONFIRMED = PaymentIntentStatus.CONFIRMED.value
_RECONCILIATION = ProviderEventOutcome.RECONCILIATION_REQUIRED.value

#: The fixed page size of an invoice's intent history and of the overview.
PAGE_SIZE = 20

#: How many of an intent's newest provider events its page shows.
EVENT_PAGE_SIZE = 20

_PUBLIC_ID_MAX_LENGTH = 36

STATUS_LABELS = {
    _PENDING: "Pending",
    _SUCCEEDED: "Browser-observed success (awaiting signed webhook)",
    _FAILED: "Failed",
    _CANCELLED: "Cancelled",
    _CONFIRMED: "Confirmed by signed webhook",
}
PROVIDER_LABELS = {"mock": "Mock/Sandbox"}
EVENT_TYPE_LABELS = {
    ProviderEventType.PAYMENT_SUCCEEDED.value: "Payment succeeded",
    ProviderEventType.PAYMENT_FAILED.value: "Payment failed",
}
OUTCOME_LABELS = {
    ProviderEventOutcome.CONFIRMED.value: "Payment confirmed",
    ProviderEventOutcome.FAILED.value: "Intent failed",
    ProviderEventOutcome.DUPLICATE.value: "Duplicate -- no change",
    ProviderEventOutcome.IGNORED_TERMINAL.value: "Ignored -- intent already cancelled",
    _RECONCILIATION: "Reconciliation required -- no payment recorded",
}

#: The only values the overview status filter accepts; anything else is
#: dropped.
STATUS_FILTERS = tuple(STATUS_LABELS)


def _public_id_ok(value):
    return bool(value) and len(value) <= _PUBLIC_ID_MAX_LENGTH


def _local(tz_name, moment):
    return None if moment is None else to_app_local(tz_name, moment)


def normalize_intent_status_filter(value):
    """One of :data:`STATUS_FILTERS`, or ``None`` for "any status"."""
    value = (value or "").strip()
    return value if value in STATUS_FILTERS else None


# ---------------------------------------------------------------------------
# One invoice's intents
# ---------------------------------------------------------------------------


def invoice_intent_rows(invoice_id):
    """Every intent of `invoice_id`, ascending internal id, at most one past
    :data:`~app.models.payment_intent.MAX_INVOICE_PAYMENT_INTENTS`. A pre-lock
    read: it decides what a page shows and what a token binds, never whether
    a write is allowed."""
    return (
        PaymentIntent.query.filter(PaymentIntent.invoice_id == invoice_id)
        .order_by(PaymentIntent.id.asc())
        .limit(MAX_INVOICE_PAYMENT_INTENTS + 1)
        .all()
    )


def invoice_intent(invoice_id, intent_public_id):
    """One intent by ``public_id`` **inside** `invoice_id`, or ``None``."""
    if not _public_id_ok(intent_public_id):
        return None
    return PaymentIntent.query.filter(
        PaymentIntent.invoice_id == invoice_id,
        PaymentIntent.public_id == intent_public_id,
    ).first()


def intent_history_page(invoice_id, page):
    """``(rows, has_next)`` for one page of `invoice_id`'s intents, newest
    first (``id DESC``), ``LIMIT PAGE_SIZE + 1`` and no ``COUNT``."""
    rows = (
        PaymentIntent.query.filter(PaymentIntent.invoice_id == invoice_id)
        .order_by(PaymentIntent.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


def intent_account_ids(rows):
    """Every account an intent page names."""
    ids = []
    for row in rows:
        ids += [row.created_by_id, row.provider_result_by_id, row.cancelled_by_id]
    return ids


def build_intent_view(row, names, tz_name="UTC", awaiting_reconciliation=False):
    """One intent as a page shows it. No internal id and no idempotency key
    survives."""
    return {
        "public_id": row.public_id,
        "status": row.status,
        "status_label": STATUS_LABELS.get(row.status, row.status),
        "is_pending": row.status == _PENDING,
        "is_provider_succeeded": row.status == _SUCCEEDED,
        "is_provider_failed": row.status == _FAILED,
        "is_cancelled": row.status == _CANCELLED,
        "is_confirmed": row.status == _CONFIRMED,
        "is_active": row.status in ACTIVE_PAYMENT_INTENT_STATUSES,
        "has_provider_result": row.provider_result_at is not None,
        "awaiting_reconciliation": awaiting_reconciliation,
        "provider_label": PROVIDER_LABELS.get(row.provider, row.provider),
        "provider_reference": row.provider_reference,
        "amount_text": format_amount(row.amount),
        "currency_code": row.currency_code,
        "created_local": _local(tz_name, row.created_at),
        "created_by_name": names.get(row.created_by_id),
        "provider_result_local": _local(tz_name, row.provider_result_at),
        "provider_result_by_name": names.get(row.provider_result_by_id),
        "terminal_local": _local(tz_name, row.terminal_at),
        "cancelled_by_name": names.get(row.cancelled_by_id),
    }


def build_intent_history_view(rows, names, tz_name="UTC", reconciliation=frozenset()):
    return [
        build_intent_view(row, names, tz_name, awaiting_reconciliation=row.id in reconciliation)
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Verified provider events (Phase 5 / M07)
# ---------------------------------------------------------------------------


def intents_awaiting_reconciliation(intent_ids):
    """The ids among `intent_ids` with a provider event awaiting
    reconciliation, from one query."""
    ids = [row_id for row_id in intent_ids if row_id is not None]
    if not ids:
        return frozenset()
    return frozenset(
        row.payment_intent_id
        for row in db.session.query(PaymentProviderEvent.payment_intent_id)
        .filter(
            PaymentProviderEvent.payment_intent_id.in_(ids),
            PaymentProviderEvent.outcome == _RECONCILIATION,
        )
        .distinct()
        .all()
    )


def invoice_reconciliation_required(invoice_id):
    """Whether any provider event of `invoice_id`'s intents awaits
    reconciliation. One query."""
    query = (
        db.session.query(PaymentProviderEvent.id)
        .join(PaymentIntent, PaymentIntent.id == PaymentProviderEvent.payment_intent_id)
        .filter(
            PaymentIntent.invoice_id == invoice_id,
            PaymentProviderEvent.outcome == _RECONCILIATION,
        )
    )
    return bool(db.session.query(query.exists()).scalar())


def intent_provider_events(intent_id):
    """``(rows, truncated)``: `intent_id`'s newest provider events (``id
    DESC``), at most :data:`EVENT_PAGE_SIZE`, and whether older ones exist.
    One query; no ``COUNT``."""
    rows = (
        PaymentProviderEvent.query.filter(PaymentProviderEvent.payment_intent_id == intent_id)
        .order_by(PaymentProviderEvent.id.desc())
        .limit(EVENT_PAGE_SIZE + 1)
        .all()
    )
    return rows[:EVENT_PAGE_SIZE], len(rows) > EVENT_PAGE_SIZE


def build_provider_event_view(rows, tz_name="UTC"):
    """Presentation dicts for provider events. No internal id, provider event
    id, payload digest or provider reference survives."""
    return [
        {
            "public_id": row.public_id,
            "event_type": row.event_type,
            "event_type_label": EVENT_TYPE_LABELS.get(row.event_type, row.event_type),
            "outcome": row.outcome,
            "outcome_label": OUTCOME_LABELS.get(row.outcome, row.outcome),
            "needs_reconciliation": row.outcome == _RECONCILIATION,
            "amount_text": format_amount(row.amount),
            "currency_code": row.currency_code,
            "provider_occurred_local": _local(tz_name, row.provider_occurred_at),
            "received_local": _local(tz_name, row.received_at),
            "processed_local": _local(tz_name, row.processed_at),
        }
        for row in rows
    ]


def intent_collection(intent_id):
    """``(payment_public_id, receipt_public_id, receipt_number)`` of the online
    collection a confirmed intent created, or ``None``. One query."""
    row = (
        db.session.query(
            PaymentTransaction.public_id, Receipt.public_id, Receipt.receipt_number
        )
        .outerjoin(Receipt, Receipt.payment_transaction_id == PaymentTransaction.id)
        .filter(PaymentTransaction.payment_intent_id == intent_id)
        .first()
    )
    return None if row is None else tuple(row)


# ---------------------------------------------------------------------------
# The sandbox intent overview
# ---------------------------------------------------------------------------


def intents_overview_page(page, status=None):
    """``(rows, has_next)`` for one page of every intent, newest first
    (``id DESC``). `status` must already be normalized. One query: the
    invoice chain, the Student and the creating account are joined in."""
    creator = aliased(User)
    student = aliased(User)
    awaiting = (
        db.session.query(PaymentProviderEvent.id)
        .filter(
            PaymentProviderEvent.payment_intent_id == PaymentIntent.id,
            PaymentProviderEvent.outcome == _RECONCILIATION,
        )
        .exists()
    )
    query = (
        db.session.query(
            awaiting.label("awaiting_reconciliation"),
            PaymentIntent.public_id,
            PaymentIntent.provider,
            PaymentIntent.status,
            PaymentIntent.amount,
            PaymentIntent.currency_code,
            PaymentIntent.created_at,
            creator.full_name.label("created_by_name"),
            Invoice.public_id.label("invoice_public_id"),
            Invoice.invoice_number,
            StudentFeeAssignment.public_id.label("assignment_public_id"),
            Enrollment.public_id.label("enrollment_public_id"),
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            student.full_name.label("student_name"),
        )
        .select_from(PaymentIntent)
        .join(Invoice, Invoice.id == PaymentIntent.invoice_id)
        .join(StudentFeeAssignment, StudentFeeAssignment.id == Invoice.student_fee_assignment_id)
        .join(Enrollment, Enrollment.id == StudentFeeAssignment.enrollment_id)
        .join(Group, Group.id == Enrollment.group_id)
        .join(student, student.id == Enrollment.student_id)
        .join(creator, creator.id == PaymentIntent.created_by_id)
    )
    if status is not None:
        query = query.filter(PaymentIntent.status == status)
    rows = (
        query.order_by(PaymentIntent.id.desc())
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
            "provider_label": PROVIDER_LABELS.get(row.provider, row.provider),
            "status": row.status,
            "status_label": STATUS_LABELS.get(row.status, row.status),
            "awaiting_reconciliation": bool(row.awaiting_reconciliation),
            "amount_text": format_amount(row.amount),
            "currency_code": row.currency_code,
            "created_local": _local(tz_name, row.created_at),
            "created_by_name": row.created_by_name,
            "invoice_public_id": row.invoice_public_id,
            "invoice_number": row.invoice_number,
            "assignment_public_id": row.assignment_public_id,
            "enrollment_public_id": row.enrollment_public_id,
            "group_public_id": row.group_public_id,
            "group_name": row.group_name,
            "student_name": row.student_name,
        }
        for row in rows
    ]
