"""Phase 5 / M05 -- the Administrator manual payment and receipt routes.

Authorization, POST-only and CSRF, nested 404s; cash collections and their
receipts; pending bank transfers, their confirmation and rejection; partial
payments, the exact outstanding balance and overpayment; full reversals and
voided receipts; the invoice freeze; corrections after the context becomes
inactive; the invoice timeline; the overview's filters, ordering and bounds;
escaping, internal ids and cache headers.
"""

import re
from decimal import Decimal

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import text, update

import tests.fee_assignment_fixtures as fees
import tests.invoice_fixtures as fx
import tests.payment_fixtures as px
from app import create_app
from app.extensions import db
from app.models import (
    AcademicTerm,
    Enrollment,
    FeePlan,
    Group,
    InvoiceItem,
    ReceiptNumberSequence,
    StudentFeeAssignment,
    User,
    UserRole,
    UserStatus,
)
from app.services import invoice_tokens
from app.services import payment_tokens as tokens

_BELOW_MINIMUM_TEXT = "below the smallest amount a payment can record"


def _flat(html):
    return " ".join(html.split())


def _record_statements(client, url):
    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    sa_event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        html = px.page(client, url)
    finally:
        sa_event.remove(db.engine, "before_cursor_execute", _rec)
    return html, statements


def _select_count(client, url):
    _, statements = _record_statements(client, url)
    return len([s for s in statements if s.upper().startswith("SELECT")])


def _direct(app, w, build):
    """Run `build(invoice, actor)` in an app context; return its value."""
    with app.app_context():
        owner, actor = px.rows_of(app, w)
        return build(owner, actor)


def _ids(*rows):
    return tuple(row.public_id for row in rows)


def _state(w, version=2):
    return {"actor_public_id": w["admin_public_id"], "invoice_public_id": w["ip"],
            "invoice_version": version}


def _make_inactive(w, condition):
    if condition == "student_suspended":
        db.session.execute(update(User).where(User.id == w["student_id"]).values(
            status=UserStatus.SUSPENDED.value))
    elif condition == "enrollment_withdrawn":
        db.session.execute(update(Enrollment).where(Enrollment.id == w["enrollment_id"]).values(
            status=fees.WITHDRAWN))
    elif condition == "assignment_cancelled":
        db.session.execute(update(StudentFeeAssignment).where(
            StudentFeeAssignment.id == w["assignment_id"]).values(
            status=fees.CANCELLED, cancelled_at=fees.CANCELLED_AT, cancelled_by_id=w["admin_id"],
            updated_at=fees.CANCELLED_AT, version=2))
    elif condition == "plan_archived":
        db.session.execute(update(FeePlan).where(FeePlan.id == w["plan_id"]).values(
            status="archived", status_changed_at=fx.CREATED_AT, status_changed_by_id=w["admin_id"],
            updated_at=fx.CREATED_AT, version=3))
    elif condition == "group_archived":
        db.session.execute(update(Group).where(Group.id == w["group_id"]).values(
            status=fees.ARCHIVED))
    else:
        db.session.execute(update(AcademicTerm).where(AcademicTerm.id == w["term_id"]).values(
            status=fees.ARCHIVED))
    db.session.commit()


# ===========================================================================
# Authorization
# ===========================================================================


def _payment_world(app, client=None):
    w = px.login_world(app, client) if client is not None else px.world(app)

    def build(owner, actor):
        pay, issued = px.cash_with_receipt(owner, actor)
        pending = px.payment(owner, actor, method="bank_transfer", status="pending", amount="10")
        return pay.public_id, issued.public_id, pending.public_id

    pp, rp, pending = _direct(app, w, build)
    return w, pp, rp, pending


def _every_route(w, pp, rp, pending):
    return [
        ("get", px.payments_url(w)), ("get", px.cash_url(w)), ("post", px.cash_url(w)),
        ("get", px.bank_url(w)), ("post", px.bank_url(w)),
        ("get", px.confirm_url(w, pending)), ("post", px.confirm_url(w, pending)),
        ("get", px.reject_url(w, pending)), ("post", px.reject_url(w, pending)),
        ("get", px.reverse_url(w, pp)), ("post", px.reverse_url(w, pp)),
        ("get", px.receipt_url(w, rp)), ("get", px.OVERVIEW_URL),
    ]


def _call(client, method, url):
    if method == "post":
        return client.post(url, data={px.STATE_FIELD: "anything", "amount": "1",
                                      "reason": "x", "confirm": "yes"})
    return client.get(url)


def test_an_administrator_opens_every_page(app, client):
    w, pp, rp, pending = _payment_world(app, client)
    for method, url in _every_route(w, pp, rp, pending):
        if method == "get":
            assert client.get(url).status_code == 200, url


@pytest.mark.parametrize(
    "role", [UserRole.STUDENT.value, UserRole.TEACHER.value, UserRole.RESEARCHER.value]
)
def test_every_other_role_is_forbidden_everywhere(app, client, role):
    w, pp, rp, pending = _payment_world(app)
    before = px.record(app)
    with app.app_context():
        px.user(f"other-{role}@example.com", role)
    px.login_as(client, f"other-{role}@example.com")
    for method, url in _every_route(w, pp, rp, pending):
        assert _call(client, method, url).status_code == 403, (method, url)
    assert px.record(app) == before


def test_anonymous_and_suspended_administrators_reach_nothing(app, client):
    w, pp, rp, pending = _payment_world(app)
    before = px.record(app)
    for method, url in _every_route(w, pp, rp, pending):
        response = _call(client, method, url)
        assert response.status_code == 302 and "/auth/login" in response.headers["Location"], url
    px.login_as(client, "admin@example.com")
    with app.app_context():
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
        db.session.commit()
    px.fresh_identity()
    for method, url in _every_route(w, pp, rp, pending):
        response = _call(client, method, url)
        assert response.status_code == 302 and "/auth/login" in response.headers["Location"], url
    assert px.record(app) == before


# ===========================================================================
# POST-only, and CSRF
# ===========================================================================


