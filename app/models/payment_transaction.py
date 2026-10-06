from app.models.code_types import CODE_COLLATION
import re
import uuid
from datetime import date, datetime

from sqlalchemy import event, text
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import PaymentMethod, PaymentTransactionKind, PaymentTransactionStatus
from app.models.fee_plan import TEXT_MISSING, normalize_single_line_text
from app.models.invoice import (
    DELETION_REASON_MAX_LENGTH,
    FinancialHistoryError,
    deletion_state_sql,
    is_changing,
    pending_value,
    refuse_or_allow_deletion,
    stored_row,
    validate_deletion_reason,
)
from app.models.payment_audit_event import INVOICE_AUDIT_REASON_MAX_LENGTH, normalize_audit_reason
from app.models.submission_feedback import whole_second_utc
from app.services.money import (
    AMOUNT_PRECISION,
    AMOUNT_SCALE,
    CURRENCY_CODE,
    MAX_AMOUNT,
    MIN_AMOUNT,
    validate_amount,
)

#: The ``bank_transfer_reference`` column's own width.
BANK_TRANSFER_REFERENCE_MAX_LENGTH = 64

#: The ``rejection_reason`` column's width: an audit reason's.
PAYMENT_REASON_MAX_LENGTH = INVOICE_AUDIT_REASON_MAX_LENGTH

#: The most collection rows -- any status -- one invoice may hold. A technical
#: bound on the payment page, the balance read and every stale-state token,
#: exactly as ``MAX_INVOICE_ITEM_ROWS`` bounds an invoice's lines. Each
#: collection has at most one reversal, so an invoice holds at most
#: :data:`MAX_INVOICE_PAYMENT_ROWS` transactions.
MAX_INVOICE_COLLECTIONS = 25
MAX_INVOICE_PAYMENT_ROWS = 2 * MAX_INVOICE_COLLECTIONS

#: A technical lower bound on a bank transfer's civil date; the upper bound is
#: the center-local date the transfer is recorded on.
EARLIEST_BANK_TRANSFER_DATE = date(2000, 1, 1)

#: Error codes of the two bank-transfer inputs, besides ``fee_plan``'s text
#: codes. The route owns the wording.
REFERENCE_CARD_LIKE = "card_like"
DATE_FORMAT = "date_format"
DATE_TOO_EARLY = "date_too_early"
DATE_IN_FUTURE = "date_in_future"

_DIGIT_GROUP = re.compile(r"[0-9]+")
_DATE_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_CARD_LENGTHS = range(13, 20)


def _passes_luhn(digits):
    total = 0
    for position, character in enumerate(reversed(digits)):
        value = ord(character) - 48
        if position % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _looks_like_a_card_number(text):
    """Whether `text` holds 13 to 19 digits, written together or split only
    by single spaces or hyphens, that pass the Luhn check -- the shape of a
    payment card number, which must never be stored."""
    groups = list(_DIGIT_GROUP.finditer(text))
    for start in range(len(groups)):
        digits = ""
        for index in range(start, len(groups)):
            if index > start and text[groups[index - 1].end():groups[index].start()] not in (" ", "-"):
                break
            digits += groups[index].group()
            if len(digits) > _CARD_LENGTHS[-1]:
                break
            if len(digits) in _CARD_LENGTHS and _passes_luhn(digits):
                return True
    return False


def normalize_bank_transfer_reference(raw):
    """``(reference, error_code)`` for one bank transfer reference.

    Required, single line, normalized exactly like a fee plan item label, and
    at most :data:`BANK_TRANSFER_REFERENCE_MAX_LENGTH` characters. A value
    that looks like a payment card number is refused: the application stores
    no card data, whatever field it is typed into.
    """
    text, error = normalize_single_line_text(raw, BANK_TRANSFER_REFERENCE_MAX_LENGTH)
    if error is not None:
        return None, error
    if _looks_like_a_card_number(text):
        return None, REFERENCE_CARD_LIKE
    return text, None


