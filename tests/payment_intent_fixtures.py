"""Shared fixtures for the Phase 5 / M06 payment intent test modules.

Kept in one module, like ``tests/payment_fixtures.py`` (whose academic chain,
invoice, payment and token helpers it reuses), so the model, provider, route,
transaction and migration suites build the same intents.

Row helpers write straight into the tables, so a test about (say) a cancelled
intent refusing a change is not also a test of the cancellation route. The
route helpers at the bottom drive the application's own write path and read
every signed token and checkout context out of the page the server rendered.

Every stored moment is a whole-second 2026 UTC instant earlier than the real
clock, so the timestamp CHECKs hold when a route later writes "now" on top of
a fixture row.
"""

import hashlib
import re
from datetime import datetime

import tests.payment_fixtures as px
from app import create_app
from app.extensions import db
from app.models import Invoice, PaymentIntent, User
from app.services.mock_payment_provider import mock_reference

PW = px.PW
STATE_FIELD = px.STATE_FIELD
CHECKOUT_FIELD = "checkout_context"
STALE_TEXT = px.STALE_TEXT
INTEGRITY_TEXT = px.INTEGRITY_TEXT

CREATED_AT = datetime(2026, 7, 5, 9, 0, 0)
RESULT_AT = datetime(2026, 7, 5, 10, 0, 0)
TERMINAL_AT = datetime(2026, 7, 5, 11, 0, 0)

NOTICE_RECORDED = "Provider result recorded for sandbox testing only."
NOTICE_M07 = "No payment is confirmed until a signed provider webhook is verified."
CREATED_OK_TEXT = "Sandbox payment intent created for"
ALREADY_CREATED_TEXT = "was already created by the same request"
CANCELLED_OK_TEXT = "Payment intent cancelled. It is kept as history."
CHECKOUT_INVALID_TEXT = "This sandbox checkout is not valid for this payment intent"
OUTCOME_INVALID_TEXT = "Choose one of the simulated outcomes"
SIMULATED_TEXT = "The sandbox provider recorded a simulated"
SIMULATION_REFUSED_TEXT = "The sandbox provider did not record that outcome"
RESULT_PENDING_TEXT = "has not reported a result for this payment intent yet"
RESULT_CANCELLED_TEXT = "reports that the checkout was cancelled"
PROVIDER_REFUSED_TEXT = "The sandbox provider did not accept this request"
NOT_PENDING_TEXT = "This payment intent is not pending"
SUCCEEDED_NOT_CANCELLABLE_TEXT = "The provider reported success for this payment intent"
MOCK_DISABLED_TEXT = "The Mock/Sandbox payment provider is not enabled"
DISABLED_BANNER_TEXT = "Online payments are disabled in this environment"
CREATE_CONFIRM_TEXT = "tick the confirmation box before creating a sandbox payment intent"
CANCEL_CONFIRM_TEXT = "tick the confirmation box before cancelling this payment intent"
NOT_ISSUED_TEXT = "A payment intent is created only for an issued invoice"
ITEMS_INVALID_TEXT = "lines are not a valid charge, so no payment intent"
BALANCE_BROKEN_TEXT = "do not describe a valid balance, so no payment intent"
MANUAL_PAYMENT_TEXT = "has a pending or confirmed payment, so no online payment intent"
ACTIVE_INTENT_TEXT = "already has an active payment intent"
LIMIT_TEXT = "already holds 25 payment intents"
SETTLED_TEXT = "has no outstanding balance, so no payment intent"
ABOVE_MAXIMUM_TEXT = "more than the largest single payment"
INTENT_FROZEN_TEXT = "has an active online payment intent, so its lines can no longer be changed"
SANDBOX_LABEL_TEXT = "Simulation only. No real payment is taken."

#: The world's issued invoice: 1,250.500 LYD in two lines.
INVOICE_AMOUNT = "1250.5000"

login_as = px.login_as
fresh_identity = px.fresh_identity
admin = px.admin
user = px.user
state_in = px.state_in
page = px.page
followed = px.followed
world = px.world
rows_of = px.rows_of

_SEQUENCE = {"n": 0}


def _next():
    _SEQUENCE["n"] += 1
    return _SEQUENCE["n"]


#: The suite's own, test-only Mock/Sandbox webhook secret, injected
#: explicitly (``TestingConfig`` pins none). Never a real value.
TEST_WEBHOOK_SECRET = "test-only-Mk7wQ2vN9xR4tB8zL3pH6sJ1fD5gK0aYc"


