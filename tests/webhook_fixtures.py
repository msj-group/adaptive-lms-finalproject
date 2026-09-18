"""Shared fixtures for the Phase 5 / M07 signed-webhook test modules.

Builds on ``tests/payment_intent_fixtures.py`` (whose app, world, intent and
route helpers it reuses). Events are built and signed here exactly as the
Mock/Sandbox provider signs them -- HMAC-SHA256 under the suite's own test-only
secret over ``"<timestamp>." + raw body`` -- so a test can deliver a genuine,
tampered, stale, conflicting or malformed event to the public endpoint.
"""

import hashlib
import json
import secrets
import time

import tests.payment_fixtures as px
import tests.payment_intent_fixtures as ix
from app.extensions import db
from app.models import (
    PaymentAuditEvent,
    PaymentIntent,
    PaymentProviderEvent,
    PaymentTransaction,
    Receipt,
)
from app.services.mock_payment_provider import (
    WEBHOOK_SIGNATURE_HEADER,
    WEBHOOK_TIMESTAMP_HEADER,
    webhook_signature,
)

WEBHOOK_URL = "/webhooks/payments/mock"
SECRET = ix.TEST_WEBHOOK_SECRET
OK = {"status": "ok"}
REJECTED = {"status": "rejected"}
RETRY = {"status": "retry"}

SUCCEEDED = "payment.succeeded"
FAILED = "payment.failed"

ONLINE_BLOCK_TEXT = "has an active online payment intent, so no cash or bank-transfer payment"
DELIVERED_CONFIRMED_TEXT = "the online payment is confirmed and receipt"
DELIVERED_FAILED_TEXT = "the payment failed, so the intent is closed"
DELIVERED_RECONCILIATION_TEXT = "It is kept on this intent for reconciliation."
NOTHING_TO_DELIVER_TEXT = "has no decided outcome to report yet"
SYSTEM_ACTOR_TEXT = "the verified provider webhook"


def new_event_id():
    return "evt_mock_" + secrets.token_hex(16)


def event(reference, event_type=SUCCEEDED, amount="1250.5000", currency_code="LYD",
          event_id=None, occurred_at="2026-09-18T10:00:00Z", **extra):
    body = {
        "event_id": event_id or new_event_id(),
        "event_type": event_type,
        "provider_reference": reference,
        "amount": amount,
        "currency_code": currency_code,
        "occurred_at": occurred_at,
    }
    body.update(extra)
    return body


def encode(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def signed_headers(raw, timestamp=None, secret=SECRET):
    timestamp = int(time.time()) if timestamp is None else timestamp
    return {
        WEBHOOK_TIMESTAMP_HEADER: str(timestamp),
        WEBHOOK_SIGNATURE_HEADER: webhook_signature(secret.encode("ascii"), timestamp, raw),
    }


def post(client, raw, headers=None, content_type="application/json"):
    """POST `raw` bytes to the public endpoint, signed genuinely unless
    `headers` says otherwise."""
    return client.post(WEBHOOK_URL, data=raw,
                       headers=signed_headers(raw) if headers is None else headers,
                       content_type=content_type)


def deliver(client, reference, **kwargs):
    """Sign and deliver one event; ``(response, raw)``."""
    raw = encode(event(reference, **kwargs))
    return post(client, raw), raw


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def reference_of(xp):
    return ix.stored_intent(xp).provider_reference


# ---------------------------------------------------------------------------
# The Mock/Sandbox checkout's delivery control
# ---------------------------------------------------------------------------


def deliver_url(w, xp, ip=None):
    return ix.checkout_url(w, xp, ip) + "/webhook"


def delivery_context(client, w, xp):
    html = ix.page(client, ix.checkout_url(w, xp))
    context = ix.checkout_context_in(html, deliver_url(w, xp))
    assert context, html
    return context


def sandbox_deliver(client, w, xp, context=None):
    if context is None:
        context = delivery_context(client, w, xp)
    return client.post(deliver_url(w, xp), data={ix.CHECKOUT_FIELD: context})


def confirmed_intent(client, w):
    """Create an intent, simulate the payer's success and deliver the sandbox
    provider's signed webhook through the checkout; the intent's public id."""
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "success")
    response = sandbox_deliver(client, w, xp)
    assert response.status_code == 302, response.status_code
    assert ix.stored_intent(xp).status == "confirmed"
    return xp


# ---------------------------------------------------------------------------
# What "nothing was written" is compared against
# ---------------------------------------------------------------------------


def webhook_record():
    """Every intent, payment, receipt, sequence, audit event, invoice and line,
    plus every provider event."""
    db.session.expire_all()
    return (
        ix.intent_record(),
        [(r.id, r.public_id, r.provider_event_id, r.payment_intent_id, r.event_type, r.amount,
          r.outcome, r.payment_transaction_id, r.payload_digest)
         for r in PaymentProviderEvent.query.order_by(PaymentProviderEvent.id)],
    )


def record(app):
    with app.app_context():
        return webhook_record()


def counts():
    db.session.expire_all()
    return (PaymentTransaction.query.count(), Receipt.query.count(),
            PaymentAuditEvent.query.count(), PaymentProviderEvent.query.count())


def stored_events(xp=None):
    db.session.expire_all()
    query = PaymentProviderEvent.query
    if xp is not None:
        query = query.filter(PaymentProviderEvent.payment_intent_id == ix.stored_intent(xp).id)
    return query.order_by(PaymentProviderEvent.id).all()


def online_payments():
    db.session.expire_all()
    return PaymentTransaction.query.filter_by(method="online").order_by(PaymentTransaction.id).all()


def intent_row(xp):
    db.session.expire_all()
    return PaymentIntent.query.filter_by(public_id=xp).one()


login_world = ix.login_world
world = ix.world
make_app = ix.make_app
page = px.page
followed = px.followed
