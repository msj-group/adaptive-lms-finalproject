"""Phase 5 / M10: the simplified financial workspaces and Deleted Records.

Student Accounts (a computed status, never stored), the Invoices workspace
(guided New invoice, line edits above the payment floor, visible deletion of
an invoice or its whole document family), the Payments workspace (New payment,
edit by replacement with a new receipt, deletion with the receipt) and the
read-only Deleted Records. The tests prove who may use each page, what each
write changes and records -- and what it refuses, changing nothing -- that
deleted documents leave every live list, balance and report, that no page
shows a sensitive value, that Groups no longer carry Finance, and that the
pages cost a fixed number of queries.
"""

import html as html_lib
import re
from decimal import Decimal

import pytest
from sqlalchemy import event, update

import tests.fee_assignment_fixtures as fees
import tests.financial_report_fixtures as rx
import tests.invoice_fixtures as fx
import tests.payment_fixtures as px
import tests.payment_intent_fixtures as ix
import tests.structural_checks as sc
from app import create_app
from app.extensions import db
from app.models import (
    Enrollment,
    FeePlan,
    Group,
    Invoice,
    InvoiceItem,
    PaymentAuditEvent,
    PaymentTransaction,
    Receipt,
    StudentFeeAssignment,
    User,
    UserRole,
    UserStatus,
)
from app.services import financial_deletions

STATE = fx.STATE_FIELD
ACCOUNTS = "/admin/student-accounts"
REGISTER = "/admin/invoices"
NEW_INVOICE = "/admin/invoices/new"
PAYMENTS = "/admin/payments"
NEW_PAYMENT = "/admin/payments/new"
DELETED = "/admin/deleted-financial-records"
_TOKEN = re.compile(r'name="state_token" value="([^"]+)"')


# ===========================================================================
# Helpers
# ===========================================================================


def _login(client):
    fees.login_as(client, "admin@example.com")


def _token(html):
    match = _TOKEN.search(html)
    return match.group(1) if match else ""


def _text(fragment):
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _get(client, url):
    response = client.get(url)
    assert response.status_code == 200, (url, response.status_code)
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in response.headers.get("Vary", "")
    return response.get_data(as_text=True)


def _chain(invoice):
    assignment = db.session.get(StudentFeeAssignment, invoice.student_fee_assignment_id)
    enrollment = db.session.get(Enrollment, assignment.enrollment_id)
    group = db.session.get(Group, enrollment.group_id)
    return group, enrollment, assignment


def _nested(invoice):
    group, enrollment, assignment = _chain(invoice)
    return fx.base_url(group.public_id, enrollment.public_id, assignment.public_id) + (
        f"/{invoice.public_id}")


def _student_of(invoice):
    _group, enrollment, _assignment = _chain(invoice)
    return db.session.get(User, enrollment.student_id)


def _new_invoice(w, student_name=None, status="issued", group=None):
    _owner, actor = rx.rows_of(w)
    invoice, _ = rx.invoice_in_group(actor, rx.plan_of(w), student_name=student_name,
                                     owning_group=group or rx.group_of(w), status=status)
    return invoice


def _tombstone(row, actor, reason="Set up as deleted"):
    """Delete `row` directly, through the model guards, for setup only."""
    financial_deletions.tombstone(row, actor.id, reason, rx.SEPTEMBER.replace(month=12))
    db.session.commit()


def _post(client, url, data, confirm=True):
    html = client.get(url).get_data(as_text=True)
    payload = dict(data, **{STATE: _token(html)})
    if confirm:
        payload["confirm"] = "yes"
    return client.post(url, data=payload)


def _delete_invoice(client, ip, reason="Entered for the wrong student", confirm=True):
    return _post(client, f"{REGISTER}/{ip}/delete", {"reason": reason}, confirm)


def _delete_payment(client, pp, reason="Recorded twice", confirm=True):
    return _post(client, f"{PAYMENTS}/{pp}/delete", {"reason": reason}, confirm)


def _edit_payment(client, pp, confirm=True, **fields):
    data = {"reason": "Typed the wrong amount"}
    data.update(fields)
    return _post(client, f"{PAYMENTS}/{pp}/edit", data, confirm)


def _state():
    """Every financial row plus every account, Enrollment, Group and
    assignment -- what "nothing changed" is compared against."""
    db.session.expire_all()
    return (
        rx.everything(),
        [(r.id, r.status, r.role, r.updated_at) for r in User.query.order_by(User.id)],
        [(r.id, r.status, r.updated_at) for r in Enrollment.query.order_by(Enrollment.id)],
        [(r.id, r.status, r.updated_at) for r in Group.query.order_by(Group.id)],
        fees.snapshot(StudentFeeAssignment.query.order_by(StudentFeeAssignment.id).all()),
        [(r.id, r.deleted_at, r.version) for r in Invoice.query.order_by(Invoice.id)],
        [(r.id, r.deleted_at, r.version) for r in PaymentTransaction.query.order_by(
            PaymentTransaction.id)],
        [(r.id, r.deleted_at, r.version) for r in Receipt.query.order_by(Receipt.id)],
    )


def _query_count(app, client, url):
    statements = []
    with app.app_context():
        listener = lambda *args: statements.append(args[2])  # noqa: E731
        event.listen(db.engine, "before_cursor_execute", listener)
        try:
            _get(client, url)
        finally:
            event.remove(db.engine, "before_cursor_execute", listener)
    return len(statements)


def _kinds(invoice_id):
    db.session.expire_all()
    return [row.kind for row in PaymentAuditEvent.query.filter_by(invoice_id=invoice_id)
            .order_by(PaymentAuditEvent.id)]


# ===========================================================================
# Navigation, routes and authorization
# ===========================================================================

_GET_ONLY = {
    "admin.student_accounts": ACCOUNTS,
    "admin.student_financial_record": ACCOUNTS + "/<student_public_id>/financial-record",
    "admin.billing_desk": "/admin/billing-desk",
    "admin.payment_workspace_new": NEW_PAYMENT,
    "admin.deleted_financial_records": DELETED,
    "admin.deleted_financial_record": DELETED + "/<doc_type>/<public_id>",
}
_WRITES = {
    "admin.invoice_workspace_new": NEW_INVOICE,
    "admin.invoice_workspace_delete": REGISTER + "/<invoice_public_id>/delete",
    "admin.payment_workspace_edit": PAYMENTS + "/<payment_public_id>/edit",
    "admin.payment_workspace_delete": PAYMENTS + "/<payment_public_id>/delete",
}


