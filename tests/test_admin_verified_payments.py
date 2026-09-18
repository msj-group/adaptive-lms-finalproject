"""Phase 5 / M07 -- the Administrator side of verified online collections.

Manual collection (cash recording, bank-transfer recording and bank-transfer
confirmation) is refused while the invoice holds an active payment intent --
before the locks and again under them -- while rejection and reversal stay
available; the manual chains lock the invoice's intents after its payments;
the sandbox delivery control (POST-only, Administrator-only, CSRF, the
checkout context, every outcome); the intent pages' states and their safe
provider-event history; no route can record or forge an online payment; and
the overview, invoice timeline, payment history and receipt show the
system-origin rows a verified webhook writes.
"""

import re
from decimal import Decimal

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Query

import app.blueprints.admin.payments as payment_routes
import app.services.payment_webhooks as webhooks
import tests.fee_assignment_fixtures as fees
import tests.invoice_fixtures as fx
import tests.payment_fixtures as px
import tests.payment_intent_fixtures as ix
import tests.webhook_fixtures as wx
from app.extensions import db
from app.models import (
    FeePlan,
    Group,
    PaymentAuditEvent,
    PaymentProviderEvent,
    PaymentTransaction,
    Receipt,
    User,
)
from app.services.money import format_amount


@pytest.fixture
def app():
    application = ix.make_app()
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def _other_app(**kwargs):
    application = ix.make_app(**kwargs)
    ctx = application.app_context()
    ctx.push()
    db.create_all()
    return application, ctx


def _close(ctx):
    db.session.remove()
    db.drop_all()
    db.engine.dispose()
    ctx.pop()


def _direct_intent(w, status="pending"):
    owner, actor = ix.invoice_and_admin(w)
    return ix.intent(owner, actor, status=status).public_id


def _payments_only(record):
    """The payment rows, receipts, sequences and audit events of
    :func:`tests.payment_fixtures.payment_record` -- intents aside."""
    return record[:4]


def _second_world(w):
    """Another Enrollment's issued invoice under the same Administrator."""
    actor = db.session.get(User, w["admin_id"])
    plan = db.session.get(FeePlan, w["plan_id"])
    enrolled = fees.enrollment(enrolled=fees.student(name="Student Two"))
    assignment = fees.assignment(enrolled, plan, actor)
    owner = px.issued_invoice(assignment, actor)
    group = db.session.get(Group, enrolled.group_id)
    return dict(w, gp=group.public_id, ep=enrolled.public_id, ap=assignment.public_id,
                ip=owner.public_id, invoice_id=owner.id, assignment_id=assignment.id,
                enrollment_id=enrolled.id, group_id=group.id)


def _requested_locks(monkeypatch):
    requested = []
    original = Query.with_for_update

    def spy(self, *args, **kwargs):
        requested.append(self.column_descriptions[0]["entity"].__tablename__)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", spy)
    return requested


#: The manual chain's prefix (M05): hierarchy, Student, enrollment, the acting
#: Administrator, assignment, invoice.
_PREFIX = ["academic_terms", "levels", "courses", "groups", "users", "enrollments", "users",
           "student_fee_assignments", "invoices"]


# ===========================================================================
# Manual collection is blocked while an intent is active
# ===========================================================================


@pytest.mark.parametrize("status", ["pending", "provider_succeeded"])
def test_an_active_intent_hides_manual_collection_but_not_rejection_or_reversal(
    app, client, status
):
    w = ix.login_world(app, client)
    pending = px.pending_transfer(client, w, amount="100")
    px.record_cash(client, w, amount="10")
    cash = PaymentTransaction.query.filter_by(method="cash").one().public_id
    _direct_intent(w, status)
    html = px.page(client, px.payments_url(w))
    assert wx.ONLINE_BLOCK_TEXT in html
    for hidden in (px.cash_url(w), px.bank_url(w), px.confirm_url(w, pending)):
        assert f'href="{hidden}"' not in html, hidden
    for shown in (px.reject_url(w, pending), px.reverse_url(w, cash)):
        assert f'href="{shown}"' in html, shown
    for url in (px.cash_url(w), px.bank_url(w), px.confirm_url(w, pending)):
        response = client.get(url)
        assert response.status_code == 302, url
        assert wx.ONLINE_BLOCK_TEXT in px.followed(client, response), url


