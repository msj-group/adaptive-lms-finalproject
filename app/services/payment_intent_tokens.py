"""Signed, exact-shape stale-state tokens, the signed Mock/Sandbox checkout
context and the derived idempotency key of Administrator payment intents
(Phase 5 / M06).

Three purposes, each minted under its **own** salt carrying the ``phase5-m06``
milestone marker:

- ``payment-intent-create`` -- the acting Administrator, the invoice's public
  id and ``version``, its **payment state** (M05's ``[public_id, status,
  version]`` of every manual transaction, ascending internal id), its **intent
  state** (the same triple for every payment intent, ascending internal id)
  and the provider mode. Valid for :data:`TOKEN_MAX_AGE_SECONDS`.
- ``payment-intent-cancel`` -- the actor, the invoice's public id and
  ``version``, the target intent's public id, ``version`` and status, the
  intent state and the provider mode. Valid for :data:`TOKEN_MAX_AGE_SECONDS`.
- ``payment-intent-checkout`` -- the **checkout context**: only the Mock
  intent's public id, ``version``, status, provider reference, an explicit
  ``expires_at`` and the purpose. Valid for
  :data:`CHECKOUT_CONTEXT_MAX_AGE_SECONDS`, by its signed timestamp *and* its
  signed ``expires_at``.

Every creation adds an intent and every decision moves one intent's status and
version, so a replayed create or cancel token is stale, and a checkout context
is stale as soon as its intent has been decided. A token minted under any other
salt in this project fails signature verification here; a token for one purpose
fails on the other two; a payload that is not exactly its purpose's shape, or
carries a non-``int`` or out-of-range version (``bool`` excluded), an unknown
status or mode, an ill-formed state or an expired ``expires_at``, is refused.
Every failure is the same ``None`` and is treated exactly like an outdated
token.

**A token is authenticated, not encrypted.** Its payload carries public
identifiers, statuses, versions, the mode, an opaque provider reference and an
expiry only -- no internal id, name, amount, outstanding amount, invoice line,
credential, secret or authorization fact.

**The idempotency key** of a creation is derived, never chosen by the browser:
an HMAC-SHA-256 under the application secret over the canonical JSON of the
*verified* create payload (:func:`idempotency_key_for`). The same logical
request -- the same actor asking for the same invoice in the same payment and
intent state -- always yields the same 64-hex-digit key, so a replayed or
interrupted creation resolves to the intent it already created; any other
state yields another key. The key reveals nothing of the payload or secret.
"""

import hashlib
import hmac
import json
import time

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

from app.models import (
    MAX_INVOICE_PAYMENT_INTENTS,
    MAX_INVOICE_PAYMENT_ROWS,
    PaymentIntentStatus,
    PaymentTransactionStatus,
)
from app.services.payment_providers import PROVIDER_MODES

PURPOSE_CREATE = "payment-intent-create"
PURPOSE_CANCEL = "payment-intent-cancel"
PURPOSE_CHECKOUT = "payment-intent-checkout"

PURPOSES = (PURPOSE_CREATE, PURPOSE_CANCEL, PURPOSE_CHECKOUT)

_SALTS = {purpose: f"admin.{purpose}.phase5-m06.v1" for purpose in PURPOSES}

#: The exact key set each payload must carry -- no more, no fewer.
_FIELDS = {
    PURPOSE_CREATE: (
        "purpose",
        "actor_public_id",
        "invoice_public_id",
        "invoice_version",
        "payment_state",
        "intent_state",
        "provider_mode",
    ),
    PURPOSE_CANCEL: (
        "purpose",
        "actor_public_id",
        "invoice_public_id",
        "invoice_version",
        "intent_public_id",
        "intent_version",
        "intent_status",
        "intent_state",
        "provider_mode",
    ),
    PURPOSE_CHECKOUT: (
        "purpose",
        "intent_public_id",
        "intent_version",
        "intent_status",
        "provider_reference",
        "expires_at",
    ),
}

_VERSION_FIELDS = frozenset({"invoice_version", "intent_version"})
_PAYMENT_STATUSES = frozenset(status.value for status in PaymentTransactionStatus)
_INTENT_STATUSES = frozenset(status.value for status in PaymentIntentStatus)

#: How long a rendered create or cancel form stays usable.
TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60

#: How long one Mock/Sandbox checkout context stays usable.
CHECKOUT_CONTEXT_MAX_AGE_SECONDS = 30 * 60

#: A state describes at most 50 payments plus 25 intents.
_MAX_TOKEN_LENGTH = 12288
_MAX_TEXT_FIELD_LENGTH = 64
_MAX_VERSION = 2**31 - 1

_IDEMPOTENCY_CONTEXT = b"phase5-m06.payment-intent.idempotency-key.v1\x00"


def _serializer(purpose):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALTS[purpose])


def _max_age(purpose):
    return CHECKOUT_CONTEXT_MAX_AGE_SECONDS if purpose == PURPOSE_CHECKOUT else TOKEN_MAX_AGE_SECONDS


