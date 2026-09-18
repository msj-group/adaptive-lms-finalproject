import json
import re
import uuid
from datetime import datetime

from sqlalchemy import event
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import PaymentMethod, ReceiptStatus
from app.models.invoice import (
    FinancialHistoryError,
    invoice_number_is_valid,
    is_changing,
    pending_value,
    stored_row,
)
from app.models.payment_audit_event import (
    INVOICE_AUDIT_REASON_MAX_LENGTH,
    canonical_snapshot_json,
    normalize_audit_reason,
    snapshot_amount_text,
)
from app.models.receipt_number_sequence import RECEIPT_NUMBER_LENGTH, receipt_number_is_valid
from app.models.submission_feedback import whole_second_utc
from app.services.money import CURRENCY_CODE, validate_amount

#: Marks the receipt document layout of a manual (cash or bank-transfer)
#: collection, confirmed by a named Administrator.
RECEIPT_SNAPSHOT_SCHEMA = "phase5-m05.receipt.v1"

#: Phase 5 / M07: the layout of an online collection's receipt. It names the
#: payment intent and the verified provider event instead of a confirming
#: Administrator, because no person confirmed it.
ONLINE_RECEIPT_SNAPSHOT_SCHEMA = "phase5-m07.online-receipt.v1"

#: The widest display name a receipt copies (a user's ``full_name``).
RECEIPT_NAME_MAX_LENGTH = 255

#: The ``void_reason`` column's width: an audit reason's.
RECEIPT_VOID_REASON_MAX_LENGTH = INVOICE_AUDIT_REASON_MAX_LENGTH

_SNAPSHOT_KEYS = frozenset(
    {
        "schema",
        "receipt_public_id",
        "receipt_number",
        "payment_public_id",
        "invoice_public_id",
        "invoice_number",
        "student_fee_assignment_public_id",
        "method",
        "amount",
        "currency_code",
        "confirmed_at",
        "confirmed_by_name",
        "student_name",
        "group_name",
        "course_title",
        "academic_term_name",
    }
)
_ONLINE_SNAPSHOT_KEYS = (_SNAPSHOT_KEYS - {"confirmed_by_name"}) | {
    "payment_intent_public_id",
    "provider_event_public_id",
}
_PUBLIC_ID_KEYS = (
    "receipt_public_id",
    "payment_public_id",
    "invoice_public_id",
    "student_fee_assignment_public_id",
)
_ONLINE_PUBLIC_ID_KEYS = _PUBLIC_ID_KEYS + ("payment_intent_public_id", "provider_event_public_id")
_NAME_KEYS = ("confirmed_by_name", "student_name", "group_name", "course_title", "academic_term_name")
_ONLINE_NAME_KEYS = ("student_name", "group_name", "course_title", "academic_term_name")
_ONLINE = PaymentMethod.ONLINE.value
_PUBLIC_ID_MAX_LENGTH = 36
_AMOUNT_TEXT = re.compile(r"[0-9]+\.[0-9]{4}")
_MOMENT_TEXT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_MOMENT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_METHOD_VALUES = frozenset(method.value for method in PaymentMethod)
_STATUS_VALUES = tuple(status.value for status in ReceiptStatus)
_ISSUED = ReceiptStatus.ISSUED.value
_VOIDED = ReceiptStatus.VOIDED.value


def receipt_moment_text(moment):
    """``'2026-09-15T10:30:00Z'`` for a naive-UTC whole-second moment."""
    if not isinstance(moment, datetime) or moment.tzinfo is not None or moment.microsecond:
        raise ValueError("A receipt moment is a naive-UTC whole-second datetime")
    return moment.strftime(_MOMENT_FORMAT)


def parse_receipt_moment(text):
    """The naive-UTC ``datetime`` of :func:`receipt_moment_text`'s text."""
    return datetime.strptime(text, _MOMENT_FORMAT)


def receipt_is_online(snapshot):
    """Whether `snapshot` is an online collection's receipt document."""
    return isinstance(snapshot, dict) and snapshot.get("schema") == ONLINE_RECEIPT_SNAPSHOT_SCHEMA