@pytest.mark.parametrize("status", ["pending", "provider_succeeded"])
def test_manual_collection_posts_are_refused_while_an_intent_is_active(app, client, status):
    w = ix.login_world(app, client)
    pending = px.pending_transfer(client, w, amount="100")
    tokens = (px.cash_token(client, w), px.bank_token(client, w),
              px.confirm_token(client, w, pending))
    _direct_intent(w, status)
    before = px.payment_record()
    for response in (px.record_cash(client, w, amount="10", token=tokens[0]),
                     px.record_bank(client, w, amount="10", token=tokens[1]),
                     px.confirm(client, w, pending, token=tokens[2])):
        assert response.status_code == 302
        assert wx.ONLINE_BLOCK_TEXT in px.followed(client, response)
    assert px.payment_record() == before


@pytest.mark.parametrize("action", ["cash", "bank", "confirm"])
def test_an_intent_created_at_the_lock_boundary_blocks_manual_collection(
    app, client, monkeypatch, action
):
    w = ix.login_world(app, client)
    pending = px.pending_transfer(client, w, amount="100") if action == "confirm" else None
    token = {"cash": lambda: px.cash_token(client, w), "bank": lambda: px.bank_token(client, w),
             "confirm": lambda: px.confirm_token(client, w, pending)}[action]()
    before = _payments_only(px.payment_record())
    real = payment_routes.lock_payment_chain

    def chain(*args, **kwargs):
        _direct_intent(w)
        db.session.commit()
        return real(*args, **kwargs)

    monkeypatch.setattr(payment_routes, "lock_payment_chain", chain)
    response = {"cash": lambda: px.record_cash(client, w, amount="10", token=token),
                "bank": lambda: px.record_bank(client, w, amount="10", token=token),
                "confirm": lambda: px.confirm(client, w, pending, token=token)}[action]()
    monkeypatch.undo()
    assert response.status_code == 302
    assert wx.ONLINE_BLOCK_TEXT in px.followed(client, response)
    assert _payments_only(px.payment_record()) == before
    assert len(ix.stored_intents(w)) == 1


@pytest.mark.parametrize("status", ["provider_failed", "cancelled"])
def test_a_closed_intent_does_not_block_manual_collection(app, client, status):
    w = ix.login_world(app, client)
    _direct_intent(w, status)
    assert wx.ONLINE_BLOCK_TEXT not in px.page(client, px.payments_url(w))
    assert px.CASH_OK_TEXT in px.followed(client, px.record_cash(client, w, amount="10"))
    pending = px.pending_transfer(client, w, amount="20")
    assert px.BANK_CONFIRMED_OK_TEXT in px.followed(client, px.confirm(client, w, pending))


@pytest.mark.parametrize("status", ["pending", "provider_succeeded"])
def test_rejection_and_reversal_stay_available_while_an_intent_is_active(app, client, status):
    w = ix.login_world(app, client)
    pending = px.pending_transfer(client, w, amount="100")
    px.record_cash(client, w, amount="10")
    cash = PaymentTransaction.query.filter_by(method="cash").one().public_id
    xp = _direct_intent(w, status)
    assert px.BANK_REJECTED_OK_TEXT in px.followed(client, px.reject(client, w, pending))
    assert px.REVERSED_OK_TEXT in px.followed(client, px.reverse(client, w, cash))
    assert ix.stored_intent(xp).status == status


@pytest.mark.parametrize("action", ["cash", "bank", "confirm"])
def test_the_manual_chains_lock_the_invoices_intents_after_its_payments(
    app, client, monkeypatch, action
):
    w = ix.login_world(app, client)
    pending = px.pending_transfer(client, w, amount="100")
    for status in ("cancelled", "provider_failed"):
        _direct_intent(w, status)
    token = {"cash": lambda: px.cash_token(client, w), "bank": lambda: px.bank_token(client, w),
             "confirm": lambda: px.confirm_token(client, w, pending)}[action]()
    requested = _requested_locks(monkeypatch)
    response = {"cash": lambda: px.record_cash(client, w, amount="10", token=token),
                "bank": lambda: px.record_bank(client, w, amount="10", token=token),
                "confirm": lambda: px.confirm(client, w, pending, token=token)}[action]()
    locked = requested[:]
    monkeypatch.undo()
    assert response.status_code == 302
    sequence = [] if action == "bank" else ["receipt_number_sequences"]
    assert locked == _PREFIX + ["payment_transactions", "payment_intents", "payment_intents"] + (
        sequence)


