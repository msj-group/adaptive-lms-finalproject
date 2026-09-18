"""Read queries and presentation for Administrator invoices (Phase 5 / M04).

Flask-independent: explicit, column-projected queries returning rows or plain
presentation dicts, and no ``request``, ``abort`` or template. Route-level
404 / redirect handling belongs in ``app/blueprints/admin/invoices.py``.

**Every read here is reachable only by an active Administrator.** There is no
Student, Teacher, Researcher or public read of an invoice or an audit event
anywhere in M04.

**Nesting is part of every lookup.** An assignment is found only inside the
Enrollment in its URL, which is found only inside its Group and only when it
references a Student-role account; an invoice only inside its assignment; a
line only inside its invoice. Anything else is ``None``, which the route turns
into a 404 without saying whether the identifier exists elsewhere.

**Internal ids stay inside the service layer.** Rows carry them so a route can
lock and a page can fetch totals or names in one keyed query; the ``build_*``
helpers drop them before anything reaches a template.

**Bounded, and free of N+1.** Every list is a fixed page of :data:`PAGE_SIZE`
with ``LIMIT PAGE_SIZE + 1`` and no ``COUNT``; totals, names and line counts
come from one keyed query each, so a page costs a fixed number of queries.

**Money is added in Python, never in SQL**, through
:func:`~app.services.money.sum_amounts`. An invoice total is never stored.
"""

from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import (
    MAX_INVOICE_ITEM_ROWS,
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    FeePlan,
    Group,
    Invoice,
    InvoiceItem,
    InvoiceItemStatus,
    InvoiceStatus,
    Level,
    PaymentAuditEvent,
    PaymentAuditEventKind,
    StudentFeeAssignment,
    StudentFeeAssignmentStatus,
    User,
    UserRole,
    UserStatus,
)
from app.models.payment_audit_event import PAYMENT_SNAPSHOT_SCHEMA
from app.services.fee_plan_queries import KIND_LABELS
from app.services.fee_plan_queries import STATUS_LABELS as PLAN_STATUS_LABELS
from app.services.invoice_transactions import fee_plan_invoiceable
from app.services.money import amount_input_text, format_amount, sum_amounts
from app.services.payment_queries import EVENT_KIND_LABELS as PAYMENT_EVENT_KIND_LABELS
from app.services.payment_queries import build_payment_event_state, describe_payment_event
from app.services.schedule_occurrences import to_app_local
from app.services.student_fee_assignment_queries import (
    STATUS_LABELS as ASSIGNMENT_STATUS_LABELS,
)

_DRAFT = InvoiceStatus.DRAFT.value
_ISSUED = InvoiceStatus.ISSUED.value
_CANCELLED = InvoiceStatus.CANCELLED.value
_OPEN = (_DRAFT, _ISSUED)
_ITEM_ACTIVE = InvoiceItemStatus.ACTIVE.value
_ITEM_REMOVED = InvoiceItemStatus.REMOVED.value
_ASSIGNED = StudentFeeAssignmentStatus.ASSIGNED.value
_ACADEMIC_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value

#: The fixed page size of every invoice list and timeline.
PAGE_SIZE = 20

_PUBLIC_ID_MAX_LENGTH = 36

STATUS_LABELS = {_DRAFT: "Draft", _ISSUED: "Issued", _CANCELLED: "Cancelled"}
ITEM_STATUS_LABELS = {_ITEM_ACTIVE: "Active", _ITEM_REMOVED: "Removed"}
EVENT_KIND_LABELS = {
    PaymentAuditEventKind.INVOICE_DRAFT_CREATED.value: "Draft created",
    PaymentAuditEventKind.INVOICE_DRAFT_EDITED.value: "Draft edited",
    PaymentAuditEventKind.INVOICE_ISSUED.value: "Issued",
    PaymentAuditEventKind.INVOICE_ISSUED_EDITED.value: "Issued invoice edited",
    PaymentAuditEventKind.INVOICE_CANCELLED.value: "Cancelled",
}

