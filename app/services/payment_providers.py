"""Disabled online-payment boundary and retained provider evidence types.

No provider adapter, checkout or webhook route is available. Only
``PAYMENT_PROVIDER_MODE=disabled`` is accepted in every environment. Cash and
verified bank transfers use the ordinary general-account workflows.

The interface and immutable result types remain for historical evidence and
a separately approved future real-provider integration. Configuration
validation performs no I/O and never repeats configured values.
"""

import abc
import enum
from dataclasses import dataclass

PROVIDER_MODE_DISABLED = "disabled"

#: Every mode ``PAYMENT_PROVIDER_MODE`` may name.
PROVIDER_MODES = (PROVIDER_MODE_DISABLED,)

ENVIRONMENT_DEVELOPMENT = "development"
ENVIRONMENT_TESTING = "testing"
ENVIRONMENT_PRODUCTION = "production"

#: Every operation of the provider interface, in declaration order.
PROVIDER_OPERATIONS = (
    "create_payment_intent",
    "get_payment_status",
    "cancel_payment",
    "refund_payment",
    "verify_webhook",
    "normalize_event",
)

#: The operations declared but deliberately not performed (Phase 5 / M07).
UNSUPPORTED_OPERATIONS = ("refund_payment",)

class PaymentProviderConfigError(RuntimeError):
    """The configuration enables an unavailable online-payment provider."""


class PaymentProviderError(RuntimeError):
    """A provider refused an operation or returned something the caller
    cannot use. The route rolls back and reports it generically; the message
    is internal and never shown."""


class PaymentProviderUnsupportedOperation(PaymentProviderError):
    """An interface operation that is not performed (refunds). Raised instead
    of any fake result."""


class PaymentProviderWebhookError(PaymentProviderError):
    """A webhook delivery is not authentic, fresh and exactly well-formed. The
    internal message says why; the endpoint never repeats it."""


class ProviderPaymentStatus(str, enum.Enum):
    """A provider's normalized report about one payment.

    - ``pending`` -- awaiting the payer; nothing has happened yet.
    - ``succeeded`` -- the provider reports the payer completed the payment.
      A browser report alone never confirms financial money movement.
    - ``failed`` -- the provider reports the payment failed.
    - ``cancelled`` -- the payment was cancelled at the provider.
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: The two provider event types a payment intent can receive.
EVENT_PAYMENT_SUCCEEDED = "payment.succeeded"
EVENT_PAYMENT_FAILED = "payment.failed"
WEBHOOK_EVENT_TYPES = (EVENT_PAYMENT_SUCCEEDED, EVENT_PAYMENT_FAILED)


@dataclass(frozen=True)
class VerifiedWebhook:
    """A delivery whose signature and freshness were proved. ``body`` is the
    exact raw request body the signature covered; ``payload_digest`` is its
    SHA-256 hex digest. Nothing in it has been parsed yet."""

    body: bytes
    signed_at: int
    payload_digest: str


@dataclass(frozen=True)
class ProviderEvent:
    """One verified webhook normalized to the provider-independent shape.
    ``amount`` is an exact ``Decimal``; ``occurred_at`` is the provider's own
    naive-UTC whole-second clock, kept only as information."""

    event_id: str
    event_type: str
    provider_reference: str
    amount: object
    currency_code: str
    occurred_at: object
    payload_digest: str


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
        """Refund `amount` of `reference`. Not performed: refunds belong to a
        later Part."""

    @abc.abstractmethod
    def verify_webhook(self, payload, headers, now=None):
        """Prove that `payload` -- the exact raw request body, ``bytes`` -- was
        signed by the provider, recently, per `headers` (a plain mapping of the
        provider's header names to text). Returns a :class:`VerifiedWebhook`
        or raises :class:`PaymentProviderWebhookError`. `now` is the verifier's
        Unix time, defaulting to the clock."""

    @abc.abstractmethod
    def normalize_event(self, verified):
        """Parse one :class:`VerifiedWebhook` strictly into a
        :class:`ProviderEvent`, or raise :class:`PaymentProviderWebhookError`.
        Never called on an unverified body."""


@dataclass(frozen=True)
class PaymentProviderSettings:
    """The provider configuration ``create_app`` resolved at start-up."""

    mode: str
    environment: str
    provider: PaymentProvider = None


def resolve_payment_provider(config, environment):
    """Accept disabled online payment only; never construct a provider."""
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
    return PaymentProviderSettings(mode=mode, environment=environment)