def test_rejection_and_reversal_do_not_lock_the_intents(app, client, monkeypatch):
    w = ix.login_world(app, client)
    pending = px.pending_transfer(client, w, amount="100")
    px.record_cash(client, w, amount="10")
    cash = PaymentTransaction.query.filter_by(method="cash").one().public_id
    _direct_intent(w, "cancelled")
    reject_token, reverse_token = px.reject_token(client, w, pending), px.reverse_token(
        client, w, cash)
    requested = _requested_locks(monkeypatch)
    px.reject(client, w, pending, token=reject_token)
    px.reverse(client, w, cash, token=reverse_token)
    monkeypatch.undo()
    assert "payment_intents" not in requested


# ===========================================================================
# The sandbox delivery control
# ===========================================================================


def test_delivery_is_post_only_and_for_administrators(app, client):
    w = ix.world(app)
    xp = _direct_intent(w)
    url = wx.deliver_url(w, xp)
    before = wx.webhook_record()
    anonymous = client.post(url, data={ix.CHECKOUT_FIELD: "anything"})
    assert anonymous.status_code == 302 and "/auth/login" in anonymous.headers["Location"]
    for role in ("teacher", "student"):
        ix.user(f"other-{role}@example.com", role)
        ix.login_as(client, f"other-{role}@example.com")
        assert client.post(url, data={ix.CHECKOUT_FIELD: "anything"}).status_code == 403, role
    ix.login_as(client, "admin@example.com")
    for method in ("get", "put", "delete", "patch"):
        assert getattr(client, method)(url).status_code == 405, method
    assert wx.webhook_record() == before


def test_delivery_needs_csrf():
    application, ctx = _other_app(WTF_CSRF_ENABLED=True)
    try:
        client = application.test_client()
        w = ix.world(application)
        login_page = client.get("/auth/login").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', login_page).group(1)
        client.post("/auth/login", data={"email": "admin@example.com", "password": ix.PW,
                                         "csrf_token": csrf})
        owner, actor = ix.invoice_and_admin(w)
        xp = ix.registered_intent(application, owner, actor).public_id
        ix.provider_of(application).simulate_checkout_outcome(
            ix.stored_intent(xp).provider_reference, "success")
        html = px.page(client, ix.checkout_url(w, xp))
        context = ix.checkout_context_in(html, wx.deliver_url(w, xp))
        csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
        before = wx.webhook_record()
        assert client.post(wx.deliver_url(w, xp),
                           data={ix.CHECKOUT_FIELD: context}).status_code == 400
        assert wx.webhook_record() == before
        response = client.post(wx.deliver_url(w, xp),
                               data={ix.CHECKOUT_FIELD: context, "csrf_token": csrf})
        assert response.status_code == 302
        assert ix.stored_intent(xp).status == "confirmed"
    finally:
        _close(ctx)


def test_delivery_does_not_exist_when_the_sandbox_is_disabled():
    application, ctx = _other_app(mode="disabled")
    try:
        client = application.test_client()
        w = ix.login_world(application, client)
        xp = _direct_intent(w)
        response = client.post(wx.deliver_url(w, xp), data={ix.CHECKOUT_FIELD: "anything"})
        assert response.status_code == 404
        assert wx.deliver_url(w, xp) not in px.page(client, ix.intent_url(w, xp))
    finally:
        _close(ctx)


def test_delivery_nests_under_its_own_invoice(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "success")
    context = wx.delivery_context(client, w, xp)
    other = _second_world(w)
    before = wx.webhook_record()
    for url in (wx.deliver_url(other, xp), wx.deliver_url(w, xp, ip=other["ip"]),
                wx.deliver_url(w, "0" * 26)):
        assert client.post(url, data={ix.CHECKOUT_FIELD: context}).status_code == 404, url
    assert wx.webhook_record() == before


