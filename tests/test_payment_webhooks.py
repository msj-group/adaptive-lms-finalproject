"""Phase 5 / M07 -- signed Mock/Sandbox webhooks and verified online
collections.

The webhook secret's fail-closed configuration; HMAC-SHA256 signing and
constant-time verification over the exact raw body and a signed timestamp;
strict normalization; the public endpoint (POST-only, no login, the one CSRF
exception, JSON only, a strict size limit, generic answers that disclose
nothing); and processing -- the one atomic online collection with its receipt,
inbox event and system-origin audit trail, idempotent re-delivery, a reused
event id, failures, duplicates, ignored and reconciliation-required events,
legacy manual overlap, transient failures, exact balances and unique receipts.
"""

import inspect
import json
import re
import socket
import time
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Query

import app.services.payment_webhooks as webhooks
import tests.fee_assignment_fixtures as fees
import tests.invoice_fixtures as fx
import tests.payment_fixtures as px
import tests.payment_intent_fixtures as ix
import tests.webhook_fixtures as wx
from app import create_app
from app.extensions import db
from app.models import (
    FeePlan,
    Invoice,
    PaymentAuditEvent,
    PaymentIntent,
    PaymentProviderEvent,
    PaymentTransaction,
    Receipt,
    ReceiptNumberSequence,
    StudentFeeAssignment,
    User,
)
from app.models.payment_audit_event import ONLINE_PAYMENT_SNAPSHOT_SCHEMA
from app.models.receipt import ONLINE_RECEIPT_SNAPSHOT_SCHEMA
from app.services import mock_payment_provider as mock_module
from app.services.invoice_queries import active_lines, invoice_lines
from app.services.mock_payment_provider import (
    MAX_WEBHOOK_BODY_BYTES,
    WEBHOOK_MAX_AGE_SECONDS,
    WEBHOOK_MAX_FUTURE_SKEW_SECONDS,
    WEBHOOK_SIGNATURE_HEADER,
    WEBHOOK_TIMESTAMP_HEADER,
    MockPaymentProvider,
)
from app.services.payment_providers import (
    PaymentProviderConfigError,
    PaymentProviderWebhookError,
    resolve_payment_provider,
    validate_mock_webhook_secret,
)
from app.services.payment_transactions import payment_balance
from app.services.schedule_occurrences import utc_reference_now


@pytest.fixture
def app():
    application = ix.make_app()
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def _provider(secret=wx.SECRET):
    return MockPaymentProvider(webhook_secret=secret)


def _pending_intent(app, client):
    """A logged-in world with one route-created pending intent."""
    w = wx.login_world(app, client)
    return w, ix.create_intent(client, w)


# ===========================================================================
# The webhook secret: environment only, validated, fail closed
# ===========================================================================


@pytest.mark.parametrize("raw", [
    None, "", "short-secret-123",
    "replace-with-a-long-random-webhook-secret",
    "CHANGE-me-please-0123456789abcdefghijkl",
    "a" * 64,
    "abababababababababababababababababababab",
    "has a space in it 0123456789abcdefghijklmnop",
    "tab\tinside-0123456789abcdefghijklmnopqrs",
    "ünicode-0123456789abcdefghijklmnopqrstuv",
    "x" * 300,
    12345678901234567890123456789012345,
])
def test_mock_mode_refuses_a_missing_weak_or_placeholder_secret(raw):
    with pytest.raises(PaymentProviderConfigError) as raised:
        resolve_payment_provider({"PAYMENT_PROVIDER_MODE": "mock",
                                  "MOCK_PAYMENT_WEBHOOK_SECRET": raw}, "testing")
    if isinstance(raw, str) and raw:
        assert raw not in str(raised.value)


def test_create_app_refuses_mock_without_a_valid_secret_and_ignores_it_when_disabled():
    with pytest.raises(PaymentProviderConfigError):
        create_app("testing", PAYMENT_PROVIDER_MODE="mock")
    with pytest.raises(PaymentProviderConfigError):
        create_app("development", PAYMENT_PROVIDER_MODE="mock",
                   MOCK_PAYMENT_WEBHOOK_SECRET="replace-with-a-long-random-webhook-secret")
    with pytest.raises(PaymentProviderConfigError):
        create_app("production", PAYMENT_PROVIDER_MODE="mock",
                   MOCK_PAYMENT_WEBHOOK_SECRET=wx.SECRET)
    assert create_app("testing").extensions["payment_provider"].mode == "disabled"
    assert create_app("production").extensions["payment_provider"].provider is None
    assert validate_mock_webhook_secret(wx.SECRET) == wx.SECRET