def _valid_version(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _MAX_VERSION


def _valid_text(value):
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= _MAX_TEXT_FIELD_LENGTH
        and all("\x21" <= character <= "\x7e" for character in value)
    )


def _valid_triple(value, statuses):
    return (
        isinstance(value, list)
        and len(value) == 3
        and _valid_text(value[0])
        and isinstance(value[1], str)
        and value[1] in statuses
        and _valid_version(value[2])
    )


def _valid_state(value, statuses, bound):
    """A list of at most `bound` distinct ``[public_id, status, version]``
    triples."""
    if not isinstance(value, list) or len(value) > bound:
        return False
    if not all(_valid_triple(entry, statuses) for entry in value):
        return False
    return len({entry[0] for entry in value}) == len(value)


def _valid_expiry(value):
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    remaining = value - int(time.time())
    return 0 <= remaining <= CHECKOUT_CONTEXT_MAX_AGE_SECONDS


def _field_valid(field, value):
    if field in _VERSION_FIELDS:
        return _valid_version(value)
    if field == "payment_state":
        return _valid_state(value, _PAYMENT_STATUSES, MAX_INVOICE_PAYMENT_ROWS)
    if field == "intent_state":
        return _valid_state(value, _INTENT_STATUSES, MAX_INVOICE_PAYMENT_INTENTS)
    if field == "intent_status":
        return isinstance(value, str) and value in _INTENT_STATUSES
    if field == "provider_mode":
        return isinstance(value, str) and value in PROVIDER_MODES
    if field == "expires_at":
        return _valid_expiry(value)
    return _valid_text(value)


def payment_state(rows):
    """The ``payment_state`` value for an invoice's manual transactions in
    ascending internal id order, exactly as M05's tokens carry it."""
    return [[row.public_id, row.status, row.version] for row in rows]


def intent_state(rows):
    """The ``intent_state`` value for an invoice's payment intents in
    ascending internal id order, exactly as a decoded token carries it."""
    return [[row.public_id, row.status, row.version] for row in rows]


def make_token(purpose, **payload):
    """Sign one exact-shape M06 create or cancel token from freshly read
    persisted state."""
    if purpose == PURPOSE_CHECKOUT:  # pragma: no cover -- programming error
        raise ValueError("a checkout context is minted by make_checkout_context")
    body = dict(payload, purpose=purpose)
    if set(body) != set(_FIELDS[purpose]):  # pragma: no cover -- programming error
        raise ValueError(f"token payload does not match the {purpose} shape")
    return _serializer(purpose).dumps(body)


def make_checkout_context(intent):
    """Sign the checkout context of one Mock `intent`, expiring
    :data:`CHECKOUT_CONTEXT_MAX_AGE_SECONDS` from now."""
    return _serializer(PURPOSE_CHECKOUT).dumps(
        {
            "purpose": PURPOSE_CHECKOUT,
            "intent_public_id": intent.public_id,
            "intent_version": intent.version,
            "intent_status": intent.status,
            "provider_reference": intent.provider_reference,
            "expires_at": int(time.time()) + CHECKOUT_CONTEXT_MAX_AGE_SECONDS,
        }
    )


def load_token(token, purpose):
    """The payload, or ``None`` for a missing, oversized, malformed, invalidly
    signed, expired, wrong-salt, wrong-purpose or wrong-shaped token."""
    if purpose not in _SALTS:
        return None
    if not token or not isinstance(token, str) or len(token) > _MAX_TOKEN_LENGTH:
        return None
    try:
        payload = _serializer(purpose).loads(token, max_age=_max_age(purpose))
    except BadData:
        return None
    fields = _FIELDS[purpose]
    if not isinstance(payload, dict) or set(payload) != set(fields):
        return None
    if payload["purpose"] != purpose:
        return None
    if not all(_field_valid(field, payload[field]) for field in fields):
        return None
    return payload


def token_is_stale(token, purpose, **expected):
    """``True`` when `token` does not exactly describe `expected`.

    `expected` must name **every** bound field (a checkout context's
    ``expires_at`` excepted, which :func:`load_token` has already proved), and
    its values must come from the rows this request locked or read after its
    locks.
    """
    bound = set(_FIELDS[purpose]) - {"purpose", "expires_at"}
    if set(expected) != bound:  # pragma: no cover -- programming error
        raise ValueError(f"every {purpose} field must be compared")
    payload = load_token(token, purpose)
    if payload is None:
        return True
    return any(payload[field] != value for field, value in expected.items())


def idempotency_key_for(payload):
    """The 64-lowercase-hex-digit idempotency key of one **verified**
    ``payment-intent-create`` payload. See the module docstring."""
    if set(payload) != set(_FIELDS[PURPOSE_CREATE]):  # pragma: no cover
        raise ValueError("an idempotency key is derived only from a create payload")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    secret = current_app.config["SECRET_KEY"]
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(
        secret, _IDEMPOTENCY_CONTEXT + canonical.encode("ascii"), hashlib.sha256
    ).hexdigest()
