"""Phase 5 / M06 -- the provider-independent payment boundary, the
Mock/Sandbox provider and the fail-closed provider configuration.

Interface completeness and the Mock-only implementation; the mock's
idempotent creation, status, cancellation and sandbox simulation; the three
operation that still fails closed (refunds; the webhook operations became
real in Phase 5 / M07 and are proved in ``tests/test_payment_webhooks.py``);
``PAYMENT_PROVIDER_MODE`` resolution per environment, including production
refusing to start; that the only secret is the M07 webhook key; the absence of
any network call; and the adapter layer's independence from Flask, the ORM and
HTTP clients.
"""

import ast
import inspect
import pathlib
import socket
import threading
from decimal import Decimal

import pytest

from app import create_app
from app.config import Config, DevelopmentConfig, ProductionConfig, TestingConfig
from app.models import PAYMENT_INTENT_PROVIDERS
import tests.payment_intent_fixtures as ix
from app.services import mock_payment_provider as mock_module
from app.services import payment_providers as providers
from app.services.mock_payment_provider import (
    SANDBOX_OUTCOMES,
    MockPaymentProvider,
    is_mock_reference,
    mock_reference,
)
from app.services.payment_providers import (
    PROVIDER_OPERATIONS,
    UNSUPPORTED_OPERATIONS,
    PaymentProvider,
    PaymentProviderConfigError,
    PaymentProviderError,
    PaymentProviderSettings,
    PaymentProviderUnsupportedOperation,
    PaymentProviderWebhookError,
    ProviderPaymentStatus,
    resolve_payment_provider,
)

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_KEY = "a" * 64
_OTHER_KEY = "b" * 64
_AMOUNT = Decimal("1250.5000")
#: Mock mode needs a valid webhook secret since Phase 5 / M07.
_MOCK = {"PAYMENT_PROVIDER_MODE": "mock", "MOCK_PAYMENT_WEBHOOK_SECRET": ix.TEST_WEBHOOK_SECRET}


def _provider():
    return MockPaymentProvider()


# ===========================================================================
# The interface
# ===========================================================================


def test_the_interface_declares_exactly_the_six_operations():
    assert PROVIDER_OPERATIONS == (
        "create_payment_intent", "get_payment_status", "cancel_payment", "refund_payment",
        "verify_webhook", "normalize_event",
    )
    assert PaymentProvider.__abstractmethods__ == frozenset(PROVIDER_OPERATIONS)
    assert UNSUPPORTED_OPERATIONS == ("refund_payment",)
    with pytest.raises(TypeError):
        PaymentProvider()

    class Incomplete(PaymentProvider):
        def create_payment_intent(self, *, idempotency_key, amount, currency_code):
            return None

    with pytest.raises(TypeError):
        Incomplete()


def test_the_mock_is_the_only_implementation_and_implements_every_operation():
    implementations = [cls for cls in PaymentProvider.__subclasses__()
                       if cls.__module__.startswith("app.")]
    assert implementations == [MockPaymentProvider]
    for name in PROVIDER_OPERATIONS:
        method = getattr(MockPaymentProvider, name)
        assert not getattr(method, "__isabstractmethod__", False), name
        assert method is not getattr(PaymentProvider, name), name
    assert MockPaymentProvider.name == "mock"
    assert PAYMENT_INTENT_PROVIDERS == ("mock",)
    assert providers.PROVIDER_MODES == ("disabled", "mock")
    assert MockPaymentProvider.__abstractmethods__ == frozenset()


@pytest.mark.parametrize("operation", UNSUPPORTED_OPERATIONS)
def test_unsupported_operations_fail_closed_and_never_fake_success(operation):
    provider = _provider()
    provider.create_payment_intent(idempotency_key=_KEY, amount=_AMOUNT, currency_code="LYD")
    reference = mock_reference(_KEY)
    arguments = {
        "refund_payment": (reference, _AMOUNT),
        "verify_webhook": (b'{"type": "payment.succeeded"}', {"Signature": "forged"}),
        "normalize_event": ({"type": "payment.succeeded", "reference": reference},),
    }[operation]
    with pytest.raises(PaymentProviderUnsupportedOperation, match="not performed"):
        getattr(provider, operation)(*arguments)
    assert issubclass(PaymentProviderUnsupportedOperation, PaymentProviderError)
    # Nothing moved: the payment is still pending.
    assert provider.get_payment_status(reference).status == "pending"