def test_the_route_inventory_is_exact_and_mutations_are_post_only(app, client):
    w, pp, rp, pending = _payment_world(app, client)
    invoice = ("/admin/groups/<group_public_id>/enrollments/<enrollment_public_id>"
               "/fee-assignments/<assignment_public_id>/invoices/<invoice_public_id>")
    one = invoice + "/payments/<payment_public_id>"
    both = frozenset({"GET", "POST"})
    # Phase 5 / M06's payment intent routes are inventoried by
    # tests/test_admin_payment_intents.py, and Phase 5 / M10's Payments
    # workspace rules by tests/test_admin_financial_workspaces.py.
    rules = {
        (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
        if (("payment" in rule.rule and "payment-intents" not in rule.rule
             and not rule.rule.startswith("/webhooks"))
            or (rule.rule.startswith("/admin") and "receipt" in rule.rule))
        and not rule.endpoint.startswith("admin.payment_workspace")
    }
    assert rules == {
        ("/admin/payments", frozenset({"GET"})),
        (invoice + "/payments", frozenset({"GET"})),
        (invoice + "/payments/cash", both),
        (invoice + "/payments/bank-transfer", both),
        (one + "/confirm", both),
        (one + "/reject", both),
        (one + "/reverse", both),
        (invoice + "/receipts/<receipt_public_id>", frozenset({"GET"})),
    }
    for rule in app.url_map.iter_rules():
        # Phase 5 / M07's public signed webhook is the one exception;
        # tests/test_payment_webhooks.py inventories it.
        if not rule.rule.startswith("/admin") and rule.rule != "/webhooks/payments/mock":
            assert "payment" not in f"{rule.rule} {rule.endpoint}".lower(), rule.rule
    before = px.record(app)
    for url in (px.payments_url(w), px.receipt_url(w, rp), px.OVERVIEW_URL):
        assert client.post(url).status_code == 405, url
    for _method, url in _every_route(w, pp, rp, pending):
        assert client.delete(url).status_code == 405, url
        assert client.put(url).status_code == 405, url
        assert client.patch(url).status_code == 405, url
    assert px.record(app) == before


@pytest.fixture
def csrf_app():
    """The testing app with CSRF protection **enabled**."""
    csrf_enabled = create_app("testing", WTF_CSRF_ENABLED=True)
    with csrf_enabled.app_context():
        db.create_all()
        yield csrf_enabled
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def _csrf(html):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)


def test_csrf_is_enforced_on_every_payment_mutation(csrf_app):
    client = csrf_app.test_client()
    w = px.world(csrf_app)
    login_page = client.get("/auth/login").get_data(as_text=True)
    client.post("/auth/login", data={"email": "admin@example.com", "password": px.PW,
                                     "csrf_token": _csrf(login_page)})

    def attempt(url, data):
        html = client.get(url).get_data(as_text=True)
        token = px.state_in(html, url)
        assert token, url
        before = px.record(csrf_app)
        assert client.post(url, data=dict(data, state_token=token)).status_code == 400, url
        assert px.record(csrf_app) == before
        response = client.post(url, data=dict(data, state_token=token, csrf_token=_csrf(html)))
        assert response.status_code == 302, url
        return response

    attempt(px.cash_url(w), {"amount": "100", "confirm": "yes"})
    transfer = {"amount": "50", "reference": "TRX-1", "transfer_date": px.TRANSFER_DATE_TEXT}
    attempt(px.bank_url(w), transfer)
    attempt(px.bank_url(w), dict(transfer, reference="TRX-2"))
    with csrf_app.app_context():
        cash, first, second = px.stored_payments(w)
        ids = (cash.public_id, first.public_id, second.public_id)
    attempt(px.confirm_url(w, ids[1]), {"confirm": "yes"})
    attempt(px.reject_url(w, ids[2]), {"reason": "Not received", "confirm": "yes"})
    attempt(px.reverse_url(w, ids[0]), {"reason": "Wrong amount", "confirm": "yes"})
    with csrf_app.app_context():
        assert [(r.kind, r.status) for r in px.stored_payments(w)] == [
            ("collection", "confirmed"), ("collection", "confirmed"), ("collection", "rejected"),
            ("reversal", "confirmed")]


# ===========================================================================
# Nested identifiers
# ===========================================================================


def test_identifiers_that_do_not_nest_are_404_and_change_nothing(app, client):
    w = px.login_world(app, client)
    with app.app_context():
        owner, actor = px.rows_of(app, w)
        plan = db.session.get(FeePlan, w["plan_id"])
        pay, issued = px.cash_with_receipt(owner, actor)
        pending = px.payment(owner, actor, method="bank_transfer", status="pending", amount="5")
        assignment = db.session.get(StudentFeeAssignment, w["assignment_id"])
        cancelled = fx.invoice(assignment, actor, status=fx.CANCELLED, number="INV-2026-000777")

        sibling = fees.enrollment(db.session.get(Group, w["group_id"]))
        sibling_assignment = fees.assignment(sibling, plan, actor)
        sibling_invoice = px.issued_invoice(sibling_assignment, actor)
        s_pay, s_issued = px.cash_with_receipt(sibling_invoice, actor)
        s_pending = px.payment(sibling_invoice, actor, method="bank_transfer", status="pending",
                               amount="5")
        elsewhere = fees.enrollment()
        other_group = db.session.get(Group, elsewhere.group_id).public_id
        numeric = {"invoice": str(owner.id), "payment": str(pay.id), "pending": str(pending.id),
                   "receipt": str(issued.id), "group": str(w["group_id"])}
        own = (pay.public_id, issued.public_id, pending.public_id)
        strange = (s_pay.public_id, s_issued.public_id, s_pending.public_id)
        wrong_chains = [
            dict(w, gp=other_group), dict(w, ep=sibling.public_id),
            dict(w, ap=sibling_assignment.public_id), dict(w, gp=numeric["group"]),
            dict(w, ip=sibling_invoice.public_id), dict(w, ip=numeric["invoice"]),
            dict(w, ip=px.MISSING), dict(w, ip="x" * 300),
        ]
        # A cancelled invoice of the same assignment nests; the invoice's own
        # payments and receipts do not nest below it.
        cancelled_chain = dict(w, ip=cancelled.public_id)
    targets = []
    pp, rp, qp = own
    targets += [("get", px.confirm_url(cancelled_chain, qp)),
                ("post", px.confirm_url(cancelled_chain, qp)),
                ("get", px.reject_url(cancelled_chain, qp)),
                ("post", px.reject_url(cancelled_chain, qp)),
                ("get", px.reverse_url(cancelled_chain, pp)),
                ("post", px.reverse_url(cancelled_chain, pp)),
                ("get", px.receipt_url(cancelled_chain, rp))]
    for chain in wrong_chains:
        pp, rp, qp = own
        targets += [("get", px.payments_url(chain)), ("get", px.cash_url(chain)),
                    ("post", px.cash_url(chain)), ("get", px.bank_url(chain)),
                    ("post", px.bank_url(chain)), ("get", px.confirm_url(chain, qp)),
                    ("post", px.confirm_url(chain, qp)), ("post", px.reject_url(chain, qp)),
                    ("get", px.reverse_url(chain, pp)), ("post", px.reverse_url(chain, pp)),
                    ("get", px.receipt_url(chain, rp))]
    for pp, qp in ((strange[0], strange[2]), (numeric["payment"], numeric["pending"]),
                   (px.MISSING, px.MISSING), ("x" * 300, "x" * 300)):
        targets += [("get", px.confirm_url(w, qp)), ("post", px.confirm_url(w, qp)),
                    ("get", px.reject_url(w, qp)), ("post", px.reject_url(w, qp)),
                    ("get", px.reverse_url(w, pp)), ("post", px.reverse_url(w, pp))]
    for rp in (strange[1], numeric["receipt"], px.MISSING, "x" * 300):
        targets.append(("get", px.receipt_url(w, rp)))

    before = px.record(app)
    data = {px.STATE_FIELD: "anything", "amount": "1", "reason": "x", "confirm": "yes",
            "reference": "TRX", "transfer_date": px.TRANSFER_DATE_TEXT}
    for method, url in targets:
        response = client.get(url) if method == "get" else client.post(url, data=data)
        assert response.status_code == 404, (method, url)
    assert px.record(app) == before