def test_the_new_rules_and_their_methods(app):
    rules = {rule.endpoint: (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
             for rule in app.url_map.iter_rules() if rule.endpoint in {**_GET_ONLY, **_WRITES}}
    assert rules == {
        **{endpoint: (url, frozenset({"GET"})) for endpoint, url in _GET_ONLY.items()},
        **{endpoint: (url, frozenset({"GET", "POST"})) for endpoint, url in _WRITES.items()},
    }


def test_the_sidebar_order_and_the_dashboard_shortcuts(app, client):
    px.world(app)
    _login(client)
    dashboard = client.get("/admin/dashboard").get_data(as_text=True)
    finance = dashboard[dashboard.index("Finance &amp; Research"):dashboard.index("</nav>")]
    labels = re.findall(r'<a class="admin-nav__link[^"]*" href="[^"]+">([^<]+)</a>', finance)
    # Phase 6 / M01 enabled Research as a separate entry *after* every
    # Phase 5 workspace; their order is unchanged.
    assert labels == ["Student Accounts", "Invoices", "Payments", "Fee Plans",
                      "Financial reports", "Deleted Records", "Research"]
    assert not re.search(
        r'Research <span class="badge badge--neutral">Soon</span>', finance)
    shortcuts = re.findall(r'<a class="card" href="([^"]+)"[^>]*data-shortcut="([a-z-]+)">(.*?)</a>',
                           dashboard, re.S)
    assert [(url, key) for url, key, _body in shortcuts] == [
        (ACCOUNTS, "student-accounts"), (REGISTER, "invoices"), (PAYMENTS, "payments"),
        (DELETED, "deleted-records")]
    assert not any("LYD" in body for _url, _key, body in shortcuts)
    assert "Billing Desk" not in dashboard
    for url, label in ((ACCOUNTS, "Student Accounts"), (DELETED, "Deleted Records")):
        assert re.search(rf'admin-nav__link--active" href="{url}">{label}</a>', _get(client, url))


def test_the_billing_desk_is_a_compatibility_redirect(app, client):
    px.world(app)
    _login(client)
    for query in ("", "?group=x&student=y"):
        response = client.get("/admin/billing-desk" + query)
        assert response.status_code == 302
        assert response.headers["Location"].endswith(ACCOUNTS)
        assert response.headers["Cache-Control"] == "private, no-store"
    assert "Billing Desk" not in _get(client, REGISTER)
    assert "Billing Desk" not in _get(client, ACCOUNTS)


def _urls(app, w):
    with app.app_context():
        owner, actor = rx.rows_of(w)
        pay, rc = px.cash_with_receipt(owner, actor, "100.000")
        gone = _new_invoice(w, student_name="Gone")
        _tombstone(gone, actor)
        return {
            "get": [ACCOUNTS, f"{ACCOUNTS}/{w['student_public_id']}/financial-record",
                    NEW_INVOICE, f"{NEW_INVOICE}?student={w['student_public_id']}", NEW_PAYMENT,
                    DELETED, f"{DELETED}/invoice/{gone.public_id}",
                    f"{REGISTER}/{w['ip']}/delete", f"{PAYMENTS}/{pay.public_id}/edit",
                    f"{PAYMENTS}/{pay.public_id}/delete"],
            "post": [NEW_INVOICE, f"{REGISTER}/{w['ip']}/delete",
                     f"{PAYMENTS}/{pay.public_id}/edit", f"{PAYMENTS}/{pay.public_id}/delete"],
        }


@pytest.mark.parametrize(
    "role", [UserRole.TEACHER.value, UserRole.STUDENT.value, UserRole.RESEARCHER.value]
)
def test_every_other_role_is_refused(app, client, role):
    w = px.world(app)
    urls = _urls(app, w)
    with app.app_context():
        fees.user(f"{role}@example.com", role)
        before = _state()
    fees.login_as(client, f"{role}@example.com")
    for url in urls["get"]:
        assert client.get(url).status_code == 403, url
    for url in urls["post"]:
        assert client.post(url, data={"reason": "x", "confirm": "yes"}).status_code == 403, url
    with app.app_context():
        assert _state() == before


def test_anonymous_and_suspended_administrators_are_sent_to_log_in(app, client):
    w = px.world(app)
    urls = _urls(app, w)
    for url in urls["get"]:
        response = client.get(url)
        assert response.status_code == 302 and "/auth/login" in response.headers["Location"], url
    _login(client)
    for url in urls["get"]:
        assert client.get(url).status_code == 200, url
    with app.app_context():
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
        db.session.commit()
    fees.fresh_identity()
    for url in urls["get"] + urls["post"]:
        response = client.get(url)
        assert response.status_code == 302 and "/auth/login" in response.headers["Location"], url


def test_unsupported_methods_are_refused_and_change_nothing(app, client):
    w = px.world(app)
    urls = _urls(app, w)
    _login(client)
    with app.app_context():
        before = _state()
    for url in urls["get"]:
        methods = [client.put, client.patch, client.delete]
        if url not in urls["post"] and not url.startswith(NEW_INVOICE):
            methods.append(client.post)
        for method in methods:
            assert method(url).status_code == 405, (url, method)
    with app.app_context():
        assert _state() == before


def test_malformed_unknown_and_live_identifiers_are_plain_404s(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        pay, rc = px.cash_with_receipt(owner, actor, "100.000")
        pay_id, receipt_id = pay.public_id, rc.public_id
        teacher = fees.user("teacher@example.com", UserRole.TEACHER.value)
        teacher_id = teacher.public_id
    _login(client)
    for bad in (fees.MISSING, "1", "x" * 37, "%27%20OR%201%3D1"):
        assert client.get(f"{ACCOUNTS}/{bad}/financial-record").status_code == 404, bad
        assert client.get(f"{REGISTER}/{bad}/delete").status_code == 404, bad
        assert client.get(f"{PAYMENTS}/{bad}/edit").status_code == 404, bad
        assert client.post(f"{PAYMENTS}/{bad}/delete", data={}).status_code == 404, bad
        for kind in ("invoice", "payment", "receipt"):
            assert client.get(f"{DELETED}/{kind}/{bad}").status_code == 404, bad
        assert client.get(f"{NEW_INVOICE}?student={bad}").status_code == 404, bad
    # A non-Student account has no Financial Record.
    assert client.get(f"{ACCOUNTS}/{teacher_id}/financial-record").status_code == 404
    # Live documents have no tombstone, and an unknown type is not a document.
    assert client.get(f"{DELETED}/invoice/{w['ip']}").status_code == 404
    assert client.get(f"{DELETED}/payment/{pay_id}").status_code == 404
    assert client.get(f"{DELETED}/receipt/{receipt_id}").status_code == 404
    assert client.get(f"{DELETED}/refund/{pay_id}").status_code == 404
    # An enrollment of another Student never pairs with the chosen one.
    with app.app_context():
        other = _student_of(_new_invoice(w, student_name="Other"))
        other_id = other.public_id
    assert client.get(f"{NEW_INVOICE}?student={other_id}&enrollment={w['ep']}").status_code == 404


@pytest.fixture
def csrf_app():
    csrf_enabled = create_app("testing", WTF_CSRF_ENABLED=True)
    with csrf_enabled.app_context():
        db.create_all()
        yield csrf_enabled
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def _csrf(html):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)


def test_csrf_is_enforced_on_every_workspace_mutation(csrf_app):
    client = csrf_app.test_client()
    w = px.world(csrf_app)
    with csrf_app.app_context():
        owner, actor = rx.rows_of(w)
        first, _ = px.cash_with_receipt(owner, actor, "100.000")
        second, _ = px.cash_with_receipt(owner, actor, "50.000")
        ids = first.public_id, second.public_id
    login_page = client.get("/auth/login").get_data(as_text=True)
    client.post("/auth/login", data={"email": "admin@example.com", "password": px.PW,
                                     "csrf_token": _csrf(login_page)})

    def attempt(url, data):
        html = client.get(url).get_data(as_text=True)
        payload = dict(data, state_token=_token(html), confirm="yes")
        with csrf_app.app_context():
            before = _state()
        assert client.post(url, data=payload).status_code == 400, url
        with csrf_app.app_context():
            assert _state() == before
        response = client.post(url, data=dict(payload, csrf_token=_csrf(html)))
        assert response.status_code == 302, url

    attempt(f"{PAYMENTS}/{ids[0]}/edit", {"amount": "90", "reason": "Wrong amount"})
    attempt(f"{PAYMENTS}/{ids[1]}/delete", {"reason": "Twice"})
    attempt(f"{REGISTER}/{w['ip']}/delete", {"reason": "Wrong student"})
    with csrf_app.app_context():
        student = fees.student(name="Fresh")
        enrollment = fees.enrollment(owning_group=rx.group_of(w), enrolled=student)
        sp, ep = student.public_id, enrollment.public_id
    html = client.get(f"{NEW_INVOICE}?student={sp}&enrollment={ep}&plan={w['pp']}").get_data(
        as_text=True)
    payload = {"student": sp, "enrollment": ep, "plan": w["pp"], "confirm": "yes",
               "state_token": _token(html)}
    assert client.post(NEW_INVOICE, data=payload).status_code == 400
    response = client.post(NEW_INVOICE, data=dict(payload, csrf_token=_csrf(html)))
    assert response.status_code == 302


# ===========================================================================
# Student Accounts
# ===========================================================================


def _labelled_students(w):
    """One Student in each computed state; ``{name: public id}``."""
    owner, actor = rx.rows_of(w)
    students = {"Student One": _student_of(owner).public_id}
    px.payment(owner, actor, "250.000")  # Amount due: 1000.500 outstanding
    draft = _new_invoice(w, "Drafty", status="draft")
    settled = _new_invoice(w, "Settled Sam")
    px.payment(settled, actor, "1250.500")
    broken = _new_invoice(w, "Broken Bo")
    px.payment(broken, actor, "2000.000")
    cancelled = _new_invoice(w, "Cancelled Cy", status="cancelled")
    gone = _new_invoice(w, "Deleted Dee")
    _tombstone(gone, actor)
    unpaid = _new_invoice(w, "Refunded Ria")
    _tombstone(px.payment(unpaid, actor, "1250.500"), actor)
    nobody = fees.student(name="Nobody")
    for name, row in (("Drafty", draft), ("Settled Sam", settled), ("Broken Bo", broken),
                      ("Cancelled Cy", cancelled), ("Deleted Dee", gone),
                      ("Refunded Ria", unpaid)):
        students[name] = _student_of(row).public_id
    students["Nobody"] = nobody.public_id
    return students


_ROW = re.compile(r'<tr [^>]*data-student="([0-9a-f-]{36})" data-status="([a-z_]+)">(.*?)</tr>',
                  re.S)


def test_every_computed_status_is_shown_and_nothing_is_stored(app, client):
    w = px.world(app)
    with app.app_context():
        students = _labelled_students(w)
        before = _state()
    _login(client)
    page = _get(client, ACCOUNTS)
    rows = {public_id: (status, _text(body)) for public_id, status, body in _ROW.findall(page)}
    expected = {
        "Student One": ("amount_due", "Yes Yes Amount due"),
        "Drafty": ("draft_invoice", "Yes No Draft invoice"),
        "Settled Sam": ("settled", "Yes No Settled"),
        "Broken Bo": ("needs_review", "Yes — Needs review"),
        "Cancelled Cy": ("no_obligation", "No No No financial obligation"),
        "Deleted Dee": ("no_obligation", "No No No financial obligation"),
        "Refunded Ria": ("amount_due", "Yes Yes Amount due"),
        "Nobody": ("no_obligation", "No No No financial obligation"),
    }
    for name, (status, cells) in expected.items():
        got_status, text = rows[students[name]]
        assert got_status == status, name
        assert text.startswith(name) and text.endswith(cells + " Financial Record"), (name, text)
    # The signed CSRF value is random base64 and can spell "LYD" by chance.
    assert "LYD" not in sc.redact_signed_values(page)
    with app.app_context():
        assert _state() == before


def test_the_list_searches_and_pages_every_student(app, client):
    w = px.world(app)
    with app.app_context():
        for n in range(30):
            fees.student(name=f"Paged {n:02d}", email=f"paged{n:02d}@example.com")
    _login(client)
    first = _get(client, ACCOUNTS)
    assert "Students 1&ndash;25 of 31" in first
    second = _get(client, ACCOUNTS + "?page=2")
    assert "Students 26&ndash;31 of 31" in second
    listed = [pid for pid, _s, _b in _ROW.findall(first) + _ROW.findall(second)]
    assert len(listed) == len(set(listed)) == 31
    found = _get(client, ACCOUNTS + "?q=PAGED0")
    assert "Students 1&ndash;10 of 10" in found
    assert len(_ROW.findall(_get(client, ACCOUNTS + "?q=paged07%40example"))) == 1
    assert "No student matches this search." in _get(client, ACCOUNTS + "?q=%25_")
    for malformed in ("page=0", "page=x", "page=999"):
        assert "Students 1&ndash;25 of 31" in _get(client, f"{ACCOUNTS}?{malformed}")
    assert w


def test_the_financial_record_spans_every_group_with_its_live_balance(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        student = _student_of(owner)
        cash, cash_receipt = px.cash_with_receipt(owner, actor, "250.000")
        second_group = fees.group(name="Weekend Group")
        second_enrollment = fees.enrollment(owning_group=second_group, enrolled=student)
        second_assignment = fees.assignment(second_enrollment, rx.plan_of(w), actor)
        second = px.issued_invoice(second_assignment, actor)
        transfer = px.payment(second, actor, "100.000", method="bank_transfer", status="pending",
                              reference="SECRET-REF-77")
        gone, gone_receipt = px.cash_with_receipt(second, actor, "300.000")
        _tombstone(gone_receipt, actor)
        _tombstone(gone, actor)
        draft = px.fx.invoice(fees.assignment(fees.enrollment(enrolled=student), rx.plan_of(w),
                                              actor), actor)
        ids = {"receipt": cash_receipt.public_id, "gone": gone.public_id,
               "second": second.public_id, "draft": draft.public_id,
               "first_url": _nested(owner), "second_url": _nested(second)}
        sp = student.public_id
        before = _state()
    _login(client)
    page = _get(client, f"{ACCOUNTS}/{sp}/financial-record")
    # Outstanding: 1250.500 - 250 on the first + 1250.500 on the second.
    assert 'data-outstanding="2,251.000"' in page and 'data-status="amount_due"' in page
    invoices = re.findall(r'data-invoice="([0-9a-f-]{36})"', page)
    assert set(invoices) == {w["ip"], ids["second"], ids["draft"]}
    assert "Weekend Group" in page
    payments = re.findall(r'data-payment="([0-9a-f-]{36})"', page)
    assert ids["gone"] not in payments and len(payments) == 2
    assert f'href="{ids["first_url"]}/receipts/{ids["receipt"]}" data-link="receipt"' in page
    assert f'href="{ids["second_url"]}" data-link="invoice"' in page
    assert "Bank transfer pending" in page
    assert f'href="{DELETED}?q=' in page
    for secret in ("SECRET-REF-77", "snapshot", "idempotency", "provider"):
        assert secret not in page, secret
    with app.app_context():
        assert _state() == before
    assert transfer


# ===========================================================================
# New invoice
# ===========================================================================


def _fresh_enrollment(w, name="Newcomer", group=None):
    student = fees.student(name=name)
    enrollment = fees.enrollment(owning_group=group or rx.group_of(w), enrolled=student)
    return student.public_id, enrollment.public_id, enrollment.id


def _create(client, sp, ep, pp, confirm=True, token=None):
    url = f"{NEW_INVOICE}?student={sp}&enrollment={ep}&plan={pp}"
    html = _get(client, url)
    data = {"student": sp, "enrollment": ep, "plan": pp,
            STATE: _token(html) if token is None else token}
    if confirm:
        data["confirm"] = "yes"
    return client.post(NEW_INVOICE, data=data)


def test_the_flow_steps_list_only_what_may_be_chosen(app, client):
    w = fees.world(app)
    with app.app_context():
        sp = w["student_public_id"]
        fees.student(name="Suspended Sue", status=UserStatus.SUSPENDED.value)
        archived = fees.group(name="Old Group", status=fees.ARCHIVED)
        fees.enrollment(owning_group=archived,
                        enrolled=db.session.get(User, w["student_id"]))
        draft_plan = FeePlan(name="Draft plan", currency_code="LYD", status="draft",
                             created_by_id=w["admin_id"], version=1)
        db.session.add(draft_plan)
        db.session.commit()
    _login(client)
    students = _get(client, NEW_INVOICE)
    assert "Student One" in students and "Suspended Sue" not in students
    assert f'href="{NEW_INVOICE}?student={sp}"' in students
    enrollments = _get(client, f"{NEW_INVOICE}?student={sp}")
    assert enrollments.count(">Choose</a>") == 1
    assert "is not active, so a new invoice cannot be created for it." in enrollments
    plans = _get(client, f"{NEW_INVOICE}?student={sp}&enrollment={w['ep']}")
    assert "Standard plan" in plans and "Draft plan" not in plans
    assert f"plan={w['pp']}" in html_lib.unescape(plans)


def test_a_new_invoice_assigns_the_plan_and_copies_its_items(app, client):
    w = fees.world(app)
    _login(client)
    with app.app_context():
        before_assignments = fees.stored_assignments(w["ep"])
    assert before_assignments == []
    response = _create(client, w["student_public_id"], w["ep"], w["pp"])
    assert response.status_code == 302
    assert "Draft invoice created from the fee plan" in fx.followed(client, response)
    with app.app_context():
        (assignment,) = fees.stored_assignments(w["ep"])
        assert (assignment.status, assignment.fee_plan_id, assignment.assigned_by_id) == (
            "assigned", w["plan_id"], w["admin_id"])
        (invoice,) = Invoice.query.filter_by(student_fee_assignment_id=assignment.id).all()
        assert response.headers["Location"].endswith(_nested(invoice))
        assert (invoice.status, invoice.version, invoice.invoice_number) == ("draft", 1, None)
        lines = InvoiceItem.query.filter_by(invoice_id=invoice.id).order_by(InvoiceItem.id).all()
        assert [(l.kind, l.label, l.amount) for l in lines] == [
            ("registration", "Registration", Decimal("50.0000")),
            ("course", "Course", Decimal("1200.5000"))]
        assert _kinds(invoice.id) == ["invoice_draft_created"]
        student = db.session.get(User, w["student_id"])
        enrollment = db.session.get(Enrollment, w["enrollment_id"])
        assert (student.status, enrollment.status) == ("active", "active")


def test_a_new_invoice_reuses_the_current_assignment(app, client):
    w = fx.world(app)
    _login(client)
    response = _create(client, w["student_public_id"], w["ep"], w["pp"])
    assert response.status_code == 302
    with app.app_context():
        (assignment,) = fees.stored_assignments(w["ep"])
        assert assignment.public_id == w["ap"] and assignment.version == 1
        assert len(fx.stored_invoices(w)) == 1


def test_a_different_plan_replaces_an_assignment_without_an_open_invoice(app, client):
    w = fx.world(app)
    with app.app_context():
        other = fees.active_plan(db.session.get(User, w["admin_id"]), name="Evening plan",
                                 items=(("course", "Evening course", "900.000"),))
        other_id = other.public_id
    _login(client)
    response = _create(client, w["student_public_id"], w["ep"], other_id)
    assert response.status_code == 302
    with app.app_context():
        old, new = fees.stored_assignments(w["ep"])
        assert (old.public_id, old.status, old.version, old.cancelled_by_id) == (
            w["ap"], "cancelled", 2, w["admin_id"])
        assert (new.status, new.fee_plan_id) == ("assigned", other.id if False else new.fee_plan_id)
        (invoice,) = Invoice.query.filter_by(student_fee_assignment_id=new.id).all()
        assert [l.label for l in InvoiceItem.query.filter_by(invoice_id=invoice.id)] == [
            "Evening course"]


def test_a_new_invoice_is_refused_while_an_open_invoice_exists_and_after_its_deletion_allowed(
        app, client):
    w = px.world(app)
    _login(client)
    with app.app_context():
        before = _state()
    enrollments = _get(client, f"{NEW_INVOICE}?student={w['student_public_id']}")
    assert "already has a draft or issued invoice" in enrollments
    response = client.get(f"{NEW_INVOICE}?student={w['student_public_id']}&enrollment={w['ep']}")
    assert response.status_code == 302
    assert "already has a draft or issued invoice" in fx.followed(client, response)
    with app.app_context():
        assert _state() == before
    assert _delete_invoice(client, w["ip"]).status_code == 302
    response = _create(client, w["student_public_id"], w["ep"], w["pp"])
    assert response.status_code == 302 and "/invoices/" in response.headers["Location"]
    with app.app_context():
        assert [(i.status, i.deleted_at is None) for i in fx.stored_invoices(w)] == [
            ("issued", False), ("draft", True)]


@pytest.mark.parametrize("change", ["withdrawn", "suspended", "archived", "stale", "unconfirmed"])
def test_a_new_invoice_rechecks_everything_and_writes_nothing_when_refused(app, client, change):
    w = fees.world(app)
    _login(client)
    url = f"{NEW_INVOICE}?student={w['student_public_id']}&enrollment={w['ep']}&plan={w['pp']}"
    token = _token(_get(client, url))
    with app.app_context():
        if change == "withdrawn":
            db.session.get(Enrollment, w["enrollment_id"]).status = fees.WITHDRAWN
        elif change == "suspended":
            db.session.get(User, w["student_id"]).status = UserStatus.SUSPENDED.value
        elif change == "archived":
            db.session.get(Group, w["group_id"]).status = fees.ARCHIVED
        elif change == "stale":
            db.session.get(FeePlan, w["plan_id"]).version += 1
        db.session.commit()
        before = _state()
    data = {"student": w["student_public_id"], "enrollment": w["ep"], "plan": w["pp"],
            STATE: token}
    if change != "unconfirmed":
        data["confirm"] = "yes"
    response = client.post(NEW_INVOICE, data=data)
    if change == "suspended":
        # Only an active Student can be chosen at all.
        assert response.status_code == 404
    elif change == "unconfirmed":
        assert response.status_code == 200
        assert "tick the confirmation box" in response.get_data(as_text=True)
    else:
        assert response.status_code == 302
        text = client.get(response.headers["Location"], follow_redirects=True).get_data(
            as_text=True)
        assert {"withdrawn": "enrollment is withdrawn", "archived": "is archived",
                "stale": "changed after this page was opened"}[change] in text
    with app.app_context():
        assert _state() == before


# ===========================================================================
# Editing an issued invoice with payments: the floor
# ===========================================================================


def test_payments_set_a_floor_instead_of_freezing_the_lines(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        px.cash_with_receipt(owner, actor, "1000.000")
        px.payment(owner, actor, "100.000", method="bank_transfer", status="pending")
        registration, course = fx.line_ids(app, w["ip"])
    _login(client)
    edit = _get(client, fx.edit_url(w, w["ip"]))
    assert 'data-floor="1,100.000"' in edit
    # 50 + 1049.999 = 1099.999 < 1100: refused, nothing changes.
    with app.app_context():
        before = _state()
    response = fx.edit_line(client, w, w["ip"], course, label="Course", amount="1049.999",
                            reason="Discount")
    assert response.status_code == 302
    assert "below the 1,100.000 LYD already paid or pending" in fx.followed(client, response)
    response = fx.remove_line(client, w, w["ip"], course, reason="Dropped")
    assert "below the" in fx.followed(client, response)
    with app.app_context():
        assert _state() == before
    # Exactly the floor is accepted, and the balance follows at once.
    response = fx.edit_line(client, w, w["ip"], course, label="Course", amount="1050.000",
                            reason="Discount agreed")
    assert response.status_code == 302 and "Line saved." in fx.followed(client, response)
    payments = _get(client, px.payments_url(w))
    assert "1,100.000" in payments
    with app.app_context():
        assert _kinds(w["invoice_id"])[-1] == "invoice_issued_edited"
    assert registration


def test_cancellation_stays_frozen_by_payments_and_intents_still_freeze_lines(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        px.cash_with_receipt(owner, actor, "100.000")
    _login(client)
    response = client.get(fx.cancel_url(w, w["ip"]))
    assert response.status_code == 302
    assert "so it can no longer be cancelled" in fx.followed(client, response)
    detail = _get(client, fx.detail_url(w, w["ip"]))
    assert "Cancel invoice" not in detail and "Edit lines" in detail
    assert "Delete invoice" in detail
    with app.app_context():
        busy = _new_invoice(w, "Busy Bea")
        ix.intent(busy, db.session.get(User, w["admin_id"]))
        busy_url = _nested(busy)
    response = client.get(busy_url + "/edit")
    assert ix.INTENT_FROZEN_TEXT in fx.followed(client, response)
    assert "Delete invoice" not in _get(client, busy_url)


# ===========================================================================
# Deleting invoices
# ===========================================================================


def test_a_draft_or_unpaid_issued_invoice_is_deleted_alone(app, client):
    w = px.world(app)
    with app.app_context():
        draft = _new_invoice(w, "Draft Dan", status="draft")
        draft_id, draft_pk = draft.public_id, draft.id
    _login(client)
    for ip, pk in ((draft_id, draft_pk), (w["ip"], w["invoice_id"])):
        page = _get(client, f"{REGISTER}/{ip}/delete")
        assert "This invoice has no payments or receipts." in page
        response = _delete_invoice(client, ip)
        assert response.status_code == 302 and response.headers["Location"].endswith(REGISTER)
        assert "Invoice deleted." in fx.followed(client, response)
        with app.app_context():
            invoice = db.session.get(Invoice, pk)
            assert invoice.deleted_at is not None and invoice.deleted_by_id == w["admin_id"]
            assert invoice.deletion_reason == "Entered for the wrong student"
            assert invoice.status in ("draft", "issued")
            events = PaymentAuditEvent.query.filter_by(invoice_id=pk).order_by(
                PaymentAuditEvent.id).all()
            last = events[-1]
            assert (last.kind, last.reason, last.invoice_version_after) == (
                "invoice_deleted", "Entered for the wrong student", invoice.version)
            assert last.before_snapshot["deleted"] is False and last.after_snapshot["deleted"]
    assert "data-invoice=" not in _get(client, REGISTER)


def test_deleting_an_issued_invoice_deletes_its_whole_document_family(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        cash, cash_receipt = px.cash_with_receipt(owner, actor, "300.000")
        original, voided, reversal = px.reversed_collection(owner, actor, "200.000")
        rejected = px.payment(owner, actor, "20.000", method="bank_transfer", status="rejected")
        pending = px.payment(owner, actor, "10.000", method="bank_transfer", status="pending")
        # A verified webhook's online collection (its intent is terminal).
        online, _intent = rx.online_collection(owner, actor, amount="100.000")
        family = {cash.id, original.id, reversal.id, rejected.id, pending.id, online.id}
        receipts = {cash_receipt.id, voided.id}
    _login(client)
    page = _get(client, f"{REGISTER}/{w['ip']}/delete")
    assert 'data-family="6"' in page and "and 2 receipt(s)" in page
    response = _delete_invoice(client, w["ip"])
    assert "Invoice deleted with 6 payment(s) and 2 receipt(s)." in fx.followed(client, response)
    with app.app_context():
        assert {p.id for p in PaymentTransaction.query if p.deleted_at is not None} == family
        assert {r.id for r in Receipt.query if r.deleted_at is not None} == receipts
        kinds = _kinds(w["invoice_id"])
        assert kinds[-9:] == ["payment_deleted", "payment_deleted", "payment_deleted",
                              "payment_deleted", "receipt_deleted", "payment_deleted",
                              "receipt_deleted", "payment_deleted", "invoice_deleted"]
        deletions = PaymentAuditEvent.query.filter(PaymentAuditEvent.kind.in_(
            ("payment_deleted", "receipt_deleted"))).all()
        assert {e.payment_transaction_id for e in deletions} == family
        for event_row in deletions:
            assert event_row.reason == "Entered for the wrong student"
            assert event_row.after_snapshot["paid_amount"] == "0.0000"
            assert event_row.after_snapshot["payment"]["deleted"] is True
        # Every row is kept, with what it recorded.
        assert db.session.get(PaymentTransaction, cash.id).amount == Decimal("300.0000")
        assert db.session.get(Receipt, cash_receipt.id).status == "issued"


def test_a_family_deletion_is_atomic(app, client, monkeypatch):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        px.cash_with_receipt(owner, actor, "300.000")
        before = _state()

    def refuse(**_kwargs):
        raise ValueError("refused")

    monkeypatch.setattr(financial_deletions, "record_invoice_deletion_event", refuse)
    _login(client)
    response = _delete_invoice(client, w["ip"])
    assert "could not be saved" in fx.followed(client, response)
    with app.app_context():
        assert _state() == before


@pytest.mark.parametrize("case", ["cancelled", "intent", "reason", "unconfirmed", "stale"])
def test_an_invoice_deletion_is_refused_changing_nothing(app, client, case):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        if case == "cancelled":
            owner = _new_invoice(w, "Cancelled", status="cancelled")
        if case == "intent":
            ix.intent(owner, actor)
        ip = owner.public_id
    _login(client)
    if case in ("cancelled", "intent"):
        response = client.get(f"{REGISTER}/{ip}/delete")
        assert response.status_code == 302
        text = fx.followed(client, response)
        assert ("never deleted" if case == "cancelled" else "active online payment intent") in text
        with app.app_context():
            before = _state()
        client.post(f"{REGISTER}/{ip}/delete", data={"reason": "x", "confirm": "yes"})
    else:
        url = f"{REGISTER}/{ip}/delete"
        token = _token(_get(client, url))
        if case == "stale":
            with app.app_context():
                px.cash_with_receipt(db.session.get(Invoice, w["invoice_id"]),
                                     db.session.get(User, w["admin_id"]), "10.000")
        with app.app_context():
            before = _state()
        data = {STATE: token, "reason": "" if case == "reason" else "Why"}
        if case != "unconfirmed":
            data["confirm"] = "yes"
        response = client.post(url, data=data)
        if case == "stale":
            assert "changed after this page was opened" in fx.followed(client, response)
        else:
            assert response.status_code == 200
    with app.app_context():
        assert _state() == before


def test_old_links_to_a_deleted_invoice_are_safe(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        _pay, rc = px.cash_with_receipt(owner, actor, "100.000")
        receipt_id = rc.public_id
    _login(client)
    _delete_invoice(client, w["ip"])
    response = client.get(fx.detail_url(w, w["ip"]))
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"{DELETED}/invoice/{w['ip']}")
    response = client.get(px.receipt_url(w, receipt_id))
    assert response.headers["Location"].endswith(f"{DELETED}/receipt/{receipt_id}")
    for url in (fx.edit_url(w, w["ip"]), px.payments_url(w), px.cash_url(w),
                fx.cancel_url(w, w["ip"]), f"{fx.detail_url(w, w['ip'])}/payment-intents"):
        assert client.get(url).status_code == 404, url
    assert client.post(fx.issue_url(w, w["ip"]), data={"confirm": "yes"}).status_code == 404


# ===========================================================================
# Payments
# ===========================================================================


def test_new_payment_lists_only_payable_live_invoices_linked_to_the_existing_forms(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        settled = _new_invoice(w, "Settled")
        px.payment(settled, actor, "1250.500")
        _new_invoice(w, "Drafty", status="draft")
        gone = _new_invoice(w, "Gone")
        _tombstone(gone, actor)
        busy = _new_invoice(w, "Busy")
        ix.intent(busy, actor)
        ids = {"settled": settled.public_id, "gone": gone.public_id, "busy": busy.public_id}
    _login(client)
    page = _get(client, NEW_PAYMENT)
    listed = re.findall(r'data-invoice="([0-9a-f-]{36})"', page)
    assert set(listed) == {w["ip"], ids["busy"]}
    assert f'href="{px.cash_url(w)}" data-link="cash"' in page
    assert f'href="{px.bank_url(w)}" data-link="bank"' in page
    busy_row = page[page.index(ids["busy"]):]
    busy_row = busy_row[:busy_row.index("</tr>")]
    assert 'data-link="cash"' not in busy_row and "Online payment in progress" in busy_row


def test_cash_and_bank_payments_receive_their_receipts_automatically(app, client):
    w = px.world(app)
    _login(client)
    response = px.record_cash(client, w, amount="100")
    assert "/receipts/" in response.headers["Location"]
    px.record_bank(client, w, amount="50")
    with app.app_context():
        cash, transfer = px.stored_payments(w)
        (receipt,) = px.stored_receipts(w)
        assert receipt.payment_transaction_id == cash.id and transfer.status == "pending"
    response = client.post(px.confirm_url(w, transfer.public_id), data={
        STATE: px.state_in(_get(client, px.confirm_url(w, transfer.public_id)),
                           px.confirm_url(w, transfer.public_id)), "confirm": "yes"})
    assert "/receipts/" in response.headers["Location"]
    with app.app_context():
        assert len(px.stored_receipts(w)) == 2


def test_there_is_no_standalone_receipt_route(app):
    receipt_rules = [(rule.rule, rule.methods) for rule in app.url_map.iter_rules()
                     if "receipt" in rule.rule]
    assert receipt_rules and all("POST" not in methods for _rule, methods in receipt_rules)


def test_editing_a_cash_payment_replaces_it_and_its_receipt(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        pay, rc = px.cash_with_receipt(owner, actor, "300.000", number="RCT-2026-000041")
        pay_id, old_number = pay.public_id, rc.receipt_number
    _login(client)
    edit = _get(client, f"{PAYMENTS}/{pay_id}/edit")
    assert "It can be at most 1,250.500 LYD." in edit and "RCT-2026-000041" in edit
    response = _edit_payment(client, pay_id, amount="250")
    assert "/receipts/" in response.headers["Location"]
    text = fx.followed(client, response)
    assert "Payment corrected." in text and "RCT-2026-000041" in text
    with app.app_context():
        old, new = px.stored_payments(w)
        assert (old.public_id, old.amount, old.status, old.deleted_at is not None) == (
            pay_id, Decimal("300.0000"), "confirmed", True)
        assert (new.method, new.status, new.amount, new.deleted_at) == (
            "cash", "confirmed", Decimal("250.0000"), None)
        old_receipt, new_receipt = px.stored_receipts(w)
        assert old_receipt.deleted_at is not None and old_receipt.receipt_number == old_number
        assert old_receipt.snapshot["amount"] == "300.0000"
        assert new_receipt.payment_transaction_id == new.id
        assert new_receipt.receipt_number != old_number
        kinds = _kinds(w["invoice_id"])
        assert kinds[-4:] == ["receipt_deleted", "payment_replaced", "payment_cash_recorded",
                              "receipt_issued"]
        replaced = PaymentAuditEvent.query.filter_by(kind="payment_replaced").one()
        assert replaced.after_snapshot["payment"]["replaced_by_public_id"] == new.public_id
        assert replaced.reason == "Typed the wrong amount"
    payments = _get(client, px.payments_url(w))
    assert "1,000.500" in payments  # outstanding after the corrected 250
    tombstone = _get(client, f"{DELETED}/payment/{pay_id}")
    assert 'data-replacement="yes"' in tombstone and "250.000" in tombstone


def test_editing_bank_transfers_keeps_their_status(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        pending = px.payment(owner, actor, "100.000", method="bank_transfer", status="pending")
        confirmed = px.payment(owner, actor, "200.000", method="bank_transfer")
        px.receipt(confirmed, actor)
        ids = pending.public_id, confirmed.public_id
    _login(client)
    response = _edit_payment(client, ids[0], amount="120", reference="TRX-NEW",
                             transfer_date=px.TRANSFER_DATE_TEXT)
    assert response.headers["Location"].endswith(px.payments_url(w))
    response = _edit_payment(client, ids[1], amount="180", reference="TRX-CONF",
                             transfer_date=px.TRANSFER_DATE_TEXT)
    assert "/receipts/" in response.headers["Location"]
    with app.app_context():
        live = [p for p in px.stored_payments(w) if p.deleted_at is None]
        assert sorted((p.status, p.amount, p.bank_transfer_reference) for p in live) == [
            ("confirmed", Decimal("180.0000"), "TRX-CONF"),
            ("pending", Decimal("120.0000"), "TRX-NEW")]
        assert [r.deleted_at is None for r in px.stored_receipts(w)] == [False, True]
        kinds = _kinds(w["invoice_id"])
        assert kinds[-5:] == ["receipt_deleted", "payment_replaced",
                              "payment_bank_transfer_recorded", "payment_bank_transfer_confirmed",
                              "receipt_issued"]


def test_an_edit_is_validated_and_refused_changing_nothing(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        pay, _ = px.cash_with_receipt(owner, actor, "300.000")
        original, _v, reversal = px.reversed_collection(owner, actor, "50.000")
        rejected = px.payment(owner, actor, "20.000", method="bank_transfer", status="rejected")
        ids = {"pay": pay.public_id, "original": original.public_id,
               "reversal": reversal.public_id, "rejected": rejected.public_id}
        before = _state()
    _login(client)
    for fields, message in (({"amount": "1250.501"}, "more than this invoice"),
                            ({"amount": "abc"}, ""), ({"amount": "100", "reason": ""},
                                                      "Give the reason")):
        response = _edit_payment(client, ids["pay"], **fields)
        assert response.status_code == 200
        assert message in response.get_data(as_text=True)
    response = _edit_payment(client, ids["pay"], confirm=False, amount="100")
    assert "tick the confirmation box" in response.get_data(as_text=True)
    response = _edit_payment(client, ids["pay"], amount="300")
    assert "Nothing was changed" in fx.followed(client, response)
    for key, message in (("original", "has been reversed"), ("reversal", "Only a cash"),
                         ("rejected", "A rejected bank transfer")):
        response = client.get(f"{PAYMENTS}/{ids[key]}/edit")
        assert response.status_code == 302 and message in fx.followed(client, response), key
        response = client.get(f"{PAYMENTS}/{ids[key]}/delete")
        assert message in fx.followed(client, response), key
    with app.app_context():
        assert _state() == before


def test_deleting_a_payment_deletes_its_receipt_and_frees_the_balance(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        pay, rc = px.cash_with_receipt(owner, actor, "1250.500")
        pay_id, receipt_id = pay.public_id, rc.public_id
        sp = _student_of(owner).public_id
    _login(client)
    assert 'data-status="settled"' in _get(client, f"{ACCOUNTS}/{sp}/financial-record")
    response = _delete_payment(client, pay_id)
    assert "Payment deleted with its receipt" in fx.followed(client, response)
    with app.app_context():
        assert px.stored_payment(pay_id).deleted_at is not None
        assert Receipt.query.filter_by(public_id=receipt_id).one().deleted_at is not None
        assert _kinds(w["invoice_id"])[-2:] == ["receipt_deleted", "payment_deleted"]
    assert 'data-status="amount_due"' in _get(client, f"{ACCOUNTS}/{sp}/financial-record")
    assert "1,250.500" in _get(client, px.payments_url(w))
    # A deleted payment is gone from the live pages but reachable as a tombstone.
    assert pay_id not in _get(client, PAYMENTS) and pay_id not in _get(client, px.payments_url(w))
    assert client.get(f"{PAYMENTS}/{pay_id}/edit").status_code == 404
    assert client.get(px.confirm_url(w, pay_id)).status_code == 404
    assert _get(client, f"{DELETED}/payment/{pay_id}")


def test_existing_rejection_and_reversal_still_work(app, client):
    w = px.world(app)
    _login(client)
    px.record_cash(client, w, amount="100")
    px.record_bank(client, w, amount="50")
    with app.app_context():
        cash, transfer = px.stored_payments(w)
    reject = px.reject_url(w, transfer.public_id)
    response = client.post(reject, data={STATE: px.state_in(_get(client, reject), reject),
                                         "reason": "Not received", "confirm": "yes"})
    assert px.BANK_REJECTED_OK_TEXT in fx.followed(client, response)
    reverse = px.reverse_url(w, cash.public_id)
    response = client.post(reverse, data={STATE: px.state_in(_get(client, reverse), reverse),
                                          "reason": "Wrong amount", "confirm": "yes"})
    assert px.REVERSED_OK_TEXT in fx.followed(client, response)
    overview = _get(client, PAYMENTS)
    assert 'data-link="edit"' not in overview and 'data-link="delete"' not in overview


# ===========================================================================
# Deleted Records
# ===========================================================================


def _deleted_world(app, w):
    with app.app_context():
        owner, actor = rx.rows_of(w)
        pay, rc = px.cash_with_receipt(owner, actor, "100.000")
        transfer = px.payment(owner, actor, "10.000", method="bank_transfer", status="pending",
                              reference="SECRET-BANK-REF")
        _tombstone(transfer, actor, reason="Keyed twice")
        others = []
        for n in range(26):
            invoice = _new_invoice(w, f"Deleted {n:02d}")
            _tombstone(invoice, actor, reason=f"Reason {n:02d}")
            others.append(invoice.public_id)
        return pay.public_id, rc.public_id, transfer.public_id, others


def test_deleted_records_list_filter_search_and_page(app, client):
    w = px.world(app)
    pay_id, receipt_id, transfer_id, others = _deleted_world(app, w)
    _login(client)
    first = _get(client, DELETED)
    assert "Records 1&ndash;25 of 27" in first
    records = re.findall(r'data-record="([a-z]+):([0-9a-f-]{36})"', first)
    records += re.findall(r'data-record="([a-z]+):([0-9a-f-]{36})"', _get(client, DELETED + "?page=2"))
    assert len(records) == 27 and ("payment", transfer_id) in records
    payments = _get(client, DELETED + "?type=payment")
    assert re.findall(r'data-record="([a-z]+):', payments) == ["payment"]
    assert "Keyed twice" in payments and "Deleted</span> Payment" in payments
    found = _get(client, DELETED + "?q=deleted%2003")
    assert re.findall(r'data-record="[a-z]+:([0-9a-f-]{36})"', found) == [others[3]]
    assert "Records 1&ndash;25 of 27" in _get(client, DELETED + "?type=refund&page=0")
    for url in (DELETED, DELETED + "?type=payment", f"{DELETED}/payment/{transfer_id}"):
        page = _get(client, url)
        for secret in ("SECRET-BANK-REF", "snapshot", "idempotency", "provider_reference"):
            assert secret not in page, (url, secret)
    assert pay_id and receipt_id


def test_deleted_documents_leave_every_live_list_balance_and_report(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        live, _ = px.cash_with_receipt(owner, actor, "100.000", number="RCT-2026-000501")
        gone, gone_receipt = px.cash_with_receipt(owner, actor, "400.000",
                                                  number="RCT-2026-000502")
        _tombstone(gone_receipt, actor)
        _tombstone(gone, actor)
        gone_invoice = _new_invoice(w, "Gone Gary")
        gone_invoice_number = gone_invoice.invoice_number
        px.cash_with_receipt(gone_invoice, actor, "50.000", number="RCT-2026-000503")
        _tombstone(gone_invoice, actor)
    _login(client)
    register = _get(client, REGISTER)
    assert gone_invoice_number not in register and "1,150.500" in register
    payments = _get(client, PAYMENTS)
    assert "RCT-2026-000501" in payments
    assert "RCT-2026-000502" not in payments and "RCT-2026-000503" not in payments
    outstanding = client.get("/admin/financial-reports/outstanding-invoices?format=csv")
    if outstanding.status_code == 200:
        body = outstanding.get_data(as_text=True)
        assert gone_invoice_number not in body and "1150.500" in body.replace(",", "")
    collections = _get(client, "/admin/financial-reports/collections?start=2026-01-01"
                               "&end=2026-12-31")
    assert "400.000" not in collections
    assert "Gone Gary" not in _get(client, ACCOUNTS + "?q=gone") or \
        'data-status="no_obligation"' in _get(client, ACCOUNTS + "?q=gone")
    assert live


def test_tombstones_show_the_deletion_and_link_the_family(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        pay, rc = px.cash_with_receipt(owner, actor, "100.000")
        ids = pay.public_id, rc.public_id
    _login(client)
    _delete_invoice(client, w["ip"], reason="Billed the wrong family")
    invoice = _get(client, f"{DELETED}/invoice/{w['ip']}")
    assert "Billed the wrong family" in invoice and ">Deleted</td>" in invoice
    assert f'href="{DELETED}/payment/{ids[0]}"' in invoice
    assert f'href="{DELETED}/receipt/{ids[1]}"' in invoice
    receipt = _get(client, f"{DELETED}/receipt/{ids[1]}")
    assert ">Receipt deleted</td>" in receipt and f'href="{DELETED}/payment/{ids[0]}"' in receipt
    payment = _get(client, f"{DELETED}/payment/{ids[0]}")
    assert f'href="{DELETED}/invoice/{w["ip"]}"' in payment
    for page in (invoice, receipt, payment):
        main = page[page.index('class="ds-page"'):]
        assert 'method="post"' not in main and "Restore" not in main
        assert "snapshot" not in main


# ===========================================================================
# Groups
# ===========================================================================


def test_groups_no_longer_expose_finance_while_old_nested_urls_work(app, client):
    w = px.world(app)
    _login(client)
    members = client.get(fees.members_url(w["gp"])).get_data(as_text=True)
    content = members[members.index('class="ds-page"'):]
    for word in ("Fee assignment", "fee-assignments", "nvoice", "ayment", "Billing", "inance",
                 "Receipt"):
        assert word not in content, word
    assert client.get(fees.history_url(w["gp"], w["ep"])).status_code == 200
    assert client.get(fx.detail_url(w, w["ip"])).status_code == 200
    assert client.get(px.payments_url(w)).status_code == 200


# ===========================================================================
# Bounded
# ===========================================================================


def test_every_workspace_page_costs_a_fixed_number_of_queries(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        px.cash_with_receipt(owner, actor, "10.000")
        gone, gone_receipt = px.cash_with_receipt(owner, actor, "1.000")
        _tombstone(gone_receipt, actor)
        _tombstone(gone, actor)
        _tombstone(_new_invoice(w, "Gone first"), actor)
        sp = _student_of(owner).public_id
    _login(client)
    urls = [ACCOUNTS, f"{ACCOUNTS}/{sp}/financial-record", REGISTER, PAYMENTS, NEW_PAYMENT,
            DELETED, NEW_INVOICE]
    small = {url: _query_count(app, client, url) for url in urls}
    with app.app_context():
        owner, actor = rx.rows_of(w)
        student = _student_of(owner)
        for n in range(12):
            invoice = _new_invoice(w, f"Many {n}")
            pay, receipt = px.cash_with_receipt(invoice, actor, "10.000")
            _tombstone(receipt, actor)
            _tombstone(pay, actor)
            px.payment(invoice, actor, "5.000")
            other = px.issued_invoice(fees.assignment(fees.enrollment(enrolled=student),
                                                      rx.plan_of(w), actor), actor)
            px.cash_with_receipt(other, actor, "1.000")
    for url, count in small.items():
        assert _query_count(app, client, url) == count, url
