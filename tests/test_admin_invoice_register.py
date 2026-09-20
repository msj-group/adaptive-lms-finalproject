"""Phase 5 / M09R: the Administrator Invoice Register.

A read-only list of every invoice, newest first, with lifecycle, payment-state
and search filters. Each row links to its existing invoice page -- and an
issued invoice to its payments and receipts page -- from its own verified
public ids. The tests prove who may use it, what it lists and how it pages and
filters without dropping an invoice, that every state is shown truthfully with
no collection shortcut, that nothing sensitive appears, and that it changes no
row.
"""

import html as html_lib
import re

import pytest
from sqlalchemy import event, update

import tests.fee_assignment_fixtures as fees
import tests.financial_report_fixtures as rx
import tests.invoice_fixtures as fx
import tests.payment_fixtures as px
import tests.payment_intent_fixtures as ix
from app.extensions import db
from app.models import (
    Enrollment,
    Group,
    Invoice,
    StudentFeeAssignment,
    User,
    UserRole,
    UserStatus,
)
from app.services import invoice_register_queries as register

REGISTER = "/admin/invoices"
_ROW = re.compile(r'<tr [^>]*data-invoice="([0-9a-f-]{36})">(.*?)</tr>', re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_LINK = re.compile(r'href="([^"]+)" data-link="([a-z]+)"')


def _text(fragment):
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _rows(page_html):
    """``[{public_id, cells, links}]`` of the register table, in page order."""
    rows = []
    for public_id, body in _ROW.findall(page_html):
        rows.append({
            "public_id": public_id,
            "cells": [_text(cell) for cell in _CELL.findall(body)],
            "links": {key: html_lib.unescape(url) for url, key in _LINK.findall(body)},
        })
    return rows


def _page(client, query=""):
    response = client.get(f"{REGISTER}?{query}" if query else REGISTER)
    assert response.status_code == 200, response.status_code
    assert "no-store" in response.headers["Cache-Control"]
    assert "Cookie" in response.headers.get("Vary", "")
    return response.get_data(as_text=True)


def _login(client):
    fees.login_as(client, "admin@example.com")


def _new_invoice(w, status="issued", student_name=None, group=None, group_name=None):
    """Another invoice for a new Student, written directly."""
    owner, actor = rx.rows_of(w)
    home = group or db.session.get(Group, w["group_id"])
    invoice, _ = rx.invoice_in_group(actor, rx.plan_of(w), student_name=student_name,
                                     owning_group=None if group_name else home,
                                     group_name=group_name, status=status)
    return invoice


def _nested(invoice):
    """The existing invoice page of `invoice`, from its own rows."""
    assignment = db.session.get(StudentFeeAssignment, invoice.student_fee_assignment_id)
    enrollment = db.session.get(Enrollment, assignment.enrollment_id)
    gp = db.session.get(Group, enrollment.group_id).public_id
    return fx.base_url(gp, enrollment.public_id, assignment.public_id) + f"/{invoice.public_id}"


# ===========================================================================
# Routes, authorization and navigation
# ===========================================================================


def test_the_register_is_one_get_only_rule(app, client):
    rules = {(rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
             for rule in app.url_map.iter_rules() if rule.endpoint == "admin.invoice_register"}
    assert rules == {(REGISTER, frozenset({"GET"}))}
    px.world(app)
    _login(client)
    with app.app_context():
        before = rx.everything()
    for url in (REGISTER, f"{REGISTER}?status=issued&q=inv"):
        for method in (client.post, client.put, client.patch, client.delete):
            assert method(url).status_code == 405, (url, method)
    with app.app_context():
        assert rx.everything() == before


@pytest.mark.parametrize(
    "role", [UserRole.TEACHER.value, UserRole.STUDENT.value, UserRole.RESEARCHER.value]
)
def test_every_other_role_is_refused(app, client, role):
    px.world(app)
    with app.app_context():
        fees.user(f"{role}@example.com", role)
    fees.login_as(client, f"{role}@example.com")
    assert client.get(REGISTER).status_code == 403
    assert client.get(f"{REGISTER}?status=issued").status_code == 403


def test_anonymous_and_suspended_administrators_are_sent_to_log_in(app, client):
    w = px.world(app)
    response = client.get(REGISTER)
    assert response.status_code == 302 and "/auth/login" in response.headers["Location"]
    _login(client)
    assert client.get(REGISTER).status_code == 200
    with app.app_context():
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
        db.session.commit()
    fees.fresh_identity()
    response = client.get(REGISTER)
    assert response.status_code == 302 and "/auth/login" in response.headers["Location"]


def test_the_sidebar_order_and_the_dashboard_shortcut(app, client):
    px.world(app)
    _login(client)
    dashboard = client.get("/admin/dashboard").get_data(as_text=True)
    finance = dashboard[dashboard.index("Finance &amp; Research"):dashboard.index("</nav>")]
    labels = re.findall(r'<a class="admin-nav__link[^"]*" href="[^"]+">([^<]+)</a>', finance)
    # Phase 5 / M10's order: Student Accounts replaced the Billing Desk.
    # Phase 6 / M01 appended Research as its own entry after all of them.
    assert labels == ["Student Accounts", "Invoices", "Payments", "Fee Plans",
                      "Financial reports", "Deleted Records", "Research"]
    assert not re.search(
        r'Research <span class="badge badge--neutral">Soon</span>', finance)
    card = re.search(r'<a class="card" href="/admin/invoices"[^>]*>(.*?)</a>', dashboard, re.S)
    assert card and "Invoices" in card.group(1) and "LYD" not in card.group(1)
    assert re.search(r'<a class="card" href="/admin/student-accounts"', dashboard)
    page = _page(client)
    assert re.search(r'admin-nav__link--active" href="/admin/invoices">Invoices</a>', page)


# ===========================================================================
# Listing, paging, filtering and search
# ===========================================================================


def test_an_empty_register_says_so(app, client):
    fees.world(app)
    _login(client)
    page = _page(client)
    assert "No invoice has been created yet." in page
    assert "Invoices 0&ndash;0 of 0" in page
    assert "No invoice matches these filters." in _page(client, "status=issued")


def test_pages_hold_every_invoice_newest_first_with_the_exact_range(app, client):
    w = px.world(app)
    with app.app_context():
        created = [w["ip"]] + [_new_invoice(w, student_name=f"Student {n:02d}").public_id
                               for n in range(30)]
    _login(client)
    first = _page(client)
    second = _page(client, "page=2")
    assert "Invoices 1&ndash;25 of 31, newest first" in first
    assert "Invoices 26&ndash;31 of 31, newest first" in second
    listed = [row["public_id"] for row in _rows(first) + _rows(second)]
    assert listed == list(reversed(created))
    for malformed in ("page=0", "page=-3", "page=abc", "page=999"):
        assert [row["public_id"] for row in _rows(_page(client, malformed))] == listed[:25]


def test_filters_are_kept_across_pages(app, client):
    w = px.world(app)
    with app.app_context():
        for n in range(27):
            _new_invoice(w, student_name=f"Kept {n:02d}")
    _login(client)
    page = _page(client, "status=issued&payment=outstanding&q=kept")
    assert "Invoices 1&ndash;25 of 27" in page
    next_url = html_lib.unescape(re.search(r'href="([^"]+)">Next</a>', page).group(1))
    assert next_url.startswith(REGISTER + "?")
    assert {"status=issued", "payment=outstanding", "q=kept", "page=2"} == set(
        next_url.split("?")[1].split("&"))
    second = _page(client, next_url.split("?")[1])
    assert "Invoices 26&ndash;27 of 27" in second
    previous = html_lib.unescape(re.search(r'href="([^"]+)">Previous</a>', second).group(1))
    assert "status=issued" in previous and "q=kept" in previous and "page=1" in previous


def test_search_finds_every_field_literally_and_case_insensitively(app, client):
    w = px.world(app)
    with app.app_context():
        coded = fees.group(name="Evening Lions")
        coded.code = "EV-7"
        db.session.commit()
        by_group = _new_invoice(w, student_name="Nadia Omar", group=coded)
        pct = _new_invoice(w, student_name="Percent 100% Sure")
        home_number = db.session.get(Invoice, w["invoice_id"]).invoice_number
        email = db.session.get(User, w["student_id"]).email
        ids = {"home": w["ip"], "group": by_group.public_id, "pct": pct.public_id}
    _login(client)

    def found(q):
        return {row["public_id"] for row in _rows(_page(client, f"q={q}"))}

    assert found(home_number.lower()) == {ids["home"]}
    assert found("student one") == {ids["home"]}
    assert found(email.upper()) == {ids["home"]}
    assert found("evening lions") == {ids["group"]}
    assert found("ev-7") == {ids["group"]}
    assert found("100%25") == {ids["pct"]}
    assert found("%25") == {ids["pct"]}
    assert found("_") == set()
    assert "Invoices 1&ndash;3 of 3" in _page(client, "q=%20%20")


def test_the_payment_filter_classifies_before_paging_and_normalizes(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        paid = _new_invoice(w, student_name="Paid Student")
        rx.transaction(paid, actor, "1250.5000")
        broken = _new_invoice(w, student_name="Broken Student")
        rx.transaction(broken, actor, "2000.000")
        draft = _new_invoice(w, status="draft", student_name="Draft Student")
        cancelled = _new_invoice(w, status="cancelled", student_name="Cancelled Student")
        ids = {"outstanding": w["ip"], "paid": paid.public_id, "broken": broken.public_id,
               "draft": draft.public_id, "cancelled": cancelled.public_id}
    _login(client)

    def listed(query):
        return {row["public_id"] for row in _rows(_page(client, query))}

    assert listed("payment=outstanding") == {ids["outstanding"]}
    assert listed("payment=paid") == {ids["paid"]}
    assert listed("status=issued") == {ids["outstanding"], ids["paid"], ids["broken"]}
    assert listed("status=draft") == {ids["draft"]}
    assert listed("status=cancelled") == {ids["cancelled"]}
    assert listed("status=draft&payment=paid") == {ids["draft"]}
    assert listed("status=bogus&payment=bogus") == set(ids.values())
    page = _page(client, "status=draft&payment=paid")
    assert '<option value="all" selected>Any payment state</option>' in page
    assert register.normalize_filters(" issued ", "paid", "  a   b ") == ("issued", "paid", "a b")


# ===========================================================================
# Each state, truthfully, with no collection shortcut
# ===========================================================================


def _state_world(app):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        states = {"outstanding": db.session.get(Invoice, w["invoice_id"])}
        states["draft"] = _new_invoice(w, status="draft", student_name="Draft Student")
        states["cancelled"] = _new_invoice(w, status="cancelled", student_name="Cancelled Student")
        states["paid"] = _new_invoice(w, student_name="Paid Student")
        rx.transaction(states["paid"], actor, "1250.5000")
        states["broken"] = _new_invoice(w, student_name="Broken Student")
        rx.transaction(states["broken"], actor, "2000.000")
        states["pending"] = _new_invoice(w, student_name="Pending Student")
        rx.transaction(states["pending"], actor, "100.000", method="bank_transfer",
                       status="pending", reference="REGISTER-REF-SECRET")
        states["intent"] = _new_invoice(w, student_name="Intent Student")
        ix.intent(states["intent"], actor)
        states["reconciliation"] = _new_invoice(w, student_name="Reconciliation Student")
        failed = ix.intent(states["reconciliation"], actor, status="provider_failed")
        event_row = rx.provider_event(failed)
        secrets = [event_row.provider_event_id, event_row.payload_digest, failed.provider_reference,
                   failed.idempotency_key, "REGISTER-REF-SECRET"]
        facts = {key: {"public_id": invoice.public_id, "number": invoice.invoice_number,
                       "nested": _nested(invoice)} for key, invoice in states.items()}
    return w, facts, secrets


def test_every_state_is_shown_truthfully(app, client):
    w, facts, _ = _state_world(app)
    _login(client)
    rows = {row["public_id"]: row for row in _rows(_page(client))}
    by = {key: rows[fact["public_id"]] for key, fact in facts.items()}
    draft = by["draft"]["cells"]
    assert draft[0] == "Draft" and draft[1] == "Draft" and draft[5] == "1,250.500 LYD"
    assert draft[6] == "—" and draft[7] == "—"
    outstanding = by["outstanding"]["cells"]
    assert outstanding[0] == facts["outstanding"]["number"] and outstanding[1] == "Issued"
    assert outstanding[2] == "Student One" and outstanding[4] == "Standard plan"
    assert outstanding[5:8] == ["1,250.500 LYD", "0.000 LYD", "1,250.500 LYD"]
    paid = by["paid"]["cells"]
    assert paid[1] == "Issued Paid" and paid[5:8] == ["1,250.500 LYD", "1,250.500 LYD",
                                                      "0.000 LYD"]
    cancelled = by["cancelled"]["cells"]
    assert cancelled[0] == facts["cancelled"]["number"] and cancelled[1] == "Cancelled"
    assert cancelled[6] == "—" and cancelled[7] == "—"
    broken = by["broken"]["cells"]
    assert broken[1] == "Issued Balance unavailable" and broken[6:8] == ["—", "—"]
    assert "Bank transfer pending" in by["pending"]["cells"][1]
    assert "Online payment in progress" in by["intent"]["cells"][1]
    assert "Reconciliation required" in by["reconciliation"]["cells"][1]
    assert register.ALLOCATION_NOTE.replace("'", "&#39;") in _page(client)


def test_links_are_the_existing_pages_and_only_where_applicable(app, client):
    w, facts, _ = _state_world(app)
    _login(client)
    page = _page(client)
    rows = {row["public_id"]: row for row in _rows(page)}
    for key, fact in facts.items():
        links = rows[fact["public_id"]]["links"]
        assert links["invoice"] == fact["nested"], key
        assert client.get(links["invoice"]).status_code == 200, key
        if key in ("draft", "cancelled"):
            assert "payments" not in links, key
        else:
            assert links["payments"] == fact["nested"] + "/payments", key
            assert client.get(links["payments"]).status_code == 200, key
    for fragment in ("/payments/cash", "/payments/bank-transfer", "/issue", "/cancel",
                     "/payment-intents/new", "Pay registration", "Pay course"):
        assert fragment not in page, fragment
    forms = re.findall(r'<form method="([a-z]+)" action="([^"]+)"', page)
    assert all(method == "get" and action == REGISTER for method, action in forms
               if "logout" not in action), forms


def test_no_sensitive_or_internal_value_is_shown(app, client):
    w, facts, secrets = _state_world(app)
    _login(client)
    page = _page(client) + _page(client, "status=issued&page=2")
    for secret in secrets:
        assert secret not in page, secret
    for fragment in ("evt_mock_", "mock_pi_", "digest", "snapshot", "BANKREF"):
        assert fragment not in page, fragment
    for row in _rows(page):
        for url in row["links"].values():
            assert not any(segment.isdigit() for segment in url.split("/")), url


# ===========================================================================
# Bounded and read-only
# ===========================================================================


def _query_count(app, client, query=""):
    statements = []
    with app.app_context():
        listener = lambda *args: statements.append(args[2])  # noqa: E731
        event.listen(db.engine, "before_cursor_execute", listener)
        try:
            _page(client, query)
        finally:
            event.remove(db.engine, "before_cursor_execute", listener)
    return len(statements)


def test_a_page_costs_a_fixed_number_of_queries(app, client):
    w = px.world(app)
    with app.app_context():
        for n in range(3):
            _new_invoice(w, student_name=f"Few {n}")
    _login(client)
    small = {query: _query_count(app, client, query) for query in ("", "payment=outstanding")}
    with app.app_context():
        owner, actor = rx.rows_of(w)
        for n in range(30):
            invoice = _new_invoice(w, student_name=f"Many {n}")
            rx.transaction(invoice, actor, "10.000")
            ix.intent(invoice, actor)
    for query, count in small.items():
        assert _query_count(app, client, query) == count, query


def test_no_register_request_changes_anything(app, client):
    w, facts, _ = _state_world(app)
    _login(client)

    def state():
        db.session.expire_all()
        return (rx.everything(), fees.snapshot(StudentFeeAssignment.query.all()),
                [(row.id, row.status, row.updated_at) for row in Enrollment.query.all()])

    with app.app_context():
        before = state()
    for query in ("", "page=2", "status=issued", "payment=paid", "payment=outstanding&q=student",
                  "status=cancelled", "q=%25", "status=bogus"):
        client.get(f"{REGISTER}?{query}")
        client.head(f"{REGISTER}?{query}")
    with app.app_context():
        assert state() == before
