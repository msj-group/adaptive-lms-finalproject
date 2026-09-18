"""Phase 5 / M06 -- the Administrator Mock/Sandbox payment intent routes.

Authorization, the exact route inventory, POST-only and CSRF, nested 404s and
disclosure; the disabled provider mode; intent creation for the exact
outstanding balance, its blocks and its idempotent replay; the credential-free
sandbox checkout; the browser return, which records a provider result only from
``get_payment_status()`` and never from anything the browser claims; the
cancellation of a pending intent; the invoice freeze; the proof that no
interaction writes a payment, receipt, audit event or balance; the overview and
history bounds, escaping and cache headers.
"""

import base64
import json
import re
import time
import zlib
from decimal import Decimal

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import update

import app.blueprints.admin.invoices as invoice_routes
import app.blueprints.admin.payment_intents as routes
import tests.fee_assignment_fixtures as fees
import tests.invoice_fixtures as fx
import tests.payment_fixtures as px
import tests.payment_intent_fixtures as ix
from app.extensions import db
from app.models import (
    Enrollment,
    FeePlan,
    Group,
    Invoice,
    PaymentAuditEvent,
    PaymentIntent,
    PaymentTransaction,
    Receipt,
    StudentFeeAssignment,
    User,
    UserRole,
    UserStatus,
)
from app.services import payment_intent_tokens as tokens
from app.services.mock_payment_provider import mock_reference
from app.services.payment_providers import PaymentProviderError, ProviderPayment


@pytest.fixture
def app():
    """The testing application with the Mock/Sandbox provider enabled."""
    application = ix.make_app()
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def disabled_app():
    """The testing application with the provider mode left at its default."""
    application = ix.make_app(mode="disabled")
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def csrf_app():
    """The mock-enabled testing application with CSRF protection **enabled**."""
    application = ix.make_app(WTF_CSRF_ENABLED=True)
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def _flat(html):
    return " ".join(html.split())


def _record_statements(client, url):
    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    sa_event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        html = ix.page(client, url)
    finally:
        sa_event.remove(db.engine, "before_cursor_execute", _rec)
    return html, statements


def _select_count(client, url):
    _, statements = _record_statements(client, url)
    return len([s for s in statements if s.upper().startswith("SELECT")])


def _direct(app, w, build):
    with app.app_context():
        owner, actor = ix.invoice_and_admin(w)
        return build(owner, actor)


def _pending(app, w, **kwargs):
    """A pending intent written directly and known to the sandbox; its public id."""
    return _direct(app, w, lambda owner, actor: ix.registered_intent(app, owner, actor,
                                                                     **kwargs).public_id)


def _row(app, w, status):
    return _direct(app, w, lambda owner, actor: ix.intent(owner, actor, status=status).public_id)


def _second_world(app, w, name="Student Two"):
    """Another Enrollment's issued invoice, under the same Administrator."""
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        plan = db.session.get(FeePlan, w["plan_id"])
        enrolled = fees.enrollment(enrolled=fees.student(name=name))
        assignment = fees.assignment(enrolled, plan, actor)
        owner = px.issued_invoice(assignment, actor)
        group = db.session.get(Group, enrolled.group_id)
        return dict(w, gp=group.public_id, ep=enrolled.public_id, ap=assignment.public_id,
                    ip=owner.public_id, invoice_id=owner.id, assignment_id=assignment.id,
                    enrollment_id=enrolled.id, group_id=group.id)


def _ledger_status(app, xp):
    with app.app_context():
        reference = ix.stored_intent(xp).provider_reference
    return ix.provider_of(app).get_payment_status(reference).status


def _payload(token):
    """What anyone holding `token` can read: it is signed, not encrypted
    (itsdangerous marks a zlib-compressed body with a leading dot)."""
    compressed = token.startswith(".")
    body = token.lstrip(".").split(".")[0]
    raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    return json.loads(zlib.decompress(raw) if compressed else raw)


# ===========================================================================
# Authorization
# ===========================================================================


def _every_route(w, xp):
    return [
        ("get", ix.intents_url(w)), ("get", ix.new_url(w)), ("post", ix.new_url(w)),
        ("get", ix.intent_url(w, xp)), ("get", ix.checkout_url(w, xp)),
        ("post", ix.checkout_url(w, xp)), ("post", ix.return_url(w, xp)),
        ("get", ix.result_url(w, xp)), ("get", ix.cancel_url(w, xp)),
        ("post", ix.cancel_url(w, xp)), ("get", ix.OVERVIEW_URL),
    ]


def _call(client, method, url):
    if method == "post":
        return client.post(url, data={ix.STATE_FIELD: "anything", ix.CHECKOUT_FIELD: "anything",
                                      "outcome": "success", "confirm": "yes"})
    return client.get(url)


def test_an_administrator_opens_every_page(app, client):
    w = ix.login_world(app, client)
    assert client.get(ix.new_url(w)).status_code == 200
    xp = _pending(app, w)
    for url in (ix.intents_url(w), ix.intent_url(w, xp), ix.checkout_url(w, xp),
                ix.result_url(w, xp), ix.cancel_url(w, xp), ix.OVERVIEW_URL):
        assert client.get(url).status_code == 200, url


@pytest.mark.parametrize(
    "role", [UserRole.STUDENT.value, UserRole.TEACHER.value, UserRole.RESEARCHER.value]
)
def test_every_other_role_is_forbidden_everywhere(app, client, role):
    w = ix.world(app)
    xp = _pending(app, w)
    before = ix.record(app)
    with app.app_context():
        ix.user(f"other-{role}@example.com", role)
    ix.login_as(client, f"other-{role}@example.com")
    for method, url in _every_route(w, xp):
        assert _call(client, method, url).status_code == 403, (method, url)
    assert ix.record(app) == before
    assert _ledger_status(app, xp) == "pending"


def test_anonymous_and_suspended_administrators_reach_nothing(app, client):
    w = ix.world(app)
    xp = _pending(app, w)
    before = ix.record(app)
    for method, url in _every_route(w, xp):
        response = _call(client, method, url)
        assert response.status_code == 302 and "/auth/login" in response.headers["Location"], url
    ix.login_as(client, "admin@example.com")
    with app.app_context():
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
        db.session.commit()
    ix.fresh_identity()
    for method, url in _every_route(w, xp):
        response = _call(client, method, url)
        assert response.status_code == 302 and "/auth/login" in response.headers["Location"], url
    assert ix.record(app) == before
    assert _ledger_status(app, xp) == "pending"