#: Why a draft cannot be created now, as a code. The route owns the wording.
BLOCK_ASSIGNMENT_CANCELLED = "assignment_cancelled"
BLOCK_OPEN_INVOICE = "open_invoice"
BLOCK_ENROLLMENT_INACTIVE = "enrollment_inactive"
BLOCK_STUDENT_INACTIVE = "student_inactive"
BLOCK_ACADEMIC_INACTIVE = "academic_inactive"
BLOCK_PLAN_UNAVAILABLE = "plan_unavailable"


def _public_id_ok(value):
    return bool(value) and len(value) <= _PUBLIC_ID_MAX_LENGTH


def _local(tz_name, moment):
    return None if moment is None else to_app_local(tz_name, moment)


def _page(query, page):
    rows = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1).all()
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


# ---------------------------------------------------------------------------
# The assignment in the URL
# ---------------------------------------------------------------------------


def assignment_invoice_context(group_public_id, enrollment_public_id, assignment_public_id):
    """One row describing the assignment, its fee plan, its Enrollment, its
    Student and its academic chain -- or ``None`` when any link of the URL
    does not nest inside the one before it.

    One query. A pre-lock read: it decides which rows a write locks and what
    a page shows, never whether a write is allowed.
    """
    if not all(
        _public_id_ok(value)
        for value in (group_public_id, enrollment_public_id, assignment_public_id)
    ):
        return None
    return (
        db.session.query(
            Group.id.label("group_id"),
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            Group.code.label("group_code"),
            Group.status.label("group_status"),
            AcademicTerm.id.label("academic_term_id"),
            AcademicTerm.name.label("term_name"),
            AcademicTerm.status.label("term_status"),
            Course.id.label("course_id"),
            Course.title.label("course_title"),
            Course.status.label("course_status"),
            Level.id.label("level_id"),
            Level.name.label("level_name"),
            Level.status.label("level_status"),
            Enrollment.id.label("enrollment_id"),
            Enrollment.public_id.label("enrollment_public_id"),
            Enrollment.status.label("enrollment_status"),
            User.id.label("student_id"),
            User.full_name.label("student_name"),
            User.email.label("student_email"),
            User.status.label("student_status"),
            StudentFeeAssignment.id.label("assignment_id"),
            StudentFeeAssignment.public_id.label("assignment_public_id"),
            StudentFeeAssignment.status.label("assignment_status"),
            StudentFeeAssignment.version.label("assignment_version"),
            FeePlan.id.label("fee_plan_id"),
            FeePlan.public_id.label("plan_public_id"),
            FeePlan.name.label("plan_name"),
            FeePlan.status.label("plan_status"),
            FeePlan.currency_code.label("plan_currency_code"),
            FeePlan.first_activated_at.label("plan_first_activated_at"),
            FeePlan.first_activated_by_id.label("plan_first_activated_by_id"),
        )
        .select_from(StudentFeeAssignment)
        .join(Enrollment, Enrollment.id == StudentFeeAssignment.enrollment_id)
        .join(Group, Group.id == Enrollment.group_id)
        .join(Course, Course.id == Group.course_id)
        .join(Level, Level.id == Course.level_id)
        .join(AcademicTerm, AcademicTerm.id == Group.academic_term_id)
        .join(User, User.id == Enrollment.student_id)
        .join(FeePlan, FeePlan.id == StudentFeeAssignment.fee_plan_id)
        .filter(
            Group.public_id == group_public_id,
            Enrollment.public_id == enrollment_public_id,
            StudentFeeAssignment.public_id == assignment_public_id,
            User.role == _STUDENT,
        )
        .first()
    )


def context_plan(context):
    """The context's plan columns in the shape
    :func:`~app.services.invoice_transactions.fee_plan_invoiceable` reads."""
    return SimpleNamespace(
        status=context.plan_status,
        first_activated_at=context.plan_first_activated_at,
        first_activated_by_id=context.plan_first_activated_by_id,
        currency_code=context.plan_currency_code,
    )


