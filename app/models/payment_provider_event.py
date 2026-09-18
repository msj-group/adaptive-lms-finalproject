import re
import uuid
from datetime import datetime

from sqlalchemy import event
from sqlalchemy.orm import Session, validates

from app.extensions import db
from app.models.enums import ProviderEventOutcome, ProviderEventType
from app.models.invoice import FinancialHistoryError
from app.models.payment_intent import PAYMENT_INTENT_PROVIDERS
from app.models.submission_feedback import whole_second_utc
from app.services.money import (
    AMOUNT_PRECISION,
    AMOUNT_SCALE,
    CURRENCY_CODE,
    MAX_AMOUNT,
    MIN_AMOUNT,
    validate_amount,
)

#: The ``provider_event_id`` column's width.
PROVIDER_EVENT_ID_MAX_LENGTH = 64

#: A payload digest is exactly 64 lowercase hex digits of SHA-256.
PAYLOAD_DIGEST_LENGTH = 64

_TYPE_VALUES = tuple(member.value for member in ProviderEventType)
_OUTCOME_VALUES = tuple(member.value for member in ProviderEventOutcome)
_SUCCEEDED = ProviderEventType.PAYMENT_SUCCEEDED.value
_FAILED_TYPE = ProviderEventType.PAYMENT_FAILED.value
_CONFIRMED = ProviderEventOutcome.CONFIRMED.value
_FAILED = ProviderEventOutcome.FAILED.value
_DIGEST_SHAPE = re.compile(r"[0-9a-f]{64}")
_EVENT_ID_SHAPE = re.compile(r"[\x21-\x7e]+")


def _closed_set_sql(column, values):
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


#: A ``confirmed`` event created exactly one online collection, from a
#: ``payment.succeeded`` event; no other outcome names a collection.
_OUTCOME_LINK_SQL = (
    "(outcome = 'confirmed' AND event_type = 'payment.succeeded'"
    " AND payment_transaction_id IS NOT NULL)"
    " OR (outcome <> 'confirmed' AND payment_transaction_id IS NULL)"
)

#: Only a ``payment.failed`` event can fail an intent.
_OUTCOME_TYPE_SQL = "outcome <> 'failed' OR event_type = 'payment.failed'"

_TIMESTAMPS_ORDERED_SQL = "processed_at >= received_at AND created_at >= received_at"