def validate_receipt_snapshot(snapshot):
    """The canonical copy of one server-built receipt document, or raise
    ``ValueError``.

    The layout is exact. A manual collection's document
    (:data:`RECEIPT_SNAPSHOT_SCHEMA`) holds the receipt, payment, invoice and
    fee assignment public ids, the receipt and invoice numbers, the method
    (cash or bank transfer), the confirmed amount as four-place text in
    ``LYD``, the confirmation moment as ``YYYY-MM-DDTHH:MM:SSZ``, and five
    display names -- the confirming Administrator, the Student, the Group, the
    Course and the Academic Term. An online collection's document
    (:data:`ONLINE_RECEIPT_SNAPSHOT_SCHEMA`, Phase 5 / M07) holds the same
    except the confirming Administrator, and adds the payment intent's and the
    verified provider event's public ids; its method is ``online``. There is no
    internal id, card or bank data, transfer reference, provider reference,
    token, secret, CSRF value, session value or client JSON.
    """
    online = receipt_is_online(snapshot)
    expected_keys = _ONLINE_SNAPSHOT_KEYS if online else _SNAPSHOT_KEYS
    if not isinstance(snapshot, dict) or set(snapshot) != expected_keys:
        raise ValueError("A receipt document has the wrong keys")
    if not online and snapshot["schema"] != RECEIPT_SNAPSHOT_SCHEMA:
        raise ValueError("A receipt document has an unknown schema")
    for key in _ONLINE_PUBLIC_ID_KEYS if online else _PUBLIC_ID_KEYS:
        value = snapshot[key]
        if not isinstance(value, str) or not 0 < len(value) <= _PUBLIC_ID_MAX_LENGTH:
            raise ValueError("A receipt document public id is invalid")
    if not receipt_number_is_valid(snapshot["receipt_number"]):
        raise ValueError("A receipt document number is invalid")
    if not invoice_number_is_valid(snapshot["invoice_number"]):
        raise ValueError("A receipt document invoice number is invalid")
    if snapshot["method"] not in _METHOD_VALUES or (snapshot["method"] == _ONLINE) != online:
        raise ValueError("A receipt document method is invalid")
    if snapshot["currency_code"] != CURRENCY_CODE:
        raise ValueError("A receipt document currency is invalid")
    amount = snapshot["amount"]
    if not isinstance(amount, str) or _AMOUNT_TEXT.fullmatch(amount) is None:
        raise ValueError("A receipt document amount is not exact four-place text")
    try:
        if snapshot_amount_text(validate_amount(amount)) != amount:
            raise ValueError("A receipt document amount is not canonical")
    except ValueError:
        raise ValueError("A receipt document amount is invalid") from None
    moment = snapshot["confirmed_at"]
    if not isinstance(moment, str) or _MOMENT_TEXT.fullmatch(moment) is None:
        raise ValueError("A receipt document moment is invalid")
    try:
        parse_receipt_moment(moment)
    except ValueError:
        raise ValueError("A receipt document moment is invalid") from None
    for key in _ONLINE_NAME_KEYS if online else _NAME_KEYS:
        value = snapshot[key]
        if not isinstance(value, str) or not 0 < len(value) <= RECEIPT_NAME_MAX_LENGTH:
            raise ValueError("A receipt document name is invalid")
    return json.loads(canonical_snapshot_json(snapshot))


_STATUS_CHECK_SQL = "status IN (" + ", ".join(f"'{v}'" for v in _STATUS_VALUES) + ")"

#: The portable part of the ``RCT-YYYY-NNNNNN`` shape; the digits are proved
#: by :func:`~app.models.receipt_number_sequence.receipt_number_is_valid`.
_NUMBER_FORMAT_SQL = (
    f"receipt_number LIKE 'RCT-____-______' AND LENGTH(receipt_number) = {RECEIPT_NUMBER_LENGTH}"
)

_VOID_STATE_SQL = (
    "(status = 'issued' AND voided_at IS NULL AND voided_by_id IS NULL AND void_reason IS NULL)"
    " OR (status = 'voided' AND voided_at IS NOT NULL AND voided_by_id IS NOT NULL"
    " AND void_reason IS NOT NULL AND LENGTH(void_reason) > 0)"
)

_TIMESTAMPS_ORDERED_SQL = (
    "issued_at >= created_at AND updated_at >= issued_at"
    " AND (voided_at IS NULL OR (voided_at >= issued_at AND updated_at >= voided_at))"
)


