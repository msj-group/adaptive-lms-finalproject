"""Phase 5 / M09: the Administrator Billing Desk.

A read-only guide: choose a Group, then one of its enrolled Students, then
follow the one next step for that Student's fee assignment, invoice and
payments. The tests prove who may use it, that selection is by validated
public ids, that each billing state links exactly the existing route for its
next step -- and nothing misleading -- and that no desk request changes any
financial row.
"""

import re

import pytest
from sqlalchemy import update

import tests.fee_assignment_fixtures as fees
import tests.financial_report_fixtures as rx
import tests.invoice_fixtures as fx
import tests.payment_fixtures as px
import tests.payment_intent_fixtures as ix
from app.blueprints.admin.fee_assignments import _BLOCK_MESSAGES as ASSIGN_BLOCK_MESSAGES
from app.blueprints.admin.payments import _BALANCE_BROKEN_MESSAGE, _ONLINE_INTENT_MESSAGE
from app.extensions import db
from app.models import (
    Enrollment,
    FeePlan,
    Group,
    StudentFeeAssignment,
    User,
    UserRole,
    UserStatus,
)
from app.services import billing_desk_queries as desk
from app.services.student_fee_assignment_queries import BLOCK_STUDENT_INACTIVE

DESK = "/admin/billing-desk"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_ACTION = re.compile(r'href="([^"]+)" data-action="([a-z_]+)"')
_HISTORY = re.compile(r'href="([^"]+)" data-history="([a-z_]+)"')


def _student_url(w):
    return f"{DESK}?group={w['gp']}&student={w['student_public_id']}"


def _page(client, url, status=200):
    response = client.get(url)
    assert response.status_code == status, (url, response.status_code)
    if status == 200:
        assert "no-store" in response.headers["Cache-Control"]
        assert "Cookie" in response.headers.get("Vary", "")
    return response.get_data(as_text=True)


def _actions(html):
    return [(url, key) for url, key in _ACTION.findall(html)]


def _history(html):
    return [(url, key) for url, key in _HISTORY.findall(html)]


def _fee_history(w):
    return fees.history_url(w["gp"], w["ep"])


def _choices(w):
    return fees.choices_url(w["gp"], w["ep"])


def _login(app, client, w):
    fees.login_as(client, "admin@example.com")
    return w


# ===========================================================================
# Routes, authorization and methods
# ===========================================================================


