import re
import uuid

from sqlalchemy import event
from sqlalchemy.orm import Session, validates

from app.extensions import db
from app.models.enums import PaymentIntentStatus
from app.models.invoice import FinancialHistoryError, is_changing, pending_value, stored_row
from app.models.submission_feedback import whole_second_utc
from app.services.money import (
    AMOUNT_PRECISION,
    AMOUNT_SCALE,
    CURRENCY_CODE,
    MAX_AMOUNT,
    MIN_AMOUNT,
    validate_amount,
)

#: The providers a payment intent may name. Phase 5 / M06 has exactly one,
#: the Mock/Sandbox provider; adding one is a schema change.
PAYMENT_INTENT_PROVIDERS = ("mock",)

#: The ``provider_reference`` column's width.
PROVIDER_REFERENCE_MAX_LENGTH = 64

#: A server-generated idempotency key: exactly 64 lowercase hex digits.
IDEMPOTENCY_KEY_LENGTH = 64

#: The most payment intents -- any status -- one invoice may hold. A technical
#: bound on the intent page, the rows a write locks and every stale-state
#: token, exactly as ``MAX_INVOICE_COLLECTIONS`` bounds manual payments.
MAX_INVOICE_PAYMENT_INTENTS = 25

_PENDING = PaymentIntentStatus.PENDING.value
_SUCCEEDED = PaymentIntentStatus.PROVIDER_SUCCEEDED.value
_FAILED = PaymentIntentStatus.PROVIDER_FAILED.value
_CANCELLED = PaymentIntentStatus.CANCELLED.value

#: An active intent freezes its invoice's lines and cancellation.
ACTIVE_PAYMENT_INTENT_STATUSES = (_PENDING, _SUCCEEDED)
#: A terminal intent never changes again and freezes nothing.
TERMINAL_PAYMENT_INTENT_STATUSES = (_FAILED, _CANCELLED)

_STATUS_VALUES = tuple(status.value for status in PaymentIntentStatus)
_IDEMPOTENCY_KEY_SHAPE = re.compile(r"[0-9a-f]{64}")
_REFERENCE_SHAPE = re.compile(r"[\x21-\x7e]+")


def _closed_set_sql(column, values):
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


#: Who recorded the provider's result and when are recorded together or not
#: at all.
_PROVIDER_RESULT_PAIR_SQL = (
    "(provider_result_at IS NULL AND provider_result_by_id IS NULL)"
    " OR (provider_result_at IS NOT NULL AND provider_result_by_id IS NOT NULL)"
)

#: A terminal intent carries the moment it became terminal; an active one
#: does not.
_TERMINAL_STATE_SQL = (
    "(status IN ('pending', 'provider_succeeded') AND terminal_at IS NULL)"
    " OR (status IN ('provider_failed', 'cancelled') AND terminal_at IS NOT NULL)"
)

#: The lifecycle truth table: a pending intent has no result and no
#: cancellation; a provider result is recorded with its moment (and, for a
#: failure, that moment is when it became terminal); a cancellation records
#: who cancelled and no provider result.
_LIFECYCLE_STATE_SQL = (
    "(status = 'pending' AND provider_result_at IS NULL AND cancelled_by_id IS NULL)"
    " OR (status = 'provider_succeeded' AND provider_result_at IS NOT NULL"
    " AND cancelled_by_id IS NULL)"
    " OR (status = 'provider_failed' AND provider_result_at IS NOT NULL"
    " AND terminal_at = provider_result_at AND cancelled_by_id IS NULL)"
    " OR (status = 'cancelled' AND provider_result_at IS NULL AND cancelled_by_id IS NOT NULL)"
)

_TIMESTAMPS_ORDERED_SQL = (
    "updated_at >= created_at"
    " AND (provider_result_at IS NULL"
    " OR (provider_result_at >= created_at AND updated_at >= provider_result_at))"
    " AND (terminal_at IS NULL OR (terminal_at >= created_at AND updated_at >= terminal_at))"
)