# ===========================================================================
# Cash
# ===========================================================================


def test_a_cash_payment_is_confirmed_at_once_with_its_receipt_and_two_events(app, client):
    w = px.login_world(app, client)
    with app.app_context():
        owner = fx.stored_invoice(w["ip"])
        invoice_before = (owner.status, owner.version, owner.invoice_number, owner.updated_at)
        lines_before = fx.financial_record()[1]
    history = px.page(client, px.payments_url(w))
    assert f'href="{px.cash_url(w)}"' in history and f'href="{px.bank_url(w)}"' in history
    assert "No payments" in history

    response = px.record_cash(client, w, amount="250.25")
    assert response.status_code == 302
    rp = px.rp_from(response)
    html = px.followed(client, response)
    assert px.CASH_OK_TEXT in html and "250.250 LYD" in html
    with app.app_context():
        (row,) = px.stored_payments(w)
        assert (row.kind, row.method, row.status, row.amount, row.currency_code,
                row.bank_transfer_reference, row.bank_transfer_date, row.recorded_by_id,
                row.confirmed_by_id, row.version, row.reversal_of_payment_transaction_id) == (
            "collection", "cash", "confirmed", Decimal("250.2500"), "LYD", None, None,
            w["admin_id"], w["admin_id"], 1, None)
        assert row.recorded_at == row.confirmed_at == row.created_at == row.updated_at
        assert row.recorded_at.microsecond == 0
        year = fx.center_year(app, row.confirmed_at)
        (receipt,) = px.stored_receipts(w)
        assert (receipt.public_id, receipt.payment_transaction_id, receipt.status,
                receipt.version, receipt.issued_at, receipt.issued_by_id,
                receipt.receipt_number) == (
            rp, row.id, "issued", 1, row.confirmed_at, w["admin_id"], f"RCT-{year}-000001")
        assert (receipt.snapshot["amount"], receipt.snapshot["method"],
                receipt.snapshot["student_name"], receipt.snapshot["payment_public_id"],
                receipt.snapshot["invoice_number"]) == (
            "250.2500", "cash", "Student One", row.public_id, invoice_before[2])
        assert [(s.calendar_year, s.last_number) for s in ReceiptNumberSequence.query] == [(year, 1)]
        cash_event, receipt_event = px.stored_events(w)
        assert (cash_event.kind, cash_event.payment_transaction_id, cash_event.receipt_id,
                cash_event.reason, cash_event.invoice_version_before,
                cash_event.invoice_version_after, cash_event.occurred_at) == (
            "payment_cash_recorded", row.id, None, None, 2, 2, row.recorded_at)
        assert (cash_event.before_snapshot["paid_amount"],
                cash_event.after_snapshot["paid_amount"],
                cash_event.after_snapshot["outstanding_amount"]) == (
            "0.0000", "250.2500", "1000.2500")
        assert (receipt_event.kind, receipt_event.payment_transaction_id,
                receipt_event.receipt_id) == ("receipt_issued", row.id, receipt.id)
        assert receipt_event.before_snapshot == cash_event.after_snapshot
        assert receipt_event.after_snapshot["receipt"]["receipt_number"] == receipt.receipt_number
        owner = fx.stored_invoice(w["ip"])
        assert (owner.status, owner.version, owner.invoice_number, owner.updated_at) == invoice_before
        assert fx.financial_record()[1] == lines_before

    receipt_page = _flat(px.page(client, px.receipt_url(w, rp)))
    for fragment in (f"Receipt RCT-{year}-000001", "250.250 LYD", "Cash", "Student One",
                     invoice_before[2], "not a tax invoice"):
        assert fragment in receipt_page, fragment
    history = _flat(px.page(client, px.payments_url(w)))
    assert "<strong>1,000.250</strong>" in history and "250.250" in history
    assert px.receipt_url(w, rp) in history


def test_partial_payments_settle_the_exact_balance_and_never_overpay(app, client):
    w = px.login_world(app, client)
    assert px.CASH_OK_TEXT in px.followed(client, px.record_cash(client, w, amount="1000"))
    before = px.record(app)
    response = px.record_cash(client, w, amount="250.5001")
    assert response.status_code == 200
    assert px.OVERPAYMENT_TEXT in response.get_data(as_text=True)
    assert px.record(app) == before
    assert px.CASH_OK_TEXT in px.followed(client, px.record_cash(client, w, amount="250.5"))
    with app.app_context():
        assert [row.amount for row in px.stored_payments(w)] == [Decimal("1000.0000"),
                                                                 Decimal("250.5000")]
        assert [r.receipt_number[-6:] for r in px.stored_receipts(w)] == ["000001", "000002"]
    history = px.page(client, px.payments_url(w))
    assert px.SETTLED_TEXT in history
    assert px.cash_url(w) not in history and px.bank_url(w) not in history
    for url in (px.cash_url(w), px.bank_url(w)):
        response = client.get(url)
        assert response.status_code == 302 and px.SETTLED_TEXT in px.followed(client, response)
    with app.app_context():
        rows = px.stored_payments(w)
    token = tokens.make_token(tokens.PURPOSE_CASH_RECORD, payment_state=tokens.payment_state(rows),
                              **_state(w))
    before = px.record(app)
    response = px.record_cash(client, w, amount="1", token=token)
    assert px.SETTLED_TEXT in px.followed(client, response)
    assert px.record(app) == before


