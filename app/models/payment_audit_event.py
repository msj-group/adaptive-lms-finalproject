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
    PaymentMethod,
    PaymentTransactionKind,
    PaymentTransactionStatus,
    ReceiptStatus,
)
from app.models.fee_plan import TEXT_CONTROL, TEXT_MISSING, TEXT_TOO_LONG, _forbidden
from app.models.invoice import FinancialHistoryError, invoice_number_is_valid
from app.models.invoice_item import MAX_INVOICE_ITEM_ROWS, normalize_invoice_item_label
from app.models.receipt_number_sequence import receipt_number_is_valid
from app.services.money import CURRENCY_CODE, validate_amount

#: The ``reason`` column's own width.
INVOICE_AUDIT_REASON_MAX_LENGTH = 500

#: Marks the snapshot layout, so a later Part can add another layout without
#: reinterpreting this one.
INVOICE_SNAPSHOT_SCHEMA = "phase5-m04.invoice.v1"

#: The Phase 5 / M05 layout of a payment or receipt event's snapshots.
PAYMENT_SNAPSHOT_SCHEMA = "phase5-m05.payment.v1"

#: The Phase 5 / M07 layout of a system-origin online event's snapshots: the
#: M05 layout plus the payment intent and the verified provider event.
ONLINE_PAYMENT_SNAPSHOT_SCHEMA = "phase5-m07.online-payment.v1"

_K = PaymentAuditEventKind
_CREATED = _K.INVOICE_DRAFT_CREATED.value

#: Phase 5 / M04: the invoice's own movements.
INVOICE_EVENT_KINDS = frozenset(
    {
        _K.INVOICE_DRAFT_CREATED.value,
        _K.INVOICE_DRAFT_EDITED.value,
        _K.INVOICE_ISSUED.value,
        _K.INVOICE_ISSUED_EDITED.value,
        _K.INVOICE_CANCELLED.value,
    }
)

#: Phase 5 / M05: a payment transaction's movements. Each names the one
#: transaction it is about and no receipt.
PAYMENT_EVENT_KINDS = frozenset(
    {
        _K.PAYMENT_CASH_RECORDED.value,
        _K.PAYMENT_BANK_TRANSFER_RECORDED.value,
        _K.PAYMENT_BANK_TRANSFER_CONFIRMED.value,
        _K.PAYMENT_BANK_TRANSFER_REJECTED.value,
        _K.PAYMENT_REVERSED.value,
        _K.PAYMENT_ONLINE_CONFIRMED.value,
    }
)

#: Phase 5 / M05: a receipt's movements. Each names the receipt and the
#: collection it belongs to.
RECEIPT_EVENT_KINDS = frozenset(
    {_K.RECEIPT_ISSUED.value, _K.RECEIPT_VOIDED.value, _K.RECEIPT_ONLINE_ISSUED.value}
)

#: Phase 5 / M07: the only kinds with no acting Administrator. A verified,
#: signed provider webhook caused them; ``actor_id`` is NULL for them and for
#: nothing else.
SYSTEM_EVENT_KINDS = frozenset(
    {_K.PAYMENT_ONLINE_CONFIRMED.value, _K.RECEIPT_ONLINE_ISSUED.value}
)

#: The kinds whose event must carry a human reason, and those that carry none.
REASON_REQUIRED_KINDS = frozenset(
    {
        _K.INVOICE_ISSUED_EDITED.value,
        _K.INVOICE_CANCELLED.value,
        _K.PAYMENT_BANK_TRANSFER_REJECTED.value,
        _K.PAYMENT_REVERSED.value,
        _K.RECEIPT_VOIDED.value,
    }
)
_REASONLESS_KINDS = frozenset(kind.value for kind in PaymentAuditEventKind) - REASON_REQUIRED_KINDS
_INVOICE_CHANGE_KINDS = INVOICE_EVENT_KINDS - {_CREATED}
_PAYMENT_AND_RECEIPT_KINDS = PAYMENT_EVENT_KINDS | RECEIPT_EVENT_KINDS

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
_AMOUNT_TEXT_MAX_LENGTH = 32
_PUBLIC_ID_MAX_LENGTH = 36
_SNAPSHOT_QUANTUM = Decimal("0.0001")

_INVOICE_STATUSES = frozenset(status.value for status in InvoiceStatus)
_ITEM_KINDS = frozenset(kind.value for kind in InvoiceItemKind)
_ITEM_STATUSES = frozenset(status.value for status in InvoiceItemStatus)
_ITEM_ACTIVE = InvoiceItemStatus.ACTIVE.value

