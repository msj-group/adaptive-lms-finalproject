"""The Mock/Sandbox payment provider (Phase 5 / M06, extended by M07).

**A simulation for development and testing only.** It takes no real payment,
contacts no gateway, makes no network call, reads no real credential and
stores no card or bank data: the only facts it keeps about a payment are its
opaque reference, the idempotency key that created it, the amount and currency
it was created for, a status, and the one webhook event it emitted.
:func:`~app.services.payment_providers.resolve_payment_provider` builds it only
when ``PAYMENT_PROVIDER_MODE=mock`` in the ``development`` or ``testing``
environment, with a validated ``MOCK_PAYMENT_WEBHOOK_SECRET``.

Flask-independent, like the interface it implements. Its only project imports
besides that interface are the pure money boundary, :mod:`app.services.money`.

**The sandbox ledger lives in this process's memory**, one ledger per
application instance, guarded by a lock. It stands in for the provider's own
records, which a real provider keeps on its side:

- **References are derived, not stored.** A reference is ``mock_pi_``
  followed by 32 hex digits of SHA-256 over the idempotency key, so the same
  key always yields the same reference -- creation is idempotent even across a
  restart -- and the reference reveals neither the key nor any amount, name or
  internal id.
- **A restart forgets simulated outcomes.** A well-formed reference the ledger
  does not hold is reported ``pending`` with no amount, can still be
  cancelled, and cannot be given a new outcome or emit a webhook, so a pending
  intent is never stranded and a forgotten success is never invented.

**The sandbox checkout** (:meth:`MockPaymentProvider.simulate_checkout_outcome`)
is the only way a payment leaves ``pending``. **Its webhook**
(:meth:`MockPaymentProvider.emit_webhook`) is the one signed event describing
that decided outcome: created once, re-delivered identically. Neither is part
of the provider interface; they change only this ledger.

Signed webhooks (Phase 5 / M07)
-------------------------------
A delivery carries two headers, :data:`WEBHOOK_TIMESTAMP_HEADER` (Unix seconds)
and :data:`WEBHOOK_SIGNATURE_HEADER` (``v1=`` and 64 lowercase hex digits of
HMAC-SHA256, under the webhook secret, over ``"<timestamp>." + raw body``).
:meth:`MockPaymentProvider.verify_webhook` recomputes the HMAC over the exact
raw bytes and compares in constant time **before** anything is parsed, then
refuses a signature older than :data:`WEBHOOK_MAX_AGE_SECONDS` or more than
:data:`WEBHOOK_MAX_FUTURE_SKEW_SECONDS` ahead. The body is UTF-8 JSON with
exactly these keys, every value text::

    {"event_id": "evt_mock_<32 hex>",
     "event_type": "payment.succeeded" | "payment.failed",
     "provider_reference": "mock_pi_<32 hex>",
     "amount": "1250.5000",
     "currency_code": "LYD",
     "occurred_at": "YYYY-MM-DDTHH:MM:SSZ"}

:meth:`MockPaymentProvider.normalize_event` refuses a duplicate key, an unknown
or missing key, a number, ``NaN``, a nested value, an unknown event type, a
malformed id or reference, an amount the money boundary refuses, any currency
but ``LYD``, and an impossible moment. Refunds still raise
:class:`~app.services.payment_providers.PaymentProviderUnsupportedOperation`.
"""

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.money import CURRENCY_CODE, parse_amount, validate_amount
from app.services.payment_providers import (
    EVENT_PAYMENT_FAILED,
    EVENT_PAYMENT_SUCCEEDED,
    PROVIDER_MODE_MOCK,
    WEBHOOK_EVENT_TYPES,
    PaymentProvider,
    PaymentProviderError,
    PaymentProviderUnsupportedOperation,
    PaymentProviderWebhookError,
    ProviderEvent,
    ProviderPayment,
    ProviderPaymentStatus,
    VerifiedWebhook,
)

MOCK_REFERENCE_PREFIX = "mock_pi_"
MOCK_EVENT_PREFIX = "evt_mock_"
_REFERENCE_SHAPE = re.compile(r"mock_pi_[0-9a-f]{32}")
_EVENT_ID_SHAPE = re.compile(r"evt_mock_[0-9a-f]{32}")
_IDEMPOTENCY_KEY_SHAPE = re.compile(r"[0-9a-f]{64}")