def parse_bank_transfer_date(raw, latest):
    """``(date, error_code)`` for a submitted ``YYYY-MM-DD`` civil date no
    earlier than :data:`EARLIEST_BANK_TRANSFER_DATE` and no later than
    `latest`, the center-local date of the recording."""
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return None, TEXT_MISSING
    if _DATE_SHAPE.fullmatch(text) is None:
        return None, DATE_FORMAT
    try:
        value = date.fromisoformat(text)
    except ValueError:
        return None, DATE_FORMAT
    if value < EARLIEST_BANK_TRANSFER_DATE:
        return None, DATE_TOO_EARLY
    if value > latest:
        return None, DATE_IN_FUTURE
    return value, None


_KIND_VALUES = tuple(kind.value for kind in PaymentTransactionKind)
_METHOD_VALUES = tuple(method.value for method in PaymentMethod)
_STATUS_VALUES = tuple(status.value for status in PaymentTransactionStatus)
_COLLECTION = PaymentTransactionKind.COLLECTION.value
_REVERSAL = PaymentTransactionKind.REVERSAL.value
_CASH = PaymentMethod.CASH.value
_BANK_TRANSFER = PaymentMethod.BANK_TRANSFER.value
_ONLINE = PaymentMethod.ONLINE.value
_PENDING = PaymentTransactionStatus.PENDING.value
_CONFIRMED = PaymentTransactionStatus.CONFIRMED.value
_REJECTED = PaymentTransactionStatus.REJECTED.value


def _closed_set_sql(column, values):
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


#: A bank transfer collection carries its normalized reference and civil
#: date; a cash or online collection and every reversal carry neither.
_BANK_TRANSFER_DETAILS_SQL = (
    "(kind = 'collection' AND method = 'bank_transfer'"
    " AND bank_transfer_reference IS NOT NULL AND LENGTH(bank_transfer_reference) > 0"
    " AND bank_transfer_date IS NOT NULL)"
    " OR ((kind = 'reversal' OR method IN ('cash', 'online'))"
    " AND bank_transfer_reference IS NULL AND bank_transfer_date IS NULL)"
)

#: A confirmation has its moment and its Administrator -- except an online
#: collection's, which a verified webhook made and no person did.
_CONFIRMATION_PAIR_SQL = (
    "(confirmed_at IS NULL AND confirmed_by_id IS NULL)"
    " OR (confirmed_at IS NOT NULL AND confirmed_by_id IS NOT NULL)"
    " OR (kind = 'collection' AND method = 'online'"
    " AND confirmed_at IS NOT NULL AND confirmed_by_id IS NULL)"
)

_REJECTION_STATE_SQL = (
    "(rejected_at IS NULL AND rejected_by_id IS NULL AND rejection_reason IS NULL)"
    " OR (rejected_at IS NOT NULL AND rejected_by_id IS NOT NULL"
    " AND rejection_reason IS NOT NULL AND LENGTH(rejection_reason) > 0)"
)

#: Only a bank transfer collection is ever pending or rejected; a confirmed
#: row carries its confirmation and no rejection.
_LIFECYCLE_STATE_SQL = (
    "(status = 'pending' AND kind = 'collection' AND method = 'bank_transfer'"
    " AND confirmed_at IS NULL AND rejected_at IS NULL)"
    " OR (status = 'confirmed' AND confirmed_at IS NOT NULL AND rejected_at IS NULL)"
    " OR (status = 'rejected' AND kind = 'collection' AND method = 'bank_transfer'"
    " AND rejected_at IS NOT NULL AND confirmed_at IS NULL)"
)

#: A cash collection and a reversal are confirmed by the act of recording
#: them: the same moment, by the same Administrator. An online collection is
#: confirmed at the moment it is recorded, by no one.
_IMMEDIATE_CONFIRMATION_SQL = (
    "(kind = 'collection' AND method = 'bank_transfer')"
    " OR (kind = 'collection' AND method = 'online' AND confirmed_at = recorded_at"
    " AND recorded_by_id IS NULL AND confirmed_by_id IS NULL)"
    " OR (confirmed_at = recorded_at AND confirmed_by_id = recorded_by_id)"
)