_PAYMENT_SNAPSHOT_KEYS = frozenset(
    {
        "schema",
        "invoice_public_id",
        "invoice_number",
        "invoice_status",
        "currency_code",
        "invoice_total",
        "paid_amount",
        "outstanding_amount",
        "payment",
        "receipt",
    }
)
_ONLINE_PAYMENT_SNAPSHOT_KEYS = _PAYMENT_SNAPSHOT_KEYS | {"online"}
_ONLINE_KEYS = frozenset({"payment_intent_public_id", "provider_event_public_id"})
_PAYMENT_KEYS = frozenset({"public_id", "kind", "method", "status", "amount", "reversal_of_public_id"})
_RECEIPT_KEYS = frozenset({"public_id", "receipt_number", "status"})
_PAYMENT_KINDS = frozenset(kind.value for kind in PaymentTransactionKind)
_PAYMENT_METHODS = frozenset(method.value for method in PaymentMethod)
_PAYMENT_STATUSES = frozenset(status.value for status in PaymentTransactionStatus)
_RECEIPT_STATUSES = frozenset(status.value for status in ReceiptStatus)
_COLLECTION = PaymentTransactionKind.COLLECTION.value
_CASH = PaymentMethod.CASH.value
_ONLINE = PaymentMethod.ONLINE.value
_CONFIRMED = PaymentTransactionStatus.CONFIRMED.value


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


# ---------------------------------------------------------------------------
# Phase 5 / M05: payment and receipt event snapshots
# ---------------------------------------------------------------------------


def _balance_text_ok(value):
    """Whether `value` is canonical, non-negative four-place amount text."""
    if (
        not isinstance(value, str)
        or len(value) > _AMOUNT_TEXT_MAX_LENGTH
        or _AMOUNT_TEXT.fullmatch(value) is None
    ):
        return False
    return snapshot_amount_text(Decimal(value)) == value


def _payment_error(payment):
    if not isinstance(payment, dict) or set(payment) != _PAYMENT_KEYS:
        return "the payment has the wrong keys"
    if not _public_id_ok(payment["public_id"]):
        return "the payment public id is invalid"
    kind, method, status = payment["kind"], payment["method"], payment["status"]
    if kind not in _PAYMENT_KINDS or method not in _PAYMENT_METHODS or status not in _PAYMENT_STATUSES:
        return "the payment kind, method or status is invalid"
    amount = payment["amount"]
    if not isinstance(amount, str) or _AMOUNT_TEXT.fullmatch(amount) is None:
        return "the payment amount is not exact four-place text"
    try:
        if snapshot_amount_text(validate_amount(amount)) != amount:
            return "the payment amount is not canonical"
    except ValueError:
        return "the payment amount is out of range"
    reversal_of = payment["reversal_of_public_id"]
    if kind == _COLLECTION:
        if reversal_of is not None:
            return "a collection reverses nothing"
        if method in (_CASH, _ONLINE) and status != _CONFIRMED:
            return "a cash or online collection is confirmed when it is recorded"
    elif (
        not _public_id_ok(reversal_of)
        or reversal_of == payment["public_id"]
        or status != _CONFIRMED
    ):
        return "a reversal is confirmed and names another collection"
    return None


def _receipt_error(receipt):
    if not isinstance(receipt, dict) or set(receipt) != _RECEIPT_KEYS:
        return "the receipt has the wrong keys"
    if not _public_id_ok(receipt["public_id"]):
        return "the receipt public id is invalid"
    if not receipt_number_is_valid(receipt["receipt_number"]):
        return "the receipt number is invalid"
    if receipt["status"] not in _RECEIPT_STATUSES:
        return "the receipt status is invalid"
    return None