def test_cash_form_errors_are_shown_and_write_nothing(app, client):
    w = px.login_world(app, client)
    before = px.record(app)
    for amount, error in (("0", "greater than zero"), ("-5", "Do not use commas"),
                          ("1,0", "Do not use commas"), ("12.34567", "never rounded"),
                          ("abc", "Do not use commas"), ("", "Enter the amount"),
                          ("99999.9999", "at most")):
        response = px.record_cash(client, w, amount=amount)
        assert response.status_code == 200, amount
        assert error in response.get_data(as_text=True), amount
    response = px.record_cash(client, w, amount="10", confirm=False)
    assert response.status_code == 200 and px.CASH_CONFIRM_TEXT in response.get_data(as_text=True)
    assert px.record(app) == before


def test_an_outstanding_balance_below_the_minimum_payment_blocks_recording(app, client):
    w = px.login_world(app, client)
    with app.app_context():
        assignment = db.session.get(StudentFeeAssignment, w["assignment_id"])
        actor = db.session.get(User, w["admin_id"])
        owner = px.issued_invoice(assignment, actor, lines=(("course", "Course", "1.0005"),))
        px.cash_with_receipt(owner, actor, amount="1.000")
        other = dict(w, ip=owner.public_id)
    assert _BELOW_MINIMUM_TEXT in px.page(client, px.payments_url(other))
    response = client.get(px.cash_url(other))
    assert response.status_code == 302 and _BELOW_MINIMUM_TEXT in px.followed(client, response)


# ===========================================================================
# Bank transfers
# ===========================================================================


def test_a_bank_transfer_is_recorded_pending_without_receipt_or_balance_effect(app, client):
    w = px.login_world(app, client)
    response = px.record_bank(client, w, amount="200.5", reference="  TRX   0001 ")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(px.payments_url(w))
    html = _flat(px.followed(client, response))
    assert px.BANK_RECORDED_OK_TEXT in html and "<strong>1,250.500</strong>" in html
    assert "TRX 0001" in html and px.TRANSFER_DATE_TEXT in html
    with app.app_context():
        (row,) = px.stored_payments(w)
        assert (row.kind, row.method, row.status, row.amount, row.bank_transfer_reference,
                row.bank_transfer_date.isoformat(), row.confirmed_at, row.rejected_at,
                row.version) == ("collection", "bank_transfer", "pending", Decimal("200.5000"),
                                 "TRX 0001", px.TRANSFER_DATE_TEXT, None, None, 1)
        assert px.stored_receipts(w) == [] and ReceiptNumberSequence.query.count() == 0
        (entry,) = px.stored_events(w)
        assert (entry.kind, entry.payment_transaction_id, entry.receipt_id, entry.reason) == (
            "payment_bank_transfer_recorded", row.id, None, None)
        assert entry.after_snapshot["paid_amount"] == entry.before_snapshot["paid_amount"] == "0.0000"
        assert entry.after_snapshot["payment"]["status"] == "pending"
        assert "TRX" not in str(entry.after_snapshot) + str(entry.before_snapshot)


def test_bank_transfer_form_errors_are_shown_and_write_nothing(app, client):
    w = px.login_world(app, client)
    before = px.record(app)
    for fields, error in (
        ({"reference": "4111 1111 1111 1111"}, px.CARD_LIKE_TEXT),
        ({"reference": "   "}, px.REFERENCE_MISSING_TEXT),
        ({"reference": "x" * 65}, "at most 64 characters"),
        ({"transfer_date": "2999-01-01"}, px.FUTURE_DATE_TEXT),
        ({"transfer_date": "31/12/2025"}, "real date"),
        ({"transfer_date": "1999-12-31"}, "on or after 2000-01-01"),
        ({"transfer_date": ""}, "Enter the date of the transfer"),
        ({"amount": "0"}, "greater than zero"),
        ({"amount": "1250.5001"}, px.OVERPAYMENT_TEXT),
    ):
        response = px.record_bank(client, w, **fields)
        assert response.status_code == 200, fields
        assert error in response.get_data(as_text=True), fields
    assert px.record(app) == before


def test_pending_transfers_reserve_nothing_and_only_a_confirmation_that_fits_succeeds(app, client):
    w = px.login_world(app, client)
    assert px.record_cash(client, w, amount="1000").status_code == 302
    first = px.pending_transfer(client, w, amount="200")
    second = px.pending_transfer(client, w, amount="200")
    assert px.BANK_CONFIRMED_OK_TEXT in px.followed(client, px.confirm(client, w, first))

    response = client.get(px.confirm_url(w, second))
    assert response.status_code == 302 and px.OVERPAYMENT_TEXT in px.followed(client, response)
    with app.app_context():
        rows = px.stored_payments(w)
        state = tokens.payment_state(rows)
        target = px.stored_payment(second)
    token = tokens.make_token(tokens.PURPOSE_BANK_CONFIRM, actor_public_id=w["admin_public_id"],
                              invoice_public_id=w["ip"], payment_public_id=second,
                              payment_version=target.version, payment_state=state)
    before = px.record(app)
    response = px.confirm(client, w, second, token=token)
    assert px.OVERPAYMENT_TEXT in px.followed(client, response)
    assert px.record(app) == before
    assert px.BANK_REJECTED_OK_TEXT in px.followed(client, px.reject(client, w, second))
    with app.app_context():
        assert [(r.method, r.status, r.amount) for r in px.stored_payments(w)] == [
            ("cash", "confirmed", Decimal("1000.0000")),
            ("bank_transfer", "confirmed", Decimal("200.0000")),
            ("bank_transfer", "rejected", Decimal("200.0000"))]
    assert "<strong>50.500</strong>" in _flat(px.page(client, px.payments_url(w)))