# ===========================================================================
# The Mock/Sandbox provider
# ===========================================================================


def test_creation_is_idempotent_by_key_and_derives_an_opaque_reference():
    provider = _provider()
    first = provider.create_payment_intent(idempotency_key=_KEY, amount=_AMOUNT,
                                           currency_code="LYD")
    again = provider.create_payment_intent(idempotency_key=_KEY, amount=Decimal("1250.5"),
                                           currency_code="LYD")
    assert first == again
    assert (first.status, first.amount, first.currency_code) == ("pending", _AMOUNT, "LYD")
    assert first.reference == mock_reference(_KEY) and is_mock_reference(first.reference)
    assert first.reference.startswith("mock_pi_") and len(first.reference) == 40
    assert _KEY[:16] not in first.reference and "1250" not in first.reference
    other = provider.create_payment_intent(idempotency_key=_OTHER_KEY, amount=_AMOUNT,
                                           currency_code="LYD")
    assert other.reference != first.reference
    # A fresh sandbox (a restart) derives the same reference from the same key.
    assert _provider().create_payment_intent(
        idempotency_key=_KEY, amount=_AMOUNT, currency_code="LYD").reference == first.reference


@pytest.mark.parametrize("kwargs", [
    {"idempotency_key": "A" * 64},
    {"idempotency_key": "a" * 63},
    {"idempotency_key": "g" * 64},
    {"idempotency_key": None},
    {"amount": 1250.5},
    {"amount": Decimal("0")},
    {"amount": Decimal("100000")},
    {"amount": Decimal("1.00001")},
    {"amount": 5},
    {"currency_code": "USD"},
    {"currency_code": None},
])
def test_creation_refuses_anything_but_a_server_key_and_an_exact_lyd_amount(kwargs):
    provider = _provider()
    arguments = dict({"idempotency_key": _KEY, "amount": _AMOUNT, "currency_code": "LYD"},
                     **kwargs)
    with pytest.raises(PaymentProviderError):
        provider.create_payment_intent(**arguments)
    assert provider._ledger == {}


def test_a_key_reused_with_different_parameters_is_refused():
    provider = _provider()
    provider.create_payment_intent(idempotency_key=_KEY, amount=_AMOUNT, currency_code="LYD")
    with pytest.raises(PaymentProviderError, match="different parameters"):
        provider.create_payment_intent(idempotency_key=_KEY, amount=Decimal("1"),
                                       currency_code="LYD")
    assert provider.get_payment_status(mock_reference(_KEY)).amount == _AMOUNT


def test_status_reports_the_ledger_and_a_forgotten_reference_as_pending_without_amount():
    provider = _provider()
    reference = provider.create_payment_intent(idempotency_key=_KEY, amount=_AMOUNT,
                                               currency_code="LYD").reference
    assert provider.get_payment_status(reference).status == "pending"
    forgotten = _provider().get_payment_status(reference)
    assert (forgotten.status, forgotten.amount, forgotten.currency_code) == ("pending", None, None)
    for bad in ("", "mock_pi_" + "g" * 32, "mock_pi_" + "a" * 31, "pi_" + "a" * 32, None, 42):
        with pytest.raises(PaymentProviderError):
            provider.get_payment_status(bad)


@pytest.mark.parametrize("outcome, status", sorted(SANDBOX_OUTCOMES.items()))
def test_each_simulated_outcome_is_recorded_once_and_is_then_terminal(outcome, status):
    provider = _provider()
    reference = provider.create_payment_intent(idempotency_key=_KEY, amount=_AMOUNT,
                                               currency_code="LYD").reference
    assert provider.simulate_checkout_outcome(reference, outcome).status == status
    assert provider.simulate_checkout_outcome(reference, outcome).status == status
    for other in set(SANDBOX_OUTCOMES) - {outcome}:
        with pytest.raises(PaymentProviderError, match="already"):
            provider.simulate_checkout_outcome(reference, other)
    assert provider.get_payment_status(reference).status == status