def build_assignment_view(context):
    """The facts every M04 page shows about its assignment. No internal id."""
    return {
        "public_id": context.assignment_public_id,
        "status": context.assignment_status,
        "status_label": ASSIGNMENT_STATUS_LABELS.get(
            context.assignment_status, context.assignment_status
        ),
        "plan_public_id": context.plan_public_id,
        "plan_name": context.plan_name,
        "plan_status": context.plan_status,
        "plan_status_label": PLAN_STATUS_LABELS.get(context.plan_status, context.plan_status),
    }


def assignment_has_open_invoice(assignment_id):
    """Whether `assignment_id` has a ``draft`` or ``issued`` invoice. A
    friendly preview; writes decide against locked rows."""
    query = db.session.query(Invoice.id).filter(
        Invoice.student_fee_assignment_id == assignment_id, Invoice.status.in_(_OPEN)
    )
    return bool(db.session.query(query.exists()).scalar())


def assignments_with_open_invoice(assignment_public_ids):
    """The subset of `assignment_public_ids` whose assignment holds a
    ``draft`` or ``issued`` invoice. One query, for the fee assignment history
    page, which shows public ids only."""
    ids = [value for value in assignment_public_ids if _public_id_ok(value)]
    if not ids:
        return set()
    rows = (
        db.session.query(StudentFeeAssignment.public_id)
        .join(Invoice, Invoice.student_fee_assignment_id == StudentFeeAssignment.id)
        .filter(StudentFeeAssignment.public_id.in_(ids), Invoice.status.in_(_OPEN))
        .distinct()
        .all()
    )
    return {row[0] for row in rows}


def draft_block_reason(
    assignment_status,
    has_open_invoice,
    enrollment_status,
    student_status,
    academic_statuses,
    plan_invoiceable,
):
    """The first reason a new draft cannot be created, as a ``BLOCK_*`` code,
    or ``None``. `academic_statuses` are the Group, Course, Level and Academic
    Term statuses. Used for the preview and, with locked values, for the
    authoritative decision."""
    if assignment_status != _ASSIGNED:
        return BLOCK_ASSIGNMENT_CANCELLED
    if has_open_invoice:
        return BLOCK_OPEN_INVOICE
    if enrollment_status != _ENROLLMENT_ACTIVE:
        return BLOCK_ENROLLMENT_INACTIVE
    if student_status != _USER_ACTIVE:
        return BLOCK_STUDENT_INACTIVE
    if any(status != _ACADEMIC_ACTIVE for status in academic_statuses):
        return BLOCK_ACADEMIC_INACTIVE
    if not plan_invoiceable:
        return BLOCK_PLAN_UNAVAILABLE
    return None


def context_draft_block_reason(context):
    """:func:`draft_block_reason` for a pre-lock context row."""
    return draft_block_reason(
        context.assignment_status,
        assignment_has_open_invoice(context.assignment_id),
        context.enrollment_status,
        context.student_status,
        (context.group_status, context.course_status, context.level_status, context.term_status),
        fee_plan_invoiceable(context_plan(context)),
    )


# ---------------------------------------------------------------------------
# One assignment's invoices
# ---------------------------------------------------------------------------


def invoice_history_page(assignment_id, page):
    """``(rows, has_next)`` for one page of the assignment's invoices, newest
    first (``id DESC``). One query: the issuing and cancelling accounts are
    joined in."""
    issuer = aliased(User)
    canceller = aliased(User)
    query = (
        db.session.query(
            Invoice.id,
            Invoice.public_id,
            Invoice.status,
            Invoice.invoice_number,
            Invoice.currency_code,
            Invoice.version,
            Invoice.created_at,
            Invoice.issued_at,
            Invoice.cancelled_at,
            issuer.full_name.label("issued_by_name"),
            canceller.full_name.label("cancelled_by_name"),
        )
        .select_from(Invoice)
        .outerjoin(issuer, issuer.id == Invoice.issued_by_id)
        .outerjoin(canceller, canceller.id == Invoice.cancelled_by_id)
        .filter(Invoice.student_fee_assignment_id == assignment_id)
        .order_by(Invoice.id.desc())
    )
    return _page(query, page)