def test_the_secret_comes_only_from_the_environment_and_testing_pins_none():
    from app.config import Config, TestingConfig

    source = (Path(__file__).resolve().parents[1] / "app" / "config.py").read_text(encoding="utf-8")
    assert 'MOCK_PAYMENT_WEBHOOK_SECRET = os.environ.get("MOCK_PAYMENT_WEBHOOK_SECRET")' in source
    assert TestingConfig.MOCK_PAYMENT_WEBHOOK_SECRET is None
    assert "MOCK_PAYMENT_WEBHOOK_SECRET" in vars(Config)


# ===========================================================================
# Signing and verification
# ===========================================================================


def _sandbox_event(provider, outcome="success"):
    reference = provider.create_payment_intent(idempotency_key="a" * 64, amount=Decimal("100"),
                                               currency_code="LYD").reference
    provider.simulate_checkout_outcome(reference, outcome)
    return reference, *provider.emit_webhook(reference)


def test_a_genuine_signed_event_verifies_and_normalizes_exactly():
    provider = _provider()
    reference, raw, headers = _sandbox_event(provider)
    verified = provider.verify_webhook(raw, headers)
    assert verified.body == raw and verified.payload_digest == wx.digest(raw)
    event = provider.normalize_event(verified)
    assert (event.event_type, event.provider_reference, event.amount, event.currency_code) == (
        "payment.succeeded", reference, Decimal("100.0000"), "LYD")
    assert re.fullmatch(r"evt_mock_[0-9a-f]{32}", event.event_id)
    assert event.occurred_at.microsecond == 0 and event.occurred_at.tzinfo is None
    assert headers[WEBHOOK_SIGNATURE_HEADER].startswith("v1=")
    assert wx.SECRET not in json.dumps(headers) and wx.SECRET.encode() not in raw


def test_verification_compares_in_constant_time_before_parsing():
    code = inspect.getsource(MockPaymentProvider.verify_webhook)
    assert "hmac.compare_digest" in code and "json" not in code


@pytest.mark.parametrize("change", [
    "tampered_body", "tampered_signature", "other_secret", "missing_timestamp",
    "missing_signature", "uppercase_signature", "no_version_prefix", "short_signature",
    "signed_other_timestamp", "timestamp_not_digits", "empty_body", "oversized_body",
    "text_body", "headers_not_a_mapping",
])
def test_anything_but_the_exact_signed_bytes_is_refused(change):
    provider = _provider()
    _reference, raw, headers = _sandbox_event(provider)
    stamp = headers[WEBHOOK_TIMESTAMP_HEADER]
    signature = headers[WEBHOOK_SIGNATURE_HEADER]
    cases = {
        "tampered_body": (raw.replace(b"100.0000", b"100.0001"), headers),
        "tampered_signature": (raw, dict(headers, **{WEBHOOK_SIGNATURE_HEADER: signature[:-1] + (
            "0" if signature[-1] != "0" else "1")})),
        "other_secret": (raw, wx.signed_headers(raw, secret="another-secret-0123456789abcdefghij")),
        "missing_timestamp": (raw, {WEBHOOK_SIGNATURE_HEADER: signature}),
        "missing_signature": (raw, {WEBHOOK_TIMESTAMP_HEADER: stamp}),
        "uppercase_signature": (raw, dict(headers, **{
            WEBHOOK_SIGNATURE_HEADER: signature.upper()})),
        "no_version_prefix": (raw, dict(headers, **{WEBHOOK_SIGNATURE_HEADER: signature[3:]})),
        "short_signature": (raw, dict(headers, **{WEBHOOK_SIGNATURE_HEADER: signature[:-2]})),
        "signed_other_timestamp": (raw, dict(headers, **{
            WEBHOOK_TIMESTAMP_HEADER: str(int(stamp) - 1)})),
        "timestamp_not_digits": (raw, dict(headers, **{WEBHOOK_TIMESTAMP_HEADER: "12e3"})),
        "empty_body": (b"", wx.signed_headers(b"")),
        "oversized_body": (b" " * (MAX_WEBHOOK_BODY_BYTES + 1),
                           wx.signed_headers(b" " * (MAX_WEBHOOK_BODY_BYTES + 1))),
        "text_body": (raw.decode(), headers),
        "headers_not_a_mapping": (raw, [("x", "y")]),
    }
    body, sent = cases[change]
    with pytest.raises(PaymentProviderWebhookError):
        provider.verify_webhook(body, sent)