# ===========================================================================
# The route inventory, POST-only and CSRF
# ===========================================================================


def test_the_route_inventory_is_exact_and_mutations_are_post_only(app, client):
    w = ix.login_world(app, client)
    xp = _pending(app, w)
    invoice = ("/admin/groups/<group_public_id>/enrollments/<enrollment_public_id>"
               "/fee-assignments/<assignment_public_id>/invoices/<invoice_public_id>")
    one = invoice + "/payment-intents/<intent_public_id>"
    both = frozenset({"GET", "POST"})
    rules = {
        (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
        if "intent" in rule.rule or "checkout" in rule.rule or "sandbox" in rule.rule
    }
    assert rules == {
        ("/admin/payment-intents", frozenset({"GET"})),
        (invoice + "/payment-intents", frozenset({"GET"})),
        (invoice + "/payment-intents/new", both),
        (one, frozenset({"GET"})),
        (one + "/checkout", both),
        (one + "/return", frozenset({"POST"})),
        (one + "/result", frozenset({"GET"})),
        (one + "/cancel", both),
    }
    for rule in app.url_map.iter_rules():
        text = f"{rule.rule} {rule.endpoint}".lower()
        for fragment in ("webhook", "refund", "customer", "credential"):
            assert fragment not in text, (rule.rule, fragment)
        if not rule.rule.startswith("/admin"):
            for fragment in ("intent", "checkout", "sandbox", "provider", "payment"):
                assert fragment not in text, (rule.rule, fragment)
    before = ix.record(app)
    assert client.get(ix.return_url(w, xp)).status_code == 405
    for url in (ix.intents_url(w), ix.intent_url(w, xp), ix.result_url(w, xp), ix.OVERVIEW_URL):
        assert client.post(url).status_code == 405, url
    for _method, url in _every_route(w, xp):
        assert client.delete(url).status_code == 405, url
        assert client.put(url).status_code == 405, url
        assert client.patch(url).status_code == 405, url
    assert ix.record(app) == before
    assert _ledger_status(app, xp) == "pending"


def _csrf(html):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)


def test_csrf_is_enforced_on_every_intent_mutation(csrf_app):
    client = csrf_app.test_client()
    w = ix.world(csrf_app)
    login_page = client.get("/auth/login").get_data(as_text=True)
    client.post("/auth/login", data={"email": "admin@example.com", "password": ix.PW,
                                     "csrf_token": _csrf(login_page)})

    def attempt(url, page_url, data, field=ix.STATE_FIELD, action=None):
        html = client.get(page_url).get_data(as_text=True)
        token = (ix.checkout_context_in(html, action or url) if field == ix.CHECKOUT_FIELD
                 else ix.state_in(html, url))
        assert token, url
        def ledger():
            return {k: dict(vars(v)) for k, v in ix.provider_of(csrf_app)._ledger.items()}

        before, sandbox = ix.record(csrf_app), ledger()
        assert client.post(url, data=dict(data, **{field: token})).status_code == 400, url
        assert ix.record(csrf_app) == before and ledger() == sandbox
        response = client.post(url, data=dict(data, csrf_token=_csrf(html), **{field: token}))
        assert response.status_code == 302, url
        return response

    xp = ix.xp_from(attempt(ix.new_url(w), ix.new_url(w), {"confirm": "yes"}))
    attempt(ix.checkout_url(w, xp), ix.checkout_url(w, xp), {"outcome": "failure"},
            field=ix.CHECKOUT_FIELD)
    attempt(ix.return_url(w, xp), ix.checkout_url(w, xp), {}, field=ix.CHECKOUT_FIELD)
    with csrf_app.app_context():
        assert ix.stored_intent(xp).status == "provider_failed"
    second = ix.xp_from(attempt(ix.new_url(w), ix.new_url(w), {"confirm": "yes"}))
    attempt(ix.cancel_url(w, second), ix.cancel_url(w, second), {"confirm": "yes"})
    with csrf_app.app_context():
        assert ix.stored_intent(second).status == "cancelled"


# ===========================================================================
# The disabled provider mode
# ===========================================================================


def test_disabled_mode_hides_every_sandbox_route_and_keeps_history_read_only(disabled_app):
    client = disabled_app.test_client()
    w = ix.login_world(disabled_app, client)
    xp = _row(disabled_app, w, "pending")
    before = ix.record(disabled_app)
    for method, url in (("get", ix.new_url(w)), ("post", ix.new_url(w)),
                        ("get", ix.checkout_url(w, xp)), ("post", ix.checkout_url(w, xp)),
                        ("post", ix.return_url(w, xp)), ("get", ix.cancel_url(w, xp)),
                        ("post", ix.cancel_url(w, xp))):
        assert _call(client, method, url).status_code == 404, (method, url)
    for url in (ix.intents_url(w), ix.intent_url(w, xp), ix.OVERVIEW_URL):
        response = client.get(url)
        html = response.get_data(as_text=True)
        assert response.status_code == 200 and ix.DISABLED_BANNER_TEXT in html, url
        for hidden in (ix.new_url(w), ix.checkout_url(w, xp), ix.cancel_url(w, xp)):
            assert hidden not in html, (url, hidden)
    assert client.get(ix.result_url(w, xp)).status_code == 200
    assert ix.record(disabled_app) == before
    assert disabled_app.extensions["payment_provider"].provider is None
    # A pending intent recorded earlier still freezes the invoice.
    assert ix.INTENT_FROZEN_TEXT in ix.followed(client, client.get(fx.edit_url(w, w["ip"])))


# ===========================================================================
# Nesting, identifiers and disclosure
# ===========================================================================


