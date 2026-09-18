"""Signed, expiring, exact-shape stale-state tokens for Administrator manual
payments (Phase 5 / M05).

Five purposes, each minted under its **own** salt carrying the ``phase5-m05``
milestone marker:

- ``payment-cash-record`` / ``payment-bank-record`` -- the acting
  Administrator, the invoice's public id and ``version``, and the invoice's
  complete current payment state.
- ``payment-bank-confirm`` / ``payment-bank-reject`` -- the actor, the
  invoice's public id, the pending transaction's public id and ``version``,
  and the payment state.
- ``payment-reverse`` -- the actor, the invoice's public id and ``version``,
  the original collection's public id and ``version``, and the state of its
  receipt (or ``None`` when it has none).

**A payment state** is ``[public_id, status, version]`` for every
transaction of the invoice, in ascending internal id order. Every recording,
confirmation, rejection and reversal adds a row or moves a status and a
version, so a record, confirm or reject token goes stale as soon as anything in
the invoice's payments changed after its page was rendered, and a replayed
token is stale because its own write changed the state. A reversal voids the
original's receipt, which moves the receipt state a reverse token binds.

A token minted under any other salt in this project fails signature
verification here; a token for one purpose fails on the other four; a payload
that is not exactly the purpose's shape, carries a non-``int`` or out-of-range
version (``bool`` excluded), an unknown status or an ill-formed state is
refused; a token older than :data:`TOKEN_MAX_AGE_SECONDS` is expired. Every
failure is the same ``None`` and is treated exactly like an outdated token.

**A token is authenticated, not encrypted.** Its payload carries public
identifiers, statuses, versions and a purpose only -- no internal id, name,
amount, total, reference, reason, snapshot or authorization fact.
"""

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

from app.models import MAX_INVOICE_PAYMENT_ROWS, PaymentTransactionStatus, ReceiptStatus

PURPOSE_CASH_RECORD = "payment-cash-record"
PURPOSE_BANK_RECORD = "payment-bank-record"
PURPOSE_BANK_CONFIRM = "payment-bank-confirm"
PURPOSE_BANK_REJECT = "payment-bank-reject"
PURPOSE_REVERSE = "payment-reverse"

PURPOSES = (
    PURPOSE_CASH_RECORD,
    PURPOSE_BANK_RECORD,
    PURPOSE_BANK_CONFIRM,
    PURPOSE_BANK_REJECT,
    PURPOSE_REVERSE,
)

_SALTS = {purpose: f"admin.{purpose}.phase5-m05.v1" for purpose in PURPOSES}

_RECORD_FIELDS = ("purpose", "actor_public_id", "invoice_public_id", "invoice_version",
                  "payment_state")
_DECISION_FIELDS = ("purpose", "actor_public_id", "invoice_public_id", "payment_public_id",
                    "payment_version", "payment_state")

#: The exact key set each payload must carry -- no more, no fewer.
_FIELDS = {
    PURPOSE_CASH_RECORD: _RECORD_FIELDS,
    PURPOSE_BANK_RECORD: _RECORD_FIELDS,
    PURPOSE_BANK_CONFIRM: _DECISION_FIELDS,
    PURPOSE_BANK_REJECT: _DECISION_FIELDS,
    PURPOSE_REVERSE: (
        "purpose",
        "actor_public_id",
        "invoice_public_id",
        "invoice_version",
        "payment_public_id",
        "payment_version",
        "receipt_state",
    ),
}

_VERSION_FIELDS = frozenset({"invoice_version", "payment_version"})
_PAYMENT_STATE_FIELD = "payment_state"
_RECEIPT_STATE_FIELD = "receipt_state"
_PAYMENT_STATUSES = frozenset(status.value for status in PaymentTransactionStatus)
_RECEIPT_STATUSES = frozenset(status.value for status in ReceiptStatus)

#: How long a rendered form stays usable.
TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60

#: A payment state describes up to :data:`MAX_INVOICE_PAYMENT_ROWS` rows.
_MAX_TOKEN_LENGTH = 8192
_MAX_TEXT_FIELD_LENGTH = 64
_MAX_VERSION = 2**31 - 1


def _serializer(purpose):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALTS[purpose])


def _valid_version(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _MAX_VERSION


def _valid_text(value):
    return isinstance(value, str) and bool(value) and len(value) <= _MAX_TEXT_FIELD_LENGTH


def _valid_triple(value, statuses):
    return (
        isinstance(value, list)
        and len(value) == 3
        and _valid_text(value[0])
        and isinstance(value[1], str)
        and value[1] in statuses
        and _valid_version(value[2])
    )


def _valid_payment_state(value):
    """A list of at most :data:`MAX_INVOICE_PAYMENT_ROWS` distinct
    ``[public_id, status, version]`` triples."""
    if not isinstance(value, list) or len(value) > MAX_INVOICE_PAYMENT_ROWS:
        return False
    if not all(_valid_triple(entry, _PAYMENT_STATUSES) for entry in value):
        return False
    return len({entry[0] for entry in value}) == len(value)


def _valid_receipt_state(value):
    return value is None or _valid_triple(value, _RECEIPT_STATUSES)


def payment_state(rows):
    """The ``payment_state`` value for `rows` -- an invoice's transactions in
    ascending internal id order -- exactly as a decoded token carries it."""
    return [[row.public_id, row.status, row.version] for row in rows]


def receipt_state(receipt):
    """The ``receipt_state`` value for `receipt`, or ``None``."""
    return None if receipt is None else [receipt.public_id, receipt.status, receipt.version]


def make_token(purpose, **payload):
    """Sign one exact-shape M05 token from freshly read persisted state."""
    body = dict(payload, purpose=purpose)
    if set(body) != set(_FIELDS[purpose]):  # pragma: no cover -- programming error
        raise ValueError(f"token payload does not match the {purpose} shape")
    return _serializer(purpose).dumps(body)


def load_token(token, purpose):
    """The payload, or ``None`` for a missing, oversized, malformed, invalidly
    signed, expired, wrong-salt, wrong-purpose or wrong-shaped token."""
    if purpose not in _SALTS:
        return None
    if not token or not isinstance(token, str) or len(token) > _MAX_TOKEN_LENGTH:
        return None
    try:
        payload = _serializer(purpose).loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except BadData:
        return None
    fields = _FIELDS[purpose]
    if not isinstance(payload, dict) or set(payload) != set(fields):
        return None
    if payload["purpose"] != purpose:
        return None
    for field in fields:
        value = payload[field]
        if field in _VERSION_FIELDS:
            valid = _valid_version(value)
        elif field == _PAYMENT_STATE_FIELD:
            valid = _valid_payment_state(value)
        elif field == _RECEIPT_STATE_FIELD:
            valid = _valid_receipt_state(value)
        else:
            valid = _valid_text(value)
        if not valid:
            return None
    return payload


def token_is_stale(token, purpose, **expected):
    """``True`` when `token` does not exactly describe `expected`.

    `expected` must name **every** bound field, and its values must come from
    the rows this request locked or read after its locks.
    """
    if set(expected) != set(_FIELDS[purpose]) - {"purpose"}:  # pragma: no cover
        raise ValueError(f"every {purpose} field must be compared")
    payload = load_token(token, purpose)
    if payload is None:
        return True
    return any(payload[field] != value for field, value in expected.items())