#: Only an online collection names a payment intent, and it names exactly one
#: and no human recorder or confirmer; every other row names no intent and
#: was recorded by an Administrator.
_ONLINE_ORIGIN_SQL = (
    "(kind = 'collection' AND method = 'online' AND payment_intent_id IS NOT NULL"
    " AND recorded_by_id IS NULL AND confirmed_by_id IS NULL)"
    " OR ((kind <> 'collection' OR method <> 'online') AND payment_intent_id IS NULL"
    " AND recorded_by_id IS NOT NULL)"
)

_REVERSAL_LINK_SQL = (
    "(kind = 'collection' AND reversal_of_payment_transaction_id IS NULL)"
    " OR (kind = 'reversal' AND reversal_of_payment_transaction_id IS NOT NULL)"
)

_TIMESTAMPS_ORDERED_SQL = (
    "recorded_at >= created_at AND updated_at >= recorded_at"
    " AND (confirmed_at IS NULL OR (confirmed_at >= recorded_at AND updated_at >= confirmed_at))"
    " AND (rejected_at IS NULL OR (rejected_at >= recorded_at AND updated_at >= rejected_at))"
)

#: Phase 5 / M10: any transaction may be deleted -- alone, when edited, or with
#: its invoice's whole document family.
_DELETION_STATE_SQL = deletion_state_sql("recorded_at")


