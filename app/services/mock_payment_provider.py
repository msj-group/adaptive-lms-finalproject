"""The Mock/Sandbox payment provider (Phase 5 / M06).

**A simulation for development and testing only.** It takes no real payment,
contacts no gateway, makes no network call, reads no credential and stores no
card or bank data: the only facts it keeps about a payment are its opaque
reference, the idempotency key that created it, the amount and currency it was
created for, and a status. :func:`~app.services.payment_providers.resolve_payment_provider`
builds it only when ``PAYMENT_PROVIDER_MODE=mock`` in the ``development`` or
``testing`` environment.

Flask-independent, like the interface it implements. Its only project import
besides that interface is the pure money boundary, :mod:`app.services.money`.

**The sandbox ledger lives in this process's memory**, one ledger per
application instance, guarded by a lock. It stands in for the provider's own
records, which a real provider keeps on its side:

- **References are derived, not stored.** A reference is
  ``mock_pi_`` followed by 32 hex digits of SHA-256 over the idempotency key,
  so the same key always yields the same reference -- creation is idempotent
  even across a restart -- and the reference reveals neither the key nor any
  amount, name or internal id.
- **A restart forgets simulated outcomes.** A well-formed reference the
  ledger does not hold is reported ``pending`` with no amount (the sandbox has
  no record of any outcome), can still be cancelled, and cannot be given a new
  simulated outcome. So a pending intent is never stranded by a restart, and a
  forgotten success is never invented.

**The sandbox checkout** (:meth:`MockPaymentProvider.simulate_checkout_outcome`)
is the only way a payment leaves ``pending``: an Administrator on the clearly
labelled sandbox page picks success, failure or cancellation. It is *not* part
of the provider interface and changes only this ledger -- never a local payment
intent, which is updated only from :meth:`get_payment_status` by the browser
return route.

Refunds, webhook verification and event normalization raise
:class:`~app.services.payment_providers.PaymentProviderUnsupportedOperation`.
"""

import hashlib
import re
import threading
from dataclasses import dataclass

from app.services.money import CURRENCY_CODE, validate_amount
from app.services.payment_providers import (
    PROVIDER_MODE_MOCK,
    PaymentProvider,
    PaymentProviderError,
    PaymentProviderUnsupportedOperation,
    ProviderPayment,
    ProviderPaymentStatus,
)

MOCK_REFERENCE_PREFIX = "mock_pi_"
_REFERENCE_SHAPE = re.compile(r"mock_pi_[0-9a-f]{32}")
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

_UNSUPPORTED_MESSAGE = (
    "{operation} is not performed by the mock provider in Phase 5 / M06: refunds, webhook "
    "verification and event normalization belong to later Parts."
)


def mock_reference(idempotency_key):
    """The sandbox reference an idempotency key always creates."""
    digest = hashlib.sha256(b"phase5-m06.mock-reference:" + idempotency_key.encode("ascii"))
    return MOCK_REFERENCE_PREFIX + digest.hexdigest()[:32]


def is_mock_reference(value):
    """Whether `value` is exactly the shape of a sandbox reference."""
    return isinstance(value, str) and _REFERENCE_SHAPE.fullmatch(value) is not None


@dataclass
class _LedgerEntry:
    idempotency_key: object
    amount: object
    currency_code: object
    status: str


class MockPaymentProvider(PaymentProvider):
    """The in-process sandbox. See the module docstring."""

    name = PROVIDER_MODE_MOCK

    def __init__(self):
        self._lock = threading.Lock()
        self._ledger = {}

    def __repr__(self):
        return "MockPaymentProvider(sandbox)"

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

    def verify_webhook(self, payload, headers):
        raise PaymentProviderUnsupportedOperation(
            _UNSUPPORTED_MESSAGE.format(operation="verify_webhook")
        )

    def normalize_event(self, event):
        raise PaymentProviderUnsupportedOperation(
            _UNSUPPORTED_MESSAGE.format(operation="normalize_event")
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