def test_confirming_a_transfer_issues_its_receipt_once(app, client):
    w = px.login_world(app, client)
    pp = px.pending_transfer(client, w, amount="300")
    page = _flat(px.page(client, px.confirm_url(w, pp)))
    assert "300.000 LYD" in page and "TRX-0001" in page
    token = px.confirm_token(client, w, pp)
    before = px.record(app)
    response = px.confirm(client, w, pp, confirm=False, token=token)
    assert response.status_code == 200
    assert px.BANK_CONFIRM_TEXT in response.get_data(as_text=True)
    assert px.record(app) == before

    response = px.confirm(client, w, pp, token=token)
    rp = px.rp_from(response)
    assert px.BANK_CONFIRMED_OK_TEXT in px.followed(client, response)
    with app.app_context():
        row = px.stored_payment(pp)
        assert (row.status, row.version, row.confirmed_by_id, row.rejected_at) == (
            "confirmed", 2, w["admin_id"], None)
        assert row.confirmed_at == row.updated_at and row.confirmed_at >= row.recorded_at
        (receipt,) = px.stored_receipts(w)
        assert (receipt.public_id, receipt.payment_transaction_id, receipt.issued_at) == (
            rp, row.id, row.confirmed_at)
        assert receipt.snapshot["method"] == "bank_transfer"
        assert [e.kind for e in px.stored_events(w)] == [
            "payment_bank_transfer_recorded", "payment_bank_transfer_confirmed", "receipt_issued"]
        confirmed = px.stored_events(w)[1]
        assert (confirmed.before_snapshot["payment"]["status"],
                confirmed.after_snapshot["payment"]["status"],
                confirmed.after_snapshot["outstanding_amount"]) == ("pending", "confirmed",
                                                                     "950.5000")
    receipt_page = _flat(px.page(client, px.receipt_url(w, rp)))
    assert "Bank transfer" in receipt_page and "TRX-0001" in receipt_page

    before = px.record(app)
    response = px.confirm(client, w, pp, token=token)
    assert px.NOT_PENDING_TEXT in px.followed(client, response)
    response = client.get(px.confirm_url(w, pp))
    assert response.status_code == 302 and px.NOT_PENDING_TEXT in px.followed(client, response)
    for url in (px.reject_url(w, pp),):
        response = client.get(url)
        assert px.NOT_PENDING_TEXT in px.followed(client, response)
    assert px.record(app) == before


def test_rejecting_a_transfer_needs_a_reason_and_changes_no_balance(app, client):
    w = px.login_world(app, client)
    pp = px.pending_transfer(client, w, amount="300")
    token = px.reject_token(client, w, pp)
    before = px.record(app)
    for kwargs, error in (({"reason": None}, px.REJECT_REASON_TEXT),
                          ({"reason": "   "}, px.REJECT_REASON_TEXT),
                          ({"confirm": False}, px.REJECT_CONFIRM_TEXT)):
        response = px.reject(client, w, pp, token=token, **kwargs)
        assert response.status_code == 200, kwargs
        assert error in response.get_data(as_text=True), kwargs
    assert px.record(app) == before

    response = px.reject(client, w, pp, reason="  Not\r\nreceived ", token=token)
    assert px.BANK_REJECTED_OK_TEXT in px.followed(client, response)
    with app.app_context():
        row = px.stored_payment(pp)
        assert (row.status, row.version, row.rejected_by_id, row.rejection_reason,
                row.confirmed_at) == ("rejected", 2, w["admin_id"], "Not\nreceived", None)
        assert row.rejected_at == row.updated_at
        assert px.stored_receipts(w) == []
        entry = px.stored_events(w)[-1]
        assert (entry.kind, entry.reason, entry.payment_transaction_id) == (
            "payment_bank_transfer_rejected", "Not\nreceived", row.id)
        assert entry.after_snapshot["paid_amount"] == "0.0000"
    history = _flat(px.page(client, px.payments_url(w)))
    assert "Rejected" in history and "<strong>1,250.500</strong>" in history


# ===========================================================================
# Reversal
# ===========================================================================


def test_a_full_reversal_voids_the_receipt_and_reopens_the_amount(app, client):
    w = px.login_world(app, client)
    rp = px.rp_from(px.record_cash(client, w, amount="100"))
    with app.app_context():
        (original,) = px.stored_payments(w)
        pp = original.public_id
        original_state = px.payment_record()[0]
        receipt = px.stored_receipts(w)[0]
        number, document = receipt.receipt_number, receipt.snapshot
    page = _flat(px.page(client, px.reverse_url(w, pp)))
    assert number in page and "not a refund" in page and "100.000 LYD" in page
    token = px.reverse_token(client, w, pp)
    before = px.record(app)
    for kwargs, error in (({"reason": None}, px.REVERSE_REASON_TEXT),
                          ({"confirm": False}, px.REVERSE_CONFIRM_TEXT)):
        response = px.reverse(client, w, pp, token=token, **kwargs)
        assert response.status_code == 200 and error in response.get_data(as_text=True), kwargs
    assert px.record(app) == before

    response = px.reverse(client, w, pp, reason="Recorded twice", token=token)
    assert response.headers["Location"].endswith(px.payments_url(w))
    assert px.REVERSED_OK_TEXT in px.followed(client, response)
    with app.app_context():
        rows = px.stored_payments(w)
        assert px.payment_record()[0][0] == original_state[0]
        reversal = rows[1]
        assert (reversal.kind, reversal.method, reversal.status, reversal.amount,
                reversal.reversal_of_payment_transaction_id, reversal.bank_transfer_reference,
                reversal.version, reversal.recorded_by_id) == (
            "reversal", "cash", "confirmed", Decimal("100.0000"), rows[0].id, None, 1,
            w["admin_id"])
        assert reversal.recorded_at == reversal.confirmed_at == reversal.updated_at
        receipt = px.stored_receipts(w)[0]
        assert (receipt.status, receipt.version, receipt.void_reason, receipt.voided_by_id,
                receipt.receipt_number, receipt.snapshot, receipt.voided_at) == (
            "voided", 2, "Recorded twice", w["admin_id"], number, document, reversal.recorded_at)
        reversed_event, voided_event = px.stored_events(w)[-2:]
        assert (reversed_event.kind, reversed_event.reason, reversed_event.payment_transaction_id,
                reversed_event.receipt_id) == ("payment_reversed", "Recorded twice", reversal.id,
                                               None)
        assert (reversed_event.before_snapshot["paid_amount"],
                reversed_event.after_snapshot["paid_amount"],
                reversed_event.after_snapshot["payment"]["reversal_of_public_id"]) == (
            "100.0000", "0.0000", pp)
        assert (voided_event.kind, voided_event.reason, voided_event.payment_transaction_id,
                voided_event.receipt_id) == ("receipt_voided", "Recorded twice", rows[0].id,
                                             receipt.id)
        assert voided_event.after_snapshot["receipt"]["status"] == "voided"
    history = _flat(px.page(client, px.payments_url(w)))
    assert "Reversed" in history and "Recorded twice" in history
    assert "<strong>1,250.500</strong>" in history and px.reverse_url(w, pp) not in history
    receipt_page = _flat(px.page(client, px.receipt_url(w, rp)))
    assert "This receipt is void" in receipt_page and "Recorded twice" in receipt_page

    before = px.record(app)
    response = client.get(px.reverse_url(w, pp))
    assert response.status_code == 302 and px.ALREADY_REVERSED_TEXT in px.followed(client, response)
    response = px.reverse(client, w, pp, token=token)
    assert px.ALREADY_REVERSED_TEXT in px.followed(client, response)
    assert px.record(app) == before
    assert px.CASH_OK_TEXT in px.followed(client, px.record_cash(client, w, amount="100"))