def test_a_stale_or_future_signature_is_refused_at_the_exact_bounds():
    provider = _provider()
    _reference, raw, _headers = _sandbox_event(provider)
    now = int(time.time())
    for offset, accepted in ((WEBHOOK_MAX_AGE_SECONDS, True), (WEBHOOK_MAX_AGE_SECONDS + 1, False),
                             (-WEBHOOK_MAX_FUTURE_SKEW_SECONDS, True),
                             (-WEBHOOK_MAX_FUTURE_SKEW_SECONDS - 1, False)):
        headers = wx.signed_headers(raw, timestamp=now - offset)
        if accepted:
            provider.verify_webhook(raw, headers, now=now)
        else:
            with pytest.raises(PaymentProviderWebhookError):
                provider.verify_webhook(raw, headers, now=now)


def _normalized(body_bytes):
    provider = _provider()
    verified = provider.verify_webhook(body_bytes, wx.signed_headers(body_bytes))
    return provider.normalize_event(verified)


_GOOD = wx.event("mock_pi_" + "a" * 32, amount="100.0000")


@pytest.mark.parametrize("raw", [
    b'{"event_id":"evt_mock_' + b"a" * 32 + b'","event_id":"evt_mock_' + b"b" * 32 + b'"}',
    wx.encode(dict(_GOOD, extra="1")),
    wx.encode({k: v for k, v in _GOOD.items() if k != "occurred_at"}),
    wx.encode(dict(_GOOD, amount=100)),
    wx.encode(dict(_GOOD, amount=100.0)),
    wx.encode(dict(_GOOD, amount={"value": "100"})),
    wx.encode(dict(_GOOD, amount=None)),
    wx.encode(dict(_GOOD, amount=True)),
    json.dumps([_GOOD]).encode(),
    b'{"amount": NaN}',
    b"\xff\xfe not utf-8",
    b"{not json",
    wx.encode(dict(_GOOD, event_id="evt_other_" + "a" * 32)),
    wx.encode(dict(_GOOD, event_id="evt_mock_" + "A" * 32)),
    wx.encode(dict(_GOOD, event_type="payment.refunded")),
    wx.encode(dict(_GOOD, event_type="PAYMENT.SUCCEEDED")),
    wx.encode(dict(_GOOD, provider_reference="mock_pi_short")),
    wx.encode(dict(_GOOD, amount="0")),
    wx.encode(dict(_GOOD, amount="-5")),
    wx.encode(dict(_GOOD, amount="1,000")),
    wx.encode(dict(_GOOD, amount=" 100")),
    wx.encode(dict(_GOOD, amount="100.00001")),
    wx.encode(dict(_GOOD, amount="100000")),
    wx.encode(dict(_GOOD, currency_code="USD")),
    wx.encode(dict(_GOOD, occurred_at="2026-09-18 10:00:00")),
    wx.encode(dict(_GOOD, occurred_at="2026-02-30T10:00:00Z")),
])
def test_a_verified_body_that_is_not_exactly_an_event_is_refused(raw):
    with pytest.raises(PaymentProviderWebhookError):
        _normalized(raw)


def test_the_sandbox_emits_one_event_per_decided_outcome_and_redelivers_it_identically():
    provider = _provider()
    reference = provider.create_payment_intent(idempotency_key="a" * 64, amount=Decimal("100"),
                                               currency_code="LYD").reference
    from app.services.payment_providers import PaymentProviderError

    with pytest.raises(PaymentProviderError):
        provider.emit_webhook(reference)
    provider.simulate_checkout_outcome(reference, "success")
    first, _ = provider.emit_webhook(reference)
    again, _ = provider.emit_webhook(reference)
    assert first == again and json.loads(first)["amount"] == "100.0000"
    cancelled = _provider()
    other = cancelled.create_payment_intent(idempotency_key="b" * 64, amount=Decimal("1"),
                                            currency_code="LYD").reference
    cancelled.cancel_payment(other)
    with pytest.raises(PaymentProviderError):
        cancelled.emit_webhook(other)
    with pytest.raises(PaymentProviderError):
        _provider().emit_webhook(reference)
    with pytest.raises(PaymentProviderWebhookError):
        MockPaymentProvider().emit_webhook(reference)


# ===========================================================================
# The public endpoint
# ===========================================================================