def invoice_line_summaries(invoice_ids):
    """``{invoice_id: (active_line_count, exact_total)}`` for `invoice_ids`,
    from one query, added in Python ``Decimal``."""
    ids = list(invoice_ids)
    if not ids:
        return {}
    rows = (
        db.session.query(InvoiceItem.invoice_id, InvoiceItem.amount)
        .filter(InvoiceItem.invoice_id.in_(ids), InvoiceItem.status == _ITEM_ACTIVE)
        .order_by(InvoiceItem.invoice_id.asc(), InvoiceItem.id.asc())
        .all()
    )
    amounts = {invoice_id: [] for invoice_id in ids}
    for invoice_id, amount in rows:
        amounts[invoice_id].append(amount)
    return {
        invoice_id: (len(values), sum_amounts(values)) for invoice_id, values in amounts.items()
    }


def build_invoice_history_view(rows, summaries, tz_name="UTC"):
    """Presentation dicts for one history page. No internal id survives."""
    view = []
    for row in rows:
        count, total = summaries.get(row.id, (0, Decimal(0)))
        view.append(
            {
                "public_id": row.public_id,
                "status": row.status,
                "status_label": STATUS_LABELS.get(row.status, row.status),
                "invoice_number": row.invoice_number,
                "currency_code": row.currency_code,
                "line_count": count,
                "total_text": format_amount(total),
                "created_local": _local(tz_name, row.created_at),
                "issued_local": _local(tz_name, row.issued_at),
                "issued_by_name": row.issued_by_name,
                "cancelled_local": _local(tz_name, row.cancelled_at),
                "cancelled_by_name": row.cancelled_by_name,
            }
        )
    return view


# ---------------------------------------------------------------------------
# One invoice
# ---------------------------------------------------------------------------


def assignment_invoice(assignment_id, invoice_public_id):
    """One invoice by ``public_id`` **inside** `assignment_id`, or ``None``."""
    if not _public_id_ok(invoice_public_id):
        return None
    return Invoice.query.filter(
        Invoice.student_fee_assignment_id == assignment_id,
        Invoice.public_id == invoice_public_id,
    ).first()


def invoice_line(invoice_id, item_public_id):
    """One line by ``public_id`` **inside** `invoice_id`, or ``None``."""
    if not _public_id_ok(item_public_id):
        return None
    return InvoiceItem.query.filter(
        InvoiceItem.invoice_id == invoice_id, InvoiceItem.public_id == item_public_id
    ).first()


def invoice_lines(invoice_id):
    """Every line of `invoice_id`, ascending internal id, at most one past
    :data:`~app.models.invoice_item.MAX_INVOICE_ITEM_ROWS`."""
    return (
        InvoiceItem.query.filter(InvoiceItem.invoice_id == invoice_id)
        .order_by(InvoiceItem.id.asc())
        .limit(MAX_INVOICE_ITEM_ROWS + 1)
        .all()
    )


def active_lines(lines):
    return [line for line in lines if line.status == _ITEM_ACTIVE]


def account_names(user_ids):
    """``{user_id: full_name}`` for `user_ids`, from one query."""
    ids = {user_id for user_id in user_ids if user_id is not None}
    if not ids:
        return {}
    return dict(db.session.query(User.id, User.full_name).filter(User.id.in_(ids)).all())