def validate_payment_snapshot(snapshot, schema=PAYMENT_SNAPSHOT_SCHEMA):
    """The canonical copy of one server-built payment snapshot, or raise
    ``ValueError``.

    The layout is exact: the issued invoice's public id, number, status and
    currency; its exact total, paid and outstanding amounts as four-place
    text, with ``paid + outstanding == total``; the event's payment
    transaction (public id, kind, method, status, amount and, for a reversal,
    the reversed collection's public id) or ``None``; and the event's receipt
    (public id, number, status) or ``None``. There is no internal id, name,
    reference, reason, token, session value or client JSON.
    """
    online = schema == ONLINE_PAYMENT_SNAPSHOT_SCHEMA
    expected_keys = _ONLINE_PAYMENT_SNAPSHOT_KEYS if online else _PAYMENT_SNAPSHOT_KEYS
    if not isinstance(snapshot, dict) or set(snapshot) != expected_keys:
        raise ValueError("A payment snapshot has the wrong keys")
    if snapshot["schema"] != schema:
        raise ValueError("A payment snapshot has an unknown schema")
    if online:
        context = snapshot["online"]
        if (
            not isinstance(context, dict)
            or set(context) != _ONLINE_KEYS
            or not all(_public_id_ok(context[key]) for key in _ONLINE_KEYS)
        ):
            raise ValueError("An online payment snapshot names its intent and provider event")
        payment = snapshot["payment"]
        if payment is not None and (
            not isinstance(payment, dict)
            or payment.get("kind") != _COLLECTION
            or payment.get("method") != _ONLINE
        ):
            raise ValueError("An online payment snapshot describes an online collection")
    if not _public_id_ok(snapshot["invoice_public_id"]):
        raise ValueError("A payment snapshot invoice public id is invalid")
    if snapshot["invoice_status"] != InvoiceStatus.ISSUED.value or not invoice_number_is_valid(
        snapshot["invoice_number"]
    ):
        raise ValueError("A payment snapshot describes an issued, numbered invoice")
    if snapshot["currency_code"] != CURRENCY_CODE:
        raise ValueError("A payment snapshot currency is invalid")
    amounts = [snapshot[key] for key in ("invoice_total", "paid_amount", "outstanding_amount")]
    if not all(_balance_text_ok(value) for value in amounts):
        raise ValueError("A payment snapshot amount is not canonical four-place text")
    total, paid, outstanding = (Decimal(value) for value in amounts)
    if paid + outstanding != total:
        raise ValueError("A payment snapshot's paid and outstanding amounts do not add up")
    payment, receipt = snapshot["payment"], snapshot["receipt"]
    if payment is not None:
        error = _payment_error(payment)
        if error is not None:
            raise ValueError(f"A payment snapshot is invalid: {error}")
    if receipt is not None:
        error = _receipt_error(receipt)
        if error is not None:
            raise ValueError(f"A payment snapshot is invalid: {error}")
        if payment is None or payment["kind"] != _COLLECTION or payment["status"] != _CONFIRMED:
            raise ValueError("A receipt belongs to a confirmed collection")
    return json.loads(canonical_snapshot_json(snapshot))


def validate_online_payment_snapshot(snapshot):
    """:func:`validate_payment_snapshot` for the Phase 5 / M07 online layout:
    the M05 content, an online collection as its payment when it has one, and
    ``online`` naming the payment intent's and the verified provider event's
    public ids. No provider reference, event id, digest, signature or secret."""
    return validate_payment_snapshot(snapshot, schema=ONLINE_PAYMENT_SNAPSHOT_SCHEMA)


def validate_audit_snapshot(snapshot):
    """The canonical copy of any audit snapshot: a payment snapshot when it
    says so, otherwise the invoice layout, whose refusal it keeps."""
    schema = snapshot.get("schema") if isinstance(snapshot, dict) else None
    if schema == PAYMENT_SNAPSHOT_SCHEMA:
        return validate_payment_snapshot(snapshot)
    if schema == ONLINE_PAYMENT_SNAPSHOT_SCHEMA:
        return validate_online_payment_snapshot(snapshot)
    return validate_invoice_snapshot(snapshot)


def _in_list(values):
    return "(" + ", ".join(f"'{v}'" for v in sorted(values)) + ")"


_KIND_VALUES = tuple(kind.value for kind in PaymentAuditEventKind)
_KIND_CHECK_SQL = "kind IN (" + ", ".join(f"'{v}'" for v in _KIND_VALUES) + ")"

_VERSIONS_POSITIVE_SQL = (
    "invoice_version_after > 0"
    " AND (invoice_version_before IS NULL OR invoice_version_before > 0)"
)

#: A draft is created at version 1 from nothing; every other invoice event
#: moves the invoice by exactly one; a payment or receipt event records the
#: invoice version it observed and moves nothing.
_VERSION_TRANSITION_SQL = (
    f"(kind = '{_CREATED}' AND invoice_version_before IS NULL AND invoice_version_after = 1)"
    f" OR (kind IN {_in_list(_INVOICE_CHANGE_KINDS)} AND invoice_version_before IS NOT NULL"
    " AND invoice_version_after = invoice_version_before + 1)"
    f" OR (kind IN {_in_list(_PAYMENT_AND_RECEIPT_KINDS)} AND invoice_version_before IS NOT NULL"
    " AND invoice_version_after = invoice_version_before)"
)

