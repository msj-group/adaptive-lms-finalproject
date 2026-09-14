import json
import re
from decimal import Decimal, Inexact, localcontext

from sqlalchemy import event
from sqlalchemy.orm import Session, validates

from app.extensions import db
from app.models.enums import (
    InvoiceItemKind,
    InvoiceItemStatus,
    InvoiceStatus,
    PaymentAuditEventKind,
)
from app.models.fee_plan import TEXT_CONTROL, TEXT_MISSING, TEXT_TOO_LONG, _forbidden
from app.models.invoice import FinancialHistoryError, invoice_number_is_valid
from app.models.invoice_item import MAX_INVOICE_ITEM_ROWS, normalize_invoice_item_label
from app.services.money import CURRENCY_CODE, validate_amount

#: The ``reason`` column's own width.
INVOICE_AUDIT_REASON_MAX_LENGTH = 500

#: Marks the snapshot layout, so a later Part can add another layout without
#: reinterpreting this one.
INVOICE_SNAPSHOT_SCHEMA = "phase5-m04.invoice.v1"

_CREATED = PaymentAuditEventKind.INVOICE_DRAFT_CREATED.value

#: The kinds whose event must carry a human reason, and those that carry none.
REASON_REQUIRED_KINDS = frozenset(
    {
        PaymentAuditEventKind.INVOICE_ISSUED_EDITED.value,
        PaymentAuditEventKind.INVOICE_CANCELLED.value,
    }
)
_REASONLESS_KINDS = frozenset(kind.value for kind in PaymentAuditEventKind) - REASON_REQUIRED_KINDS

_SNAPSHOT_KEYS = frozenset(
    {
        "schema",
        "invoice_public_id",
        "status",
        "invoice_number",
        "student_fee_assignment_public_id",
        "currency_code",
        "total",
        "items",
    }
)
_ITEM_KEYS = frozenset({"public_id", "kind", "label", "status", "amount"})
_AMOUNT_TEXT = re.compile(r"[0-9]+\.[0-9]{4}")
_PUBLIC_ID_MAX_LENGTH = 36
_SNAPSHOT_QUANTUM = Decimal("0.0001")

_INVOICE_STATUSES = frozenset(status.value for status in InvoiceStatus)
_ITEM_KINDS = frozenset(kind.value for kind in InvoiceItemKind)
_ITEM_STATUSES = frozenset(status.value for status in InvoiceItemStatus)
_ITEM_ACTIVE = InvoiceItemStatus.ACTIVE.value


def normalize_audit_reason(raw, required=True):
    """``(reason_or_None, error_code)`` for one human reason.

    Plain text: line endings become ``\\n``, interior newlines and tabs are
    kept, every other control, bidi or separator character is rejected, and
    the outer whitespace is stripped. Blank is missing, which is an error
    only when `required`.
    """
    text = (raw if isinstance(raw, str) else "").replace("\r\n", "\n").replace("\r", "\n")
    if any(_forbidden(ch, allowed=frozenset("\n\t")) for ch in text):
        return None, TEXT_CONTROL
    text = text.strip()
    if not text:
        return (None, TEXT_MISSING) if required else (None, None)
    if len(text) > INVOICE_AUDIT_REASON_MAX_LENGTH:
        return None, TEXT_TOO_LONG
    return text, None


def snapshot_amount_text(value):
    """``'1250.5000'`` -- an exact ``Decimal`` at four places, never rounded."""
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("A snapshot amount must be a finite, non-negative Decimal")
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            return format(value.quantize(_SNAPSHOT_QUANTUM), "f")
    except Inexact:
        raise ValueError("A snapshot amount has more than four decimal places") from None


def canonical_snapshot_json(snapshot):
    """The one canonical text of a snapshot: sorted keys, no insignificant
    whitespace, no ``NaN``."""
    return json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _public_id_ok(value):
    return isinstance(value, str) and 0 < len(value) <= _PUBLIC_ID_MAX_LENGTH


def _item_error(item):
    if not isinstance(item, dict) or set(item) != _ITEM_KEYS:
        return "an item has the wrong keys"
    if not _public_id_ok(item["public_id"]):
        return "an item public id is invalid"
    if item["kind"] not in _ITEM_KINDS or item["status"] not in _ITEM_STATUSES:
        return "an item kind or status is invalid"
    label, error = normalize_invoice_item_label(item["label"])
    if error is not None or label != item["label"]:
        return "an item label is not normalized plain text"
    amount = item["amount"]
    if not isinstance(amount, str) or _AMOUNT_TEXT.fullmatch(amount) is None:
        return "an item amount is not exact four-place text"
    try:
        if snapshot_amount_text(validate_amount(amount)) != amount:
            return "an item amount is not canonical"
    except ValueError:
        return "an item amount is out of range"
    return None