def test_the_endpoint_is_one_post_only_rule_with_no_login(app, client):
    rules = [(rule.rule, set(rule.methods - {"HEAD", "OPTIONS"}))
             for rule in app.url_map.iter_rules() if rule.rule.startswith("/webhooks")]
    assert rules == [("/webhooks/payments/mock", {"POST"})]
    for method in ("get", "put", "delete", "patch"):
        assert getattr(client, method)(wx.WEBHOOK_URL).status_code == 405, method
    # Anonymous: no login redirect, just the generic refusal of an empty body.
    response = client.post(wx.WEBHOOK_URL, data=b"", content_type="application/json")
    assert response.status_code == 400 and response.get_json() == wx.REJECTED


def test_the_endpoint_is_the_one_csrf_exception_and_still_needs_its_signature():
    application = ix.make_app(WTF_CSRF_ENABLED=True)
    with application.app_context():
        db.create_all()
        try:
            client = application.test_client()
            w = wx.world(application)
            with application.app_context():
                owner, actor = ix.invoice_and_admin(w)
                row = ix.registered_intent(application, owner, actor)
                reference = row.provider_reference
            response, _raw = wx.deliver(client, reference)
            assert response.status_code == 200 and response.get_json() == wx.OK
            forged = wx.encode(wx.event(reference))
            response = wx.post(client, forged, headers={})
            assert response.status_code == 400
            # Every other POST still needs CSRF.
            refused = client.post("/auth/login", data={"email": "x", "password": "y"})
            assert refused.status_code == 400
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_the_endpoint_does_not_exist_unless_the_sandbox_is_enabled():
    application = ix.make_app(mode="disabled")
    with application.app_context():
        db.create_all()
        try:
            client = application.test_client()
            raw = wx.encode(wx.event("mock_pi_" + "a" * 32))
            assert wx.post(client, raw).status_code == 404
            assert PaymentProviderEvent.query.count() == 0
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded",
                                          "application/jsonp", "multipart/form-data"])
def test_only_json_is_read(app, client, content_type):
    w, xp = _pending_intent(app, client)
    before = wx.record(app)
    raw = wx.encode(wx.event(wx.reference_of(xp)))
    response = wx.post(client, raw, content_type=content_type)
    assert response.status_code == 400 and response.get_json() == wx.REJECTED
    assert wx.record(app) == before


def test_an_oversized_body_is_refused_unread(app, client, monkeypatch):
    w, xp = _pending_intent(app, client)
    before = wx.record(app)
    provider = ix.provider_of(app)
    verified = []
    real = provider.verify_webhook
    monkeypatch.setattr(provider, "verify_webhook",
                        lambda *args, **kwargs: verified.append(1) or real(*args, **kwargs))
    big = wx.encode(wx.event(wx.reference_of(xp), padding="x" * MAX_WEBHOOK_BODY_BYTES))
    response = wx.post(client, big)
    assert response.status_code == 400 and response.get_json() == wx.REJECTED
    assert verified == [] and wx.record(app) == before


@pytest.mark.parametrize("kind", ["unsigned", "bad_signature", "stale", "future", "invalid_json",
                                  "duplicate_key", "unknown_field", "unknown_reference",
                                  "wrong_currency", "number_amount"])
def test_every_refusal_is_the_same_generic_answer_and_stores_nothing(app, client, kind):
    w, xp = _pending_intent(app, client)
    reference = wx.reference_of(xp)
    good = wx.encode(wx.event(reference))
    now = int(time.time())
    cases = {
        "unsigned": (good, {}),
        "bad_signature": (good, wx.signed_headers(
            good, secret="wrong-secret-0123456789abcdefghijk")),
        "stale": (good, wx.signed_headers(good, timestamp=now - WEBHOOK_MAX_AGE_SECONDS - 5)),
        "future": (good, wx.signed_headers(
            good, timestamp=now + WEBHOOK_MAX_FUTURE_SKEW_SECONDS + 5)),
        "invalid_json": (b"{oops", None),
        "duplicate_key": (good[:-1] + b',"amount":"1.0000"}', None),
        "unknown_field": (wx.encode(wx.event(reference, card_number="4111111111111111")), None),
        "unknown_reference": (wx.encode(wx.event("mock_pi_" + "0" * 32)), None),
        "wrong_currency": (wx.encode(wx.event(reference, currency_code="USD")), None),
        "number_amount": (wx.encode(wx.event(reference, amount=1250.5)), None),
    }
    raw, headers = cases[kind]
    before = wx.record(app)
    response = wx.post(client, raw, headers=headers)
    assert response.status_code == 400 and response.get_json() == wx.REJECTED
    assert response.get_data() == wx.post(client, b"", headers={}).get_data()
    assert response.headers["Cache-Control"] == "no-store"
    text = response.get_data(as_text=True) + json.dumps(dict(response.headers))
    for secret in (wx.SECRET, reference, "v1=", "signature", "stale", "reference"):
        assert secret not in text, secret
    assert wx.record(app) == before