class PaymentTransaction(db.Model):
    """One payment movement against one issued
    :class:`~app.models.invoice.Invoice` (Phase 5 / M05, extended by M07).

    **It belongs to the invoice, and only the invoice.** ``invoice_id`` is a
    plain foreign key; the assignment, Enrollment, Student and academic chain
    are read through it and are not duplicated here.

    **Two kinds.** A ``collection`` is money recorded as received: by an
    Administrator, by ``cash`` or ``bank_transfer``, or -- Phase 5 / M07 -- by
    a verified, signed provider webhook, as ``online``. An online collection is
    confirmed when recorded, names exactly one
    :class:`~app.models.payment_intent.PaymentIntent` through the unique
    ``payment_intent_id`` (``uq_payment_transactions_payment_intent_id``), and
    names **no** recorder or confirmer: no Administrator identity is invented
    for it. No other row names an intent. A ``reversal`` is a full
    reversing entry for exactly one confirmed collection -- the same amount,
    currency and (for classification only) method -- and the sole reversal of
    it (``uq_payment_transactions_reversal_of``). A reversal is an internal
    correction, not a refund and not evidence that money was returned.

    **Lifecycle.** See :class:`~app.models.enums.PaymentTransactionStatus`: a
    cash collection and a reversal are ``confirmed`` when recorded; a bank
    transfer is recorded ``pending`` and then ``confirmed`` or ``rejected``,
    the latter with a reason. A confirmed or rejected row never changes again.

    **Money** is exact ``DECIMAL(19, 4)`` within Phase 5 / M02's bounds, in
    ``LYD``. Only confirmed rows count toward an invoice's balance, which is
    computed in Python ``Decimal`` and never stored.

    **No card or bank credential is stored.** A bank transfer keeps only a
    normalized reference and its civil date; there is no account number,
    card, proof upload or provider column.

    **Nothing is ever physically deleted or edited in place.** The ORM guards
    refuse a delete, any change to a confirmed or rejected row, any change to
    what was recorded, and a decision that does not move ``version`` by
    exactly one.

    **Visible deletion (Phase 5 / M10).** A transaction may be *deleted* once:
    ``deleted_at``, ``deleted_by_id`` and ``deletion_reason`` set together
    with the version moved by one and nothing else changed
    (``ck_payment_transactions_deletion_state``). An edit of a manual
    collection deletes it the same way and records a new collection in its
    place. A deleted row keeps everything it recorded, counts for nothing in
    any balance, list or report, and never changes again.

    Database invariants (final defense only):

    - ``public_id`` unique; ``uq_payment_transactions_reversal_of``;
    - ``ck_payment_transactions_kind_valid``, ``_method_valid``,
      ``_status_valid``, ``_currency_code``, ``_amount_range`` and
      ``_version_positive``;
    - ``ck_payment_transactions_bank_transfer_details``;
    - ``ck_payment_transactions_confirmation_pair`` and
      ``ck_payment_transactions_rejection_state``;
    - ``ck_payment_transactions_lifecycle_state``,
      ``ck_payment_transactions_immediate_confirmation`` and
      ``ck_payment_transactions_reversal_link``;
    - ``ck_payment_transactions_timestamps_ordered``;
    - ``ck_payment_transactions_online_origin`` and
      ``uq_payment_transactions_payment_intent_id`` (M07);
    - ``ck_payment_transactions_deletion_state`` (M10).

    Rules no CHECK can read, proved by the application under the invoice
    lock: a reversal names a confirmed **collection** of the **same** invoice
    with the same amount and method; a collection never exceeds the invoice's
    outstanding balance; an invoice holds at most
    :data:`MAX_INVOICE_COLLECTIONS` collections. (MySQL also refuses a CHECK
    that reads the auto-increment ``id``, so "not its own reversal" is the
    application's too.)

    Indexes: ``ix_payment_transactions_invoice_id_id`` (``invoice_id``,
    ``id``) -- one invoice's rows in id order, the rows to lock and the
    foreign key; ``ix_payment_transactions_status_id`` and
    ``ix_payment_transactions_method_id`` -- the filtered overview; and one
    index per ``users`` foreign key. Phase 5 / M10 adds
    ``ix_payment_transactions_deleted_at_id`` for Deleted Records and
    ``ix_payment_transactions_deleted_by_id``. The reversal link is indexed by its
    unique constraint. **No MySQL execution plan has been measured for this
    table.** No ORM relationship is declared in either direction.
    """

    __tablename__ = "payment_transactions"
    __table_args__ = (
        db.CheckConstraint("movement_direction IN ('in', 'out')", name="ck_payment_transactions_direction"),
        db.CheckConstraint("MOD(amount, 0.001) = 0", name="ck_payment_transactions_quantum"),
        db.ForeignKeyConstraint(["invoice_id", "student_id"], ["invoices.id", "invoices.student_id"], name="fk_payment_transactions_invoice_student"),
        db.UniqueConstraint(
            "reversal_of_payment_transaction_id", name="uq_payment_transactions_reversal_of"
        ),
        db.CheckConstraint(
            _closed_set_sql("kind", _KIND_VALUES), name="ck_payment_transactions_kind_valid"
        ),
        db.CheckConstraint(
            _closed_set_sql("method", _METHOD_VALUES), name="ck_payment_transactions_method_valid"
        ),
        db.CheckConstraint(
            _closed_set_sql("status", _STATUS_VALUES), name="ck_payment_transactions_status_valid"
        ),
        db.CheckConstraint(
            f"currency_code = '{CURRENCY_CODE}'", name="ck_payment_transactions_currency_code"
        ),
        db.CheckConstraint(
            f"amount >= {MIN_AMOUNT} AND amount <= {MAX_AMOUNT}",
            name="ck_payment_transactions_amount_range",
        ),
        db.CheckConstraint("version > 0", name="ck_payment_transactions_version_positive"),
        db.CheckConstraint(
            _BANK_TRANSFER_DETAILS_SQL, name="ck_payment_transactions_bank_transfer_details"
        ),
        db.CheckConstraint(
            _CONFIRMATION_PAIR_SQL, name="ck_payment_transactions_confirmation_pair"
        ),
        db.CheckConstraint(_REJECTION_STATE_SQL, name="ck_payment_transactions_rejection_state"),
        db.CheckConstraint(_LIFECYCLE_STATE_SQL, name="ck_payment_transactions_lifecycle_state"),
        db.CheckConstraint(
            _IMMEDIATE_CONFIRMATION_SQL, name="ck_payment_transactions_immediate_confirmation"
        ),
        db.CheckConstraint(_REVERSAL_LINK_SQL, name="ck_payment_transactions_reversal_link"),
        db.CheckConstraint(
            _TIMESTAMPS_ORDERED_SQL, name="ck_payment_transactions_timestamps_ordered"
        ),
        db.UniqueConstraint(
            "payment_intent_id", name="uq_payment_transactions_payment_intent_id"
        ),
        db.CheckConstraint(_ONLINE_ORIGIN_SQL, name="ck_payment_transactions_online_origin"),
        db.CheckConstraint(_DELETION_STATE_SQL, name="ck_payment_transactions_deletion_state"),
        db.Index("ix_payment_transactions_invoice_id_id", "invoice_id", "id"),
        db.Index("ix_payment_transactions_status_id", "status", "id"),
        db.Index("ix_payment_transactions_method_id", "method", "id"),
        db.Index("ix_payment_transactions_recorded_by_id", "recorded_by_id"),
        db.Index("ix_payment_transactions_confirmed_by_id", "confirmed_by_id"),
        db.Index("ix_payment_transactions_rejected_by_id", "rejected_by_id"),
        db.Index("ix_payment_transactions_deleted_at_id", "deleted_at", "id"),
        db.Index("ix_payment_transactions_deleted_by_id", "deleted_by_id"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    invoice_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("invoices.id"),
        nullable=True,
    )
    student_id = db.Column(db.BigInteger, db.ForeignKey("users.id", name="fk_payment_transactions_student_id"), nullable=False, index=True)
    movement_direction = db.Column(db.String(16, collation=CODE_COLLATION), nullable=False, default="in")
    operation_key = db.Column(db.String(36), nullable=True, unique=True)
    kind = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False)
    method = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False)
    status = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False)
    currency_code = db.Column(db.String(3, collation=CODE_COLLATION), nullable=False, default=CURRENCY_CODE)
    #: Exact fixed point, never a float -- see :mod:`app.services.money`.
    amount = db.Column(
        db.DECIMAL(precision=AMOUNT_PRECISION, scale=AMOUNT_SCALE, asdecimal=True),
        nullable=False,
    )
    bank_transfer_reference = db.Column(
        db.String(BANK_TRANSFER_REFERENCE_MAX_LENGTH), nullable=True
    )
    bank_transfer_date = db.Column(db.Date, nullable=True)
    recorded_at = db.Column(db.DateTime, nullable=False)
    #: NULL exactly for an online collection (Phase 5 / M07).
    recorded_by_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    confirmed_at = db.Column(db.DateTime, nullable=True)
    confirmed_by_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    rejected_at = db.Column(db.DateTime, nullable=True)
    rejected_by_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    rejection_reason = db.Column(db.String(PAYMENT_REASON_MAX_LENGTH), nullable=True)
    reversal_of_payment_transaction_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("payment_transactions.id"),
        nullable=True,
    )
    #: Phase 5 / M07: the one payment intent an online collection settles.
    #: A named foreign key, because the revision that added it to an existing
    #: table must be able to name it again.
    payment_intent_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("payment_intents.id", name="fk_payment_transactions_payment_intent_id"),
        nullable=True,
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Defaults are defense in depth only; every write supplies its own
    #: post-lock whole-second moment. Deliberately no ``onupdate`` hook.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    #: Phase 5 / M10: the visible deletion.
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id", name="fk_payment_transactions_deleted_by_id"),
        nullable=True,
    )
    deletion_reason = db.Column(db.String(DELETION_REASON_MAX_LENGTH), nullable=True)

    @validates("kind")
    def validate_kind(self, _key, value):
        if value not in _KIND_VALUES:
            raise ValueError(f"Invalid payment transaction kind: {value}")
        return value

    @validates("method")
    def validate_method(self, _key, value):
        if value not in _METHOD_VALUES:
            raise ValueError(f"Invalid payment method: {value}")
        return value

    @validates("status")
    def validate_status(self, _key, value):
        if value not in _STATUS_VALUES:
            raise ValueError(f"Invalid payment transaction status: {value}")
        return value

    @validates("currency_code")
    def validate_currency_code(self, _key, value):
        if value != CURRENCY_CODE:
            raise ValueError(f"Payment currency must be {CURRENCY_CODE}")
        return value

    @validates("amount")
    def validate_amount(self, _key, value):
        return validate_amount(value)

    @validates("bank_transfer_reference")
    def validate_bank_transfer_reference(self, _key, value):
        if value is None:
            return value
        normalized, error = normalize_bank_transfer_reference(value)
        if error is not None or normalized != value:
            raise ValueError("A bank transfer reference must be normalized plain text or None")
        return value

    @validates("bank_transfer_date")
    def validate_bank_transfer_date(self, _key, value):
        if value is None:
            return value
        if (
            not isinstance(value, date)
            or isinstance(value, datetime)
            or value < EARLIEST_BANK_TRANSFER_DATE
        ):
            raise ValueError("A bank transfer date must be a civil date from 2000-01-01")
        return value

    @validates("rejection_reason")
    def validate_rejection_reason(self, _key, value):
        if value is None:
            return value
        normalized, error = normalize_audit_reason(value)
        if error is not None or normalized != value:
            raise ValueError("A rejection reason must be normalized plain text or None")
        return value

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("Payment transaction version must be a positive integer")
        return value

    @validates("deletion_reason")
    def validate_deletion_reason(self, _key, value):
        return validate_deletion_reason(value)

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    @property
    def is_collection(self):
        return self.kind == _COLLECTION

    @property
    def is_reversal(self):
        return self.kind == _REVERSAL

    @property
    def is_online(self):
        return self.method == _ONLINE

    @property
    def is_pending(self):
        return self.status == _PENDING

    @property
    def is_confirmed(self):
        return self.status == _CONFIRMED

    @property
    def is_rejected(self):
        return self.status == _REJECTED