_SNAPSHOTS_PRESENT_SQL = (
    f"(kind = '{_CREATED}' AND before_snapshot IS NULL AND after_snapshot IS NOT NULL)"
    f" OR (kind <> '{_CREATED}' AND before_snapshot IS NOT NULL AND after_snapshot IS NOT NULL)"
)

_REASON_REQUIRED_SQL = (
    f"(kind IN {_in_list(REASON_REQUIRED_KINDS)} AND reason IS NOT NULL AND LENGTH(reason) > 0)"
    f" OR (kind IN {_in_list(_REASONLESS_KINDS)} AND reason IS NULL)"
)

#: An invoice event names no transaction or receipt; a payment event names
#: its transaction only; a receipt event names the receipt and its collection.
_SUBJECT_LINKS_SQL = (
    f"(kind IN {_in_list(INVOICE_EVENT_KINDS)}"
    " AND payment_transaction_id IS NULL AND receipt_id IS NULL)"
    f" OR (kind IN {_in_list(PAYMENT_EVENT_KINDS)}"
    " AND payment_transaction_id IS NOT NULL AND receipt_id IS NULL)"
    f" OR (kind IN {_in_list(RECEIPT_EVENT_KINDS)}"
    " AND payment_transaction_id IS NOT NULL AND receipt_id IS NOT NULL)"
)

#: Phase 5 / M07: a system-origin event has no actor; every other event has
#: its acting Administrator.
_ACTOR_ORIGIN_SQL = (
    f"(kind IN {_in_list(SYSTEM_EVENT_KINDS)} AND actor_id IS NULL)"
    f" OR (kind NOT IN {_in_list(SYSTEM_EVENT_KINDS)} AND actor_id IS NOT NULL)"
)