def test_the_desk_is_one_get_only_rule(app, client):
    rules = {(rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
             for rule in app.url_map.iter_rules() if rule.endpoint == "admin.billing_desk"}
    assert rules == {(DESK, frozenset({"GET"}))}
    assert not [rule for rule in app.url_map.iter_rules()
                if "billing" in rule.rule and rule.endpoint != "admin.billing_desk"]
    w = _login(app, client, px.world(app))
    with app.app_context():
        before = rx.everything()
    for url in (DESK, f"{DESK}?group={w['gp']}", _student_url(w)):
        for method in (client.post, client.put, client.patch, client.delete):
            assert method(url).status_code == 405, (url, method)
    with app.app_context():
        assert rx.everything() == before


@pytest.mark.parametrize(
    "role", [UserRole.TEACHER.value, UserRole.STUDENT.value, UserRole.RESEARCHER.value]
)
def test_every_other_role_is_refused(app, client, role):
    w = px.world(app)
    with app.app_context():
        fees.user(f"{role}@example.com", role)
    fees.login_as(client, f"{role}@example.com")
    for url in (DESK, f"{DESK}?group={w['gp']}", _student_url(w)):
        assert client.get(url).status_code == 403, url


def test_anonymous_and_suspended_administrators_are_sent_to_log_in(app, client):
    w = px.world(app)
    for url in (DESK, _student_url(w)):
        response = client.get(url)
        assert response.status_code == 302 and "/auth/login" in response.headers["Location"]
    fees.login_as(client, "admin@example.com")
    assert client.get(DESK).status_code == 200
    with app.app_context():
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
        db.session.commit()
    fees.fresh_identity()
    response = client.get(_student_url(w))
    assert response.status_code == 302 and "/auth/login" in response.headers["Location"]


def test_the_sidebar_and_dashboard_lead_to_the_desk(app, client):
    _login(app, client, px.world(app))
    dashboard = client.get("/admin/dashboard").get_data(as_text=True)
    assert re.search(r'<a class="card" href="/admin/billing-desk"', dashboard)
    assert "Find a student's fee assignment, invoice and payment actions in one place." in dashboard
    finance = dashboard[dashboard.index("Finance &amp; Research"):]
    order = [finance.index(label) for label in (
        ">Billing Desk<", ">Fee Plans<", ">Payments<", ">Financial reports<", "Research <span")]
    assert order == sorted(order)
    assert 'href="/admin/billing-desk">Billing Desk</a>' in finance
    desk_page = _page(client, DESK)
    assert re.search(r'admin-nav__link--active" href="/admin/billing-desk"', desk_page)


# ===========================================================================
# Selection
# ===========================================================================


def test_the_first_step_offers_every_group_by_public_id(app, client):
    w = _login(app, client, px.world(app))
    with app.app_context():
        other = fees.group(name="Evening", status="archived")
        other_gp = other.public_id
    html = _page(client, DESK)
    assert "choose a Group" in html and "choose an enrolled student" in html
    values = re.findall(r'<option value="([^"]*)"', html)
    assert values[0] == "" and set(values[1:]) == {w["gp"], other_gp}
    assert all(_UUID.fullmatch(value) for value in values[1:])
    assert "(archived)" in html
    assert 'data-action' not in html and "Students enrolled in" not in html


def test_invalid_foreign_or_mismatched_identifiers_are_404(app, client):
    w = _login(app, client, px.world(app))
    with app.app_context():
        foreign = fees.enrollment()
        foreign_student = db.session.get(User, foreign.student_id).public_id
        foreign_group = db.session.get(Group, foreign.group_id).public_id
        teacher = fees.teacher_for(db.session.get(Group, w["group_id"])).public_id
        admin_public_id = db.session.get(User, w["admin_id"]).public_id
    gp, sp = w["gp"], w["student_public_id"]
    for query in (
        f"group={w['group_id']}",
        "group=00000000-0000-0000-0000-000000000000",
        f"group={gp.upper()}",
        f"group=%20{gp}",
        "group=abc",
        f"group={gp}&group={gp}",
        f"student={sp}",
        f"group={gp}&student={w['student_id']}",
        f"group={gp}&student={sp.upper()}",
        f"group={gp}&student={sp}&student={sp}",
        f"group={gp}&student={foreign_student}",
        f"group={foreign_group}&student={sp}",
        f"group={gp}&student={teacher}",
        f"group={gp}&student={admin_public_id}",
        f"group={gp}&student={w['ep']}",
    ):
        assert client.get(f"{DESK}?{query}").status_code == 404, query


def test_the_student_list_is_paginated_searchable_and_never_truncated(app, client):
    w = _login(app, client, fees.world(app))
    with app.app_context():
        home = db.session.get(Group, w["group_id"])
        for n in range(24):
            fees.enrollment(owning_group=home, enrolled=fees.student(name=f"Zed {n:02d}"))
        fees.enrollment(owning_group=home, enrolled=fees.student(name="Zed Withdrawn"),
                        status=fees.WITHDRAWN)
        fees.enrollment(enrolled=fees.student(name="Zed Elsewhere"))
        fees.enrollment(owning_group=home, enrolled=fees.student(name="Pct 100% Sure"))
    base = f"{DESK}?group={w['gp']}"
    first = _page(client, base)
    assert "Students 1&ndash;20 of 26" in first
    assert first.count(">Open billing<") == 20
    second = _page(client, base + "&page=2")
    assert "Students 21&ndash;26 of 26" in second and second.count(">Open billing<") == 6
    names = re.findall(r"<td[^>]*>([^<]+?)(?: <span|</td>)", first + second)
    for expected in ["Student One", "Pct 100% Sure"] + [f"Zed {n:02d}" for n in range(24)]:
        assert expected in names, expected
    assert "Zed Withdrawn" not in first + second and "Zed Elsewhere" not in first + second
    assert _page(client, base + "&page=9").count(">Open billing<") == 20
    found = _page(client, base + "&q=zed")
    assert "Students 1&ndash;20 of 24" in found and "Student One" not in found
    assert "Students 1&ndash;1 of 1" in _page(client, base + "&q=100%25")
    assert "Students 0&ndash;0 of 0" in _page(client, base + "&q=%25")
    links = re.findall(r'href="(/admin/billing-desk\?[^"]+)">Open billing', first)
    for link in links:
        query = dict(part.split("=") for part in link.split("?")[1].replace("&amp;", "&").split("&"))
        assert set(query) == {"group", "student"} and query["group"] == w["gp"]
        assert _UUID.fullmatch(query["student"])


def test_the_list_states_each_students_plan_and_invoice(app, client):
    w = _login(app, client, px.world(app))
    html = _page(client, f"{DESK}?group={w['gp']}")
    row = re.search(r"Student One</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>([^<]*)</td>\s*"
                    r"<td[^>]*>([^<]*)</td>", html)
    assert row.groups() == ("Assigned", "Issued")


# ===========================================================================
# The next step for each billing state
# ===========================================================================


def test_no_assignment_offers_only_the_plan_choice(app, client):
    w = _login(app, client, fees.world(app))
    html = _page(client, _student_url(w))
    assert _actions(html) == [(_choices(w), desk.ASSIGN_PLAN)]
    assert _history(html) == [(_fee_history(w), desk.FEE_HISTORY)]
    assert "No fee plan assigned" in html and "Invoice total" not in html
    assert client.get(_choices(w)).status_code == 200


def test_a_suspended_student_is_not_offered_an_assignment(app, client):
    w = _login(app, client, fees.world(app))
    with app.app_context():
        db.session.execute(update(User).where(User.id == w["student_id"]).values(
            status=UserStatus.SUSPENDED.value))
        db.session.commit()
    html = _page(client, _student_url(w))
    assert _actions(html) == []
    assert ASSIGN_BLOCK_MESSAGES[BLOCK_STUDENT_INACTIVE].replace("'", "&#39;") in html


def test_an_assigned_plan_without_invoice_offers_the_draft_creation(app, client):
    w = _login(app, client, fx.world(app))
    html = _page(client, _student_url(w))
    assert _actions(html) == [(fx.new_url(w), desk.CREATE_DRAFT)]
    assert _history(html) == [(_fee_history(w), desk.FEE_HISTORY),
                              (fx.invoices_url(w), desk.INVOICE_HISTORY)]
    assert "Standard plan" in html
    assert client.get(fx.new_url(w)).status_code == 200


def test_a_draft_offers_only_the_existing_draft(app, client):
    w = _login(app, client, fx.world(app))
    with app.app_context():
        draft = fx.invoice(db.session.get(StudentFeeAssignment, w["assignment_id"]),
                           db.session.get(User, w["admin_id"]))
        ip = draft.public_id
    html = _page(client, _student_url(w))
    assert _actions(html) == [(fx.detail_url(w, ip), desk.OPEN_DRAFT)]
    assert "Draft invoice" in html and "Not numbered yet" in html
    detail = fx.detail_url(w, ip)
    for forbidden in (detail + "/issue", detail + "/payments", "/payments/cash",
                      "/payments/bank-transfer"):
        assert forbidden not in html, forbidden


def test_an_issued_outstanding_invoice_links_the_existing_payment_routes(app, client):
    w = _login(app, client, px.world(app))
    html = _page(client, _student_url(w))
    assert _actions(html) == [
        (px.cash_url(w), desk.RECORD_CASH),
        (px.bank_url(w), desk.RECORD_BANK),
        (px.payments_url(w), desk.OPEN_PAYMENTS),
        (px.invoice_url(w), desk.OPEN_INVOICE),
    ]
    for amount in ("1,250.500 LYD", "0.000 LYD"):
        assert amount in html
    assert "Registration items" in html and "50.000 LYD" in html
    assert "Course items" in html and "1,200.500 LYD" in html
    assert desk.ALLOCATION_NOTE in html
    assert "Pay registration" not in html and "Pay course" not in html
    for url, _key in _actions(html):
        assert client.get(url).status_code == 200, url
    px.record_cash(client, w, amount="100")
    html = _page(client, _student_url(w))
    assert "100.000 LYD" in html and "1,150.500 LYD" in html
    assert [key for _url, key in _actions(html)][:2] == [desk.RECORD_CASH, desk.RECORD_BANK]


def _issued_with(app, client, setup):
    w = _login(app, client, px.world(app))
    with app.app_context():
        owner, actor = rx.rows_of(w)
        setup(owner, actor)
    return w, _page(client, _student_url(w))


def _no_collection(html):
    keys = [key for _url, key in _actions(html)]
    assert desk.RECORD_CASH not in keys and desk.RECORD_BANK not in keys
    assert "/payments/cash" not in html and "/payments/bank-transfer" not in html
    return keys


def test_a_pending_transfer_withholds_the_collection_shortcuts(app, client):
    w, html = _issued_with(app, client, lambda owner, actor: rx.transaction(
        owner, actor, "200.000", method="bank_transfer", status="pending",
        reference="DESK-REF-SECRET"))
    assert _no_collection(html) == [desk.OPEN_PAYMENTS, desk.OPEN_INVOICE]
    assert "1 bank transfer(s) of this invoice are pending" in html
    assert "DESK-REF-SECRET" not in html


def test_an_active_online_intent_withholds_the_collection_shortcuts(app, client):
    w, html = _issued_with(app, client, lambda owner, actor: ix.intent(owner, actor))
    assert _no_collection(html) == [desk.OPEN_PAYMENTS, desk.OPEN_INTENTS, desk.OPEN_INVOICE]
    assert (ix.intents_url(w), desk.OPEN_INTENTS) in _actions(html)
    assert _ONLINE_INTENT_MESSAGE.split(",")[0] in html


def test_a_reconciliation_event_withholds_the_collection_shortcuts(app, client):
    def setup(owner, actor):
        failed = ix.intent(owner, actor, status="provider_failed")
        rx.provider_event(failed, event_type="payment.succeeded")

    w, html = _issued_with(app, client, setup)
    assert _no_collection(html) == [desk.OPEN_PAYMENTS, desk.OPEN_INTENTS, desk.OPEN_INVOICE]
    assert "requires reconciliation" in html
    assert "evt_mock_" not in html and "mock_pi_" not in html


def test_an_invalid_balance_withholds_the_collection_shortcuts(app, client):
    w, html = _issued_with(app, client, lambda owner, actor: rx.transaction(
        owner, actor, "2000.000"))
    assert _no_collection(html) == [desk.OPEN_PAYMENTS, desk.OPEN_INVOICE]
    assert _BALANCE_BROKEN_MESSAGE.replace("'", "&#39;") in html
    assert "no amount is shown" in html


def test_a_paid_invoice_offers_no_new_collection(app, client):
    w, html = _issued_with(app, client, lambda owner, actor: rx.transaction(
        owner, actor, "1250.5000"))
    assert _no_collection(html) == [desk.OPEN_PAYMENTS, desk.OPEN_INVOICE]
    assert "Invoice fully paid" in html
    assert re.search(r"Outstanding</strong></dt><dd[^>]*>0\.000 LYD", html)


def test_an_online_payment_settled_invoice_is_paid(app, client):
    w, html = _issued_with(app, client, lambda owner, actor: rx.online_collection(owner, actor))
    assert _no_collection(html) == [desk.OPEN_PAYMENTS, desk.OPEN_INVOICE]
    assert "Invoice fully paid" in html


def test_a_cancelled_assignment_shows_history_only(app, client):
    w = _login(app, client, fees.world(app))
    with app.app_context():
        cancelled = fees.assignment(db.session.get(Enrollment, w["enrollment_id"]),
                                    db.session.get(FeePlan, w["plan_id"]),
                                    db.session.get(User, w["admin_id"]), status=fees.CANCELLED)
        ap = cancelled.public_id
    html = _page(client, _student_url(w))
    assert _actions(html) == []
    assert _history(html) == [(_fee_history(w), desk.FEE_HISTORY),
                              (fx.base_url(w["gp"], w["ep"], ap), desk.INVOICE_HISTORY)]
    assert "Fee assignment cancelled" in html


def test_a_cancelled_invoice_shows_history_only(app, client):
    w = _login(app, client, fx.world(app))
    with app.app_context():
        fx.invoice(db.session.get(StudentFeeAssignment, w["assignment_id"]),
                   db.session.get(User, w["admin_id"]), status=fx.CANCELLED,
                   number="INV-2026-000777")
    html = _page(client, _student_url(w))
    assert _actions(html) == []
    assert "Invoice cancelled" in html and "INV-2026-000777" in html


def test_a_withdrawn_enrollment_shows_history_only(app, client):
    w = _login(app, client, px.world(app))
    with app.app_context():
        db.session.execute(update(Enrollment).where(Enrollment.id == w["enrollment_id"]).values(
            status=fees.WITHDRAWN))
        db.session.commit()
    html = _page(client, _student_url(w))
    assert _actions(html) == []
    assert _history(html) == [(_fee_history(w), desk.FEE_HISTORY)]
    assert "Enrollment withdrawn" in html and "Invoice total" not in html
    assert "Student One" not in _page(client, f"{DESK}?group={w['gp']}")


def test_two_assigned_plans_offer_nothing(app, client):
    w = _login(app, client, fx.world(app))
    with app.app_context():
        fees.assignment(db.session.get(Enrollment, w["enrollment_id"]),
                        db.session.get(FeePlan, w["plan_id"]), db.session.get(User, w["admin_id"]))
    html = _page(client, _student_url(w))
    assert _actions(html) == [] and "Records need review" in html


# ===========================================================================
# Read-only and safe
# ===========================================================================


def test_the_desk_changes_nothing_and_posts_nothing(app, client):
    w = _login(app, client, px.world(app))
    with app.app_context():
        owner, actor = rx.rows_of(w)
        rx.transaction(owner, actor, "100.000", method="bank_transfer", status="pending")
        before = (rx.everything(), fees.snapshot(StudentFeeAssignment.query.all()))
    urls = [DESK, f"{DESK}?group={w['gp']}", f"{DESK}?group={w['gp']}&q=stu&page=2",
            _student_url(w), f"{DESK}?group=bad"]
    for url in urls:
        response = client.get(url)
        client.head(url)
        html = response.get_data(as_text=True)
        assert 'name="state_token"' not in html
        forms = re.findall(r'<form method="([a-z]+)" action="([^"]+)"', html)
        assert all(method == "get" and action == DESK for method, action in forms
                   if "logout" not in action), forms
    with app.app_context():
        db.session.expire_all()
        assert (rx.everything(), fees.snapshot(StudentFeeAssignment.query.all())) == before


def test_no_internal_identifier_reaches_the_page(app, client):
    w = _login(app, client, px.world(app))
    html = _page(client, _student_url(w))
    for url, _key in _actions(html) + _history(html):
        for segment in url.split("?")[0].split("/"):
            assert not segment.isdigit(), url
    assert f"student={w['student_id']}&" not in html
    assert f"group={w['group_id']}&" not in html


def test_the_list_costs_a_fixed_number_of_queries(app, client):
    from sqlalchemy import event

    w = _login(app, client, fees.world(app))
    with app.app_context():
        home = db.session.get(Group, w["group_id"])
        for n in range(6):
            fees.enrollment(owning_group=home, enrolled=fees.student(name=f"Few {n}"))

    def count(url):
        statements = []
        with app.app_context():
            listener = lambda *args: statements.append(args[2])  # noqa: E731
            event.listen(db.engine, "before_cursor_execute", listener)
            try:
                _page(client, url)
            finally:
                event.remove(db.engine, "before_cursor_execute", listener)
        return len(statements)

    small = count(f"{DESK}?group={w['gp']}&q=few 1")
    with app.app_context():
        home = db.session.get(Group, w["group_id"])
        for n in range(12):
            fees.enrollment(owning_group=home, enrolled=fees.student(name=f"Few 1{n:02d}"))
    assert count(f"{DESK}?group={w['gp']}&q=few 1") == small