class Receipt(db.Model):
    """The permanent operational receipt of one confirmed collection
    (Phase 5 / M05).

    **One per confirmed collection.** ``payment_transaction_id`` is a plain,
    unique foreign key to a ``collection``
    :class:`~app.models.payment_transaction.PaymentTransaction`; the receipt
    is written in the same transaction that confirms it (a cash collection at
    recording, a bank transfer at confirmation, an online collection by the
    verified webhook that records it). A pending, rejected or reversal row
    never has one.

    **Who issued it.** ``issued_by_id`` names the Administrator who confirmed a
    manual collection. It is NULL exactly for an online collection's receipt
    (Phase 5 / M07), whose document says a verified webhook confirmed it; the
    database cannot read the document, so a ``before_insert`` guard proves the
    pairing.

    **Its number is the system's.** ``RCT-YYYY-NNNNNN`` is allocated under the
    lock of the center-local year's
    :class:`~app.models.receipt_number_sequence.ReceiptNumberSequence` row and
    is never changed or reused (``uq_receipts_receipt_number``).

    **Its document is a snapshot.** ``snapshot`` is the server-built content
    the receipt page renders -- see :func:`validate_receipt_snapshot` -- so
    nothing that later happens to the invoice, Student, Group or accounts
    changes what the receipt says.

    **Lifecycle.** See :class:`~app.models.enums.ReceiptStatus`. Reversing the
    collection voids its receipt: ``status`` becomes ``voided`` with the void
    attribution, moment and reason, and ``version`` moves exactly once.
    Nothing else ever changes; nothing deletes, restores, reissues or
    overwrites a receipt. M05 receipts are operational only -- not a legal,
    tax, fiscal-printer or statutory document.

    Database invariants (final defense only): ``public_id``,
    ``uq_receipts_payment_transaction_id`` and ``uq_receipts_receipt_number``
    unique; ``ck_receipts_status_valid``, ``ck_receipts_version_positive``,
    ``ck_receipts_number_format``, ``ck_receipts_void_state`` and
    ``ck_receipts_timestamps_ordered``. That the linked row is a confirmed
    collection is proved by the application.

    Indexes: the two unique constraints, ``ix_receipts_issued_by_id`` and
    ``ix_receipts_voided_by_id``. **No MySQL execution plan has been measured
    for this table.** No ORM relationship is declared.
    """

    __tablename__ = "receipts"
    __table_args__ = (
        db.UniqueConstraint("payment_transaction_id", name="uq_receipts_payment_transaction_id"),
        db.UniqueConstraint("receipt_number", name="uq_receipts_receipt_number"),
        db.CheckConstraint(_STATUS_CHECK_SQL, name="ck_receipts_status_valid"),
        db.CheckConstraint("version > 0", name="ck_receipts_version_positive"),
        db.CheckConstraint(_NUMBER_FORMAT_SQL, name="ck_receipts_number_format"),
        db.CheckConstraint(_VOID_STATE_SQL, name="ck_receipts_void_state"),
        db.CheckConstraint(_TIMESTAMPS_ORDERED_SQL, name="ck_receipts_timestamps_ordered"),
        db.Index("ix_receipts_issued_by_id", "issued_by_id"),
        db.Index("ix_receipts_voided_by_id", "voided_by_id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    payment_transaction_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("payment_transactions.id"),
        nullable=False,
    )
    receipt_number = db.Column(db.String(RECEIPT_NUMBER_LENGTH), nullable=False)
    status = db.Column(db.String(32), nullable=False, default=_ISSUED)
    issued_at = db.Column(db.DateTime, nullable=False)
    #: NULL exactly for an online collection's receipt (Phase 5 / M07).
    issued_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    voided_at = db.Column(db.DateTime, nullable=True)
    voided_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    void_reason = db.Column(db.String(RECEIPT_VOID_REASON_MAX_LENGTH), nullable=True)
    snapshot = db.Column(db.JSON(none_as_null=True), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("status")
    def validate_status(self, _key, value):
        if value not in _STATUS_VALUES:
            raise ValueError(f"Invalid receipt status: {value}")
        return value

    @validates("receipt_number")
    def validate_receipt_number(self, _key, value):
        if not receipt_number_is_valid(value):
            raise ValueError("A receipt number must be exactly RCT-YYYY-NNNNNN")
        return value

    @validates("void_reason")
    def validate_void_reason(self, _key, value):
        if value is None:
            return value
        normalized, error = normalize_audit_reason(value)
        if error is not None or normalized != value:
            raise ValueError("A void reason must be normalized plain text or None")
        return value

    @validates("snapshot")
    def validate_snapshot(self, _key, value):
        return validate_receipt_snapshot(value)

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("Receipt version must be a positive integer")
        return value

    @property
    def is_issued(self):
        return self.status == _ISSUED

    @property
    def is_voided(self):
        return self.status == _VOIDED

    @property
    def is_online(self):
        return receipt_is_online(self.snapshot)


_ISSUED_COLUMNS = (
    "public_id",
    "payment_transaction_id",
    "receipt_number",
    "issued_at",
    "issued_by_id",
    "snapshot",
    "created_at",
)


@event.listens_for(Receipt, "before_insert")
def _refuse_a_receipt_without_its_issuer(_mapper, _connection, target):
    """A manual receipt names the Administrator who confirmed it; an online
    receipt names no one -- never an invented identity."""
    if (target.issued_by_id is None) != receipt_is_online(target.snapshot):
        raise ValueError("Only an online collection's receipt has no issuing Administrator")


@event.listens_for(Receipt, "before_update")
def _refuse_rewriting_a_receipt(_mapper, connection, target):
    if not is_changing(target):
        return
    stored = stored_row(connection, target, ("status", "version") + _ISSUED_COLUMNS)
    if stored is None:
        return
    if stored["status"] != _ISSUED:
        raise FinancialHistoryError("A voided receipt never changes")
    for key in _ISSUED_COLUMNS:
        setting, value = pending_value(target, key)
        if setting and value != stored[key]:
            raise FinancialHistoryError(f"A receipt's {key} never changes once issued")
    setting, status = pending_value(target, "status")
    if not setting or status != _VOIDED:
        raise FinancialHistoryError("An issued receipt only ever changes by being voided")
    setting, version = pending_value(target, "version")
    if not setting or version != stored["version"] + 1:
        raise FinancialHistoryError("Voiding a receipt moves its version by exactly one")


@event.listens_for(Receipt, "before_delete")
def _refuse_deleting_a_receipt(_mapper, _connection, _target):
    raise FinancialHistoryError("A receipt is never deleted")