# ---------------------------------------------------------------------------
# ORM guards
# ---------------------------------------------------------------------------

#: What was recorded. It never changes, whatever the status.
_RECORDED_COLUMNS = (
    "public_id",
    "invoice_id",
    "kind",
    "method",
    "currency_code",
    "amount",
    "bank_transfer_reference",
    "bank_transfer_date",
    "recorded_at",
    "recorded_by_id",
    "reversal_of_payment_transaction_id",
    "payment_intent_id",
    "created_at",
)
_DECISIONS = frozenset({_CONFIRMED, _REJECTED})


@event.listens_for(PaymentTransaction, "before_insert")
def _resolve_legacy_payment_account(_mapper, connection, target):
    if target.student_id is None and target.invoice_id:
        target.student_id = connection.execute(text("SELECT student_id FROM invoices WHERE id = :id"), {"id": target.invoice_id}).scalar()


@event.listens_for(PaymentTransaction, "before_update")
def _refuse_rewriting_a_payment(_mapper, connection, target):
    if not is_changing(target):
        return
    from app.services.financial_history import revision_authorizes_update
    if revision_authorizes_update(connection, target):
        return
    stored = stored_row(
        connection, target, ("status", "version", "deleted_at") + _RECORDED_COLUMNS
    )
    if stored is None:
        return
    if refuse_or_allow_deletion(target, stored, "payment"):
        return
    if stored["status"] != _PENDING:
        raise FinancialHistoryError("A confirmed or rejected payment never changes")
    for key in _RECORDED_COLUMNS:
        setting, value = pending_value(target, key)
        if setting and value != stored[key]:
            raise FinancialHistoryError(f"A payment's {key} never changes once recorded")
    setting, status = pending_value(target, "status")
    if setting and status not in _DECISIONS:
        raise FinancialHistoryError("A pending payment is only ever confirmed or rejected")
    setting, version = pending_value(target, "version")
    if not setting or version != stored["version"] + 1:
        raise FinancialHistoryError("A payment decision moves its version by exactly one")


@event.listens_for(PaymentTransaction, "before_delete")
def _refuse_deleting_a_payment(_mapper, _connection, _target):
    raise FinancialHistoryError("A payment transaction is never deleted")