def test_anything_that_does_not_nest_is_a_plain_404(app, client):
    w = ix.login_world(app, client)
    xp = _pending(app, w)
    other = _second_world(app, w)
    foreign = _pending(app, other)
    with app.app_context():
        numeric = str(ix.stored_intent(xp).id)
    before = ix.record(app)
    wrong = [
        ix.intent_url(w, foreign), ix.checkout_url(w, foreign), ix.result_url(w, foreign),
        ix.cancel_url(w, foreign), ix.intent_url(w, numeric), ix.intent_url(w, "x" * 37),
        ix.intent_url(w, "00000000-0000-0000-0000-000000000000"),
        ix.intent_url(other, xp), ix.intents_url(dict(w, ip=other["ip"])),
        ix.intent_url(dict(w, ap=other["ap"]), xp), ix.intent_url(dict(w, ep=other["ep"]), xp),
        ix.intent_url(dict(w, gp=other["gp"]), xp), ix.intents_url(dict(w, ip=numeric)),
    ]
    for url in wrong:
        assert client.get(url).status_code == 404, url
    for url in (ix.return_url(w, foreign), ix.cancel_url(w, foreign), ix.checkout_url(w, foreign),
                ix.return_url(other, xp)):
        assert _call(client, "post", url).status_code == 404, url
    assert ix.record(app) == before
    assert _ledger_status(app, xp) == _ledger_status(app, foreign) == "pending"


def test_pages_disclose_no_internal_id_key_or_unrelated_payment(app, client):
    w = ix.login_world(app, client)
    other = _second_world(app, w, name="Unrelated Student")
    _pending(app, other)
    xp = ix.create_intent(client, w)
    with app.app_context():
        row = ix.stored_intent(xp)
        key, internal, other_number = row.idempotency_key, row.id, db.session.get(
            Invoice, other["invoice_id"]).invoice_number
    for url in (ix.intents_url(w), ix.intent_url(w, xp), ix.checkout_url(w, xp),
                ix.result_url(w, xp), ix.cancel_url(w, xp)):
        html = ix.page(client, url)
        assert key not in html and key[:16] not in html, url
        assert "Unrelated Student" not in html and other_number not in html, url
        assert not re.search(rf"/payment-intents/{internal}[\"?/]", html), url
    checkout = ix.page(client, ix.checkout_url(w, xp))
    assert "Student One" not in checkout


# ===========================================================================
# Creation
# ===========================================================================


def test_creation_records_one_pending_intent_for_the_exact_outstanding_balance(app, client):
    w = ix.login_world(app, client)
    before = ix.financial_record(app)
    response = ix.create(client, w)
    assert response.status_code == 302
    xp = ix.xp_from(response)
    html = ix.followed(client, response)
    assert ix.CREATED_OK_TEXT in html and "1,250.500 LYD" in html
    with app.app_context():
        (row,) = ix.stored_intents(w)
        assert row.public_id == xp
        assert (row.status, row.provider, row.currency_code, row.amount, row.version) == (
            "pending", "mock", "LYD", Decimal("1250.5000"), 1)
        assert re.fullmatch(r"[0-9a-f]{64}", row.idempotency_key)
        assert row.provider_reference == mock_reference(row.idempotency_key)
        assert row.created_by_id == w["admin_id"]
        assert row.created_at == row.updated_at and row.created_at.microsecond == 0
        assert (row.provider_result_at, row.provider_result_by_id, row.terminal_at,
                row.cancelled_by_id) == (None, None, None, None)
        reported = ix.provider_of(app).get_payment_status(row.provider_reference)
        assert (reported.status, reported.amount, reported.currency_code) == (
            "pending", row.amount, "LYD")
    assert ix.financial_record(app) == before
    assert len(ix.provider_of(app)._ledger) == 1


def test_the_amount_is_the_servers_snapshot_never_a_submitted_value(app, client):
    w = ix.login_world(app, client)
    token = ix.create_token(client, w)
    client.post(ix.new_url(w), data={ix.STATE_FIELD: token, "confirm": "yes", "amount": "1",
                                     "currency_code": "USD", "provider": "stripe",
                                     "idempotency_key": "f" * 64})
    with app.app_context():
        (row,) = ix.stored_intents(w)
        assert (row.amount, row.currency_code, row.provider) == (Decimal("1250.5000"), "LYD",
                                                                 "mock")
        assert row.idempotency_key != "f" * 64


def test_creation_needs_the_confirmation_box(app, client):
    w = ix.login_world(app, client)
    before = ix.record(app)
    response = ix.create(client, w, confirm=False)
    assert response.status_code == 200
    assert ix.CREATE_CONFIRM_TEXT in response.get_data(as_text=True)
    assert ix.record(app) == before and ix.provider_of(app)._ledger == {}


def test_a_replayed_creation_resolves_to_the_intent_it_created(app, client):
    w = ix.login_world(app, client)
    token = ix.create_token(client, w)
    first = ix.xp_from(ix.create(client, w, token=token))
    replay = ix.create(client, w, token=token)
    assert replay.status_code == 302 and replay.headers["Location"].endswith(first)
    assert ix.ALREADY_CREATED_TEXT in ix.followed(client, replay)
    assert ix.cancel(client, w, first).status_code == 302
    after_cancel = ix.create(client, w, token=token)
    assert after_cancel.headers["Location"].endswith(first)
    with app.app_context():
        assert [row.public_id for row in ix.stored_intents(w)] == [first]
    assert len(ix.provider_of(app)._ledger) == 1


def test_an_interrupted_creation_retried_resolves_to_one_intent(app, client, monkeypatch):
    """The provider accepted, then the local write failed. The same logical
    request retried reuses the same idempotency key, so the provider returns
    the same reference and exactly one intent is recorded."""
    w = ix.login_world(app, client)
    token = ix.create_token(client, w)
    real_commit = routes.db.session.commit
    calls = {"n": 0}

    def fail_once():
        calls["n"] += 1
        if calls["n"] == 1:
            from sqlalchemy.exc import IntegrityError
            raise IntegrityError("INSERT", {}, Exception("simulated interruption"))
        return real_commit()

    monkeypatch.setattr(routes.db.session, "commit", fail_once)
    failed = ix.create(client, w, token=token)
    assert ix.INTEGRITY_TEXT in ix.followed(client, failed)
    with app.app_context():
        assert ix.stored_intents(w) == []
    (reference,) = ix.provider_of(app)._ledger
    retried = ix.create(client, w, token=token)
    monkeypatch.undo()
    xp = ix.xp_from(retried)
    with app.app_context():
        (row,) = ix.stored_intents(w)
        assert row.public_id == xp and row.provider_reference == reference
    assert list(ix.provider_of(app)._ledger) == [reference]


def test_a_stale_creation_changes_nothing(app, client):
    w = ix.login_world(app, client)
    token = ix.create_token(client, w)
    _direct(app, w, lambda owner, actor: px.payment(owner, actor, method="bank_transfer",
                                                    status="rejected", amount="5"))
    before = ix.record(app)
    response = ix.create(client, w, token=token)
    assert ix.STALE_TEXT in ix.followed(client, response)
    assert ix.record(app) == before and ix.provider_of(app)._ledger == {}