def test_a_confirmed_transfer_is_reversed_keeping_its_method(app, client):
    w = px.login_world(app, client)
    pp = px.pending_transfer(client, w, amount="75")
    assert px.confirm(client, w, pp).status_code == 302
    assert px.REVERSED_OK_TEXT in px.followed(client, px.reverse(client, w, pp))
    with app.app_context():
        assert [(r.kind, r.method, r.status) for r in px.stored_payments(w)] == [
            ("collection", "bank_transfer", "confirmed"), ("reversal", "bank_transfer", "confirmed")]


def test_only_a_confirmed_collection_with_its_issued_receipt_can_be_reversed(app, client):
    w = px.login_world(app, client)

    def build(owner, actor):
        pending = px.payment(owner, actor, method="bank_transfer", status="pending", amount="5")
        rejected = px.payment(owner, actor, method="bank_transfer", status="rejected", amount="5")
        _original, _voided, reversal = px.reversed_collection(owner, actor, amount="5")
        bare = px.payment(owner, actor, amount="5")
        return _ids(pending, rejected, reversal, bare)

    pending, rejected, reversal, bare = _direct(app, w, build)
    before = px.record(app)
    for pp in (pending, rejected, reversal):
        for send in (lambda: client.get(px.reverse_url(w, pp)),
                     lambda: client.post(px.reverse_url(w, pp), data={px.STATE_FIELD: "x"})):
            response = send()
            assert response.status_code == 302
            assert px.NOT_REVERSIBLE_TEXT in px.followed(client, response), pp
    response = client.get(px.reverse_url(w, bare))
    assert px.RECEIPT_MISSING_TEXT in px.followed(client, response)
    assert px.record(app) == before


# ===========================================================================
# Eligibility
# ===========================================================================


@pytest.mark.parametrize("status", [fx.DRAFT, fx.CANCELLED])
def test_payments_are_recorded_only_against_an_issued_invoice(app, client, status):
    w = px.login_world(app, client)
    with app.app_context():
        owner = fx.invoice(db.session.get(StudentFeeAssignment, w["assignment_id"]),
                           db.session.get(User, w["admin_id"]), status=status)
        other = dict(w, ip=owner.public_id, invoice_id=owner.id)
    detail = px.page(client, fx.detail_url(w, other["ip"]))
    assert px.payments_url(other) not in detail and "Payments and receipts" not in detail
    history = px.page(client, px.payments_url(other))
    assert px.NOT_ISSUED_TEXT in history and px.cash_url(other) not in history
    for url in (px.cash_url(other), px.bank_url(other)):
        response = client.get(url)
        assert response.status_code == 302 and px.NOT_ISSUED_TEXT in px.followed(client, response)
    token = tokens.make_token(tokens.PURPOSE_CASH_RECORD, payment_state=[],
                              **dict(_state(other), invoice_version=owner_version(app, other)))
    before = px.record(app)
    assert px.NOT_ISSUED_TEXT in px.followed(client, px.record_cash(client, other, token=token))
    assert px.record(app) == before


def owner_version(app, w):
    with app.app_context():
        return fx.stored_invoice(w["ip"]).version


def test_an_invoice_whose_lines_are_not_a_valid_charge_takes_no_payment(app, client):
    w = px.login_world(app, client)
    with app.app_context():
        db.session.execute(update(InvoiceItem).where(InvoiceItem.invoice_id == w["invoice_id"])
                           .values(label="Same"))
        db.session.commit()
    assert px.ITEMS_INVALID_TEXT in px.page(client, px.payments_url(w))
    response = client.get(px.cash_url(w))
    assert px.ITEMS_INVALID_TEXT in px.followed(client, response)


def test_an_invoice_holds_at_most_twenty_five_collections(app, client):
    w = px.login_world(app, client)

    def build(owner, actor):
        cash, _receipt = px.cash_with_receipt(owner, actor, amount="1")
        for _ in range(24):
            px.payment(owner, actor, method="bank_transfer", status="rejected", amount="1")
        return cash.public_id

    cash = _direct(app, w, build)
    history = px.page(client, px.payments_url(w))
    assert px.LIMIT_TEXT in history and px.cash_url(w) not in history
    response = client.get(px.bank_url(w))
    assert px.LIMIT_TEXT in px.followed(client, response)
    # A correction is never blocked by the bound.
    assert px.REVERSED_OK_TEXT in px.followed(client, px.reverse(client, w, cash))


@pytest.mark.parametrize(
    "condition",
    ["student_suspended", "enrollment_withdrawn", "assignment_cancelled", "plan_archived",
     "group_archived", "term_archived"],
)
def test_payments_and_corrections_stay_available_when_the_context_becomes_inactive(
    app, client, condition
):
    w = px.login_world(app, client)
    pending = px.pending_transfer(client, w, amount="20")
    second = px.pending_transfer(client, w, amount="30")
    with app.app_context():
        _make_inactive(w, condition)
    assert px.CASH_OK_TEXT in px.followed(client, px.record_cash(client, w, amount="10"))
    assert px.BANK_CONFIRMED_OK_TEXT in px.followed(client, px.confirm(client, w, pending))
    assert px.BANK_REJECTED_OK_TEXT in px.followed(client, px.reject(client, w, second))
    assert px.REVERSED_OK_TEXT in px.followed(client, px.reverse(client, w, pending))
    with app.app_context():
        assert [e.kind for e in px.stored_events(w)] == [
            "payment_bank_transfer_recorded", "payment_bank_transfer_recorded",
            "payment_cash_recorded", "receipt_issued", "payment_bank_transfer_confirmed",
            "receipt_issued", "payment_bank_transfer_rejected", "payment_reversed",
            "receipt_voided"]


# ===========================================================================
# The invoice freeze
# ===========================================================================