# ===========================================================================
# Processing: the verified online collection
# ===========================================================================


def test_a_verified_success_records_exactly_one_online_collection_atomically(app, client):
    w, xp = _pending_intent(app, client)
    intent = wx.intent_row(xp)
    before_counts = wx.counts()
    invoice_version = db.session.get(Invoice, w["invoice_id"]).version
    response, raw = wx.deliver(client, intent.provider_reference,
                               occurred_at="2020-01-01T00:00:00Z")
    assert response.status_code == 200 and response.get_json() == wx.OK
    assert response.headers["Cache-Control"] == "no-store"
    assert wx.counts() == tuple(n + d for n, d in zip(before_counts, (1, 1, 2, 1)))

    (payment,) = wx.online_payments()
    intent = wx.intent_row(xp)
    assert (payment.kind, payment.method, payment.status, payment.amount,
            payment.currency_code) == ("collection", "online", "confirmed", intent.amount, "LYD")
    assert (payment.recorded_by_id, payment.confirmed_by_id, payment.payment_intent_id) == (
        None, None, intent.id)
    assert payment.recorded_at == payment.confirmed_at == payment.created_at
    assert payment.recorded_at.microsecond == 0 and payment.recorded_at.year >= 2026
    assert (payment.bank_transfer_reference, payment.bank_transfer_date) == (None, None)

    receipt = Receipt.query.filter_by(payment_transaction_id=payment.id).one()
    assert re.fullmatch(r"RCT-\d{4}-\d{6}", receipt.receipt_number)
    assert (receipt.status, receipt.issued_by_id, receipt.snapshot["schema"]) == (
        "issued", None, ONLINE_RECEIPT_SNAPSHOT_SCHEMA)
    assert "confirmed_by_name" not in receipt.snapshot

    (stored,) = wx.stored_events(xp)
    assert (stored.outcome, stored.event_type, stored.payment_transaction_id) == (
        "confirmed", "payment.succeeded", payment.id)
    assert stored.payload_digest == wx.digest(raw) and stored.amount == intent.amount
    assert stored.provider_occurred_at.year == 2020  # the provider's clock, kept as information
    assert stored.processed_at == payment.recorded_at and stored.received_at <= stored.processed_at

    events = PaymentAuditEvent.query.filter_by(invoice_id=w["invoice_id"]).order_by(
        PaymentAuditEvent.id).all()[-2:]
    assert [e.kind for e in events] == ["payment_online_confirmed", "receipt_online_issued"]
    for audit in events:
        assert audit.actor_id is None and audit.occurred_at == payment.recorded_at
        for snapshot in (audit.before_snapshot, audit.after_snapshot):
            assert snapshot["schema"] == ONLINE_PAYMENT_SNAPSHOT_SCHEMA
            assert snapshot["online"] == {"payment_intent_public_id": xp,
                                          "provider_event_public_id": stored.public_id}
            text = json.dumps(snapshot)
            for leak in (intent.provider_reference, stored.provider_event_id, wx.digest(raw),
                         wx.SECRET):
                assert leak not in text
    assert events[1].after_snapshot["outstanding_amount"] == "0.0000"

    assert (intent.status, intent.version, intent.provider_result_at) == ("confirmed", 2, None)
    assert intent.terminal_at == payment.recorded_at
    owner = db.session.get(Invoice, w["invoice_id"])
    assert owner.version == invoice_version
    balance = payment_balance(active_lines(invoice_lines(owner.id)),
                              PaymentTransaction.query.filter_by(invoice_id=owner.id).all())
    assert (balance.paid, balance.outstanding) == (Decimal("1250.5000"), Decimal("0"))


def test_a_browser_observed_success_is_confirmed_only_by_its_webhook(app, client):
    w, xp = _pending_intent(app, client)
    ix.simulate(client, w, xp, "success")
    ix.return_to_lms(client, w, xp)
    intent = wx.intent_row(xp)
    assert intent.status == "provider_succeeded" and wx.counts()[:2] == (0, 0)
    observed_at, observed_by = intent.provider_result_at, intent.provider_result_by_id
    response, _raw = wx.deliver(client, intent.provider_reference)
    assert response.status_code == 200
    intent = wx.intent_row(xp)
    assert (intent.status, intent.version) == ("confirmed", 3)
    assert (intent.provider_result_at, intent.provider_result_by_id) == (observed_at, observed_by)
    assert len(wx.online_payments()) == 1