def build_invoice_view(invoice, lines, tz_name="UTC"):
    """One invoice in full: its lifecycle, attribution, active lines with
    their exact total, and its removed lines. Two queries' worth of rows (the
    lines, and one keyed read of names). No internal id survives."""
    shown = lines[:MAX_INVOICE_ITEM_ROWS]
    names = account_names(
        [invoice.issued_by_id, invoice.cancelled_by_id]
        + [line.removed_by_id for line in shown]
    )
    active = active_lines(shown)
    return {
        "public_id": invoice.public_id,
        "status": invoice.status,
        "status_label": STATUS_LABELS.get(invoice.status, invoice.status),
        "is_draft": invoice.status == _DRAFT,
        "is_issued": invoice.status == _ISSUED,
        "is_cancelled": invoice.status == _CANCELLED,
        "invoice_number": invoice.invoice_number,
        "currency_code": invoice.currency_code,
        "version": invoice.version,
        "created_local": _local(tz_name, invoice.created_at),
        "updated_local": _local(tz_name, invoice.updated_at),
        "issued_local": _local(tz_name, invoice.issued_at),
        "issued_by_name": names.get(invoice.issued_by_id),
        "cancelled_local": _local(tz_name, invoice.cancelled_at),
        "cancelled_by_name": names.get(invoice.cancelled_by_id),
        "active_lines": [
            {
                "public_id": line.public_id,
                "kind": line.kind,
                "kind_label": KIND_LABELS.get(line.kind, line.kind),
                "label": line.label,
                "amount_text": format_amount(line.amount),
                "version": line.version,
            }
            for line in active
        ],
        "removed_lines": [
            {
                "kind_label": KIND_LABELS.get(line.kind, line.kind),
                "label": line.label,
                "amount_text": format_amount(line.amount),
                "removed_local": _local(tz_name, line.removed_at),
                "removed_by_name": names.get(line.removed_by_id),
            }
            for line in shown
            if line.status == _ITEM_REMOVED
        ],
        "active_count": len(active),
        "row_count": len(shown),
        "rows_truncated": len(lines) > MAX_INVOICE_ITEM_ROWS,
        "total_text": format_amount(sum_amounts([line.amount for line in active])),
    }


def line_form_data(line):
    """The ungrouped values a line's edit form is pre-filled with."""
    return {"kind": line.kind, "label": line.label, "amount": amount_input_text(line.amount)}


# ---------------------------------------------------------------------------
# The audit timeline
# ---------------------------------------------------------------------------


def audit_event_page(invoice_id, page):
    """``(rows, has_next)`` for one page of the invoice's audit events, newest
    first (``id DESC``). One query: the acting account is joined in."""
    actor = aliased(User)
    query = (
        db.session.query(
            PaymentAuditEvent.kind,
            PaymentAuditEvent.occurred_at,
            PaymentAuditEvent.invoice_version_before,
            PaymentAuditEvent.invoice_version_after,
            PaymentAuditEvent.reason,
            PaymentAuditEvent.before_snapshot,
            PaymentAuditEvent.after_snapshot,
            actor.full_name.label("actor_name"),
        )
        .select_from(PaymentAuditEvent)
        .join(actor, actor.id == PaymentAuditEvent.actor_id)
        .filter(PaymentAuditEvent.invoice_id == invoice_id)
        .order_by(PaymentAuditEvent.id.desc())
    )
    return _page(query, page)


def _amount_text(text):
    return format_amount(Decimal(text))


def _line_name(line):
    return f"{KIND_LABELS.get(line['kind'], line['kind'])} line “{line['label']}”"