@pytest.mark.parametrize("state", ["pending", "confirmed", "reversed"])
def test_a_pending_or_confirmed_payment_freezes_the_invoice(app, client, state):
    """Phase 5 / M10 (owners' decision): a pending or confirmed payment still
    freezes the cancellation, but no longer the lines -- they keep a floor of
    the live confirmed payments plus pending transfers."""
    w = px.login_world(app, client)

    def build(owner, actor):
        if state == "pending":
            px.payment(owner, actor, method="bank_transfer", status="pending",
                       amount="1100.000")
        elif state == "confirmed":
            px.cash_with_receipt(owner, actor, amount="1100.000")
        else:
            px.reversed_collection(owner, actor, amount="1100.000")
        return InvoiceItem.query.filter_by(invoice_id=owner.id, label="Course").one().public_id

    lp = _direct(app, w, build)
    ip = w["ip"]
    detail = _flat(px.page(client, fx.detail_url(w, ip)))
    assert "so it can no longer be cancelled" in detail
    assert fx.edit_url(w, ip) in detail and fx.cancel_url(w, ip) not in detail
    assert f'href="{px.payments_url(w)}"' in detail
    for url in (fx.edit_url(w, ip), fx.line_new_url(w, ip), fx.line_edit_url(w, ip, lp),
                fx.line_remove_url(w, ip, lp)):
        assert client.get(url).status_code == 200, url
    response = client.get(fx.cancel_url(w, ip))
    assert response.status_code == 302 and px.FROZEN_TEXT in px.followed(client, response)
    before = px.record(app)
    state_ = _state(w)
    response = client.post(fx.cancel_url(w, ip), data={
        fx.STATE_FIELD: invoice_tokens.make_token(invoice_tokens.PURPOSE_CANCEL, **state_),
        "reason": "x", "confirm": "yes"})
    assert response.status_code == 302 and px.FROZEN_TEXT in px.followed(client, response)
    assert px.record(app) == before
    # Removing the 1,200.500 course line would leave 50.000: below the floor
    # of a live pending or confirmed 1,100.000, above a reversed one's zero.
    response = client.post(fx.line_remove_url(w, ip, lp), data={
        fx.STATE_FIELD: invoice_tokens.make_token(invoice_tokens.PURPOSE_ITEM_REMOVE,
                                                  item_public_id=lp, **state_),
        "reason": "x"})
    assert response.status_code == 302
    if state == "reversed":
        assert "Line removed." in px.followed(client, response)
    else:
        assert "below the 1,100.000 LYD already paid or pending" in px.followed(client, response)
        assert px.record(app) == before
    # The fee assignment stays uncancellable while its invoice is issued.
    history = px.page(client, fees.history_url(w["gp"], w["ep"]))
    assert fees.cancel_url(w["gp"], w["ep"], w["ap"]) not in history


def test_a_rejected_transfer_alone_freezes_nothing(app, client):
    w = px.login_world(app, client)
    pp = px.pending_transfer(client, w, amount="20")
    assert px.reject(client, w, pp).status_code == 302
    ip = w["ip"]
    detail = px.page(client, fx.detail_url(w, ip))
    assert f'href="{fx.edit_url(w, ip)}"' in detail and f'href="{fx.cancel_url(w, ip)}"' in detail
    response = fx.add_line(client, w, ip, label="Books", amount="10", reason="Books were missing")
    assert fx.LINE_ADDED_TEXT in fx.followed(client, response)
    assert fx.CANCELLED_OK_TEXT in fx.followed(client, fx.cancel(client, w, ip))


def test_the_invoice_page_shows_the_balance_and_payment_events_on_its_timeline(app, client):
    w = px.login_world(app, client)
    assert px.record_cash(client, w, amount="100").status_code == 302
    with app.app_context():
        pp = px.stored_payments(w)[0].public_id
    assert px.reverse(client, w, pp, reason="Typed <b>twice</b>").status_code == 302
    detail = _flat(px.page(client, fx.detail_url(w, w["ip"])))
    for fragment in ("Cash payment recorded", "Receipt issued", "Payment reversed",
                     "Receipt voided", "(invoice version 2 unchanged)",
                     "Cash payment of 100.000 LYD recorded and confirmed.",
                     "outstanding <strong>1,250.500 LYD</strong>",
                     "Reason: Typed &lt;b&gt;twice&lt;/b&gt;"):
        assert fragment in detail, fragment
    assert "<b>twice</b>" not in detail


# ===========================================================================
# The overview
# ===========================================================================


def test_the_overview_is_newest_first_filtered_and_paginated_without_a_count(app, client):
    w = px.login_world(app, client)

    def build(owner, actor):
        for index in range(45):
            if index % 3 == 0:
                px.payment(owner, actor, method="bank_transfer", status="pending",
                           amount=f"{index + 1}")
            else:
                px.payment(owner, actor, amount=f"{index + 1}")

    _direct(app, w, build)
    pattern = r'font-variant-numeric: tabular-nums">([0-9,]+)\.000</td>'
    first, statements = _record_statements(client, px.OVERVIEW_URL)
    assert re.findall(pattern, first) == [str(n) for n in range(45, 25, -1)]
    assert "page=2" in first and "Previous" not in first
    assert not any("count(" in s.lower() for s in statements)
    assert any("FROM payment_transactions" in s and "LIMIT" in s for s in statements)
    assert re.findall(pattern, px.page(client, px.OVERVIEW_URL + "?page=3")) == [
        str(n) for n in range(5, 0, -1)]
    for bad in ("?page=99", "?page=abc", "?page=0", "?status=paid", "?method=card",
                "?status=PENDING"):
        assert re.findall(pattern, px.page(client, px.OVERVIEW_URL + bad))[0] == "45", bad
    pending = re.findall(pattern, px.page(client, px.OVERVIEW_URL + "?status=pending"))
    assert pending == [str(n) for n in range(43, 0, -3)]
    cash = px.page(client, px.OVERVIEW_URL + "?status=confirmed&method=cash&page=2")
    assert re.findall(pattern, cash) == [str(n) for n in range(15, 0, -1) if n % 3 != 1]
    assert "status=confirmed" in cash and "method=cash" in cash
    assert f'href="{px.payments_url(w)}"' in first
    assert re.findall(pattern, px.page(client, px.OVERVIEW_URL + "?method=bank_transfer&"
                                       "status=rejected")) == []