def test_the_browser_return_alone_never_creates_a_payment_or_receipt(app, client):
    w, xp = _pending_intent(app, client)
    before = wx.counts()
    ix.simulate(client, w, xp, "success")
    ix.return_to_lms(client, w, xp)
    for extra in ({"status": "confirmed"}, {"payment": "online"},
                  {"event_type": "payment.succeeded"}):
        client.post(ix.return_url(w, xp), data=extra)
    assert wx.counts() == before and wx.stored_events() == []


def test_a_redelivered_event_is_idempotent_and_a_reused_id_is_refused(app, client):
    w, xp = _pending_intent(app, client)
    reference = wx.reference_of(xp)
    raw = wx.encode(wx.event(reference))
    assert wx.post(client, raw).status_code == 200
    after_first = wx.record(app)
    for _ in range(3):
        response = wx.post(client, raw)  # a fresh signature over the same body
        assert response.status_code == 200 and response.get_json() == wx.OK
    assert wx.record(app) == after_first
    (stored,) = wx.stored_events()
    tampered = wx.encode(wx.event(reference, event_id=stored.provider_event_id,
                                  occurred_at="2026-09-18T11:00:00Z"))
    response = wx.post(client, tampered)
    assert response.status_code == 400 and response.get_json() == wx.REJECTED
    assert wx.record(app) == after_first


def test_a_second_distinct_success_event_is_a_duplicate_and_collects_nothing(app, client):
    w, xp = _pending_intent(app, client)
    reference = wx.reference_of(xp)
    wx.deliver(client, reference)
    response, _raw = wx.deliver(client, reference)
    assert response.status_code == 200
    assert [e.outcome for e in wx.stored_events(xp)] == ["confirmed", "duplicate"]
    assert len(wx.online_payments()) == 1 and Receipt.query.count() == 1


@pytest.mark.parametrize("prior", ["pending", "provider_succeeded"])
def test_a_verified_failure_closes_an_active_intent_without_any_financial_record(
    app, client, prior
):
    w, xp = _pending_intent(app, client)
    if prior == "provider_succeeded":
        ix.simulate(client, w, xp, "success")
        ix.return_to_lms(client, w, xp)
    before = wx.counts()
    response, _raw = wx.deliver(client, wx.reference_of(xp), event_type=wx.FAILED)
    assert response.status_code == 200
    intent = wx.intent_row(xp)
    assert intent.status == "provider_failed" and intent.terminal_at is not None
    assert (intent.provider_result_at is None) == (prior == "pending")
    assert wx.counts() == tuple(n + d for n, d in zip(before, (0, 0, 0, 1)))
    assert [e.outcome for e in wx.stored_events(xp)] == ["failed"]
    # A second failure event restates it; a later success conflicts.
    wx.deliver(client, wx.reference_of(xp), event_type=wx.FAILED)
    wx.deliver(client, wx.reference_of(xp))
    assert [e.outcome for e in wx.stored_events(xp)] == [
        "failed", "duplicate", "reconciliation_required"]
    assert wx.online_payments() == []


def test_the_intents_terminal_states_decide_ignored_and_reconciliation_outcomes(app, client):
    w = wx.login_world(app, client)
    with app.app_context():
        owner, actor = ix.invoice_and_admin(w)
        cancelled = ix.registered_intent(app, owner, actor)
    ix.cancel(client, w, cancelled.public_id)
    reference = wx.reference_of(cancelled.public_id)
    wx.deliver(client, reference, event_type=wx.FAILED)
    wx.deliver(client, reference)
    assert [e.outcome for e in wx.stored_events(cancelled.public_id)] == [
        "ignored_terminal", "reconciliation_required"]
    confirmed = wx.confirmed_intent(client, w)
    wx.deliver(client, wx.reference_of(confirmed), event_type=wx.FAILED)
    assert [e.outcome for e in wx.stored_events(confirmed)][-1] == "reconciliation_required"
    assert wx.intent_row(confirmed).status == "confirmed" and len(wx.online_payments()) == 1


@pytest.mark.parametrize("field, value", [("amount", "1250.4990"), ("amount", "1.0000"),
                                          ("amount", "1250.5010")])
