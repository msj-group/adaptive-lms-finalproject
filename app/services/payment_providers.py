"""The provider-independent online-payment boundary (Phase 5 / M06).

Flask-independent: no ``request``, session, template, ORM model, database
session or route code is imported here, and nothing here performs I/O. A route
reaches a provider only through the :class:`PaymentProviderSettings` that
``create_app`` resolved at start-up.

**No real payment provider exists.** The only implementation is the clearly
labelled Mock/Sandbox provider in :mod:`app.services.mock_payment_provider`,
available only in the ``development`` and ``testing`` environments. No adapter
makes an HTTP request, contacts a gateway, reads a provider credential or
stores a card or bank credential, and no provider secret setting exists.

**Configuration** is one non-secret setting, ``PAYMENT_PROVIDER_MODE``:

- ``disabled`` -- the default, and the only value production accepts. No
  provider is built and every online-payment write is refused.
- ``mock`` -- the Mock/Sandbox provider, only when the application was
  created for ``development`` or ``testing``.

:func:`resolve_payment_provider` runs once, at start-up, and **fails closed**:
an unknown mode, ``mock`` outside development and testing, or any
non-``disabled`` mode in production raises :class:`PaymentProviderConfigError`
and the application refuses to start. No error message repeats the configured
value, so nothing mistakenly pasted into the setting is ever echoed.

**A provider result is never a financial confirmation.** Phase 5 / M06 only
creates and tracks payment intents; the verified-webhook transition to a
financial payment belongs to Phase 5 / M07. The three webhook, event and refund
operations therefore exist on the interface but **fail closed** with
:class:`PaymentProviderUnsupportedOperation` -- never a fake success.
"""

import abc
import enum
from dataclasses import dataclass

PROVIDER_MODE_DISABLED = "disabled"
PROVIDER_MODE_MOCK = "mock"

#: Every mode ``PAYMENT_PROVIDER_MODE`` may name.
PROVIDER_MODES = (PROVIDER_MODE_DISABLED, PROVIDER_MODE_MOCK)

ENVIRONMENT_DEVELOPMENT = "development"
ENVIRONMENT_TESTING = "testing"
ENVIRONMENT_PRODUCTION = "production"

#: The only environments the Mock/Sandbox provider may run in.
MOCK_ENVIRONMENTS = frozenset({ENVIRONMENT_DEVELOPMENT, ENVIRONMENT_TESTING})

#: Every operation of the provider interface, in declaration order.
PROVIDER_OPERATIONS = (
    "create_payment_intent",
    "get_payment_status",
    "cancel_payment",
    "refund_payment",
    "verify_webhook",
    "normalize_event",
)

#: The operations Phase 5 / M06 declares but deliberately does not perform.
M06_UNSUPPORTED_OPERATIONS = ("refund_payment", "verify_webhook", "normalize_event")


class PaymentProviderConfigError(RuntimeError):
    """``PAYMENT_PROVIDER_MODE`` is invalid for this environment. The
    application must refuse to start rather than guess a provider."""


class PaymentProviderError(RuntimeError):
    """A provider refused an operation or returned something the caller
    cannot use. The route rolls back and reports it generically; the message
    is internal and never shown."""


class PaymentProviderUnsupportedOperation(PaymentProviderError):
    """An interface operation Phase 5 / M06 does not perform (refunds, webhook
    verification, event normalization). Raised instead of any fake result."""