def _block_world(app, kind):
    w = ix.world(app)
    with app.app_context():
        owner, actor = ix.invoice_and_admin(w)
        assignment = db.session.get(StudentFeeAssignment, w["assignment_id"])
        if kind == "draft":
            owner = fx.invoice(assignment, actor)
            w = dict(w, ip=owner.public_id, invoice_id=owner.id)
        elif kind == "cancelled_invoice":
            owner = fx.invoice(assignment, actor, status=fx.CANCELLED, number="INV-2026-000900")
            w = dict(w, ip=owner.public_id, invoice_id=owner.id)
        elif kind == "pending_transfer":
            px.payment(owner, actor, method="bank_transfer", status="pending", amount="10")
        elif kind == "confirmed_cash":
            px.cash_with_receipt(owner, actor)
        elif kind == "reversed_cash":
            px.reversed_collection(owner, actor)
        elif kind == "balance_broken":
            px.payment(owner, actor, amount="2000.000")
        elif kind == "pending_intent":
            ix.intent(owner, actor)
        elif kind == "succeeded_intent":
            ix.intent(owner, actor, status="provider_succeeded")
        elif kind == "limit":
            for index in range(25):
                ix.intent(owner, actor, status="provider_failed" if index % 2 else "cancelled")
        elif kind == "above_maximum":
            owner = px.issued_invoice(
                fees.assignment(fees.enrollment(), db.session.get(FeePlan, w["plan_id"]), actor),
                actor, lines=(("course", "Course", "60000.000"), ("course", "Books", "40000.000")))
            w = _nest(w, owner)
        elif kind == "invalid_lines":
            owner = px.issued_invoice(
                fees.assignment(fees.enrollment(), db.session.get(FeePlan, w["plan_id"]), actor),
                actor, lines=(("course", "Books", "10.000"), ("course", "books", "20.000")))
            w = _nest(w, owner)
    return w


def _nest(w, owner):
    assignment = db.session.get(StudentFeeAssignment, owner.student_fee_assignment_id)
    enrolled = db.session.get(Enrollment, assignment.enrollment_id)
    group = db.session.get(Group, enrolled.group_id)
    return dict(w, gp=group.public_id, ep=enrolled.public_id, ap=assignment.public_id,
                ip=owner.public_id, invoice_id=owner.id)


@pytest.mark.parametrize("kind, text", [
    ("draft", ix.NOT_ISSUED_TEXT),
    ("cancelled_invoice", ix.NOT_ISSUED_TEXT),
    ("pending_transfer", ix.MANUAL_PAYMENT_TEXT),
    ("confirmed_cash", ix.MANUAL_PAYMENT_TEXT),
    ("reversed_cash", ix.MANUAL_PAYMENT_TEXT),
    ("balance_broken", ix.BALANCE_BROKEN_TEXT),
    ("pending_intent", ix.ACTIVE_INTENT_TEXT),
    ("succeeded_intent", ix.ACTIVE_INTENT_TEXT),
    ("limit", ix.LIMIT_TEXT),
    ("above_maximum", ix.ABOVE_MAXIMUM_TEXT),
    ("invalid_lines", ix.ITEMS_INVALID_TEXT),
])
def test_every_block_is_shown_and_refused_without_writing(app, client, kind, text):
    w = _block_world(app, kind)
    ix.login_as(client, "admin@example.com")
    before = ix.record(app)
    history = ix.page(client, ix.intents_url(w))
    assert text in history and ix.new_url(w) not in history
    response = client.get(ix.new_url(w))
    assert response.status_code == 302 and text in ix.followed(client, response)
    response = ix.create(client, w, token="forged")
    assert response.status_code == 302
    assert ix.record(app) == before and ix.provider_of(app)._ledger == {}


@pytest.mark.parametrize("kind", ["rejected_transfer", "failed_intent", "cancelled_intent"])
def test_a_rejected_transfer_or_a_terminal_intent_does_not_block_creation(app, client, kind):
    w = ix.login_world(app, client)

    def build(owner, actor):
        if kind == "rejected_transfer":
            px.payment(owner, actor, method="bank_transfer", status="rejected", amount="10")
        else:
            ix.intent(owner, actor, status="provider_failed" if kind == "failed_intent"
                      else "cancelled")

    _direct(app, w, build)
    xp = ix.create_intent(client, w)
    with app.app_context():
        assert ix.stored_intent(xp).status == "pending"


@pytest.mark.parametrize("amount", ["0.001", "99999.999"])
def test_the_money_bounds_are_inclusive(app, client, amount):
    w = ix.world(app)
    with app.app_context():
        owner, actor = ix.invoice_and_admin(w)
        other = px.issued_invoice(
            fees.assignment(fees.enrollment(), db.session.get(FeePlan, w["plan_id"]), actor),
            actor, lines=(("course", "Course", amount),))
        w = _nest(w, other)
    ix.login_as(client, "admin@example.com")
    xp = ix.create_intent(client, w)
    with app.app_context():
        assert ix.stored_intent(xp).amount == Decimal(amount)


# ===========================================================================
# The Mock/Sandbox checkout
# ===========================================================================