def validate_invoice_snapshot(snapshot):
    """The canonical copy of one server-built invoice snapshot, or raise
    ``ValueError``.

    Proves the exact layout -- no missing, extra or nested key, no internal
    id, no client JSON -- and that ``total`` is the exact sum of the active
    lines. Business rules (at least one line, distinct labels) are the
    routes'; a snapshot only records state.
    """
    if not isinstance(snapshot, dict) or set(snapshot) != _SNAPSHOT_KEYS:
        raise ValueError("An invoice snapshot has the wrong keys")
    if snapshot["schema"] != INVOICE_SNAPSHOT_SCHEMA:
        raise ValueError("An invoice snapshot has an unknown schema")
    if not _public_id_ok(snapshot["invoice_public_id"]) or not _public_id_ok(
        snapshot["student_fee_assignment_public_id"]
    ):
        raise ValueError("An invoice snapshot public id is invalid")
    status, number = snapshot["status"], snapshot["invoice_number"]
    if status not in _INVOICE_STATUSES:
        raise ValueError("An invoice snapshot status is invalid")
    if number is not None and not invoice_number_is_valid(number):
        raise ValueError("An invoice snapshot number is invalid")
    if (status == InvoiceStatus.DRAFT.value and number is not None) or (
        status == InvoiceStatus.ISSUED.value and number is None
    ):
        raise ValueError("An invoice snapshot number disagrees with its status")
    if snapshot["currency_code"] != CURRENCY_CODE:
        raise ValueError("An invoice snapshot currency is invalid")
    items = snapshot["items"]
    if not isinstance(items, list) or len(items) > MAX_INVOICE_ITEM_ROWS:
        raise ValueError("An invoice snapshot has an invalid item list")
    for item in items:
        error = _item_error(item)
        if error is not None:
            raise ValueError(f"An invoice snapshot is invalid: {error}")
    if len({item["public_id"] for item in items}) != len(items):
        raise ValueError("An invoice snapshot repeats an item")
    total = snapshot["total"]
    active_total = sum(
        (Decimal(item["amount"]) for item in items if item["status"] == _ITEM_ACTIVE),
        Decimal(0),
    )
    if (
        not isinstance(total, str)
        or _AMOUNT_TEXT.fullmatch(total) is None
        or snapshot_amount_text(active_total) != total
    ):
        raise ValueError("An invoice snapshot total is not the exact sum of its active items")
    return json.loads(canonical_snapshot_json(snapshot))


_KIND_VALUES = tuple(kind.value for kind in PaymentAuditEventKind)
_KIND_CHECK_SQL = "kind IN (" + ", ".join(f"'{v}'" for v in _KIND_VALUES) + ")"


def _in_list(values):
    return "(" + ", ".join(f"'{v}'" for v in sorted(values)) + ")"


_VERSIONS_POSITIVE_SQL = (
    "invoice_version_after > 0"
    " AND (invoice_version_before IS NULL OR invoice_version_before > 0)"
)

#: A draft is created at version 1 from nothing; every other event moves the
#: invoice by exactly one.
_VERSION_TRANSITION_SQL = (
    f"(kind = '{_CREATED}' AND invoice_version_before IS NULL AND invoice_version_after = 1)"
    f" OR (kind <> '{_CREATED}' AND invoice_version_before IS NOT NULL"
    " AND invoice_version_after = invoice_version_before + 1)"
)

_SNAPSHOTS_PRESENT_SQL = (
    f"(kind = '{_CREATED}' AND before_snapshot IS NULL AND after_snapshot IS NOT NULL)"
    f" OR (kind <> '{_CREATED}' AND before_snapshot IS NOT NULL AND after_snapshot IS NOT NULL)"
)

_REASON_REQUIRED_SQL = (
    f"(kind IN {_in_list(REASON_REQUIRED_KINDS)} AND reason IS NOT NULL AND LENGTH(reason) > 0)"
    f" OR (kind IN {_in_list(_REASONLESS_KINDS)} AND reason IS NULL)"
)