class ProviderPaymentStatus(str, enum.Enum):
    """A provider's normalized report about one payment.

    - ``pending`` -- awaiting the payer; nothing has happened yet.
    - ``succeeded`` -- the provider reports the payer completed the payment.
      For the Mock/Sandbox provider this is a simulation; in M06 it is never a
      financial confirmation.
    - ``failed`` -- the provider reports the payment failed.
    - ``cancelled`` -- the payment was cancelled at the provider.
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ProviderPayment:
    """What a provider reports about one payment. ``amount`` and
    ``currency_code`` are ``None`` when the provider does not report them."""

    reference: str
    status: str
    amount: object = None
    currency_code: str = None


class PaymentProvider(abc.ABC):
    """The interface every online-payment provider adapter implements.

    Every method is keyword-explicit and returns a :class:`ProviderPayment`
    or raises :class:`PaymentProviderError`; none returns ``None`` for a
    failure. An adapter holds no Flask, request, session or ORM object.
    """

    #: The provider's stored name (``payment_intents.provider``).
    name = None

    @abc.abstractmethod
    def create_payment_intent(self, *, idempotency_key, amount, currency_code):
        """Create -- or, for an ``idempotency_key`` already seen, return --
        the provider's payment for `amount` in `currency_code`."""

    @abc.abstractmethod
    def get_payment_status(self, reference):
        """The provider's current report about `reference`."""

    @abc.abstractmethod
    def cancel_payment(self, reference):
        """Cancel `reference` at the provider; report its resulting state."""

    @abc.abstractmethod
    def refund_payment(self, reference, amount):
        """Refund `amount` of `reference`. Not performed in Phase 5 / M06."""

    @abc.abstractmethod
    def verify_webhook(self, payload, headers):
        """Verify a signed webhook delivery. Not performed in Phase 5 / M06."""

    @abc.abstractmethod
    def normalize_event(self, event):
        """Turn a verified provider event into a normalized one. Not performed
        in Phase 5 / M06."""


@dataclass(frozen=True)
class PaymentProviderSettings:
    """The provider configuration ``create_app`` resolved at start-up."""

    mode: str
    environment: str
    provider: PaymentProvider = None

    @property
    def mock_enabled(self):
        """Whether the Mock/Sandbox provider may be used right now: the mode
        is ``mock``, the environment allows it, and the built provider is
        the mock. Re-read by every online-payment write after its locks."""
        return (
            self.mode == PROVIDER_MODE_MOCK
            and self.environment in MOCK_ENVIRONMENTS
            and self.provider is not None
            and self.provider.name == PROVIDER_MODE_MOCK
        )


def resolve_payment_provider(config, environment):
    """Validate ``PAYMENT_PROVIDER_MODE`` for `environment` (the name the
    application was created for) and build the provider. Returns a
    :class:`PaymentProviderSettings`; raises
    :class:`PaymentProviderConfigError` on anything else.

    An unset or blank mode is ``disabled``. The mode must otherwise be exactly
    one of :data:`PROVIDER_MODES`. Production accepts only ``disabled``;
    ``mock`` is accepted only in :data:`MOCK_ENVIRONMENTS`, so an environment
    name this project does not define never runs the mock.
    """
    raw = config.get("PAYMENT_PROVIDER_MODE")
    if raw is None:
        mode = PROVIDER_MODE_DISABLED
    elif isinstance(raw, str):
        mode = raw.strip() or PROVIDER_MODE_DISABLED
    else:
        raise PaymentProviderConfigError("PAYMENT_PROVIDER_MODE must be text.")
    if mode not in PROVIDER_MODES:
        raise PaymentProviderConfigError(
            "PAYMENT_PROVIDER_MODE must be one of: " + ", ".join(PROVIDER_MODES) + "."
        )
    if environment == ENVIRONMENT_PRODUCTION and mode != PROVIDER_MODE_DISABLED:
        raise PaymentProviderConfigError(
            "PAYMENT_PROVIDER_MODE must be 'disabled' in production: no real payment provider "
            "is supported and the mock provider is for development and testing only."
        )
    if mode == PROVIDER_MODE_MOCK and environment not in MOCK_ENVIRONMENTS:
        raise PaymentProviderConfigError(
            "PAYMENT_PROVIDER_MODE 'mock' is permitted only in the development and testing "
            "environments."
        )
    provider = None
    if mode == PROVIDER_MODE_MOCK:
        # Imported here: the mock module builds on this one.
        from app.services.mock_payment_provider import MockPaymentProvider

        provider = MockPaymentProvider()
    return PaymentProviderSettings(mode=mode, environment=environment, provider=provider)