def test_a_forged_or_foreign_checkout_context_delivers_nothing(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "success")
    other = _second_world(w)
    foreign = ix.create_intent(client, other)
    ix.simulate(client, other, foreign, "success")
    foreign_context = wx.delivery_context(client, other, foreign)
    before = wx.webhook_record()
    for context in ("", "forged", foreign_context):
        response = wx.sandbox_deliver(client, w, xp, context=context)
        assert ix.CHECKOUT_INVALID_TEXT in px.followed(client, response), context
    assert wx.webhook_record() == before


@pytest.mark.parametrize("outcome", [None, "cancellation"])
def test_nothing_is_delivered_before_a_decided_outcome(app, client, outcome):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    if outcome:
        ix.simulate(client, w, xp, outcome)
    before = wx.webhook_record()
    response = wx.sandbox_deliver(client, w, xp)
    assert wx.NOTHING_TO_DELIVER_TEXT in px.followed(client, response)
    assert wx.webhook_record() == before


@pytest.mark.parametrize("returned", [False, True])
def test_a_delivered_success_confirms_the_intent_with_no_human_actor(app, client, returned):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "success")
    if returned:
        ix.return_to_lms(client, w, xp)
        html = px.page(client, ix.checkout_url(w, xp))
        assert wx.deliver_url(w, xp) in html
        assert f'action="{ix.return_url(w, xp)}"' not in html
        assert 'name="outcome"' not in html
    response = wx.sandbox_deliver(client, w, xp)
    assert response.status_code == 302 and response.headers["Location"].endswith(
        ix.intent_url(w, xp))
    html = px.followed(client, response)
    (payment,) = wx.online_payments()
    receipt = Receipt.query.filter_by(payment_transaction_id=payment.id).one()
    assert wx.DELIVERED_CONFIRMED_TEXT in html and receipt.receipt_number in html
    assert (payment.recorded_by_id, receipt.issued_by_id) == (None, None)
    events = PaymentAuditEvent.query.filter(PaymentAuditEvent.kind.in_(
        ["payment_online_confirmed", "receipt_online_issued"])).all()
    assert len(events) == 2 and {e.actor_id for e in events} == {None}
    assert ix.stored_intent(xp).status == "confirmed"
    # Decided: there is no checkout, no delivery and no cancellation any more.
    assert client.get(ix.checkout_url(w, xp)).status_code == 302
    again = wx.sandbox_deliver(client, w, xp, context="anything")
    assert ix.NOT_PENDING_TEXT in px.followed(client, again)
    assert len(wx.online_payments()) == 1


def test_a_delivered_failure_closes_the_intent_and_records_nothing_financial(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "failure")
    before = wx.counts()
    response = wx.sandbox_deliver(client, w, xp)
    assert wx.DELIVERED_FAILED_TEXT in px.followed(client, response)
    assert ix.stored_intent(xp).status == "provider_failed"
    assert wx.counts() == tuple(n + d for n, d in zip(before, (0, 0, 0, 1)))


def test_a_conflicting_delivery_awaits_reconciliation_and_repeats_idempotently(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "success")
    owner, actor = ix.invoice_and_admin(w)
    px.payment(owner, actor, method="bank_transfer", status="pending", amount="10")
    before = wx.counts()
    response = wx.sandbox_deliver(client, w, xp)
    assert wx.DELIVERED_RECONCILIATION_TEXT in px.followed(client, response)
    assert wx.counts() == tuple(n + d for n, d in zip(before, (0, 0, 0, 1)))
    assert ix.stored_intent(xp).status == "pending"

    detail = px.page(client, ix.intent_url(w, xp))
    assert "Awaiting reconciliation." in detail
    assert "Reconciliation required -- no payment recorded" in detail
    assert "Awaiting reconciliation" in px.page(client, ix.intents_url(w))
    assert "Awaiting reconciliation" in px.page(client, ix.OVERVIEW_URL)
    history = px.page(client, px.payments_url(w))
    assert "needs\n    reconciliation. No payment was recorded from it." in history

    repeated = wx.sandbox_deliver(client, w, xp)
    assert "was already processed. Delivering it again changed nothing." in px.followed(
        client, repeated)
    assert wx.counts() == tuple(n + d for n, d in zip(before, (0, 0, 0, 1)))