def test_page_costs_do_not_grow_with_payments_receipts_or_accounts(app, client):
    w = px.login_world(app, client)
    with app.app_context():
        assignment = db.session.get(StudentFeeAssignment, w["assignment_id"])
        others = [fx.admin(f"actor{index}@example.com") for index in range(3)]
        small = px.issued_invoice(assignment, others[0])
        px.cash_with_receipt(small, others[0])
        large = px.issued_invoice(assignment, others[0])
        for index in range(20):
            pay = px.payment(large, others[index % 3], amount="1")
            if index % 2:
                px.receipt(pay, others[(index + 1) % 3])
        for index in range(5):
            px.payment(large, others[index % 3], method="bank_transfer", status="rejected",
                       amount="1")
        small_w, large_w = dict(w, ip=small.public_id), dict(w, ip=large.public_id)
    assert _select_count(client, px.payments_url(small_w)) == _select_count(
        client, px.payments_url(large_w))
    assert _select_count(client, fx.detail_url(w, small_w["ip"])) == _select_count(
        client, fx.detail_url(w, large_w["ip"]))
    overview = _select_count(client, px.OVERVIEW_URL)
    with app.app_context():
        owner = fx.stored_invoice(small_w["ip"])
        others = User.query.filter(User.email.like("actor%")).order_by(User.id).all()
        for index in range(15):
            px.cash_with_receipt(owner, others[index % 3], amount="1")
    assert _select_count(client, px.OVERVIEW_URL) == overview


# ===========================================================================
# Responses, escaping and what never reaches a page
# ===========================================================================


def test_every_response_is_private_and_no_store(app, client):
    w = px.login_world(app, client)
    responses = [client.get(url) for url in (px.payments_url(w), px.cash_url(w), px.bank_url(w),
                                             px.OVERVIEW_URL, px.OVERVIEW_URL + "?page=9")]
    responses += [px.record_cash(client, w, token="forged"), px.record_cash(client, w, amount="x")]
    cash = px.record_cash(client, w, amount="10")
    responses.append(cash)
    rp = px.rp_from(cash)
    responses += [px.record_bank(client, w, amount="x"), px.record_bank(client, w, amount="5"),
                  px.record_bank(client, w, amount="6")]
    with app.app_context():
        cash_pp, first, second = [row.public_id for row in px.stored_payments(w)]
    responses += [client.get(url) for url in (px.receipt_url(w, rp), px.confirm_url(w, first),
                                              px.reject_url(w, second),
                                              px.reverse_url(w, cash_pp))]
    responses += [px.confirm(client, w, first, confirm=False), px.confirm(client, w, first),
                  px.reject(client, w, second, reason=None), px.reject(client, w, second),
                  px.reverse(client, w, cash_pp, confirm=False), px.reverse(client, w, cash_pp),
                  client.get(px.reverse_url(w, cash_pp)), client.get(px.confirm_url(w, first))]
    for response in responses:
        assert response.headers.get("Cache-Control") == "private, no-store"
        assert "Cookie" in response.headers.get("Vary", "")


def test_references_and_reasons_are_escaped_everywhere(app, client):
    w = px.login_world(app, client)
    pp = px.pending_transfer(client, w, amount="5")
    with app.app_context():
        db.session.execute(text("UPDATE payment_transactions SET bank_transfer_reference = "
                                "'<b>REF</b>' WHERE public_id = :pp"), {"pp": pp})
        db.session.commit()
    second = px.pending_transfer(client, w, amount="6")
    assert px.reject(client, w, second, reason="<script>alert(1)</script>").status_code == 302
    assert px.confirm(client, w, pp).status_code == 302
    rp = px.rp_from(px.record_cash(client, w, amount="7"))
    for url in (px.payments_url(w), px.receipt_url(w, rp), px.reverse_url(w, pp),
                fx.detail_url(w, w["ip"])):
        html = px.page(client, url)
        assert "<b>REF</b>" not in html and "<script>alert(1)</script>" not in html, url
    history = px.page(client, px.payments_url(w))
    assert "&lt;b&gt;REF&lt;/b&gt;" in history
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in history


def test_a_receipt_renders_from_its_permanent_document(app, client):
    w = px.login_world(app, client)
    rp = px.rp_from(px.record_cash(client, w, amount="12"))
    with app.app_context():
        db.session.execute(update(User).where(User.id == w["student_id"]).values(
            full_name="Renamed Student"))
        db.session.execute(update(Group).where(Group.id == w["group_id"]).values(name="Renamed"))
        db.session.commit()
    page = px.page(client, px.receipt_url(w, rp))
    assert "Student One" in page and "Renamed Student" not in page.split('class="admin-main"')[1]


def test_no_internal_identifier_card_field_or_out_of_scope_control_reaches_a_page(app, client):
    w = px.login_world(app, client)
    cash = px.record_cash(client, w, amount="10")
    rp = px.rp_from(cash)
    pending = px.pending_transfer(client, w, amount="5")
    with app.app_context():
        rows = px.stored_payments(w)
        internal = {str(value) for value in (
            w["invoice_id"], w["assignment_id"], w["enrollment_id"], w["group_id"], w["plan_id"],
            w["admin_id"], w["student_id"], *[row.id for row in rows],
            *[r.id for r in px.stored_receipts(w)])}
        cash_pp = rows[0].public_id
    for url in (px.payments_url(w), px.cash_url(w), px.bank_url(w), px.confirm_url(w, pending),
                px.reject_url(w, pending), px.reverse_url(w, cash_pp), px.receipt_url(w, rp),
                px.OVERVIEW_URL):
        html = px.page(client, url)
        assert not re.search(r"/(groups|enrollments|fee-assignments|invoices|payments|receipts)"
                             r"/\d+[/\"?]", html), url
        for name, value in re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html):
            assert name in ("csrf_token", "state_token", "confirm", "amount", "reference",
                            "transfer_date"), (url, name)
            if name not in ("csrf_token", "state_token"):
                assert value not in internal, (url, name, value)
        body = html.split('class="admin-main"', 1)[1]
        # Phase 5 / M10: the Payments workspace's own edit and delete links and
        # the link to Deleted Records are the approved controls.
        body = re.sub(r'<a class="btn btn--[a-z-]+" href="/admin/payments/[0-9a-f-]{36}/'
                      r'(edit|delete)" data-link="(edit|delete)">(Edit|Delete)</a>', "", body)
        body = body.replace(
            '<a href="/admin/deleted-financial-records?type=payment">Deleted Records</a>', "")
        forbidden = ['name="card', 'name="account', 'name="iban', 'name="cvv', 'name="pin',
                     'name="currency', 'name="receipt_number"', 'type="file"', "Refund",
                     "Delete", "Pay now", "Edit payment", "invoice_id", "payment_transaction_id",
                     "calendar_year", "last_number"]
        if url != px.OVERVIEW_URL:
            # The overview's two GET filters are the only status / method inputs.
            forbidden += ['name="status"', 'name="method"']
        for fragment in forbidden:
            assert fragment not in body, (url, fragment)


def test_the_payments_navigation_entry_is_active_on_the_overview(app, client):
    px.login_world(app, client)
    html = px.page(client, px.OVERVIEW_URL)
    assert 'admin-nav__link--active" href="/admin/payments"' in html