class PaymentIntent(db.Model):
    """One online-payment intent for one issued
    :class:`~app.models.invoice.Invoice` (Phase 5 / M06).

    **An intent is not a payment.** It records that the provider was asked to
    collect the invoice's outstanding amount and what the provider has
    reported since. No :class:`~app.models.payment_transaction.PaymentTransaction`,
    receipt, audit event or balance change is ever derived from it in M06: a
    reported success stays ``provider_succeeded`` until Phase 5 / M07 verifies
    a signed webhook.

    **It belongs to the invoice, and only the invoice.** ``invoice_id`` is a
    plain foreign key; the assignment, Enrollment, Student and academic chain
    are read through it and are not duplicated here.

    **What was asked is fixed at creation**: the provider (``mock`` only), its
    opaque, unique ``provider_reference``, the opaque, unique, server-generated
    ``idempotency_key``, and the exact ``amount`` -- the invoice's outstanding
    balance at that moment, within M02's money bounds -- in ``LYD``.

    **Lifecycle.** See :class:`~app.models.enums.PaymentIntentStatus`::

        pending -> provider_succeeded | provider_failed | cancelled

    ``pending`` and ``provider_succeeded`` are **active** and freeze the
    invoice's lines and cancellation; ``provider_failed`` and ``cancelled``
    are **terminal**, carry ``terminal_at``, and never change again. At most
    one active intent exists per invoice -- an application invariant proved
    under the invoice lock, because MySQL has no portable partial unique index.

    **No payment credential is stored.** There is no card number, CVV/CVC,
    PIN, account number, bank credential, proof, customer or provider secret
    column.

    **Nothing is ever physically deleted.** The ORM guards refuse a delete, a
    change to what was asked, any change to a decided intent, a transition out
    of ``pending`` to anything but a decision, a decision that does not move
    ``version`` by exactly one, and bulk ``UPDATE`` / ``DELETE`` statements.

    Database invariants (final defense only):

    - ``public_id``, ``uq_payment_intents_provider_reference`` and
      ``uq_payment_intents_idempotency_key`` unique;
    - ``ck_payment_intents_status_valid``, ``_provider_valid``,
      ``_currency_code``, ``_amount_range`` and ``_version_positive``;
    - ``ck_payment_intents_provider_reference_present`` and
      ``ck_payment_intents_idempotency_key_length``;
    - ``ck_payment_intents_provider_result_pair`` and
      ``ck_payment_intents_terminal_state``;
    - ``ck_payment_intents_lifecycle_state`` and
      ``ck_payment_intents_timestamps_ordered``.

    Indexes: ``ix_payment_intents_invoice_id_id`` (``invoice_id``, ``id``) --
    one invoice's rows in id order, the rows to lock and the foreign key;
    ``ix_payment_intents_status_id`` -- the filtered overview; and one index
    per ``users`` foreign key. **No MySQL execution plan has been measured for
    this table.** No ORM relationship is declared in either direction.
    """

    __tablename__ = "payment_intents"
    __table_args__ = (
        db.UniqueConstraint("provider_reference", name="uq_payment_intents_provider_reference"),
        db.UniqueConstraint("idempotency_key", name="uq_payment_intents_idempotency_key"),
        db.CheckConstraint(
            _closed_set_sql("status", _STATUS_VALUES), name="ck_payment_intents_status_valid"
        ),
        db.CheckConstraint(
            _closed_set_sql("provider", PAYMENT_INTENT_PROVIDERS),
            name="ck_payment_intents_provider_valid",
        ),
        db.CheckConstraint(
            f"currency_code = '{CURRENCY_CODE}'", name="ck_payment_intents_currency_code"
        ),
        db.CheckConstraint(
            f"amount >= {MIN_AMOUNT} AND amount <= {MAX_AMOUNT}",
            name="ck_payment_intents_amount_range",
        ),
        db.CheckConstraint("version > 0", name="ck_payment_intents_version_positive"),
        db.CheckConstraint(
            "LENGTH(provider_reference) > 0", name="ck_payment_intents_provider_reference_present"
        ),
        db.CheckConstraint(
            f"LENGTH(idempotency_key) = {IDEMPOTENCY_KEY_LENGTH}",
            name="ck_payment_intents_idempotency_key_length",
        ),
        db.CheckConstraint(
            _PROVIDER_RESULT_PAIR_SQL, name="ck_payment_intents_provider_result_pair"
        ),
        db.CheckConstraint(_TERMINAL_STATE_SQL, name="ck_payment_intents_terminal_state"),
        db.CheckConstraint(_LIFECYCLE_STATE_SQL, name="ck_payment_intents_lifecycle_state"),
        db.CheckConstraint(_TIMESTAMPS_ORDERED_SQL, name="ck_payment_intents_timestamps_ordered"),
        db.Index("ix_payment_intents_invoice_id_id", "invoice_id", "id"),
        db.Index("ix_payment_intents_status_id", "status", "id"),
        db.Index("ix_payment_intents_created_by_id", "created_by_id"),
        db.Index("ix_payment_intents_provider_result_by_id", "provider_result_by_id"),
        db.Index("ix_payment_intents_cancelled_by_id", "cancelled_by_id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    invoice_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("invoices.id"),
        nullable=False,
    )
    provider = db.Column(db.String(16), nullable=False)
    provider_reference = db.Column(db.String(PROVIDER_REFERENCE_MAX_LENGTH), nullable=False)
    idempotency_key = db.Column(db.String(IDEMPOTENCY_KEY_LENGTH), nullable=False)
    status = db.Column(db.String(32), nullable=False)
    currency_code = db.Column(db.String(3), nullable=False, default=CURRENCY_CODE)
    #: Exact fixed point, never a float -- see :mod:`app.services.money`.
    amount = db.Column(
        db.DECIMAL(precision=AMOUNT_PRECISION, scale=AMOUNT_SCALE, asdecimal=True),
        nullable=False,
    )
    created_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    #: When, and by whose browser return, the provider's success or failure
    #: was read and recorded.
    provider_result_at = db.Column(db.DateTime, nullable=True)
    provider_result_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    #: When a failed or cancelled intent became terminal.
    terminal_at = db.Column(db.DateTime, nullable=True)
    cancelled_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Defaults are defense in depth only; every write supplies its own
    #: post-lock whole-second moment. Deliberately no ``onupdate`` hook.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("provider")
    def validate_provider(self, _key, value):
        if value not in PAYMENT_INTENT_PROVIDERS:
            raise ValueError(f"Invalid payment intent provider: {value}")
        return value

    @validates("provider_reference")
    def validate_provider_reference(self, _key, value):
        if (
            not isinstance(value, str)
            or len(value) > PROVIDER_REFERENCE_MAX_LENGTH
            or _REFERENCE_SHAPE.fullmatch(value) is None
        ):
            raise ValueError("A provider reference must be 1 to 64 visible ASCII characters")
        return value

    @validates("idempotency_key")
    def validate_idempotency_key(self, _key, value):
        if not isinstance(value, str) or _IDEMPOTENCY_KEY_SHAPE.fullmatch(value) is None:
            raise ValueError("An idempotency key must be 64 lowercase hex digits")
        return value

    @validates("status")
    def validate_status(self, _key, value):
        if value not in _STATUS_VALUES:
            raise ValueError(f"Invalid payment intent status: {value}")
        return value

    @validates("currency_code")
    def validate_currency_code(self, _key, value):
        if value != CURRENCY_CODE:
            raise ValueError(f"Payment intent currency must be {CURRENCY_CODE}")
        return value

    @validates("amount")
    def validate_amount(self, _key, value):
        return validate_amount(value)

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("Payment intent version must be a positive integer")
        return value

    @property
    def is_pending(self):
        return self.status == _PENDING

    @property
    def is_active(self):
        return self.status in ACTIVE_PAYMENT_INTENT_STATUSES

    @property
    def is_terminal(self):
        return self.status in TERMINAL_PAYMENT_INTENT_STATUSES


# ---------------------------------------------------------------------------
# ORM guards
# ---------------------------------------------------------------------------

#: What was asked of the provider. It never changes, whatever the status.
_ASKED_COLUMNS = (
    "public_id",
    "invoice_id",
    "provider",
    "provider_reference",
    "idempotency_key",
    "currency_code",
    "amount",
    "created_by_id",
    "created_at",
)
_DECISIONS = frozenset({_SUCCEEDED, _FAILED, _CANCELLED})


@event.listens_for(PaymentIntent, "before_update")
def _refuse_rewriting_a_payment_intent(_mapper, connection, target):
    if not is_changing(target):
        return
    stored = stored_row(connection, target, ("status", "version") + _ASKED_COLUMNS)
    if stored is None:
        return
    if stored["status"] != _PENDING:
        raise FinancialHistoryError(
            "A decided payment intent never changes in Phase 5 / M06; a provider-succeeded "
            "intent awaits the verified-webhook flow"
        )
    for key in _ASKED_COLUMNS:
        setting, value = pending_value(target, key)
        if setting and value != stored[key]:
            raise FinancialHistoryError(f"A payment intent's {key} never changes once created")
    setting, status = pending_value(target, "status")
    if not setting or status not in _DECISIONS:
        raise FinancialHistoryError(
            "A pending payment intent only ever records a provider result or a cancellation"
        )
    setting, version = pending_value(target, "version")
    if not setting or version != stored["version"] + 1:
        raise FinancialHistoryError("A payment intent decision moves its version by exactly one")


@event.listens_for(PaymentIntent, "before_delete")
def _refuse_deleting_a_payment_intent(_mapper, _connection, _target):
    raise FinancialHistoryError("A payment intent is never deleted")


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_rewrites_of_payment_intents(orm_execute_state):
    """Mapper events do not see ``session.execute(update(...))`` or
    ``delete(...)``; payment intents change only through the guarded ORM
    path and are never deleted."""
    if not (orm_execute_state.is_update or orm_execute_state.is_delete):
        return
    table = getattr(orm_execute_state.statement, "table", None)
    if getattr(table, "name", None) == PaymentIntent.__tablename__:
        raise FinancialHistoryError("payment_intents rows cannot be rewritten in bulk")