def test_a_transient_failure_asks_for_another_delivery(app, client, monkeypatch):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "success")
    context = wx.delivery_context(client, w, xp)
    before = wx.webhook_record()

    def lost_lock():
        raise OperationalError("COMMIT", {}, Exception("Deadlock found when trying to get lock"))

    monkeypatch.setattr(webhooks.db.session, "commit", lost_lock)
    response = wx.sandbox_deliver(client, w, xp, context=context)
    monkeypatch.undo()
    html = px.followed(client, response)
    assert "could not be processed just now. Nothing was recorded; send it again." in html
    assert "Deadlock" not in html
    assert wx.webhook_record() == before
    retried = px.followed(client, wx.sandbox_deliver(client, w, xp))
    assert wx.DELIVERED_CONFIRMED_TEXT in retried and len(wx.online_payments()) == 1


# ===========================================================================
# The intent pages
# ===========================================================================


def test_the_detail_page_shows_each_state_plainly(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    html = px.page(client, ix.intent_url(w, xp))
    assert "No signed provider event has been received for this intent." in html
    assert ix.cancel_url(w, xp) in html and ix.checkout_url(w, xp) in html

    ix.simulate(client, w, xp, "success")
    ix.return_to_lms(client, w, xp)
    html = px.page(client, ix.intent_url(w, xp))
    assert "Browser-observed success (awaiting signed webhook)" in html
    assert "until the provider's signed\n    webhook is verified" in html
    assert ix.cancel_url(w, xp) not in html and ix.checkout_url(w, xp) in html

    wx.sandbox_deliver(client, w, xp)
    html = px.page(client, ix.intent_url(w, xp))
    receipt = Receipt.query.one()
    assert "Confirmed by signed webhook" in html
    assert "Confirmed by a verified signed provider webhook." in html
    assert px.receipt_url(w, receipt.public_id) in html and receipt.receipt_number in html
    assert "Payment succeeded" in html and "Payment confirmed" in html
    assert ix.cancel_url(w, xp) not in html and ix.checkout_url(w, xp) not in html


def test_the_provider_event_history_discloses_nothing_sensitive(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    reference = wx.reference_of(xp)
    wx.deliver(client, reference, amount="1.0000")
    wx.deliver(client, reference, event_type=wx.FAILED)
    events = wx.stored_events(xp)
    assert [e.outcome for e in events] == ["reconciliation_required", "failed"]
    for url in (ix.intent_url(w, xp), ix.intents_url(w), ix.OVERVIEW_URL):
        html = px.page(client, url)
        for event in events:
            for hidden in (event.provider_event_id, event.payload_digest):
                assert hidden not in html, (url, hidden)
        for hidden in (wx.SECRET, "evt_mock_", "v1=", "X-Mock-Webhook", "event_id",
                       "payload_digest"):
            assert hidden not in html, (url, hidden)
    html = px.page(client, ix.intent_url(w, xp))
    assert "Payment failed" in html and "Intent failed" in html
    assert format_amount(Decimal("1")) in html


def test_there_is_no_human_confirmation_of_an_online_payment(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "success")
    ix.return_to_lms(client, w, xp)
    html = px.page(client, ix.intent_url(w, xp))
    actions = re.findall(r'action="([^"]+)"', html)
    assert not [a for a in actions if "confirm" in a], actions
    assert "Confirm payment" not in html and "Confirm online payment" not in html
    for rule in app.url_map.iter_rules():
        if "payment-intents" in rule.rule:
            assert "confirm" not in rule.rule, rule.rule
        assert "online" not in rule.rule, rule.rule
    before = wx.webhook_record()
    for suffix in ("/confirm", "/webhook", "/checkout/confirm"):
        assert client.post(ix.intent_url(w, xp) + suffix,
                           data={ix.CHECKOUT_FIELD: "anything"}).status_code in (404, 405)
    assert wx.webhook_record() == before


# ===========================================================================
# No route can record or forge an online payment
# ===========================================================================


def test_forged_fields_on_the_manual_routes_never_make_an_online_payment(app, client):
    w = ix.login_world(app, client)
    xp = _direct_intent(w, "cancelled")
    forged = {"method": "online", "payment_intent_id": "1", "kind": "collection",
              "recorded_by_id": "", "status": "confirmed", "intent": xp}
    token = px.cash_token(client, w)
    response = client.post(px.cash_url(w), data=dict(forged, **{
        px.STATE_FIELD: token, "amount": "10", "confirm": "yes"}))
    assert px.CASH_OK_TEXT in px.followed(client, response)
    token = px.bank_token(client, w)
    client.post(px.bank_url(w), data=dict(forged, **{
        px.STATE_FIELD: token, "amount": "20", "reference": "TRX-0001",
        "transfer_date": px.TRANSFER_DATE_TEXT}))
    db.session.expire_all()
    rows = PaymentTransaction.query.order_by(PaymentTransaction.id).all()
    assert [r.method for r in rows] == ["cash", "bank_transfer"]
    assert {r.payment_intent_id for r in rows} == {None}
    assert {r.recorded_by_id for r in rows} == {w["admin_id"]}
    assert wx.online_payments() == [] and wx.stored_events() == []


def test_an_online_payment_is_neither_confirmed_nor_rejected_by_hand(app, client):
    w = ix.login_world(app, client)
    wx.confirmed_intent(client, w)
    (payment,) = wx.online_payments()
    before = px.payment_record()
    for url in (px.confirm_url(w, payment.public_id), px.reject_url(w, payment.public_id)):
        response = client.get(url)
        assert response.status_code == 302, url
        assert px.NOT_PENDING_TEXT in px.followed(client, response), url
        response = client.post(url, data={px.STATE_FIELD: "anything", "confirm": "yes",
                                          "reason": "Not received"})
        assert response.status_code == 302, url
    assert px.payment_record() == before


def test_an_online_payment_is_reversed_in_full_by_an_administrator(app, client):
    w = ix.login_world(app, client)
    wx.confirmed_intent(client, w)
    (payment,) = wx.online_payments()
    html = px.page(client, px.reverse_url(w, payment.public_id))
    assert wx.SYSTEM_ACTOR_TEXT in html and "Online" in html
    response = px.reverse(client, w, payment.public_id, reason="Provider chargeback")
    assert px.REVERSED_OK_TEXT in px.followed(client, response)
    timeline = px.page(client, fx.detail_url(w, w["ip"]))
    assert "The online payment of" in timeline and "was reversed in full." in timeline
    reversal = PaymentAuditEvent.query.filter_by(kind="payment_reversed").one()
    assert reversal.actor_id == w["admin_id"]
    # The intent it settled stays confirmed and the invoice is not offered again.
    assert ix.stored_intents(w)[0].status == "confirmed"


# ===========================================================================
# System-origin rows are shown wherever they are listed
# ===========================================================================


def test_system_origin_rows_appear_in_every_listing(app, client):
    w = ix.login_world(app, client)
    xp = wx.confirmed_intent(client, w)
    (payment,) = wx.online_payments()
    receipt = Receipt.query.filter_by(payment_transaction_id=payment.id).one()
    pages = {
        "overview": px.page(client, px.OVERVIEW_URL),
        "history": px.page(client, px.payments_url(w)),
        "timeline": px.page(client, fx.detail_url(w, w["ip"])),
        "receipt": px.page(client, px.receipt_url(w, receipt.public_id)),
    }
    for name, html in pages.items():
        assert wx.SYSTEM_ACTOR_TEXT in html, name
        assert "None" not in re.sub(r"<[^>]+>", " ", html).split(), name
    assert "Online collection" in pages["overview"]
    assert receipt.receipt_number in pages["overview"]
    assert "Online payment confirmed" in pages["timeline"]
    assert "confirmed by a verified signed provider webhook." in pages["timeline"]
    assert receipt.receipt_number in pages["receipt"]
    assert "Confirmed by signed webhook" in px.page(client, ix.OVERVIEW_URL)
    assert "Confirmed by signed webhook" in px.page(client, ix.intents_url(w))
    result = px.page(client, ix.result_url(w, xp))
    assert "signed" in result and "webhook" in result
    assert PaymentProviderEvent.query.count() == 1
