"""Server-built invoice snapshots and the one audit-event writer
(Phase 5 / M04).

Flask-independent: no ``request``, ``abort``, ``flash`` or template. The
routes in ``app/blueprints/admin/invoices.py`` call :func:`record_invoice_event`
exactly once per invoice movement, after their locks and before their commit,
so the event and the change it describes commit or roll back together.

**A snapshot is never submitted.** :func:`build_invoice_snapshot` reads the
invoice and its lines -- rows the request locked or read after the invoice
lock -- and emits the complete visible financial state: public ids, the
lifecycle state, the number, the currency, every line's kind, label, status
and exact amount text, and the exact total of the active lines. There is no
internal id, name, token, session value, reason or client value in it, and
:func:`~app.models.payment_audit_event.validate_invoice_snapshot` proves the
layout before anything is stored.

**Nothing here edits or deletes an event**, and nothing ever will: the
module's only write is an insert.
"""

from app.extensions import db
from app.models import (
    InvoiceItemStatus,
    InvoiceStatus,
    PaymentAuditEvent,
    PaymentAuditEventKind,
    UserRole,
    UserStatus,
)
from app.models.payment_audit_event import (
    INVOICE_SNAPSHOT_SCHEMA,
    REASON_REQUIRED_KINDS,
    normalize_audit_reason,
    snapshot_amount_text,
    validate_invoice_snapshot,
)
from app.services.money import sum_amounts

_ITEM_ACTIVE = InvoiceItemStatus.ACTIVE.value
_DRAFT = InvoiceStatus.DRAFT.value
_ISSUED = InvoiceStatus.ISSUED.value
_CANCELLED = InvoiceStatus.CANCELLED.value
_ADMINISTRATOR = UserRole.ADMINISTRATOR.value
_USER_ACTIVE = UserStatus.ACTIVE.value

CREATED = PaymentAuditEventKind.INVOICE_DRAFT_CREATED.value
DRAFT_EDITED = PaymentAuditEventKind.INVOICE_DRAFT_EDITED.value
ISSUED = PaymentAuditEventKind.INVOICE_ISSUED.value
ISSUED_EDITED = PaymentAuditEventKind.INVOICE_ISSUED_EDITED.value
CANCELLED = PaymentAuditEventKind.INVOICE_CANCELLED.value

#: For each kind: the invoice statuses the "before" snapshot may show (``None``
#: for a creation, which has no "before") and the one status "after" shows.
_STATUS_TRANSITIONS = {
    CREATED: (None, _DRAFT),
    DRAFT_EDITED: ((_DRAFT,), _DRAFT),
    ISSUED: ((_DRAFT,), _ISSUED),
    ISSUED_EDITED: ((_ISSUED,), _ISSUED),
    CANCELLED: ((_DRAFT, _ISSUED), _CANCELLED),
}


def build_invoice_snapshot(invoice, assignment_public_id, items):
    """The canonical snapshot of `invoice` with **all** of its `items` --
    active and removed -- in ascending internal id order.

    Every item must already have its internal id (flush a new one first); the
    id orders the lines and is then dropped.
    """
    rows = sorted(items, key=lambda row: row.id)
    snapshot = {
        "schema": INVOICE_SNAPSHOT_SCHEMA,
        "invoice_public_id": invoice.public_id,
        "status": invoice.status,
        "invoice_number": invoice.invoice_number,
        "student_fee_assignment_public_id": assignment_public_id,
        "currency_code": invoice.currency_code,
        "total": snapshot_amount_text(
            sum_amounts([row.amount for row in rows if row.status == _ITEM_ACTIVE])
        ),
        "items": [
            {
                "public_id": row.public_id,
                "kind": row.kind,
                "label": row.label,
                "status": row.status,
                "amount": snapshot_amount_text(row.amount),
            }
            for row in rows
        ],
    }
    return validate_invoice_snapshot(snapshot)


def edit_event_kind(invoice_status):
    """The event kind of a line change on an invoice in `invoice_status`."""
    if invoice_status == _DRAFT:
        return DRAFT_EDITED
    if invoice_status == _ISSUED:
        return ISSUED_EDITED
    raise ValueError("Only a draft or issued invoice can be edited")


def record_invoice_event(
    *, invoice, actor, kind, version_before, before_snapshot, after_snapshot, reason, moment
):
    """Add the one :class:`PaymentAuditEvent` for a change already applied to
    `invoice` in this transaction, or raise ``ValueError`` and add nothing.

    `actor` is the **locked** acting account, re-proved here as an active
    Administrator. `invoice.version` must already be the new version. The
    reason must be present, normalized text exactly when the kind requires
    one, and absent otherwise. Both snapshots must describe `invoice`, show
    the statuses the kind allows, and differ.
    """
    if actor is None or actor.role != _ADMINISTRATOR or actor.status != _USER_ACTIVE:
        raise ValueError("An invoice audit event needs an active acting Administrator")
    if kind not in _STATUS_TRANSITIONS:
        raise ValueError(f"Unknown invoice audit event kind: {kind}")
    if invoice is None or invoice.id is None:
        raise ValueError("An invoice audit event needs a stored invoice")

    if kind == CREATED:
        if version_before is not None or before_snapshot is not None or invoice.version != 1:
            raise ValueError("A draft is created at version 1 from nothing")
    elif (
        not isinstance(version_before, int)
        or isinstance(version_before, bool)
        or before_snapshot is None
        or invoice.version != version_before + 1
    ):
        raise ValueError("Every later invoice event moves the version by exactly one")

    if kind in REASON_REQUIRED_KINDS:
        normalized, error = normalize_audit_reason(reason)
        if error is not None or normalized != reason:
            raise ValueError("This invoice change needs a normalized, non-empty reason")
    elif reason is not None:
        raise ValueError("This invoice change carries no reason")

    before_statuses, after_status = _STATUS_TRANSITIONS[kind]
    after = validate_invoice_snapshot(after_snapshot)
    if (after["invoice_public_id"], after["status"], after["invoice_number"]) != (
        invoice.public_id,
        invoice.status,
        invoice.invoice_number,
    ) or after["status"] != after_status:
        raise ValueError("The after snapshot does not describe the invoice as it now is")
    before = None
    if before_snapshot is not None:
        before = validate_invoice_snapshot(before_snapshot)
        if (
            before["invoice_public_id"] != invoice.public_id
            or before["status"] not in before_statuses
            or before == after
        ):
            raise ValueError("The before snapshot does not describe the change")

    event = PaymentAuditEvent(
        invoice_id=invoice.id,
        actor_id=actor.id,
        kind=kind,
        occurred_at=moment,
        invoice_version_before=version_before,
        invoice_version_after=invoice.version,
        reason=reason,
        before_snapshot=before,
        after_snapshot=after,
    )
    db.session.add(event)
    return event