_PENDING = ProviderPaymentStatus.PENDING.value
_SUCCEEDED = ProviderPaymentStatus.SUCCEEDED.value
_FAILED = ProviderPaymentStatus.FAILED.value
_CANCELLED = ProviderPaymentStatus.CANCELLED.value

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_CANCELLATION = "cancellation"

#: The only outcomes the sandbox checkout may simulate, and the provider
#: status each one leaves behind.
SANDBOX_OUTCOMES = {
    OUTCOME_SUCCESS: _SUCCEEDED,
    OUTCOME_FAILURE: _FAILED,
    OUTCOME_CANCELLATION: _CANCELLED,
}

#: The webhook event each decided sandbox status emits.
_EVENT_FOR_STATUS = {_SUCCEEDED: EVENT_PAYMENT_SUCCEEDED, _FAILED: EVENT_PAYMENT_FAILED}

WEBHOOK_TIMESTAMP_HEADER = "X-Mock-Webhook-Timestamp"
WEBHOOK_SIGNATURE_HEADER = "X-Mock-Webhook-Signature"
WEBHOOK_HEADERS = (WEBHOOK_TIMESTAMP_HEADER, WEBHOOK_SIGNATURE_HEADER)

#: The largest webhook body accepted, in bytes. A Mock event is under 300.
MAX_WEBHOOK_BODY_BYTES = 2048

#: How old a signature may be, and how far ahead of the verifier's clock.
WEBHOOK_MAX_AGE_SECONDS = 300
WEBHOOK_MAX_FUTURE_SKEW_SECONDS = 60

WEBHOOK_EVENT_KEYS = frozenset(
    {"event_id", "event_type", "provider_reference", "amount", "currency_code", "occurred_at"}
)

_TIMESTAMP_SHAPE = re.compile(r"[0-9]{1,12}")
_SIGNATURE_SHAPE = re.compile(r"v1=(?P<digest>[0-9a-f]{64})")
_MOMENT_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_MOMENT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_UNSUPPORTED_MESSAGE = (
    "{operation} is not performed by the mock provider: refunds belong to a later Part."
)


def mock_reference(idempotency_key):
    """The sandbox reference an idempotency key always creates."""
    digest = hashlib.sha256(b"phase5-m06.mock-reference:" + idempotency_key.encode("ascii"))
    return MOCK_REFERENCE_PREFIX + digest.hexdigest()[:32]


def is_mock_reference(value):
    """Whether `value` is exactly the shape of a sandbox reference."""
    return isinstance(value, str) and _REFERENCE_SHAPE.fullmatch(value) is not None


def webhook_signature(secret, timestamp, body):
    """``v1=<hex>`` for `body` signed at `timestamp` under `secret`."""
    message = str(timestamp).encode("ascii") + b"." + body
    return "v1=" + hmac.new(secret, message, hashlib.sha256).hexdigest()


def _moment_text(moment):
    return moment.strftime(_MOMENT_FORMAT)


def _amount_text(amount):
    """``'1250.5000'``: the exact amount at the storage scale, never rounded."""
    return format(validate_amount(amount), "f")


def _refuse_duplicate_keys(pairs):
    keys = [key for key, _value in pairs]
    if len(keys) != len(set(keys)):
        raise PaymentProviderWebhookError("the event repeats a key")
    return dict(pairs)


def _refuse_number(_text):
    raise PaymentProviderWebhookError("the event carries a number, not text")


@dataclass
class _LedgerEntry:
    idempotency_key: object
    amount: object
    currency_code: object
    status: str
    #: ``(event_id, event_type, body)`` of the one event emitted, or ``None``.
    event: object = field(default=None)