def test_simulation_refuses_an_unknown_outcome_and_a_forgotten_reference():
    provider = _provider()
    reference = provider.create_payment_intent(idempotency_key=_KEY, amount=_AMOUNT,
                                               currency_code="LYD").reference
    for bad in ("succeeded", "paid", "", None, "SUCCESS"):
        with pytest.raises(PaymentProviderError):
            provider.simulate_checkout_outcome(reference, bad)
    assert provider.get_payment_status(reference).status == "pending"
    with pytest.raises(PaymentProviderError, match="no record"):
        _provider().simulate_checkout_outcome(reference, "success")
    assert set(SANDBOX_OUTCOMES) == {"success", "failure", "cancellation"}
    assert not hasattr(PaymentProvider, "simulate_checkout_outcome")


def test_cancellation_cancels_pending_is_idempotent_and_refuses_a_decided_payment():
    provider = _provider()
    reference = provider.create_payment_intent(idempotency_key=_KEY, amount=_AMOUNT,
                                               currency_code="LYD").reference
    assert provider.cancel_payment(reference).status == "cancelled"
    assert provider.cancel_payment(reference).status == "cancelled"
    for outcome in ("success", "failure"):
        decided = _provider()
        ref = decided.create_payment_intent(idempotency_key=_KEY, amount=_AMOUNT,
                                            currency_code="LYD").reference
        decided.simulate_checkout_outcome(ref, outcome)
        with pytest.raises(PaymentProviderError, match="already"):
            decided.cancel_payment(ref)
    # A reference a restarted sandbox forgot can still be cancelled.
    assert _provider().cancel_payment(reference).status == "cancelled"
    with pytest.raises(PaymentProviderError):
        provider.cancel_payment("not-a-reference")