def test_an_event_that_does_not_match_its_intent_is_kept_for_reconciliation(
    app, client, field, value
):
    w, xp = _pending_intent(app, client)
    before = wx.counts()
    response, _raw = wx.deliver(client, wx.reference_of(xp), **{field: value})
    assert response.status_code == 200
    assert [e.outcome for e in wx.stored_events(xp)] == ["reconciliation_required"]
    assert wx.intent_row(xp).status == "pending"
    assert wx.counts() == tuple(n + d for n, d in zip(before, (0, 0, 0, 1)))


def _legacy(app, w, kind):
    with app.app_context():
        owner, actor = ix.invoice_and_admin(w)
        if kind == "pending_transfer":
            px.payment(owner, actor, method="bank_transfer", status="pending", amount="10")
        elif kind == "confirmed_cash":
            px.cash_with_receipt(owner, actor, amount="10")
        elif kind == "capacity":
            for _ in range(25):
                px.payment(owner, actor, method="bank_transfer", status="rejected", amount="1")
        elif kind == "line_added":
            db.session.execute(update(Invoice).where(Invoice.id == owner.id).values(version=9))
            fx.line(owner, label="Late fee", amount="5.000")
        elif kind == "invoice_cancelled":
            db.session.execute(update(Invoice).where(Invoice.id == owner.id).values(
                status="cancelled", cancelled_at=fx.CANCELLED_AT, cancelled_by_id=actor.id,
                updated_at=fx.CANCELLED_AT))
        elif kind == "assignment_cancelled":
            db.session.execute(update(StudentFeeAssignment).where(
                StudentFeeAssignment.id == w["assignment_id"]).values(
                status=fees.CANCELLED, cancelled_at=fees.CANCELLED_AT, cancelled_by_id=actor.id,
                updated_at=fees.CANCELLED_AT, version=2))
        elif kind == "second_active_intent":
            ix.intent(owner, actor)
        elif kind == "receipts_exhausted":
            px.sequence(fx.center_year(app, utc_reference_now()), 999999)
        db.session.commit()


@pytest.mark.parametrize("kind", ["pending_transfer", "confirmed_cash", "capacity", "line_added",
                                  "invoice_cancelled", "assignment_cancelled",
                                  "second_active_intent", "receipts_exhausted"])
def test_legacy_overlap_or_a_changed_invoice_is_reconciliation_and_financially_inert(
    app, client, kind
):
    w, xp = _pending_intent(app, client)
    _legacy(app, w, kind)
    before_payments = [(r.id, r.status, r.version) for r in PaymentTransaction.query.all()]
    before_receipts, before_audit = Receipt.query.count(), PaymentAuditEvent.query.count()
    before_sequences = [(r.calendar_year, r.last_number) for r in ReceiptNumberSequence.query.all()]
    response, _raw = wx.deliver(client, wx.reference_of(xp))
    assert response.status_code == 200 and response.get_json() == wx.OK
    assert [e.outcome for e in wx.stored_events(xp)] == ["reconciliation_required"]
    db.session.expire_all()
    assert [(r.id, r.status, r.version) for r in PaymentTransaction.query.all()] == before_payments
    assert (Receipt.query.count(), PaymentAuditEvent.query.count()) == (before_receipts,
                                                                        before_audit)
    assert [(r.calendar_year, r.last_number)
            for r in ReceiptNumberSequence.query.all()] == before_sequences
    assert wx.intent_row(xp).status == "pending"


def test_a_transient_failure_commits_nothing_and_the_retry_succeeds(app, client, monkeypatch):
    w, xp = _pending_intent(app, client)
    raw = wx.encode(wx.event(wx.reference_of(xp)))
    before = wx.record(app)
    real_commit = webhooks.db.session.commit

    def lost_lock():
        raise OperationalError("COMMIT", {}, Exception("Lock wait timeout exceeded"))

    monkeypatch.setattr(webhooks.db.session, "commit", lost_lock)
    response = wx.post(client, raw)
    monkeypatch.setattr(webhooks.db.session, "commit", real_commit)
    assert response.status_code == 503 and response.get_json() == wx.RETRY
    assert "Lock wait" not in response.get_data(as_text=True)
    assert wx.record(app) == before
    response = wx.post(client, raw)
    assert response.status_code == 200 and len(wx.online_payments()) == 1