def describe_snapshot_changes(before, after):
    """Plain sentences saying what one event changed, computed from its two
    server-built snapshots. Lines are matched by public id."""
    currency = after["currency_code"]
    if before is None:
        count = len([line for line in after["items"] if line["status"] == _ITEM_ACTIVE])
        return [
            f"Draft created with {count} line{'' if count == 1 else 's'} copied from the fee "
            f"plan, totalling {_amount_text(after['total'])} {currency}."
        ]
    changes = []
    if before["status"] != after["status"]:
        changes.append(
            f"Status changed from {STATUS_LABELS.get(before['status'], before['status'])} "
            f"to {STATUS_LABELS.get(after['status'], after['status'])}."
        )
    if after["invoice_number"] and before["invoice_number"] != after["invoice_number"]:
        changes.append(f"Invoice number {after['invoice_number']} allocated.")
    previous = {line["public_id"]: line for line in before["items"]}
    for line in after["items"]:
        old = previous.get(line["public_id"])
        if old is None:
            changes.append(f"Added {_line_name(line)} of {_amount_text(line['amount'])} {currency}.")
        elif old == line:
            continue
        elif old["status"] == _ITEM_ACTIVE and line["status"] == _ITEM_REMOVED:
            changes.append(f"Removed {_line_name(old)} of {_amount_text(old['amount'])} {currency}.")
        else:
            parts = []
            if old["kind"] != line["kind"]:
                parts.append(
                    f"kind from {KIND_LABELS.get(old['kind'], old['kind'])} "
                    f"to {KIND_LABELS.get(line['kind'], line['kind'])}"
                )
            if old["label"] != line["label"]:
                parts.append(f"label from “{old['label']}” to “{line['label']}”")
            if old["amount"] != line["amount"]:
                parts.append(
                    f"amount from {_amount_text(old['amount'])} "
                    f"to {_amount_text(line['amount'])} {currency}"
                )
            changes.append(f"Changed {_line_name(old)}: {'; '.join(parts)}.")
    if before["total"] != after["total"]:
        changes.append(
            f"Total changed from {_amount_text(before['total'])} "
            f"to {_amount_text(after['total'])} {currency}."
        )
    return changes


def _snapshot_lines(snapshot):
    if snapshot is None:
        return None
    return [
        {
            "kind_label": KIND_LABELS.get(line["kind"], line["kind"]),
            "label": line["label"],
            "status_label": ITEM_STATUS_LABELS.get(line["status"], line["status"]),
            "amount_text": _amount_text(line["amount"]),
        }
        for line in snapshot["items"]
    ]


def _invoice_timeline_entry(row, tz_name):
    return {
        "kind": row.kind,
        "kind_label": EVENT_KIND_LABELS.get(row.kind, row.kind),
        "is_payment_event": False,
        "actor_name": row.actor_name,
        "occurred_local": _local(tz_name, row.occurred_at),
        "version_before": row.invoice_version_before,
        "version_after": row.invoice_version_after,
        "reason": row.reason,
        "changes": describe_snapshot_changes(row.before_snapshot, row.after_snapshot),
        "before_lines": _snapshot_lines(row.before_snapshot),
        "after_lines": _snapshot_lines(row.after_snapshot),
        "after_total_text": _amount_text(row.after_snapshot["total"]),
        "after_status_label": STATUS_LABELS.get(
            row.after_snapshot["status"], row.after_snapshot["status"]
        ),
        "after_invoice_number": row.after_snapshot["invoice_number"],
    }


def _payment_timeline_entry(row, tz_name):
    after = row.after_snapshot
    return {
        "kind": row.kind,
        "kind_label": PAYMENT_EVENT_KIND_LABELS.get(row.kind, row.kind),
        "is_payment_event": True,
        "actor_name": row.actor_name,
        "occurred_local": _local(tz_name, row.occurred_at),
        "version_before": row.invoice_version_before,
        "version_after": row.invoice_version_after,
        "reason": row.reason,
        "changes": describe_payment_event(row.kind, row.before_snapshot, after),
        "before_lines": None,
        "after_lines": None,
        "after_balance": build_payment_event_state(after),
        "after_total_text": _amount_text(after["invoice_total"]),
        "after_status_label": STATUS_LABELS.get(after["invoice_status"], after["invoice_status"]),
        "after_invoice_number": after["invoice_number"],
    }


def build_timeline_view(rows, tz_name="UTC"):
    """Presentation dicts for one timeline page. Snapshots are rendered as
    plain text only; no internal id survives. Since Phase 5 / M05 the trail
    also holds payment and receipt events, described from their own payment
    snapshots: what moved, and the balance after it."""
    return [
        _payment_timeline_entry(row, tz_name)
        if row.after_snapshot.get("schema") == PAYMENT_SNAPSHOT_SCHEMA
        else _invoice_timeline_entry(row, tz_name)
        for row in rows
    ]