def test_the_checkout_is_labelled_and_collects_no_credential(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    html = ix.page(client, ix.checkout_url(w, xp))
    assert "Mock/Sandbox checkout" in html and ix.SANDBOX_LABEL_TEXT in html
    inputs = re.findall(r"<input\b[^>]*>", html)
    assert inputs and all('type="hidden"' in tag for tag in inputs), inputs
    assert {re.search(r'name="([^"]+)"', tag).group(1) for tag in inputs} == {
        "csrf_token", ix.CHECKOUT_FIELD}
    assert not re.search(r"<(select|textarea)\b", html)
    assert re.findall(r'<button[^>]*name="outcome"[^>]*value="([^"]+)"', html) == [
        "success", "failure", "cancellation"]
    lowered = html.lower()
    for forbidden in ("card number", "cvv", "cvc", "expiry", "iban", "account number",
                      "password", "autocomplete=\"cc", "type=\"file\"", "type=\"password\""):
        assert forbidden not in lowered, forbidden
    assert not re.search(r"\bpin\b", lowered)


def test_the_checkout_context_carries_only_its_bound_fields(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    context = ix.checkout_context(client, w, xp)
    payload = _payload(context)
    assert set(payload) == {"purpose", "intent_public_id", "intent_version", "intent_status",
                            "provider_reference", "expires_at"}
    with app.app_context():
        row = ix.stored_intent(xp)
        expected = {"purpose": "payment-intent-checkout", "intent_public_id": xp,
                    "intent_version": 1, "intent_status": "pending",
                    "provider_reference": row.provider_reference}
        assert {k: v for k, v in payload.items() if k != "expires_at"} == expected
        text = json.dumps(payload)
        for value in (row.idempotency_key, "1250", "Student", "LYD", "INV-"):
            assert value not in text, value
    assert isinstance(payload["expires_at"], int)
    assert 0 < payload["expires_at"] - time.time() <= tokens.CHECKOUT_CONTEXT_MAX_AGE_SECONDS


@pytest.mark.parametrize("status", ["provider_succeeded", "provider_failed", "cancelled"])
def test_a_decided_intent_has_no_checkout(app, client, status):
    w = ix.login_world(app, client)
    xp = _row(app, w, status)
    response = client.get(ix.checkout_url(w, xp))
    assert response.status_code == 302 and ix.NOT_PENDING_TEXT in ix.followed(client, response)
    assert "Open sandbox checkout" not in ix.page(client, ix.intent_url(w, xp))


@pytest.mark.parametrize("outcome, status", [("success", "succeeded"), ("failure", "failed"),
                                             ("cancellation", "cancelled")])
def test_a_simulated_outcome_changes_only_the_sandbox(app, client, outcome, status):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    before = ix.record(app)
    response = ix.simulate(client, w, xp, outcome)
    assert response.status_code == 302 and response.headers["Location"].endswith("/checkout")
    assert ix.SIMULATED_TEXT in ix.followed(client, response)
    assert ix.record(app) == before
    assert _ledger_status(app, xp) == status


@pytest.mark.parametrize("outcome", ["paid", "succeeded", "", None, "SUCCESS"])
def test_an_unknown_outcome_simulates_nothing(app, client, outcome):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    context = ix.checkout_context(client, w, xp)
    data = {ix.CHECKOUT_FIELD: context}
    if outcome is not None:
        data["outcome"] = outcome
    response = client.post(ix.checkout_url(w, xp), data=data)
    assert ix.OUTCOME_INVALID_TEXT in ix.followed(client, response)
    assert _ledger_status(app, xp) == "pending"


def test_a_forged_or_foreign_context_simulates_nothing(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    other = _second_world(app, w)
    foreign = ix.create_intent(client, other)
    good = ix.checkout_context(client, w, xp)
    for context in ("", "forged", good[:-3] + "abc", ix.checkout_context(client, other, foreign),
                    ix.create_token(client, w)):
        response = ix.simulate(client, w, xp, "success", context=context)
        assert ix.CHECKOUT_INVALID_TEXT in ix.followed(client, response)
    assert _ledger_status(app, xp) == _ledger_status(app, foreign) == "pending"


def test_a_decided_sandbox_payment_refuses_another_outcome(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "failure")
    response = ix.simulate(client, w, xp, "success")
    assert ix.SIMULATION_REFUSED_TEXT in ix.followed(client, response)
    assert _ledger_status(app, xp) == "failed"


# ===========================================================================
# The browser return
# ===========================================================================


def test_a_reported_success_is_recorded_for_sandbox_testing_only(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    before_financial = ix.financial_record(app)
    with app.app_context():
        invoice_before = db.session.get(Invoice, w["invoice_id"]).version
    ix.simulate(client, w, xp, "success")
    response = ix.return_to_lms(client, w, xp)
    assert response.status_code == 302 and response.headers["Location"].endswith("/result")
    html = ix.followed(client, response)
    assert ix.NOTICE_RECORDED in html and ix.NOTICE_M07 in html
    assert "Provider reported success" in html
    # The invoice's balance is exactly what it was: nothing is paid.
    assert "1,250.500" in _flat(html) and "0.000" in _flat(html)
    with app.app_context():
        row = ix.stored_intent(xp)
        assert (row.status, row.version, row.provider_result_by_id, row.terminal_at) == (
            "provider_succeeded", 2, w["admin_id"], None)
        assert row.provider_result_at == row.updated_at and row.provider_result_at.microsecond == 0
        assert db.session.get(Invoice, w["invoice_id"]).version == invoice_before
    assert ix.financial_record(app) == before_financial
    detail = ix.page(client, ix.intent_url(w, xp))
    assert ix.NOTICE_RECORDED in detail and ix.NOTICE_M07 in detail
    assert "Cancel intent" not in detail and "Open sandbox checkout" not in detail
    # Still frozen, still one active intent, and it cannot be cancelled here.
    assert ix.INTENT_FROZEN_TEXT in ix.followed(client, client.get(fx.edit_url(w, w["ip"])))
    assert ix.ACTIVE_INTENT_TEXT in ix.page(client, ix.intents_url(w))
    response = client.get(ix.cancel_url(w, xp))
    assert ix.SUCCEEDED_NOT_CANCELLABLE_TEXT in ix.followed(client, response)


def test_a_reported_failure_is_terminal_and_releases_the_invoice(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    before_financial = ix.financial_record(app)
    ix.simulate(client, w, xp, "failure")
    html = ix.followed(client, ix.return_to_lms(client, w, xp))
    assert ix.NOTICE_RECORDED in html and ix.NOTICE_M07 in html
    with app.app_context():
        row = ix.stored_intent(xp)
        assert (row.status, row.version) == ("provider_failed", 2)
        assert row.terminal_at == row.provider_result_at == row.updated_at
    assert ix.financial_record(app) == before_financial
    detail = ix.page(client, fx.detail_url(w, w["ip"]))
    assert fx.edit_url(w, w["ip"]) in detail and fx.cancel_url(w, w["ip"]) in detail
    response = fx.add_line(client, w, w["ip"], reason="Late fee agreed")
    assert fx.LINE_ADDED_TEXT in ix.followed(client, response)
    assert ix.create_intent(client, w) != xp


def test_a_return_before_any_outcome_changes_nothing(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    before = ix.record(app)
    response = ix.return_to_lms(client, w, xp)
    html = ix.followed(client, response)
    assert ix.RESULT_PENDING_TEXT in html
    assert "No provider result is recorded for this payment intent." in html
    assert ix.NOTICE_RECORDED not in html
    assert ix.record(app) == before


def test_a_sandbox_cancellation_leaves_the_intent_pending_until_cancelled(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "cancellation")
    before = ix.record(app)
    assert ix.RESULT_CANCELLED_TEXT in ix.followed(client, ix.return_to_lms(client, w, xp))
    assert ix.record(app) == before
    assert ix.CANCELLED_OK_TEXT in ix.followed(client, ix.cancel(client, w, xp))
    with app.app_context():
        assert ix.stored_intent(xp).status == "cancelled"


def test_browser_claims_never_record_a_result(app, client):
    """A direct visit, a GET, forged query parameters and forged form fields
    claiming success change nothing: only the provider's report counts."""
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    context = ix.checkout_context(client, w, xp)
    before = ix.record(app)
    assert client.get(ix.return_url(w, xp) + "?status=succeeded&outcome=success").status_code == 405
    assert ix.RESULT_PENDING_TEXT not in ix.page(client, ix.result_url(w, xp))
    forged = client.post(
        ix.return_url(w, xp) + "?status=provider_succeeded&outcome=success&result=paid",
        data={ix.CHECKOUT_FIELD: context, "status": "provider_succeeded", "outcome": "success",
              "provider_status": "succeeded", "amount": "1250.500", "paid": "yes",
              "provider_reference": ix.stored_intent(xp).provider_reference})
    assert ix.RESULT_PENDING_TEXT in ix.followed(client, forged)
    for url in (ix.result_url(w, xp) + "?status=succeeded", ix.intent_url(w, xp) + "?paid=1"):
        client.get(url)
    assert ix.record(app) == before
    assert _ledger_status(app, xp) == "pending"


def test_forged_stale_foreign_and_expired_contexts_record_nothing(app, client, monkeypatch):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    other = _second_world(app, w)
    foreign = ix.create_intent(client, other)
    ix.simulate(client, w, xp, "success")
    ix.simulate(client, other, foreign, "success")
    good = ix.checkout_context(client, w, xp)
    foreign_context = ix.checkout_context(client, other, foreign)
    before = ix.record(app)
    for context in ("", "forged", good[:-4] + "abcd", foreign_context,
                    ix.cancel_token(client, w, xp),
                    tokens._serializer(tokens.PURPOSE_CHECKOUT).dumps(
                        dict(_payload(good), expires_at=int(time.time()) - 1))):
        response = ix.return_to_lms(client, w, xp, context=context)
        assert ix.CHECKOUT_INVALID_TEXT in ix.followed(client, response), context
    # A context signed longer ago than it may live: both its signed timestamp
    # and its signed expiry have passed.
    real_time = time.time
    monkeypatch.setattr(time, "time",
                        lambda: real_time() - tokens.CHECKOUT_CONTEXT_MAX_AGE_SECONDS - 5)
    with app.app_context():
        expired = tokens.make_checkout_context(ix.stored_intent(xp))
    monkeypatch.undo()
    assert tokens.load_token(expired, tokens.PURPOSE_CHECKOUT) is None
    response = ix.return_to_lms(client, w, xp, context=expired)
    assert ix.CHECKOUT_INVALID_TEXT in ix.followed(client, response)
    assert ix.record(app) == before
    # The genuine, current context of the right intent still works -- once.
    assert ix.NOTICE_RECORDED in ix.followed(client, ix.return_to_lms(client, w, xp, context=good))
    stale = ix.return_to_lms(client, w, xp, context=good)
    assert ix.CHECKOUT_INVALID_TEXT in ix.followed(client, stale)
    with app.app_context():
        assert ix.stored_intent(xp).version == 2
        assert ix.stored_intent(foreign).status == "pending"


@pytest.mark.parametrize("answer", [
    lambda ref: ProviderPayment(ref, "succeeded", Decimal("1.0000"), "LYD"),
    lambda ref: ProviderPayment(ref, "succeeded", Decimal("1250.5000"), "USD"),
    lambda ref: ProviderPayment(ref, "succeeded", None, None),
    lambda ref: ProviderPayment("mock_pi_" + "0" * 32, "succeeded", Decimal("1250.5000"), "LYD"),
    lambda ref: ProviderPayment(ref, "paid", Decimal("1250.5000"), "LYD"),
    None,
], ids=["amount", "currency", "no-amount", "reference", "status", "error"])
def test_a_provider_report_that_does_not_correspond_records_nothing(app, client, monkeypatch,
                                                                     answer):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    context = ix.checkout_context(client, w, xp)
    provider = ix.provider_of(app)

    def report(reference):
        if answer is None:
            raise PaymentProviderError("sandbox unavailable")
        return answer(reference)

    monkeypatch.setattr(provider, "get_payment_status", report)
    before = ix.record(app)
    response = ix.return_to_lms(client, w, xp, context=context)
    assert ix.PROVIDER_REFUSED_TEXT in ix.followed(client, response)
    assert ix.record(app) == before


# ===========================================================================
# Cancellation
# ===========================================================================


def test_cancelling_a_pending_intent_asks_the_provider_and_releases_the_invoice(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    before_financial = ix.financial_record(app)
    response = ix.cancel(client, w, xp)
    assert ix.CANCELLED_OK_TEXT in ix.followed(client, response)
    with app.app_context():
        row = ix.stored_intent(xp)
        assert (row.status, row.version, row.cancelled_by_id) == ("cancelled", 2, w["admin_id"])
        assert row.terminal_at == row.updated_at and row.provider_result_at is None
    assert _ledger_status(app, xp) == "cancelled"
    assert ix.financial_record(app) == before_financial
    detail = ix.page(client, fx.detail_url(w, w["ip"]))
    assert fx.edit_url(w, w["ip"]) in detail and fx.cancel_url(w, w["ip"]) in detail
    again = ix.cancel(client, w, xp, token="anything")
    assert ix.NOT_PENDING_TEXT in ix.followed(client, again)
    assert ix.create_intent(client, w) != xp


def test_cancellation_needs_the_confirmation_box_and_a_current_token(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    before = ix.record(app)
    response = ix.cancel(client, w, xp, confirm=False)
    assert response.status_code == 200 and ix.CANCEL_CONFIRM_TEXT in response.get_data(
        as_text=True)
    token = ix.cancel_token(client, w, xp)
    with app.app_context():
        fx.admin("second@example.com")
    ix.login_as(client, "second@example.com")
    assert ix.STALE_TEXT in ix.followed(client, ix.cancel(client, w, xp, token=token))
    assert ix.record(app) == before and _ledger_status(app, xp) == "pending"


def test_the_provider_refusing_a_cancellation_changes_nothing(app, client):
    """The sandbox already holds a success the LMS has not read yet: the
    provider refuses to cancel, and the intent stays pending."""
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "success")
    before = ix.record(app)
    assert ix.PROVIDER_REFUSED_TEXT in ix.followed(client, ix.cancel(client, w, xp))
    assert ix.record(app) == before and _ledger_status(app, xp) == "succeeded"


def test_a_cancellation_the_provider_does_not_confirm_changes_nothing(app, client, monkeypatch):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    token = ix.cancel_token(client, w, xp)
    monkeypatch.setattr(ix.provider_of(app), "cancel_payment",
                        lambda ref: ProviderPayment(ref, "pending"))
    before = ix.record(app)
    assert ix.PROVIDER_REFUSED_TEXT in ix.followed(client, ix.cancel(client, w, xp, token=token))
    assert ix.record(app) == before


@pytest.mark.parametrize("status, text", [
    ("provider_succeeded", ix.SUCCEEDED_NOT_CANCELLABLE_TEXT),
    ("provider_failed", ix.NOT_PENDING_TEXT),
    ("cancelled", ix.NOT_PENDING_TEXT),
])
def test_only_a_pending_intent_can_be_cancelled(app, client, status, text):
    w = ix.login_world(app, client)
    xp = _row(app, w, status)
    before = ix.record(app)
    assert text in ix.followed(client, client.get(ix.cancel_url(w, xp)))
    assert text in ix.followed(client, ix.cancel(client, w, xp, token="anything"))
    assert ix.record(app) == before


def test_a_manual_payment_keeps_the_invoice_frozen_after_the_intent_is_cancelled(app, client):
    """M05's rules are unchanged: a transfer may be recorded while an intent is
    active, and then it alone keeps the invoice frozen."""
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    px.pending_transfer(client, w, amount="100")
    assert ix.CANCELLED_OK_TEXT in ix.followed(client, ix.cancel(client, w, xp))
    assert px.FROZEN_TEXT in ix.followed(client, client.get(fx.edit_url(w, w["ip"])))


# ===========================================================================
# The invoice freeze
# ===========================================================================


def _line_routes(w, lp):
    return [fx.edit_url(w, w["ip"]), fx.line_new_url(w, w["ip"]),
            fx.line_edit_url(w, w["ip"], lp), fx.line_remove_url(w, w["ip"], lp),
            fx.cancel_url(w, w["ip"])]


@pytest.mark.parametrize("status", ["pending", "provider_succeeded"])
def test_an_active_intent_freezes_every_line_route_and_the_cancellation(app, client, status):
    w = ix.login_world(app, client)
    lp = fx.line_ids(app, w["ip"])[0]
    _row(app, w, status)
    before = ix.record(app)
    for url in _line_routes(w, lp):
        response = client.get(url)
        assert response.status_code == 302 and ix.INTENT_FROZEN_TEXT in ix.followed(
            client, response), url
        if url == fx.edit_url(w, w["ip"]):
            continue
        response = client.post(url, data={ix.STATE_FIELD: "x", "kind": "course", "label": "X",
                                          "amount": "1", "reason": "r", "confirm": "yes"})
        assert ix.INTENT_FROZEN_TEXT in ix.followed(client, response), url
    detail = ix.page(client, fx.detail_url(w, w["ip"]))
    assert "has an active online payment intent" in detail
    assert fx.edit_url(w, w["ip"]) not in detail and fx.cancel_url(w, w["ip"]) not in detail
    assert ix.intents_url(w) in detail
    assert ix.record(app) == before


def test_terminal_intents_alone_freeze_nothing(app, client):
    w = ix.login_world(app, client)
    _row(app, w, "provider_failed")
    _row(app, w, "cancelled")
    detail = ix.page(client, fx.detail_url(w, w["ip"]))
    assert fx.edit_url(w, w["ip"]) in detail and "active online payment intent" not in detail
    assert fx.LINE_ADDED_TEXT in ix.followed(
        client, fx.add_line(client, w, w["ip"], reason="Books added"))
    assert fx.CANCELLED_OK_TEXT in ix.followed(client, fx.cancel(client, w, w["ip"]))


def test_a_manual_payment_freeze_is_reported_first(app, client):
    w = ix.login_world(app, client)
    _direct(app, w, lambda owner, actor: px.payment(owner, actor, method="bank_transfer",
                                                    status="pending", amount="5"))
    _row(app, w, "pending")
    assert px.FROZEN_TEXT in ix.followed(client, client.get(fx.edit_url(w, w["ip"])))


@pytest.mark.parametrize("action", ["add_line", "cancel_invoice"])
def test_an_intent_created_at_the_invoice_lock_boundary_freezes_the_write(
    app, client, monkeypatch, action
):
    """The token was minted while the invoice was editable; an intent is
    created just before the invoice lock is taken. The freeze is re-proved
    after the lock, so nothing is written."""
    w = ix.login_world(app, client)
    if action == "add_line":
        token = fx.add_line_token(client, w, w["ip"])
    else:
        token = fx.cancel_token(client, w, w["ip"])
    real_lock = invoice_routes._lock

    def lock_after_an_intent(*args, **kwargs):
        owner, actor = ix.invoice_and_admin(w)
        ix.intent(owner, actor)
        return real_lock(*args, **kwargs)

    monkeypatch.setattr(invoice_routes, "_lock", lock_after_an_intent)
    before = fx.record(app)
    if action == "add_line":
        response = fx.add_line(client, w, w["ip"], token=token, reason="Late")
    else:
        response = fx.cancel(client, w, w["ip"], token=token)
    monkeypatch.undo()
    assert ix.INTENT_FROZEN_TEXT in ix.followed(client, response)
    assert fx.record(app) == before


# ===========================================================================
# No financial write, ever
# ===========================================================================


def test_no_intent_interaction_writes_a_payment_receipt_event_or_balance(app, client):
    w = ix.login_world(app, client)
    other = _second_world(app, w)
    with app.app_context():
        counts = (PaymentTransaction.query.count(), Receipt.query.count(),
                  PaymentAuditEvent.query.count())
        versions = [row.version for row in Invoice.query.order_by(Invoice.id)]
    before = ix.financial_record(app)
    first = ix.create_intent(client, w)
    ix.simulate(client, w, first, "failure")
    ix.return_to_lms(client, w, first)
    second = ix.create_intent(client, w)
    ix.cancel(client, w, second)
    third = ix.create_intent(client, w)
    ix.simulate(client, w, third, "success")
    ix.return_to_lms(client, w, third)
    fourth = ix.create_intent(client, other)
    ix.simulate(client, other, fourth, "cancellation")
    ix.return_to_lms(client, other, fourth)
    for url in (ix.intents_url(w), ix.intent_url(w, third), ix.result_url(w, third),
                ix.OVERVIEW_URL, px.payments_url(w)):
        client.get(url)
    assert ix.financial_record(app) == before
    with app.app_context():
        assert (PaymentTransaction.query.count(), Receipt.query.count(),
                PaymentAuditEvent.query.count()) == counts
        assert [row.version for row in Invoice.query.order_by(Invoice.id)] == versions
        assert [row.status for row in PaymentIntent.query.order_by(PaymentIntent.id)] == [
            "provider_failed", "cancelled", "provider_succeeded", "pending"]
    payments = _flat(ix.page(client, px.payments_url(w)))
    assert "No payment has been recorded against this invoice." in payments


# ===========================================================================
# History, overview, bounds, escaping and headers
# ===========================================================================


def _many_intents(app, w, count, status="cancelled"):
    def build(owner, actor):
        return [ix.intent(owner, actor, status=status).public_id for _ in range(count)]

    return _direct(app, w, build)


def test_the_history_is_paged_newest_first_without_count(app, client):
    w = ix.login_world(app, client)
    created = _many_intents(app, w, 21)
    first = ix.page(client, ix.intents_url(w))
    shown = re.findall(r"/payment-intents/([0-9a-f-]{36})\"", first)
    assert shown == list(reversed(created))[:20]
    assert f"{ix.intents_url(w)}?page=2" in first
    second = ix.page(client, ix.intents_url(w) + "?page=2")
    assert re.findall(r"/payment-intents/([0-9a-f-]{36})\"", second) == [created[0]]
    assert re.findall(r"/payment-intents/([0-9a-f-]{36})\"",
                      ix.page(client, ix.intents_url(w) + "?page=99")) == shown
    _, statements = _record_statements(client, ix.intents_url(w))
    assert not [s for s in statements if "COUNT(" in s.upper()]
    small = _second_world(app, w)
    _many_intents(app, small, 1)
    assert _select_count(client, ix.intents_url(small)) == _select_count(client, ix.intents_url(w))


def test_the_overview_is_filtered_paged_and_bounded(app, client):
    w = ix.login_world(app, client)
    other = _second_world(app, w)
    failed = _many_intents(app, w, 12, status="provider_failed")
    cancelled = _many_intents(app, other, 9, status="cancelled")
    pending = _pending(app, other)
    everything = failed + cancelled + [pending]
    first = ix.page(client, ix.OVERVIEW_URL)
    assert re.findall(r"/payment-intents/([0-9a-f-]{36})\"", first) == list(reversed(everything))[:20]
    assert "page=2" in first
    assert re.findall(r"/payment-intents/([0-9a-f-]{36})\"",
                      ix.page(client, ix.OVERVIEW_URL + "?page=2")) == list(
        reversed(everything))[20:]
    assert re.findall(r"/payment-intents/([0-9a-f-]{36})\"",
                      ix.page(client, ix.OVERVIEW_URL + "?status=provider_failed")) == list(
        reversed(failed))
    unknown = ix.page(client, ix.OVERVIEW_URL + "?status=paid'%20OR%201=1")
    assert re.findall(r"/payment-intents/([0-9a-f-]{36})\"", unknown) == list(
        reversed(everything))[:20]
    assert re.findall(r"/payment-intents/([0-9a-f-]{36})\"",
                      ix.page(client, ix.OVERVIEW_URL + "?page=99")) == list(
        reversed(everything))[:20]
    _, statements = _record_statements(client, ix.OVERVIEW_URL)
    assert not [s for s in statements if "COUNT(" in s.upper()]
    full = _select_count(client, ix.OVERVIEW_URL)
    assert _select_count(client, ix.OVERVIEW_URL + "?status=pending") == full


def test_text_is_escaped(app, client):
    w = ix.login_world(app, client)
    with app.app_context():
        db.session.execute(update(User).where(User.id == w["student_id"]).values(
            full_name="<script>alert(1)</script>"))
        db.session.commit()
    _pending(app, w)
    for url in (ix.OVERVIEW_URL, ix.intents_url(w)):
        html = ix.page(client, url)
        assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in ix.page(client, ix.OVERVIEW_URL)


def test_every_response_carries_the_private_cache_headers(app, client):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    context = ix.checkout_context(client, w, xp)
    responses = [client.get(url) for url in (
        ix.intents_url(w), ix.new_url(w), ix.intent_url(w, xp), ix.checkout_url(w, xp),
        ix.result_url(w, xp), ix.cancel_url(w, xp), ix.OVERVIEW_URL)]
    responses += [
        ix.simulate(client, w, xp, "success", context=context),
        ix.return_to_lms(client, w, xp, context=context),
        ix.create(client, w, token="forged"),
        ix.cancel(client, w, xp, token="forged"),
    ]
    for response in responses:
        assert response.headers["Cache-Control"] == "private, no-store", response
        assert "Cookie" in response.headers.get("Vary", ""), response


def test_rendered_tokens_carry_no_internal_id_name_or_amount(app, client):
    w = ix.login_world(app, client)
    create = _payload(ix.create_token(client, w))
    assert set(create) == {"purpose", "actor_public_id", "invoice_public_id", "invoice_version",
                           "payment_state", "intent_state", "provider_mode"}
    assert create["provider_mode"] == "mock"
    xp = ix.create_intent(client, w)
    cancel = _payload(ix.cancel_token(client, w, xp))
    assert set(cancel) == {"purpose", "actor_public_id", "invoice_public_id", "invoice_version",
                           "intent_public_id", "intent_version", "intent_status", "intent_state",
                           "provider_mode"}
    with app.app_context():
        row = ix.stored_intent(xp)
        forbidden = [row.idempotency_key, row.provider_reference, "1250", "Student One",
                     "admin@example.com", "INV-"]
    for payload in (create, cancel):
        text = json.dumps(payload)
        for value in forbidden:
            assert value not in text, value