def make_app(mode="mock", config_name="testing", **overrides):
    """A testing application with the given provider mode and the suite's
    own test-only webhook secret."""
    overrides.setdefault("MOCK_PAYMENT_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    return create_app(config_name, PAYMENT_PROVIDER_MODE=mode, **overrides)


def login_world(app, client):
    w = px.world(app)
    login_as(client, "admin@example.com")
    return w


def provider_of(app):
    return app.extensions["payment_provider"].provider


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

OVERVIEW_URL = "/admin/payment-intents"


def invoice_url(w, ip=None):
    return px.invoice_url(w, ip)


def intents_url(w, ip=None):
    return invoice_url(w, ip) + "/payment-intents"


def new_url(w, ip=None):
    return intents_url(w, ip) + "/new"


def intent_url(w, xp, ip=None):
    return f"{intents_url(w, ip)}/{xp}"


def checkout_url(w, xp, ip=None):
    return intent_url(w, xp, ip) + "/checkout"


def return_url(w, xp, ip=None):
    return intent_url(w, xp, ip) + "/return"


def result_url(w, xp, ip=None):
    return intent_url(w, xp, ip) + "/result"


def cancel_url(w, xp, ip=None):
    return intent_url(w, xp, ip) + "/cancel"


# ---------------------------------------------------------------------------
# Rows, written directly
# ---------------------------------------------------------------------------


def key_for(seed):
    return hashlib.sha256(f"fixture-key-{seed}".encode()).hexdigest()


def intent(owner, actor, status="pending", amount=INVOICE_AMOUNT, key=None, reference=None,
           created_at=CREATED_AT):
    """One intent in a consistent lifecycle state, written directly. Its
    reference is the one the mock provider derives from its key. A
    ``confirmed`` intent (Phase 5 / M07) has no browser result and no
    collection here; the webhook suites build real ones."""
    key = key or key_for(_next())
    result = status in ("provider_succeeded", "provider_failed")
    terminal = status in ("provider_failed", "cancelled", "confirmed")
    row = PaymentIntent(
        invoice_id=owner.id,
        provider="mock",
        provider_reference=reference or mock_reference(key),
        idempotency_key=key,
        status=status,
        currency_code="LYD",
        amount=amount,
        created_by_id=actor.id,
        provider_result_at=RESULT_AT if result else None,
        provider_result_by_id=actor.id if result else None,
        terminal_at=(RESULT_AT if status == "provider_failed" else TERMINAL_AT) if terminal else None,
        cancelled_by_id=actor.id if status == "cancelled" else None,
        version=1 if status == "pending" else 2,
        created_at=created_at,
        updated_at=created_at if status == "pending" else TERMINAL_AT if terminal else RESULT_AT,
    )
    db.session.add(row)
    db.session.commit()
    return row


def registered_intent(app, owner, actor, **kwargs):
    """A pending intent written directly **and** known to the app's mock
    provider, exactly as a route-created one would be."""
    row = intent(owner, actor, **kwargs)
    provider_of(app).create_payment_intent(
        idempotency_key=row.idempotency_key, amount=row.amount, currency_code="LYD"
    )
    return row


def stored_intents(w):
    db.session.expire_all()
    return (PaymentIntent.query.filter_by(invoice_id=w["invoice_id"])
            .order_by(PaymentIntent.id).all())


def stored_intent(xp):
    db.session.expire_all()
    return PaymentIntent.query.filter_by(public_id=xp).one()


def intent_record():
    """Every payment intent plus every payment, receipt, sequence, event and M04
    financial row -- what "nothing was written" is compared against."""
    db.session.expire_all()
    return (
        [(r.id, r.public_id, r.invoice_id, r.provider, r.provider_reference, r.idempotency_key,
          r.status, r.currency_code, r.amount, r.created_by_id, r.provider_result_at,
          r.provider_result_by_id, r.terminal_at, r.cancelled_by_id, r.version, r.created_at,
          r.updated_at)
         for r in PaymentIntent.query.order_by(PaymentIntent.id)],
        px.payment_record(),
    )


def record(app):
    with app.app_context():
        return intent_record()


def financial_record(app):
    """Everything but the intents: payments, receipts, sequences, audit events,
    invoices and lines -- what M06 must never write."""
    with app.app_context():
        return px.payment_record()


# ---------------------------------------------------------------------------
# Route-driven helpers
# ---------------------------------------------------------------------------


def xp_from(response):
    location = response.headers["Location"]
    match = re.search(r"/payment-intents/([^/?]+)$", location)
    assert match, location
    return match.group(1)


def create_token(client, w, ip=None):
    return state_in(page(client, new_url(w, ip)), new_url(w, ip))


def create(client, w, confirm=True, token=None, ip=None):
    if token is None:
        token = create_token(client, w, ip)
    data = {STATE_FIELD: token}
    if confirm:
        data["confirm"] = "yes"
    return client.post(new_url(w, ip), data=data)


def create_intent(client, w, ip=None):
    """Drive intent creation; the new intent's public id."""
    response = create(client, w, ip=ip)
    assert response.status_code == 302, response.status_code
    return xp_from(response)


def checkout_context_in(html, action):
    match = re.search(
        rf'action="{re.escape(action)}"[^>]*>\s*'
        r'<input type="hidden" name="csrf_token" value="[^"]*">\s*'
        rf'<input type="hidden" name="{CHECKOUT_FIELD}" value="([^"]*)"',
        html,
    )
    return match.group(1) if match else ""


def checkout_context(client, w, xp, ip=None):
    html = page(client, checkout_url(w, xp, ip))
    context = checkout_context_in(html, return_url(w, xp, ip))
    assert context, html
    return context


def simulate(client, w, xp, outcome="success", context=None, ip=None):
    if context is None:
        context = checkout_context(client, w, xp, ip)
    return client.post(checkout_url(w, xp, ip), data={CHECKOUT_FIELD: context, "outcome": outcome})


def return_to_lms(client, w, xp, context=None, ip=None, **extra):
    if context is None:
        context = checkout_context(client, w, xp, ip)
    return client.post(return_url(w, xp, ip), data=dict(extra, **{CHECKOUT_FIELD: context}))


def cancel_token(client, w, xp, ip=None):
    return state_in(page(client, cancel_url(w, xp, ip)), cancel_url(w, xp, ip))


def cancel(client, w, xp, confirm=True, token=None, ip=None):
    if token is None:
        token = cancel_token(client, w, xp, ip)
    data = {STATE_FIELD: token}
    if confirm:
        data["confirm"] = "yes"
    return client.post(cancel_url(w, xp, ip), data=data)


def invoice_and_admin(w):
    return db.session.get(Invoice, w["invoice_id"]), db.session.get(User, w["admin_id"])