class PaymentProviderEvent(db.Model):
    """One verified, signed provider webhook event, as processed
    (Phase 5 / M07). The provider-event inbox.

    **Stored only after verification.** A delivery whose signature, freshness
    or exact shape fails is refused and never stored. A stored event keeps the
    normalized, non-sensitive facts only: the provider, its opaque event id,
    the payment intent it names, its type, exact amount and currency, the
    provider's own moment, when the LMS received and processed it, the SHA-256
    digest of the raw body, the outcome and -- for a confirmation only -- the
    one online collection it created. **No raw body, signature, secret,
    provider reference, card or bank data is stored.**

    **One event, at most one collection.** ``(provider, provider_event_id)`` is
    unique, so an event is stored once however often it is delivered, and a
    re-delivery with a different digest is refused; ``payment_transaction_id``
    is unique and present exactly for the ``confirmed`` outcome
    (``ck_payment_provider_events_outcome_link``), and the collection's own
    ``payment_intent_id`` is unique, so neither an event nor an intent can ever
    create a second collection.

    **Immutable.** An event is appended once, in the transaction that applies
    its outcome, and never changes or disappears: the ORM guards refuse an
    update, a delete and bulk ``UPDATE`` / ``DELETE`` statements. It has no
    version because nothing about it can move.

    Database invariants (final defense only): ``public_id``,
    ``uq_payment_provider_events_provider_event`` and
    ``uq_payment_provider_events_payment_transaction_id`` unique;
    ``ck_payment_provider_events_provider_valid``, ``_event_type_valid``,
    ``_outcome_valid``, ``_currency_code``, ``_amount_range``,
    ``_event_id_present``, ``_payload_digest_length``, ``_outcome_link``,
    ``_outcome_type`` and ``_timestamps_ordered``.

    Indexes: ``ix_payment_provider_events_intent_id_id`` (``payment_intent_id``,
    ``id``) -- one intent's events in order and the foreign key;
    ``ix_payment_provider_events_outcome_id`` -- events needing
    reconciliation. **No MySQL execution plan has been measured for this
    table.** No ORM relationship is declared in either direction.
    """

    __tablename__ = "payment_provider_events"
    __table_args__ = (
        db.UniqueConstraint(
            "provider", "provider_event_id", name="uq_payment_provider_events_provider_event"
        ),
        db.UniqueConstraint(
            "payment_transaction_id", name="uq_payment_provider_events_payment_transaction_id"
        ),
        db.CheckConstraint(
            _closed_set_sql("provider", PAYMENT_INTENT_PROVIDERS),
            name="ck_payment_provider_events_provider_valid",
        ),
        db.CheckConstraint(
            _closed_set_sql("event_type", _TYPE_VALUES),
            name="ck_payment_provider_events_event_type_valid",
        ),
        db.CheckConstraint(
            _closed_set_sql("outcome", _OUTCOME_VALUES),
            name="ck_payment_provider_events_outcome_valid",
        ),
        db.CheckConstraint(
            f"currency_code = '{CURRENCY_CODE}'", name="ck_payment_provider_events_currency_code"
        ),
        db.CheckConstraint(
            f"amount >= {MIN_AMOUNT} AND amount <= {MAX_AMOUNT}",
            name="ck_payment_provider_events_amount_range",
        ),
        db.CheckConstraint(
            "LENGTH(provider_event_id) > 0", name="ck_payment_provider_events_event_id_present"
        ),
        db.CheckConstraint(
            f"LENGTH(payload_digest) = {PAYLOAD_DIGEST_LENGTH}",
            name="ck_payment_provider_events_payload_digest_length",
        ),
        db.CheckConstraint(_OUTCOME_LINK_SQL, name="ck_payment_provider_events_outcome_link"),
        db.CheckConstraint(_OUTCOME_TYPE_SQL, name="ck_payment_provider_events_outcome_type"),
        db.CheckConstraint(
            _TIMESTAMPS_ORDERED_SQL, name="ck_payment_provider_events_timestamps_ordered"
        ),
        db.Index("ix_payment_provider_events_intent_id_id", "payment_intent_id", "id"),
        db.Index("ix_payment_provider_events_outcome_id", "outcome", "id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    provider = db.Column(db.String(16), nullable=False)
    provider_event_id = db.Column(db.String(PROVIDER_EVENT_ID_MAX_LENGTH), nullable=False)
    payment_intent_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("payment_intents.id"),
        nullable=False,
    )
    event_type = db.Column(db.String(32), nullable=False)
    currency_code = db.Column(db.String(3), nullable=False, default=CURRENCY_CODE)
    #: Exact fixed point, never a float -- see :mod:`app.services.money`.
    amount = db.Column(
        db.DECIMAL(precision=AMOUNT_PRECISION, scale=AMOUNT_SCALE, asdecimal=True),
        nullable=False,
    )
    #: The provider's own clock -- information only; every accounting moment
    #: is the LMS's.
    provider_occurred_at = db.Column(db.DateTime, nullable=False)
    received_at = db.Column(db.DateTime, nullable=False)
    processed_at = db.Column(db.DateTime, nullable=False)
    payload_digest = db.Column(db.String(PAYLOAD_DIGEST_LENGTH), nullable=False)
    outcome = db.Column(db.String(32), nullable=False)
    payment_transaction_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("payment_transactions.id"),
        nullable=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("provider")
    def validate_provider(self, _key, value):
        if value not in PAYMENT_INTENT_PROVIDERS:
            raise ValueError(f"Invalid provider event provider: {value}")
        return value

    @validates("provider_event_id")
    def validate_provider_event_id(self, _key, value):
        if (
            not isinstance(value, str)
            or len(value) > PROVIDER_EVENT_ID_MAX_LENGTH
            or _EVENT_ID_SHAPE.fullmatch(value) is None
        ):
            raise ValueError("A provider event id must be 1 to 64 visible ASCII characters")
        return value

    @validates("event_type")
    def validate_event_type(self, _key, value):
        if value not in _TYPE_VALUES:
            raise ValueError(f"Invalid provider event type: {value}")
        return value

    @validates("outcome")
    def validate_outcome(self, _key, value):
        if value not in _OUTCOME_VALUES:
            raise ValueError(f"Invalid provider event outcome: {value}")
        return value

    @validates("currency_code")
    def validate_currency_code(self, _key, value):
        if value != CURRENCY_CODE:
            raise ValueError(f"Provider event currency must be {CURRENCY_CODE}")
        return value

    @validates("amount")
    def validate_amount(self, _key, value):
        return validate_amount(value)

    @validates("payload_digest")
    def validate_payload_digest(self, _key, value):
        if not isinstance(value, str) or _DIGEST_SHAPE.fullmatch(value) is None:
            raise ValueError("A payload digest must be 64 lowercase hex digits")
        return value

    @validates("provider_occurred_at", "received_at", "processed_at")
    def validate_moment(self, _key, value):
        if not isinstance(value, datetime) or value.tzinfo is not None or value.microsecond:
            raise ValueError("A provider event moment is a naive-UTC whole-second datetime")
        return value

    @property
    def needs_reconciliation(self):
        return self.outcome == ProviderEventOutcome.RECONCILIATION_REQUIRED.value


# ---------------------------------------------------------------------------
# ORM guards
# ---------------------------------------------------------------------------


@event.listens_for(PaymentProviderEvent, "before_update")
def _refuse_editing_a_provider_event(_mapper, _connection, _target):
    raise FinancialHistoryError("A provider event is immutable")


@event.listens_for(PaymentProviderEvent, "before_delete")
def _refuse_deleting_a_provider_event(_mapper, _connection, _target):
    raise FinancialHistoryError("A provider event is never deleted")


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_rewrites_of_provider_events(orm_execute_state):
    """Mapper events do not see ``session.execute(update(...))`` or
    ``delete(...)``; provider events are never changed or deleted."""
    if not (orm_execute_state.is_update or orm_execute_state.is_delete):
        return
    table = getattr(orm_execute_state.statement, "table", None)
    if getattr(table, "name", None) == PaymentProviderEvent.__tablename__:
        raise FinancialHistoryError("payment_provider_events rows cannot be rewritten in bulk")