def test_concurrent_creations_with_one_key_leave_one_ledger_entry():
    provider = _provider()
    results = []

    def create():
        results.append(provider.create_payment_intent(idempotency_key=_KEY, amount=_AMOUNT,
                                                      currency_code="LYD"))

    threads = [threading.Thread(target=create) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(provider._ledger) == 1 and len({r.reference for r in results}) == 1


def test_the_provider_statuses_are_the_four_normalized_values():
    assert [status.value for status in ProviderPaymentStatus] == [
        "pending", "succeeded", "failed", "cancelled"]


# ===========================================================================
# Configuration: PAYMENT_PROVIDER_MODE per environment
# ===========================================================================


@pytest.mark.parametrize("environment", ["development", "testing", "production"])
@pytest.mark.parametrize("raw", [None, "", "  ", "disabled", " disabled "])
def test_disabled_is_the_default_and_builds_no_provider(environment, raw):
    settings = resolve_payment_provider({"PAYMENT_PROVIDER_MODE": raw}, environment)
    assert (settings.mode, settings.environment, settings.provider) == (
        "disabled", environment, None)
    assert not settings.mock_enabled
    assert resolve_payment_provider({}, environment).mode == "disabled"


@pytest.mark.parametrize("environment", ["development", "testing"])
def test_mock_is_built_only_in_development_and_testing(environment):
    settings = resolve_payment_provider(_MOCK, environment)
    assert settings.mode == "mock" and isinstance(settings.provider, MockPaymentProvider)
    assert settings.mock_enabled


@pytest.mark.parametrize("environment", ["production", "staging", "", None, "Production"])
def test_mock_fails_closed_outside_development_and_testing(environment):
    with pytest.raises(PaymentProviderConfigError):
        resolve_payment_provider(_MOCK, environment)


@pytest.mark.parametrize("raw", ["stripe", "Mock", "MOCK", "live", "sandbox", "enabled", 1, True,
                                 ["mock"]])
def test_an_unknown_mode_fails_closed_everywhere(raw):
    for environment in ("development", "testing", "production"):
        with pytest.raises(PaymentProviderConfigError):
            resolve_payment_provider({"PAYMENT_PROVIDER_MODE": raw}, environment)


def test_a_configuration_error_never_echoes_the_configured_value():
    secret_looking = "sk_live_4eC39HqLyjWDarjtT1zdp7dc"
    with pytest.raises(PaymentProviderConfigError) as raised:
        resolve_payment_provider({"PAYMENT_PROVIDER_MODE": secret_looking}, "development")
    assert secret_looking not in str(raised.value)
    assert "sk_live" not in repr(raised.value)


def test_mock_enabled_needs_the_mode_the_environment_and_the_mock_provider():
    mock = MockPaymentProvider()
    assert PaymentProviderSettings("mock", "testing", mock).mock_enabled
    assert not PaymentProviderSettings("mock", "production", mock).mock_enabled
    assert not PaymentProviderSettings("disabled", "testing", mock).mock_enabled
    assert not PaymentProviderSettings("mock", "testing", None).mock_enabled


def test_create_app_refuses_to_start_in_production_with_mock():
    with pytest.raises(PaymentProviderConfigError):
        create_app("production", PAYMENT_PROVIDER_MODE="mock")
    with pytest.raises(PaymentProviderConfigError):
        create_app("staging", PAYMENT_PROVIDER_MODE="mock")
    with pytest.raises(PaymentProviderConfigError):
        create_app("testing", PAYMENT_PROVIDER_MODE="stripe")
    production = create_app("production")
    assert production.extensions["payment_provider"].mode == "disabled"
    assert production.extensions["payment_provider"].provider is None


def test_create_app_resolves_the_mode_for_the_environment_it_was_created_for():
    assert create_app("testing").extensions["payment_provider"].mode == "disabled"
    mocked = ix.make_app().extensions["payment_provider"]
    assert mocked.mock_enabled and mocked.environment == "testing"
    development = ix.make_app(config_name="development")
    assert development.extensions["payment_provider"].mock_enabled
    # Two applications never share one sandbox ledger.
    other = ix.make_app().extensions["payment_provider"]
    assert other.provider is not mocked.provider


def test_the_testing_config_pins_disabled_and_the_setting_defaults_to_disabled():
    assert TestingConfig.PAYMENT_PROVIDER_MODE == "disabled"
    assert "PAYMENT_PROVIDER_MODE" not in vars(DevelopmentConfig)
    assert "PAYMENT_PROVIDER_MODE" not in vars(ProductionConfig)
    source = (_ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert 'os.environ.get("PAYMENT_PROVIDER_MODE", "disabled")' in source


# ===========================================================================
# No secrets, no network, no Flask in the adapter layer
# ===========================================================================


def test_no_provider_credential_setting_exists_beyond_the_mock_webhook_key():
    """Phase 5 / M07 added exactly one secret: the Mock/Sandbox webhook key."""
    names = [name for name in vars(Config) if name.isupper()]
    payment_names = [name for name in names if "PAYMENT" in name or "PROVIDER" in name]
    assert payment_names == ["PAYMENT_PROVIDER_MODE", "MOCK_PAYMENT_WEBHOOK_SECRET"]
    for name in names:
        for part in ("STRIPE", "API_KEY", "PUBLISHABLE", "MERCHANT", "GATEWAY"):
            assert part not in name, name
    example = (_ROOT / ".env.example").read_text(encoding="utf-8")
    settings = [line for line in example.splitlines() if line and not line.startswith("#")]
    assert "PAYMENT_PROVIDER_MODE=disabled" in settings
    assert [line for line in settings if "PAYMENT" in line] == [
        "PAYMENT_PROVIDER_MODE=disabled",
        "MOCK_PAYMENT_WEBHOOK_SECRET=replace-with-a-long-random-webhook-secret",
    ]
    for line in settings:
        for part in ("STRIPE", "API_KEY", "MERCHANT", "GATEWAY", "PROVIDER_KEY"):
            assert part not in line.upper(), line


def test_resolved_settings_expose_no_secret(app):
    settings = app.extensions["payment_provider"]
    for mode in ("disabled", "mock"):
        resolved = resolve_payment_provider(dict(_MOCK, PAYMENT_PROVIDER_MODE=mode,
                                                 SECRET_KEY="top-secret-value"), "testing")
        assert "top-secret-value" not in repr(resolved)
        assert ix.TEST_WEBHOOK_SECRET not in repr(resolved)
        assert set(vars(resolved)) == {"mode", "environment", "provider"}
    assert settings.mode == "disabled"
    assert repr(MockPaymentProvider()) == "MockPaymentProvider(sandbox)"


def test_the_mock_stores_only_reference_key_amount_currency_and_status():
    provider = _provider()
    provider.create_payment_intent(idempotency_key=_KEY, amount=_AMOUNT, currency_code="LYD")
    (entry,) = provider._ledger.values()
    assert set(vars(entry)) == {"idempotency_key", "amount", "currency_code", "status", "event"}
    assert entry.event is None


def test_no_operation_touches_the_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("a payment provider opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    provider = resolve_payment_provider(_MOCK, "testing").provider
    reference = provider.create_payment_intent(idempotency_key=_KEY, amount=_AMOUNT,
                                               currency_code="LYD").reference
    provider.get_payment_status(reference)
    provider.simulate_checkout_outcome(reference, "failure")
    other = provider.create_payment_intent(idempotency_key=_OTHER_KEY, amount=_AMOUNT,
                                           currency_code="LYD").reference
    provider.cancel_payment(other)
    with pytest.raises(PaymentProviderUnsupportedOperation):
        provider.refund_payment(reference, _AMOUNT)
    body, headers = provider.emit_webhook(reference)
    provider.normalize_event(provider.verify_webhook(body, headers))
    with pytest.raises(PaymentProviderWebhookError):
        provider.verify_webhook(b"{}", {})
    with pytest.raises(PaymentProviderWebhookError):
        provider.normalize_event({})


_ADAPTER_FILES = ("payment_providers.py", "mock_payment_provider.py")
_ALLOWED_IMPORTS = {
    "abc", "enum", "dataclasses", "hashlib", "hmac", "json", "re", "secrets", "threading", "time",
    "datetime", "app.services.money", "app.services.payment_providers",
    "app.services.mock_payment_provider",
}


@pytest.mark.parametrize("filename", _ADAPTER_FILES)
def test_the_adapter_layer_imports_no_flask_orm_template_session_or_http_client(filename):
    source = (_ROOT / "app" / "services" / filename).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported <= _ALLOWED_IMPORTS, imported - _ALLOWED_IMPORTS
    for forbidden in ("flask", "request", "session", "render_template", "sqlalchemy",
                      "app.models", "app.extensions", "requests", "urllib", "http", "socket",
                      "jinja2", "open("):
        assert forbidden not in " ".join(sorted(imported)), forbidden
    code = source.split('"""', 2)[2]
    for forbidden in ("current_app", "flask", "db.session", "urlopen", "open(", "os.environ",
                      "getenv"):
        assert forbidden not in code, forbidden


def test_the_mock_module_names_no_credential_or_card_field():
    code = inspect.getsource(mock_module).split('"""', 2)[2].lower()
    for forbidden in ("card_number", "cvv", "cvc", "pin_", "iban", "account_number", "password",
                      "api_key", "environ", "getenv"):
        assert forbidden not in code, forbidden
    # The one secret it holds is the webhook key it was handed -- never read
    # from the environment itself, and never in its repr, a report or an error.
    provider = MockPaymentProvider(webhook_secret=ix.TEST_WEBHOOK_SECRET)
    assert repr(provider) == "MockPaymentProvider(sandbox)"
    reference = provider.create_payment_intent(idempotency_key=_KEY, amount=_AMOUNT,
                                               currency_code="LYD").reference
    assert ix.TEST_WEBHOOK_SECRET not in repr(provider.get_payment_status(reference))
    with pytest.raises(PaymentProviderWebhookError) as raised:
        provider.verify_webhook(b'{"x":"y"}', {
            mock_module.WEBHOOK_TIMESTAMP_HEADER: "1",
            mock_module.WEBHOOK_SIGNATURE_HEADER: "v1=" + "0" * 64,
        })
    assert ix.TEST_WEBHOOK_SECRET not in str(raised.value)