def test_receipt_numbers_stay_unique_across_manual_and_online_collections(app, client):
    w, xp = _pending_intent(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        other = px.issued_invoice(fees.assignment(fees.enrollment(), db.session.get(
            FeePlan, w["plan_id"]), actor), actor)
        other_w = dict(w, ip=other.public_id, invoice_id=other.id)
        assignment = db.session.get(StudentFeeAssignment, other.student_fee_assignment_id)
        enrolled = db.session.get(fees.Enrollment, assignment.enrollment_id)
        group = db.session.get(fees.Group, enrolled.group_id)
        other_w.update(gp=group.public_id, ep=enrolled.public_id, ap=assignment.public_id)
    px.record_cash(client, other_w, amount="10")
    wx.deliver(client, wx.reference_of(xp))
    numbers = [r.receipt_number for r in Receipt.query.order_by(Receipt.id)]
    assert len(numbers) == len(set(numbers)) == 2
    assert int(numbers[1][-6:]) == int(numbers[0][-6:]) + 1


def test_an_online_collection_can_be_reversed_in_full_by_an_administrator(app, client):
    w = wx.login_world(app, client)
    xp = wx.confirmed_intent(client, w)
    (payment,) = wx.online_payments()
    response = px.reverse(client, w, payment.public_id, reason="Provider reported a chargeback")
    assert px.REVERSED_OK_TEXT in px.followed(client, response)
    db.session.expire_all()
    reversal = PaymentTransaction.query.filter_by(kind="reversal").one()
    assert (reversal.method, reversal.recorded_by_id, reversal.payment_intent_id) == (
        "online", w["admin_id"], None)
    receipt = Receipt.query.filter_by(payment_transaction_id=payment.id).one()
    assert (receipt.status, receipt.voided_by_id) == ("voided", w["admin_id"])
    assert wx.intent_row(xp).status == "confirmed"
    owner = db.session.get(Invoice, w["invoice_id"])
    balance = payment_balance(active_lines(invoice_lines(owner.id)),
                              PaymentTransaction.query.filter_by(invoice_id=owner.id).all())
    assert (balance.paid, balance.outstanding) == (Decimal("0"), Decimal("1250.5000"))
    # No provider refund exists; refunds stay fail-closed.
    assert "refund" not in json.dumps([r.rule for r in app.url_map.iter_rules()])


def test_processing_locks_the_documented_chain_with_no_administrator(app, client, monkeypatch):
    w, xp = _pending_intent(app, client)
    with app.app_context():
        owner, actor = ix.invoice_and_admin(w)
        px.payment(owner, actor, method="bank_transfer", status="rejected", amount="3")
        ix.intent(owner, actor, status="cancelled")
    requested = []
    original = Query.with_for_update

    def spy(self, *args, **kwargs):
        requested.append(self.column_descriptions[0]["entity"].__tablename__)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", spy)
    response, _raw = wx.deliver(client, wx.reference_of(xp))
    monkeypatch.undo()
    assert response.status_code == 200
    assert requested == ["academic_terms", "levels", "courses", "groups", "users", "enrollments",
                         "student_fee_assignments", "invoices", "payment_intents",
                         "payment_transactions", "payment_intents", "receipt_number_sequences"]


def test_processing_makes_no_network_call(app, client, monkeypatch):
    w, xp = _pending_intent(app, client)

    def refuse(*_args, **_kwargs):
        raise AssertionError("webhook processing opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    response, _raw = wx.deliver(client, wx.reference_of(xp))
    assert response.status_code == 200


def test_a_signed_in_administrator_never_becomes_the_webhooks_actor(app, client):
    w, xp = _pending_intent(app, client)  # the client holds an Administrator session
    wx.deliver(client, wx.reference_of(xp))
    events = PaymentAuditEvent.query.filter(PaymentAuditEvent.kind.in_(
        ["payment_online_confirmed", "receipt_online_issued"])).all()
    assert len(events) == 2 and all(e.actor_id is None for e in events)
    (payment,) = wx.online_payments()
    assert payment.recorded_by_id is None


def test_the_unverified_body_is_never_parsed(app, client, monkeypatch):
    w, xp = _pending_intent(app, client)
    provider = ix.provider_of(app)
    calls = []
    real = provider.normalize_event
    monkeypatch.setattr(provider, "normalize_event",
                        lambda verified: calls.append(1) or real(verified))
    raw = wx.encode(wx.event(wx.reference_of(xp)))
    wx.post(client, raw, headers=wx.signed_headers(raw, secret="wrong-secret-0123456789abcdefghij"))
    assert calls == []
    wx.post(client, raw)
    assert calls == [1]


def test_the_mock_module_needs_no_network_or_credential_import():
    source = inspect.getsource(mock_module)
    for forbidden in ("requests", "urllib", "http.client", "socket", "os.environ", "getenv"):
        assert forbidden not in source, forbidden
