"""Read queries and presentation for Administrator payment intents
(Phase 5 / M06).

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
per page with every name joined in.
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
    StudentFeeAssignment,
    User,
)
from app.services.money import format_amount
from app.services.schedule_occurrences import to_app_local

_PENDING = PaymentIntentStatus.PENDING.value
_SUCCEEDED = PaymentIntentStatus.PROVIDER_SUCCEEDED.value
_FAILED = PaymentIntentStatus.PROVIDER_FAILED.value
_CANCELLED = PaymentIntentStatus.CANCELLED.value

#: The fixed page size of an invoice's intent history and of the overview.
PAGE_SIZE = 20

_PUBLIC_ID_MAX_LENGTH = 36

STATUS_LABELS = {
    _PENDING: "Pending",
    _SUCCEEDED: "Provider reported success",
    _FAILED: "Provider reported failure",
    _CANCELLED: "Cancelled",
}
PROVIDER_LABELS = {"mock": "Mock/Sandbox"}

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


def build_intent_view(row, names, tz_name="UTC"):
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
        "is_active": row.status in ACTIVE_PAYMENT_INTENT_STATUSES,
        "has_provider_result": row.status in (_SUCCEEDED, _FAILED),
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


def build_intent_history_view(rows, names, tz_name="UTC"):
    return [build_intent_view(row, names, tz_name) for row in rows]


# ---------------------------------------------------------------------------
# The sandbox intent overview
# ---------------------------------------------------------------------------


def intents_overview_page(page, status=None):
    """``(rows, has_next)`` for one page of every intent, newest first
    (``id DESC``). `status` must already be normalized. One query: the
    invoice chain, the Student and the creating account are joined in."""
    creator = aliased(User)
    student = aliased(User)
    query = (
        db.session.query(
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
