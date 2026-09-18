"""The provider-independent online-payment boundary (Phase 5 / M06,
extended by M07).

Flask-independent: no ``request``, session, template, ORM model, database
session or route code is imported here, and nothing here performs I/O. A route
reaches a provider only through the :class:`PaymentProviderSettings` that
``create_app`` resolved at start-up.

**No real payment provider exists.** The only implementation is the clearly
labelled Mock/Sandbox provider in :mod:`app.services.mock_payment_provider`,
available only in the ``development`` and ``testing`` environments. No adapter
makes an HTTP request, contacts a gateway, reads a real provider credential or
stores a card or bank credential. The one secret is the Mock/Sandbox webhook
signing key, read from the environment and held only by the mock adapter.

**Configuration** is the non-secret ``PAYMENT_PROVIDER_MODE`` and, for the
mock only, the secret ``MOCK_PAYMENT_WEBHOOK_SECRET``:

- ``disabled`` -- the default, and the only value production accepts. No
  provider is built and every online-payment write is refused.
- ``mock`` -- the Mock/Sandbox provider, only when the application was
  created for ``development`` or ``testing``, and only with a strong,
  non-placeholder webhook secret (:func:`validate_mock_webhook_secret`).

:func:`resolve_payment_provider` runs once, at start-up, and **fails closed**:
an unknown mode, ``mock`` outside development and testing, ``mock`` without a
valid secret, or any non-``disabled`` mode in production raises
:class:`PaymentProviderConfigError` and the application refuses to start. No
error message repeats a configured value, so a secret is never echoed.

**A browser-observed result is never a financial confirmation.** Only a
webhook whose signature :meth:`PaymentProvider.verify_webhook` proves, and
whose body :meth:`PaymentProvider.normalize_event` accepts in full, may lead
to a financial payment (Phase 5 / M07). ``refund_payment`` still **fails
closed** with :class:`PaymentProviderUnsupportedOperation` -- never a fake
success.
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

#: The operations declared but deliberately not performed (Phase 5 / M07).
UNSUPPORTED_OPERATIONS = ("refund_payment",)

#: The shortest ``MOCK_PAYMENT_WEBHOOK_SECRET`` accepted, and the longest.
WEBHOOK_SECRET_MIN_LENGTH = 32
WEBHOOK_SECRET_MAX_LENGTH = 256

#: A secret must use at least this many distinct characters.
_WEBHOOK_SECRET_MIN_DISTINCT = 12

#: Fragments that mark a value copied from a template, never a real secret.
_PLACEHOLDER_FRAGMENTS = (
    "replace", "change", "placeholder", "example", "your", "secret-here", "todo", "xxxx",
    "default", "insecure", "dummy",
)


class PaymentProviderConfigError(RuntimeError):
    """``PAYMENT_PROVIDER_MODE`` or ``MOCK_PAYMENT_WEBHOOK_SECRET`` is invalid
    for this environment. The application must refuse to start rather than
    guess a provider."""


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
      For the Mock/Sandbox provider this is a simulation; read by the browser
      return, it is never a financial confirmation (only a verified signed
      webhook is).
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


def validate_mock_webhook_secret(raw):
    """The Mock provider's webhook secret, or raise
    :class:`PaymentProviderConfigError`.

    Required only in mock mode. It must be text of
    :data:`WEBHOOK_SECRET_MIN_LENGTH` to :data:`WEBHOOK_SECRET_MAX_LENGTH`
    visible ASCII characters with no whitespace, use at least 12 distinct
    characters, and contain no placeholder marker such as ``replace`` or
    ``example``. The error never repeats the value.
    """
    if not isinstance(raw, str) or not raw:
        raise PaymentProviderConfigError(
            "MOCK_PAYMENT_WEBHOOK_SECRET must be set when PAYMENT_PROVIDER_MODE is 'mock'."
        )
    if (
        not WEBHOOK_SECRET_MIN_LENGTH <= len(raw) <= WEBHOOK_SECRET_MAX_LENGTH
        or not all("\x21" <= character <= "\x7e" for character in raw)
        or len(set(raw)) < _WEBHOOK_SECRET_MIN_DISTINCT
        or any(fragment in raw.lower() for fragment in _PLACEHOLDER_FRAGMENTS)
    ):
        raise PaymentProviderConfigError(
            "MOCK_PAYMENT_WEBHOOK_SECRET must be a long random value: at least "
            f"{WEBHOOK_SECRET_MIN_LENGTH} visible characters with no whitespace, varied, and not "
            "a template placeholder."
        )
    return raw


def resolve_payment_provider(config, environment):
    """Validate ``PAYMENT_PROVIDER_MODE`` for `environment` (the name the
    application was created for) and build the provider. Returns a
    :class:`PaymentProviderSettings`; raises
    :class:`PaymentProviderConfigError` on anything else.

    An unset or blank mode is ``disabled``. The mode must otherwise be exactly
    one of :data:`PROVIDER_MODES`. Production accepts only ``disabled``;
    ``mock`` is accepted only in :data:`MOCK_ENVIRONMENTS`, so an environment
    name this project does not define never runs the mock, and only with a
    valid ``MOCK_PAYMENT_WEBHOOK_SECRET``, which is handed to the provider and
    kept nowhere else.
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

        secret = validate_mock_webhook_secret(config.get("MOCK_PAYMENT_WEBHOOK_SECRET"))
        provider = MockPaymentProvider(webhook_secret=secret)
    return PaymentProviderSettings(mode=mode, environment=environment, provider=provider)