class PaymentAuditEvent(db.Model):
    """One append-only entry of the financial audit trail (Phase 5 / M04).

    **Every invoice movement writes exactly one**, in the same transaction:
    draft creation, each line change, issue and cancellation. The event names
    the invoice, the acting Administrator, its kind, the whole-second UTC
    moment, the invoice ``version`` before and after, an optional human
    reason (required for a post-issue change and a cancellation) and the
    complete visible financial state before and after as canonical JSON.

    **Snapshots are built by the server, never submitted.** See
    :func:`validate_invoice_snapshot`: public ids, statuses, the number,
    currency, exact amount text and the exact total -- no internal id, card
    or bank data, token, session value or client JSON.

    **Append-only.** There is no route, form, service operation, relationship
    or cascade that edits or deletes an event, and the ORM guards refuse an
    update, a delete and a bulk ``UPDATE`` / ``DELETE`` statement. Raw SQL is
    outside those guards; no trigger exists, deliberately.

    **No public id.** An event is never addressed on its own: it is read only
    through its invoice's page, as the project's other append-only trail
    (:class:`~app.models.file_access_log.FileAccessLog`) is.

    Database invariants (final defense only):

    - ``ck_payment_audit_events_kind_valid``;
    - ``ck_payment_audit_events_versions_positive`` and
      ``ck_payment_audit_events_version_transition``;
    - ``ck_payment_audit_events_snapshots_present``;
    - ``ck_payment_audit_events_reason_required``.

    That the actor was an active Administrator and that a snapshot has the
    right shape are proved by the application.

    Indexes: ``ix_payment_audit_events_invoice_id_id`` (``invoice_id``,
    ``id``) -- one invoice's timeline in id order and the foreign key;
    ``ix_payment_audit_events_actor_id``. **No MySQL execution plan has been
    measured for this table.**
    """

    __tablename__ = "payment_audit_events"
    __table_args__ = (
        db.CheckConstraint(_KIND_CHECK_SQL, name="ck_payment_audit_events_kind_valid"),
        db.CheckConstraint(_VERSIONS_POSITIVE_SQL, name="ck_payment_audit_events_versions_positive"),
        db.CheckConstraint(
            _VERSION_TRANSITION_SQL, name="ck_payment_audit_events_version_transition"
        ),
        db.CheckConstraint(
            _SNAPSHOTS_PRESENT_SQL, name="ck_payment_audit_events_snapshots_present"
        ),
        db.CheckConstraint(_REASON_REQUIRED_SQL, name="ck_payment_audit_events_reason_required"),
        db.Index("ix_payment_audit_events_invoice_id_id", "invoice_id", "id"),
        db.Index("ix_payment_audit_events_actor_id", "actor_id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    invoice_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("invoices.id"),
        nullable=False,
    )
    actor_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    kind = db.Column(db.String(40), nullable=False)
    occurred_at = db.Column(db.DateTime, nullable=False)
    invoice_version_before = db.Column(db.Integer, nullable=True)
    invoice_version_after = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(INVOICE_AUDIT_REASON_MAX_LENGTH), nullable=True)
    #: ``none_as_null``: a creation's absent "before" is SQL NULL, never the
    #: JSON literal ``null``.
    before_snapshot = db.Column(db.JSON(none_as_null=True), nullable=True)
    after_snapshot = db.Column(db.JSON(none_as_null=True), nullable=False)

    @validates("kind")
    def validate_kind(self, _key, value):
        if value not in _KIND_VALUES:
            raise ValueError(f"Invalid payment audit event kind: {value}")
        return value

    @validates("invoice_version_before")
    def validate_version_before(self, _key, value):
        if value is None:
            return value
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("An audit event version must be a positive integer")
        return value

    @validates("invoice_version_after")
    def validate_version_after(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("An audit event version must be a positive integer")
        return value

    @validates("reason")
    def validate_reason(self, _key, value):
        if value is None:
            return value
        normalized, error = normalize_audit_reason(value)
        if error is not None or normalized != value:
            raise ValueError("An audit reason must be normalized plain text or None")
        return value

    @validates("before_snapshot")
    def validate_before_snapshot(self, _key, value):
        return None if value is None else validate_invoice_snapshot(value)

    @validates("after_snapshot")
    def validate_after_snapshot(self, _key, value):
        return validate_invoice_snapshot(value)


@event.listens_for(PaymentAuditEvent, "before_update")
def _refuse_editing_an_audit_event(_mapper, _connection, _target):
    raise FinancialHistoryError("A payment audit event is append-only")


@event.listens_for(PaymentAuditEvent, "before_delete")
def _refuse_deleting_an_audit_event(_mapper, _connection, _target):
    raise FinancialHistoryError("A payment audit event is never deleted")


_NO_BULK_DELETE = frozenset(
    {"invoices", "invoice_items", "payment_audit_events", "invoice_number_sequences"}
)
_NO_BULK_UPDATE = frozenset({"payment_audit_events"})


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_rewrites_of_financial_history(orm_execute_state):
    """Mapper events do not see ``session.execute(update(...))`` or
    ``delete(...)``; this refuses those statements for the tables whose rows
    are never deleted, and for the audit trail, whose rows never change."""
    if orm_execute_state.is_delete:
        protected = _NO_BULK_DELETE
    elif orm_execute_state.is_update:
        protected = _NO_BULK_UPDATE
    else:
        return
    table = getattr(orm_execute_state.statement, "table", None)
    if getattr(table, "name", None) in protected:
        raise FinancialHistoryError(f"{table.name} rows cannot be rewritten in bulk")