class PaymentAuditEvent(db.Model):
    """One append-only entry of the financial audit trail (Phase 5 / M04,
    extended by M05 and M07).

    **Every invoice, payment and receipt movement writes exactly one**, in the
    same transaction as the change it describes: draft creation, each line
    change, issue and cancellation (M04); a cash collection, a bank transfer's
    recording, confirmation and rejection, a reversal, and a receipt's issue
    and voiding (M05). The event names the invoice, the acting Administrator,
    its kind, the whole-second UTC moment, the invoice ``version`` before and
    after, an optional human reason and the complete visible financial state
    before and after as canonical JSON. A payment or receipt event also names
    its :class:`~app.models.payment_transaction.PaymentTransaction` and, for a
    receipt event, its :class:`~app.models.receipt.Receipt`; it moves no
    invoice version, so its "before" and "after" versions are the version it
    observed.

    **Snapshots are built by the server, never submitted.** An invoice event
    uses :func:`validate_invoice_snapshot`; a payment or receipt event uses
    :func:`validate_payment_snapshot`; a system-origin online event uses
    :func:`validate_online_payment_snapshot`. None carries an internal id, card
    or bank data, a transfer or provider reference, a token, a signature, a
    secret, a session value or client JSON.

    **Who acted.** ``actor_id`` names the active Administrator of every
    invoice, manual payment and receipt movement. Since Phase 5 / M07 it is
    NULL exactly for the two system-origin kinds, ``payment_online_confirmed``
    and ``receipt_online_issued``, which a verified, signed provider webhook
    caused (``ck_payment_audit_events_actor_origin``): no Administrator
    identity is ever invented for them, and no human event may omit its actor.

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
    - ``ck_payment_audit_events_reason_required``;
    - ``ck_payment_audit_events_subject_links`` (M05);
    - ``ck_payment_audit_events_actor_origin`` (M07).

    That the actor was an active Administrator, that a snapshot has the
    right shape for its kind and that a link names the right rows are proved
    by the application.

    Indexes: ``ix_payment_audit_events_invoice_id_id`` (``invoice_id``,
    ``id``) -- one invoice's timeline in id order and the foreign key;
    ``ix_payment_audit_events_actor_id``; and, since M05,
    ``ix_payment_audit_events_payment_transaction_id`` and
    ``ix_payment_audit_events_receipt_id``. **No MySQL execution plan has been
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
        db.CheckConstraint(_SUBJECT_LINKS_SQL, name="ck_payment_audit_events_subject_links"),
        db.CheckConstraint(_ACTOR_ORIGIN_SQL, name="ck_payment_audit_events_actor_origin"),
        db.Index("ix_payment_audit_events_invoice_id_id", "invoice_id", "id"),
        db.Index("ix_payment_audit_events_actor_id", "actor_id"),
        db.Index("ix_payment_audit_events_payment_transaction_id", "payment_transaction_id"),
        db.Index("ix_payment_audit_events_receipt_id", "receipt_id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    invoice_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("invoices.id"),
        nullable=False,
    )
    #: NULL exactly for a system-origin kind (Phase 5 / M07).
    actor_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=True,
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
    #: Phase 5 / M05. Named foreign keys, because the revision that added
    #: them to an existing table must be able to name them again.
    payment_transaction_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey(
            "payment_transactions.id", name="fk_payment_audit_events_payment_transaction_id"
        ),
        nullable=True,
    )
    receipt_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("receipts.id", name="fk_payment_audit_events_receipt_id"),
        nullable=True,
    )

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
        return None if value is None else validate_audit_snapshot(value)

    @validates("after_snapshot")
    def validate_after_snapshot(self, _key, value):
        return validate_audit_snapshot(value)


def _expected_shape(kind):
    """``(schema, has_payment, has_receipt)`` for a known kind, else ``None``."""
    if kind in INVOICE_EVENT_KINDS:
        return INVOICE_SNAPSHOT_SCHEMA, False, False
    if kind == _K.PAYMENT_ONLINE_CONFIRMED.value:
        return ONLINE_PAYMENT_SNAPSHOT_SCHEMA, True, False
    if kind == _K.RECEIPT_ONLINE_ISSUED.value:
        return ONLINE_PAYMENT_SNAPSHOT_SCHEMA, True, True
    if kind in PAYMENT_EVENT_KINDS:
        return PAYMENT_SNAPSHOT_SCHEMA, True, False
    if kind in RECEIPT_EVENT_KINDS:
        return PAYMENT_SNAPSHOT_SCHEMA, True, True
    return None


@event.listens_for(PaymentAuditEvent, "before_insert")
def _refuse_an_event_that_does_not_match_its_kind(_mapper, _connection, target):
    """An invoice event carries invoice snapshots and no link; a payment or
    receipt event carries payment snapshots and exactly its links. The
    database proves the links; only the application can read the JSON."""
    shape = _expected_shape(target.kind)
    if shape is None:
        return
    schema, has_payment, has_receipt = shape
    for snapshot in (target.before_snapshot, target.after_snapshot):
        if snapshot is not None and (
            not isinstance(snapshot, dict) or snapshot.get("schema") != schema
        ):
            raise ValueError("An audit event's snapshots do not match its kind")
    if (target.payment_transaction_id is not None, target.receipt_id is not None) != (
        has_payment,
        has_receipt,
    ):
        raise ValueError("An audit event's payment and receipt links do not match its kind")
    if (target.actor_id is None) != (target.kind in SYSTEM_EVENT_KINDS):
        raise ValueError("Only a system-origin online event has no acting Administrator")


@event.listens_for(PaymentAuditEvent, "before_update")
def _refuse_editing_an_audit_event(_mapper, _connection, _target):
    raise FinancialHistoryError("A payment audit event is append-only")


@event.listens_for(PaymentAuditEvent, "before_delete")
def _refuse_deleting_an_audit_event(_mapper, _connection, _target):
    raise FinancialHistoryError("A payment audit event is never deleted")


_NO_BULK_DELETE = frozenset(
    {
        "invoices",
        "invoice_items",
        "payment_audit_events",
        "invoice_number_sequences",
        "payment_transactions",
        "receipts",
        "receipt_number_sequences",
    }
)
_NO_BULK_UPDATE = frozenset({"payment_audit_events", "payment_transactions", "receipts"})


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_rewrites_of_financial_history(orm_execute_state):
    """Mapper events do not see ``session.execute(update(...))`` or
    ``delete(...)``; this refuses those statements for the tables whose rows
    are never deleted, and for the audit trail, payment transactions and
    receipts, whose rows change only through the guarded ORM path."""
    if orm_execute_state.is_delete:
        protected = _NO_BULK_DELETE
    elif orm_execute_state.is_update:
        protected = _NO_BULK_UPDATE
    else:
        return
    table = getattr(orm_execute_state.statement, "table", None)
    if getattr(table, "name", None) in protected:
        raise FinancialHistoryError(f"{table.name} rows cannot be rewritten in bulk")