class MockPaymentProvider(PaymentProvider):
    """The in-process sandbox. See the module docstring."""

    name = PROVIDER_MODE_MOCK

    def __init__(self, webhook_secret=None):
        self._lock = threading.Lock()
        self._ledger = {}
        # Name-mangled and never part of any repr, report or error.
        self.__webhook_secret = (
            webhook_secret.encode("ascii") if isinstance(webhook_secret, str) else None
        )

    def __repr__(self):
        return "MockPaymentProvider(sandbox)"

    def _secret(self):
        if not self.__webhook_secret:
            raise PaymentProviderWebhookError("the sandbox has no webhook secret")
        return self.__webhook_secret

    @staticmethod
    def _report(reference, entry):
        if entry is None:
            return ProviderPayment(reference=reference, status=_PENDING)
        return ProviderPayment(
            reference=reference,
            status=entry.status,
            amount=entry.amount,
            currency_code=entry.currency_code,
        )

    @staticmethod
    def _checked_reference(reference):
        if not is_mock_reference(reference):
            raise PaymentProviderError("not a sandbox payment reference")
        return reference

    # -- the provider interface ------------------------------------------------

    def create_payment_intent(self, *, idempotency_key, amount, currency_code):
        if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_KEY_SHAPE.fullmatch(
            idempotency_key
        ):
            raise PaymentProviderError("the idempotency key is not a server-generated key")
        try:
            amount = validate_amount(amount)
        except ValueError:
            raise PaymentProviderError("the amount is not a valid money amount") from None
        if currency_code != CURRENCY_CODE:
            raise PaymentProviderError("the sandbox accepts only the application's currency")
        reference = mock_reference(idempotency_key)
        with self._lock:
            entry = self._ledger.get(reference)
            if entry is None:
                entry = _LedgerEntry(idempotency_key, amount, currency_code, _PENDING)
                self._ledger[reference] = entry
            elif (entry.idempotency_key, entry.amount, entry.currency_code) != (
                idempotency_key,
                amount,
                currency_code,
            ):
                raise PaymentProviderError(
                    "the idempotency key was already used with different parameters"
                )
            return self._report(reference, entry)

    def get_payment_status(self, reference):
        reference = self._checked_reference(reference)
        with self._lock:
            return self._report(reference, self._ledger.get(reference))

    def cancel_payment(self, reference):
        reference = self._checked_reference(reference)
        with self._lock:
            entry = self._ledger.get(reference)
            if entry is None:
                # A reference this process has forgotten had no recorded
                # outcome: cancelling it is the sandbox's defined behaviour, so
                # a restart never strands a pending intent.
                entry = _LedgerEntry(None, None, None, _CANCELLED)
                self._ledger[reference] = entry
            elif entry.status == _PENDING:
                entry.status = _CANCELLED
            elif entry.status != _CANCELLED:
                raise PaymentProviderError(f"the sandbox payment is already {entry.status}")
            return self._report(reference, entry)

    def refund_payment(self, reference, amount):
        raise PaymentProviderUnsupportedOperation(
            _UNSUPPORTED_MESSAGE.format(operation="refund_payment")
        )

    def verify_webhook(self, payload, headers, now=None):
        if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_WEBHOOK_BODY_BYTES:
            raise PaymentProviderWebhookError("the body is missing or too large")
        try:
            timestamp_text = headers.get(WEBHOOK_TIMESTAMP_HEADER)
            signature_text = headers.get(WEBHOOK_SIGNATURE_HEADER)
        except AttributeError:
            raise PaymentProviderWebhookError("the headers are not a mapping") from None
        if not isinstance(timestamp_text, str) or not _TIMESTAMP_SHAPE.fullmatch(timestamp_text):
            raise PaymentProviderWebhookError("the signed timestamp is missing or malformed")
        if not isinstance(signature_text, str) or not _SIGNATURE_SHAPE.fullmatch(signature_text):
            raise PaymentProviderWebhookError("the signature is missing or malformed")
        timestamp = int(timestamp_text)
        expected = webhook_signature(self._secret(), timestamp, payload)
        if not hmac.compare_digest(expected.encode("ascii"), signature_text.encode("ascii")):
            raise PaymentProviderWebhookError("the signature does not match")
        now = int(time.time()) if now is None else int(now)
        if timestamp < now - WEBHOOK_MAX_AGE_SECONDS:
            raise PaymentProviderWebhookError("the signature is stale")
        if timestamp > now + WEBHOOK_MAX_FUTURE_SKEW_SECONDS:
            raise PaymentProviderWebhookError("the signature is from the future")
        return VerifiedWebhook(
            body=payload,
            signed_at=timestamp,
            payload_digest=hashlib.sha256(payload).hexdigest(),
        )

    def normalize_event(self, verified):
        if not isinstance(verified, VerifiedWebhook):
            raise PaymentProviderWebhookError("only a verified webhook is normalized")
        try:
            text = verified.body.decode("utf-8")
            body = json.loads(
                text,
                object_pairs_hook=_refuse_duplicate_keys,
                parse_int=_refuse_number,
                parse_float=_refuse_number,
                parse_constant=_refuse_number,
            )
        except PaymentProviderWebhookError:
            raise
        except (UnicodeDecodeError, ValueError):
            raise PaymentProviderWebhookError("the body is not UTF-8 JSON") from None
        if not isinstance(body, dict) or set(body) != WEBHOOK_EVENT_KEYS:
            raise PaymentProviderWebhookError("the event does not have exactly its keys")
        if not all(isinstance(value, str) for value in body.values()):
            raise PaymentProviderWebhookError("every event value is text")
        if not _EVENT_ID_SHAPE.fullmatch(body["event_id"]):
            raise PaymentProviderWebhookError("the event id is malformed")
        if body["event_type"] not in WEBHOOK_EVENT_TYPES:
            raise PaymentProviderWebhookError("the event type is unknown")
        if not is_mock_reference(body["provider_reference"]):
            raise PaymentProviderWebhookError("the provider reference is malformed")
        amount, error = parse_amount(body["amount"])
        if error is not None or body["amount"] != body["amount"].strip():
            raise PaymentProviderWebhookError("the amount is not an exact money amount")
        if body["currency_code"] != CURRENCY_CODE:
            raise PaymentProviderWebhookError("the currency is not the application's")
        moment = body["occurred_at"]
        if not _MOMENT_SHAPE.fullmatch(moment):
            raise PaymentProviderWebhookError("the event moment is malformed")
        try:
            occurred_at = datetime.strptime(moment, _MOMENT_FORMAT)
        except ValueError:
            raise PaymentProviderWebhookError("the event moment is impossible") from None
        return ProviderEvent(
            event_id=body["event_id"],
            event_type=body["event_type"],
            provider_reference=body["provider_reference"],
            amount=amount,
            currency_code=body["currency_code"],
            occurred_at=occurred_at,
            payload_digest=verified.payload_digest,
        )

    # -- sandbox only: not part of the provider interface -----------------------

    def simulate_checkout_outcome(self, reference, outcome):
        """Record what the simulated payer did on the sandbox checkout page:
        one of :data:`SANDBOX_OUTCOMES`. Changes only this ledger. Repeating
        the recorded outcome is a no-op; any other change of a decided
        payment, and any outcome for a forgotten reference, is refused."""
        reference = self._checked_reference(reference)
        if outcome not in SANDBOX_OUTCOMES:
            raise PaymentProviderError("not a sandbox checkout outcome")
        status = SANDBOX_OUTCOMES[outcome]
        with self._lock:
            entry = self._ledger.get(reference)
            if entry is None:
                raise PaymentProviderError("the sandbox has no record of this payment")
            if entry.status != status:
                if entry.status != _PENDING:
                    raise PaymentProviderError(f"the sandbox payment is already {entry.status}")
                entry.status = status
            return self._report(reference, entry)

    def emit_webhook(self, reference, now=None):
        """``(body, headers)`` of the signed webhook describing `reference`'s
        decided outcome -- ``payment.succeeded`` or ``payment.failed``.

        The event is created once, from this ledger's own amount and currency,
        and every later call re-delivers the identical body under a fresh
        signature, exactly as a provider retries one event. A payment that is
        pending, cancelled or forgotten has nothing to deliver."""
        reference = self._checked_reference(reference)
        secret = self._secret()
        now = int(time.time()) if now is None else int(now)
        with self._lock:
            entry = self._ledger.get(reference)
            if entry is None or entry.status not in _EVENT_FOR_STATUS:
                raise PaymentProviderError("the sandbox has no decided payment to report")
            event_type = _EVENT_FOR_STATUS[entry.status]
            if entry.event is None or entry.event[1] != event_type:
                event = {
                    "event_id": MOCK_EVENT_PREFIX + secrets.token_hex(16),
                    "event_type": event_type,
                    "provider_reference": reference,
                    "amount": _amount_text(entry.amount),
                    "currency_code": entry.currency_code,
                    "occurred_at": _moment_text(
                        datetime.fromtimestamp(now, timezone.utc).replace(tzinfo=None)
                    ),
                }
                body = json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
                entry.event = (event["event_id"], event_type, body)
            body = entry.event[2]
        return body, {
            WEBHOOK_TIMESTAMP_HEADER: str(now),
            WEBHOOK_SIGNATURE_HEADER: webhook_signature(secret, now, body),
        }
