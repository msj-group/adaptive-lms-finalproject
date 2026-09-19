"""Phase 5 / M04 -- the Administrator invoice routes.

Authorization, POST-only and CSRF, nested 404s, draft creation and its
eligibility, line editing before and after issue, manual issue and numbering,
cancellation and the read-only cancelled invoice, the fee assignment
cancellation and Enrollment withdrawal interactions, corrections after the
context becomes inactive, navigation, ordering, query bounds, escaping,
internal ids and cache headers.
"""

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import update

import app.blueprints.admin.invoices as routes
import tests.fee_assignment_fixtures as fees
import tests.fee_plan_fixtures as plans
import tests.invoice_fixtures as fx
from app import create_app
from app.extensions import db
from app.models import (
    AcademicTerm,
    Course,
    Enrollment,
    FeePlan,
    FeePlanItem,
    Group,
    InvoiceItem,
    InvoiceNumberSequence,
    Level,
    StudentFeeAssignment,
    User,
    UserRole,
    UserStatus,
)
from app.services import invoice_tokens as tokens
from app.services import student_fee_assignment_tokens as assignment_tokens
from app.services.invoice_audit import build_invoice_snapshot

_LATER = datetime(2026, 5, 4, 9, 0, 0)


def _plan_state(app, pp):
    with app.app_context():
        db.session.expire_all()
        row = FeePlan.query.filter_by(public_id=pp).one()
        items = FeePlanItem.query.filter_by(fee_plan_id=row.id).order_by(FeePlanItem.id).all()
        return (row.status, row.version, row.updated_at,
                [(i.id, i.kind, i.label, i.amount, i.status, i.version, i.updated_at)
                 for i in items])


def _existing(app, w, **kwargs):
    with app.app_context():
        return fx.invoice(db.session.get(StudentFeeAssignment, w["assignment_id"]),
                          db.session.get(User, w["admin_id"]), **kwargs).public_id


def _version(app, ip):
    with app.app_context():
        return fx.stored_invoice(ip).version


def _state(w, ip, version):
    return {"actor_public_id": w["admin_public_id"], "invoice_public_id": ip,
            "invoice_version": version}


def _every_route(w, ip, lp):
    return [
        ("get", fx.invoices_url(w)), ("get", fx.new_url(w)), ("post", fx.new_url(w)),
        ("get", fx.detail_url(w, ip)), ("get", fx.edit_url(w, ip)),
        ("get", fx.line_new_url(w, ip)), ("post", fx.line_new_url(w, ip)),
        ("get", fx.line_edit_url(w, ip, lp)), ("post", fx.line_edit_url(w, ip, lp)),
        ("get", fx.line_remove_url(w, ip, lp)), ("post", fx.line_remove_url(w, ip, lp)),
        ("post", fx.issue_url(w, ip)), ("get", fx.cancel_url(w, ip)),
        ("post", fx.cancel_url(w, ip)),
    ]


def _call(client, method, url):
    if method == "post":
        return client.post(url, data={fx.STATE_FIELD: "anything", "reason": "x", "confirm": "yes"})
    return client.get(url)


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
            status="archived", status_changed_at=_LATER, status_changed_by_id=w["admin_id"],
            updated_at=_LATER, version=3))
    elif condition == "plan_never_activated":
        db.session.execute(update(FeePlan).where(FeePlan.id == w["plan_id"]).values(
            status="draft", first_activated_at=None, first_activated_by_id=None))
    else:
        model, key = {
            "group_archived": (Group, "group_id"),
            "course_archived": (Course, "course_id"),
            "level_archived": (Level, "level_id"),
            "term_archived": (AcademicTerm, "term_id"),
        }[condition]
        db.session.execute(update(model).where(model.id == w[key]).values(status=fees.ARCHIVED))
    db.session.commit()


def _record_statements(client, url):
    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    sa_event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        html = fx.page(client, url)
    finally:
        sa_event.remove(db.engine, "before_cursor_execute", _rec)
    return html, statements


def _select_count(client, url):
    _, statements = _record_statements(client, url)
    return len([s for s in statements if s.upper().startswith("SELECT")])


# ===========================================================================
# Authorization
# ===========================================================================


def test_an_administrator_opens_every_page(app, client):
    w = fx.login_world(app, client)
    assert client.get(fx.invoices_url(w)).status_code == 200
    assert client.get(fx.new_url(w)).status_code == 200
    ip = fx.create_draft(client, w)
    lp = fx.line_ids(app, ip)[0]
    for url in (fx.invoices_url(w), fx.detail_url(w, ip), fx.edit_url(w, ip),
                fx.line_new_url(w, ip), fx.line_edit_url(w, ip, lp),
                fx.line_remove_url(w, ip, lp), fx.cancel_url(w, ip)):
        assert client.get(url).status_code == 200, url


@pytest.mark.parametrize(
    "role", [UserRole.STUDENT.value, UserRole.TEACHER.value, UserRole.RESEARCHER.value]
)
def test_every_other_role_is_forbidden_everywhere(app, client, role):
    w = fx.world(app)
    ip = _existing(app, w)
    lp = fx.line_ids(app, ip)[0]
    before = fx.record(app)
    with app.app_context():
        fx.user(f"other-{role}@example.com", role)
    fx.login_as(client, f"other-{role}@example.com")
    for method, url in _every_route(w, ip, lp):
        assert _call(client, method, url).status_code == 403, (method, url)
    assert fx.record(app) == before


def test_anonymous_requests_are_sent_to_login(app, client):
    w = fx.world(app)
    ip = _existing(app, w)
    lp = fx.line_ids(app, ip)[0]
    for method, url in _every_route(w, ip, lp):
        response = _call(client, method, url)
        assert response.status_code == 302, (method, url)
        assert "/auth/login" in response.headers["Location"]


def test_a_suspended_administrator_reaches_nothing(app, client):
    w = fx.login_world(app, client)
    ip = _existing(app, w)
    lp = fx.line_ids(app, ip)[0]
    before = fx.record(app)
    with app.app_context():
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
        db.session.commit()
    fx.fresh_identity()
    for method, url in _every_route(w, ip, lp):
        response = _call(client, method, url)
        assert response.status_code == 302, (method, url)
        assert "/auth/login" in response.headers["Location"]
    assert fx.record(app) == before


def test_no_other_portal_and_no_route_exposes_invoices_events_or_sequences(app):
    for rule in app.url_map.iter_rules():
        text = f"{rule.rule} {rule.endpoint}".lower()
        # Phase 5 / M07's public signed provider webhook is the one non-admin
        # payment route; tests/test_payment_webhooks.py inventories it.
        public_webhook = rule.rule == "/webhooks/payments/mock"
        # ... and the Mock/Sandbox checkout's delivery of that signed webhook.
        webhook_route = public_webhook or rule.rule.endswith("/checkout/webhook")
        if not rule.rule.startswith("/admin"):
            assert "invoice" not in text, rule.rule
            # Phase 5 / M05's manual payments are Administrator-only as well;
            # tests/test_admin_payments.py inventories their routes.
            assert public_webhook or "payment" not in text, rule.rule
        # Speaking already has an unrelated submission "receipt" route.
        for fragment in ("audit", "sequence", "refund") + (() if webhook_route else ("webhook",)):
            assert fragment not in text, (rule.rule, fragment)


# ===========================================================================
# POST-only, and CSRF
# ===========================================================================


def test_the_route_inventory_is_exact_and_mutations_are_post_only(app, client):
    w = fx.login_world(app, client)
    ip = _existing(app, w)
    lp = fx.line_ids(app, ip)[0]
    base = ("/admin/groups/<group_public_id>/enrollments/<enrollment_public_id>"
            "/fee-assignments/<assignment_public_id>/invoices")
    one = base + "/<invoice_public_id>"
    line = one + "/items/<item_public_id>"
    # Phase 5 / M05's payment and receipt routes nest below an invoice; they are
    # inventoried by tests/test_admin_payments.py. Phase 5 / M06's payment
    # intent routes nest there too; tests/test_admin_payment_intents.py
    # inventories them. Phase 5 / M09R's read-only Invoice Register,
    # GET /admin/invoices, is inventoried by tests/test_admin_invoice_register.py.
    rules = {
        (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
        if "/invoices" in rule.rule and "/payments" not in rule.rule
        and "/receipts" not in rule.rule and "/payment-intents" not in rule.rule
        and rule.rule != "/admin/invoices"
    }
    both = frozenset({"GET", "POST"})
    assert rules == {
        (base, frozenset({"GET"})),
        (base + "/new", both),
        (one, frozenset({"GET"})),
        (one + "/edit", frozenset({"GET"})),
        (one + "/items/new", both),
        (line + "/edit", both),
        (line + "/remove", both),
        (one + "/issue", frozenset({"POST"})),
        (one + "/cancel", both),
    }
    before = fx.record(app)
    assert client.get(fx.issue_url(w, ip)).status_code == 405
    for url in (fx.invoices_url(w), fx.detail_url(w, ip), fx.edit_url(w, ip)):
        assert client.post(url).status_code == 405, url
    for _method, url in _every_route(w, ip, lp):
        assert client.delete(url).status_code == 405, url
        assert client.put(url).status_code == 405, url
    assert fx.record(app) == before


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


def test_csrf_is_enforced_on_every_invoice_mutation(csrf_app):
    client = csrf_app.test_client()
    w = fx.world(csrf_app)
    login_page = client.get("/auth/login").get_data(as_text=True)
    client.post("/auth/login", data={"email": "admin@example.com", "password": fx.PW,
                                     "csrf_token": _csrf(login_page)})

    def attempt(url, page_url, data):
        html = client.get(page_url).get_data(as_text=True)
        token = fx.state_in(html, url)
        assert token, url
        before = fx.record(csrf_app)
        assert client.post(url, data=dict(data, state_token=token)).status_code == 400, url
        assert fx.record(csrf_app) == before
        response = client.post(url, data=dict(data, state_token=token, csrf_token=_csrf(html)))
        assert response.status_code == 302, url
        return response

    ip = fx.ip_from(attempt(fx.new_url(w), fx.new_url(w), {}))
    attempt(fx.line_new_url(w, ip), fx.line_new_url(w, ip),
            {"kind": "course", "label": "Books", "amount": "10"})
    attempt(fx.issue_url(w, ip), fx.detail_url(w, ip), {"confirm": "yes"})
    attempt(fx.cancel_url(w, ip), fx.cancel_url(w, ip), {"reason": "Duplicate", "confirm": "yes"})
    with csrf_app.app_context():
        row = fx.stored_invoice(ip)
        assert (row.status, row.version) == ("cancelled", 4)


# ===========================================================================
# Nested identifiers
# ===========================================================================


def test_identifiers_that_do_not_nest_are_404_and_change_nothing(app, client):
    w = fx.login_world(app, client)
    gp, ep, ap = w["gp"], w["ep"], w["ap"]
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        plan = db.session.get(FeePlan, w["plan_id"])
        group = db.session.get(Group, w["group_id"])
        assignment = db.session.get(StudentFeeAssignment, w["assignment_id"])
        own = fx.invoice(assignment, actor)
        own_cancelled = fx.invoice(assignment, actor, status=fx.CANCELLED)
        ip = own.public_id
        own_line = InvoiceItem.query.filter_by(invoice_id=own.id).order_by(InvoiceItem.id).first()
        lp, line_id = own_line.public_id, own_line.id
        other_line = InvoiceItem.query.filter_by(invoice_id=own_cancelled.id).first().public_id

        sibling = fees.enrollment(group)
        sibling_assignment = fees.assignment(sibling, plan, actor)
        sibling_invoice = fx.invoice(sibling_assignment, actor)
        sep, sap, sip = sibling.public_id, sibling_assignment.public_id, sibling_invoice.public_id
        slp = InvoiceItem.query.filter_by(invoice_id=sibling_invoice.id).first().public_id

        elsewhere = fees.enrollment()
        ogp = db.session.get(Group, elsewhere.group_id).public_id
        oep = elsewhere.public_id
        oap = fees.assignment(elsewhere, plan, actor).public_id

        teacher = fx.user("corrupt@example.com", UserRole.TEACHER.value)
        corrupted = Enrollment(student_id=teacher.id, group_id=w["group_id"], status=fees.ENROLLED)
        db.session.add(corrupted)
        db.session.commit()
        cep, cap = corrupted.public_id, fees.assignment(corrupted, plan, actor).public_id
        numeric = {"group": str(w["group_id"]), "enrollment": str(w["enrollment_id"]),
                   "assignment": str(w["assignment_id"]), "invoice": str(own.id),
                   "line": str(line_id)}
    missing, oversized = fx.MISSING, "x" * 300

    def line_urls(g, e, a, i, l):
        target = f"{fx.base_url(g, e, a)}/{i}/items/{l}"
        return [(method, target + suffix) for suffix in ("/edit", "/remove")
                for method in ("get", "post")]

    def invoice_urls(g, e, a, i):
        target = f"{fx.base_url(g, e, a)}/{i}"
        return [("get", target), ("get", target + "/edit"), ("get", target + "/items/new"),
                ("post", target + "/items/new"), ("post", target + "/issue"),
                ("get", target + "/cancel"), ("post", target + "/cancel")] + line_urls(
            g, e, a, i, lp)

    def assignment_urls(g, e, a):
        base = fx.base_url(g, e, a)
        return [("get", base), ("get", base + "/new"), ("post", base + "/new")] + invoice_urls(
            g, e, a, ip)

    targets = []
    for g, e, a in ((ogp, ep, ap), (gp, oep, ap), (gp, sep, ap), (gp, ep, sap), (gp, ep, oap),
                    (missing, ep, ap), (gp, missing, ap), (gp, ep, missing),
                    (numeric["group"], ep, ap), (gp, numeric["enrollment"], ap),
                    (gp, ep, numeric["assignment"]), (gp, ep, oversized), (gp, cep, cap)):
        targets += assignment_urls(g, e, a)
    for i in (sip, missing, numeric["invoice"], oversized):
        targets += invoice_urls(gp, ep, ap, i)
    for l in (slp, other_line, missing, numeric["line"], oversized):
        targets += line_urls(gp, ep, ap, ip, l)

    before = fx.record(app)
    data = dict(fx.line_form(reason="x", token="anything"), confirm="yes")
    for method, url in targets:
        response = client.get(url) if method == "get" else client.post(url, data=data)
        assert response.status_code == 404, (method, url)
    assert fx.record(app) == before


# ===========================================================================
# Draft creation
# ===========================================================================


def test_the_confirmation_page_shows_the_lines_to_copy_and_writes_nothing(app, client):
    w = fx.login_world(app, client)
    html = fx.page(client, fx.new_url(w))
    for text in ("Standard plan", "Registration", "50.000", "1,200.500", "1,250.500",
                 "Student One", "LYD"):
        assert text in html, text
    assert fx.state_in(html, fx.new_url(w))
    assert not re.search(r"/(groups|enrollments|fee-assignments|invoices)/\d+[/\"?]", html)
    assert fx.record(app) == ([], [], [], [])


def test_a_draft_copies_the_assigned_plans_active_items_exactly(app, client):
    w = fx.login_world(app, client)
    with app.app_context():
        plan = db.session.get(FeePlan, w["plan_id"])
        plans.item(plan, label="Old", status=plans.ITEM_REMOVED)
        copied = [(i.kind, i.label, i.amount) for i in FeePlanItem.query.filter_by(
            fee_plan_id=plan.id, status="active").order_by(FeePlanItem.id)]
    plan_before = _plan_state(app, w["pp"])

    response = fx.create(client, w)
    assert response.status_code == 302
    assert fx.CREATED_OK_TEXT in fx.followed(client, response)
    ip = fx.ip_from(response)
    with app.app_context():
        (row,) = fx.stored_invoices(w)
        assert row.public_id == ip
        assert (row.status, row.version, row.invoice_number, row.currency_code) == (
            "draft", 1, None, "LYD")
        assert row.issued_at is None and row.cancelled_at is None
        assert row.created_at == row.updated_at and row.created_at.microsecond == 0
        lines = fx.stored_lines(ip)
        assert [(line.kind, line.label, line.amount) for line in lines] == copied
        assert all(line.status == "active" and line.version == 1
                   and line.created_at == row.created_at for line in lines)
        assert "fee_plan_item_id" not in InvoiceItem.__table__.c
        (entry,) = fx.stored_events(ip)
        assert (entry.kind, entry.actor_id, entry.invoice_version_before,
                entry.invoice_version_after, entry.reason, entry.before_snapshot,
                entry.occurred_at) == (
            "invoice_draft_created", w["admin_id"], None, 1, None, None, row.created_at)
        row = fx.stored_invoice(ip)
        assert entry.after_snapshot == build_invoice_snapshot(row, w["ap"], fx.stored_lines(ip))
        assert entry.after_snapshot["total"] == "1250.5000"
    assert _plan_state(app, w["pp"]) == plan_before


def test_an_assignment_has_one_open_invoice_at_a_time(app, client):
    w = fx.login_world(app, client)
    early = fx.create_token(client, w)
    ip = fx.create_draft(client, w)

    response = client.get(fx.new_url(w))
    assert response.status_code == 302
    assert response.headers["Location"].endswith(fx.invoices_url(w))
    assert fx.OPEN_INVOICE_TEXT in fx.followed(client, response)
    history = fx.page(client, fx.invoices_url(w))
    assert fx.OPEN_INVOICE_TEXT in history and f'href="{fx.new_url(w)}"' not in history

    response = fx.create(client, w, token=early)
    html = fx.followed(client, response)
    assert fx.OPEN_INVOICE_TEXT in html or fx.STALE_TEXT in html
    assert fx.issue(client, w, ip).status_code == 302
    assert fx.OPEN_INVOICE_TEXT in fx.followed(client, client.get(fx.new_url(w)))
    with app.app_context():
        assert len(fx.stored_invoices(w)) == 1


def test_cancelling_an_invoice_allows_a_new_draft_for_the_same_assignment(app, client):
    w = fx.login_world(app, client)
    first = fx.create_draft(client, w)
    assert fx.cancel(client, w, first).status_code == 302
    assert f'href="{fx.new_url(w)}"' in fx.page(client, fx.invoices_url(w))
    second = fx.create_draft(client, w)
    with app.app_context():
        rows = fx.stored_invoices(w)
        assert [(row.public_id, row.status, row.version) for row in rows] == [
            (first, "cancelled", 2), (second, "draft", 1)]
        first_lines = {line.public_id for line in fx.stored_lines(first)}
        second_lines = fx.stored_lines(second)
        assert len(second_lines) == 2 and not first_lines & {l.public_id for l in second_lines}
    history = fx.page(client, fx.invoices_url(w))
    assert history.index(fx.detail_url(w, second)) < history.index(fx.detail_url(w, first))


def test_a_confirmation_page_cannot_be_replayed_once_the_invoice_history_moved_on(
    app, client, monkeypatch
):
    w = fx.login_world(app, client)
    token = fx.create_token(client, w)
    ip = fx.ip_from(fx.create(client, w, token=token))
    later = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=2)).replace(
        microsecond=0)
    monkeypatch.setattr(routes, "_write_moment", lambda: later)
    assert fx.cancel(client, w, ip).status_code == 302
    monkeypatch.undo()
    before = fx.record(app)
    response = fx.create(client, w, token=token)
    assert response.status_code == 302
    assert fx.STALE_TEXT in fx.followed(client, response)
    assert fx.record(app) == before


_CREATE_DENIALS = {
    "student_suspended": fx.STUDENT_INACTIVE_TEXT,
    "enrollment_withdrawn": fx.ENROLLMENT_INACTIVE_TEXT,
    "group_archived": fx.ACADEMIC_INACTIVE_TEXT,
    "course_archived": fx.ACADEMIC_INACTIVE_TEXT,
    "level_archived": fx.ACADEMIC_INACTIVE_TEXT,
    "term_archived": fx.ACADEMIC_INACTIVE_TEXT,
    "assignment_cancelled": fx.ASSIGNMENT_CANCELLED_TEXT,
    "plan_never_activated": fx.PLAN_UNAVAILABLE_TEXT,
}


@pytest.mark.parametrize("condition", sorted(_CREATE_DENIALS))
def test_a_draft_is_created_only_for_an_eligible_assignment(app, client, condition):
    w = fx.login_world(app, client)
    token = fx.create_token(client, w)
    assert token
    with app.app_context():
        _make_inactive(w, condition)
    text = _CREATE_DENIALS[condition]

    response = client.get(fx.new_url(w))
    assert response.status_code == 302
    assert text in fx.followed(client, response)
    history = fx.page(client, fx.invoices_url(w))
    assert text in history and f'href="{fx.new_url(w)}"' not in history
    response = fx.create(client, w, token=token)
    assert response.status_code == 302
    # Cancelling the assignment moved the version the token binds.
    expected = fx.STALE_TEXT if condition == "assignment_cancelled" else text
    assert expected in fx.followed(client, response)
    assert fx.record(app) == ([], [], [], [])


def test_an_archived_plan_still_invoices_the_assignment_that_uses_it(app, client):
    w = fx.login_world(app, client)
    assert plans.archive(client, w["pp"]).status_code == 302
    with app.app_context():
        assert db.session.get(FeePlan, w["plan_id"]).status == "archived"
    ip = fx.create_draft(client, w)
    with app.app_context():
        assert [line.label for line in fx.stored_lines(ip)] == ["Registration", "Course"]


def test_a_plan_with_an_invalid_item_set_is_never_copied(app, client):
    w = fx.login_world(app, client)
    token = fx.create_token(client, w)
    with app.app_context():
        plans.item(db.session.get(FeePlan, w["plan_id"]), label="course", kind="registration")
    response = client.get(fx.new_url(w))
    assert response.status_code == 302
    assert fx.PLAN_ITEMS_INVALID_TEXT in fx.followed(client, response)
    response = fx.create(client, w, token=token)
    assert fx.PLAN_ITEMS_INVALID_TEXT in fx.followed(client, response)
    assert fx.record(app) == ([], [], [], [])


# ===========================================================================
# Editing a draft
# ===========================================================================


def test_draft_lines_are_added_edited_and_removed_with_one_event_each(app, client):
    w = fx.login_world(app, client)
    ip = fx.create_draft(client, w)
    plan_before = _plan_state(app, w["pp"])

    response = fx.add_line(client, w, ip, label="Books", amount="25.500")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(fx.edit_url(w, ip))
    assert fx.LINE_ADDED_TEXT in fx.followed(client, response)
    with app.app_context():
        assert fx.stored_invoice(ip).version == 2
        lines = fx.stored_lines(ip)
        assert (lines[-1].label, lines[-1].amount, lines[-1].status, lines[-1].version) == (
            "Books", Decimal("25.5000"), "active", 1)
        entry = fx.stored_events(ip)[-1]
        assert (entry.kind, entry.invoice_version_before, entry.invoice_version_after,
                entry.reason) == ("invoice_draft_edited", 1, 2, None)
        assert entry.after_snapshot["total"] == "1276.0000"
        books = lines[-1].public_id

    response = fx.edit_line(client, w, ip, books, kind="registration", label="Books and CDs",
                            amount="30")
    assert fx.LINE_SAVED_TEXT in fx.followed(client, response)
    with app.app_context():
        line = fx.stored_lines(ip)[-1]
        assert (line.kind, line.label, line.amount, line.version) == (
            "registration", "Books and CDs", Decimal("30.0000"), 2)
        assert fx.stored_invoice(ip).version == 3

    response = fx.remove_line(client, w, ip, books)
    assert fx.LINE_REMOVED_TEXT in fx.followed(client, response)
    with app.app_context():
        line = fx.stored_lines(ip)[-1]
        row = fx.stored_invoice(ip)
        assert (line.status, line.removed_by_id, line.version, row.version) == (
            "removed", w["admin_id"], 3, 4)
        assert line.removed_at == line.updated_at == row.updated_at
        events = fx.stored_events(ip)
        assert [e.kind for e in events] == ["invoice_draft_created"] + ["invoice_draft_edited"] * 3
        for earlier, later in zip(events, events[1:]):
            assert later.invoice_version_before == earlier.invoice_version_after
            assert later.before_snapshot == earlier.after_snapshot
        row = fx.stored_invoice(ip)
        assert events[-1].after_snapshot == build_invoice_snapshot(row, w["ap"],
                                                                   fx.stored_lines(ip))
    html = fx.page(client, fx.detail_url(w, ip))
    assert "1,250.500" in html and "Removed lines" in html and "Books and CDs" in html
    assert _plan_state(app, w["pp"]) == plan_before


def test_a_line_edit_that_changes_nothing_writes_nothing(app, client):
    w = fx.login_world(app, client)
    ip = fx.create_draft(client, w)
    course = fx.line_ids(app, ip)[1]
    before = fx.record(app)
    response = fx.edit_line(client, w, ip, course, kind="course", label="Course", amount="1200.5")
    assert response.status_code == 302
    assert fx.NO_CHANGES_TEXT in fx.followed(client, response)
    assert fx.record(app) == before


def test_line_form_errors_are_shown_and_write_nothing(app, client):
    w = fx.login_world(app, client)
    ip = fx.create_draft(client, w)
    before = fx.record(app)
    for fields, error in (({"label": "course"}, fx.LABEL_TAKEN_TEXT),
                          ({"amount": "1,0"}, "Do not use commas"),
                          ({"label": "   "}, "Give this item a label."),
                          ({"kind": "discount"}, "registration fee or a course fee"),
                          ({"amount": "12.34567"}, "It is never rounded")):
        response = fx.add_line(client, w, ip, **fields)
        assert response.status_code == 200, fields
        assert error in response.get_data(as_text=True), fields
    course = fx.line_ids(app, ip)[1]
    response = fx.edit_line(client, w, ip, course, kind="course", label="REGISTRATION", amount="5")
    assert response.status_code == 200
    assert fx.LABEL_TAKEN_TEXT in response.get_data(as_text=True)
    assert fx.record(app) == before


def test_an_invoice_keeps_at_least_one_line(app, client):
    w = fx.login_world(app, client)
    ip = fx.create_draft(client, w)
    first, second = fx.line_ids(app, ip)
    assert fx.remove_line(client, w, ip, first).status_code == 302
    before = fx.record(app)
    response = client.get(fx.line_remove_url(w, ip, second))
    assert response.status_code == 302
    assert fx.LAST_LINE_TEXT in fx.followed(client, response)
    token = tokens.make_token(tokens.PURPOSE_ITEM_REMOVE, item_public_id=second,
                              **_state(w, ip, _version(app, ip)))
    response = fx.remove_line(client, w, ip, second, token=token)
    assert fx.LAST_LINE_TEXT in fx.followed(client, response)
    assert fx.record(app) == before


def test_the_active_line_and_row_limits_hold(app, client):
    w = fx.login_world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        assignment = db.session.get(StudentFeeAssignment, w["assignment_id"])
        full = fx.invoice(assignment, actor,
                          lines=[("course", f"Line {index}", "1") for index in range(20)])
        crowded = fx.invoice(assignment, actor, lines=[("course", "Only", "1")])
        for index in range(99):
            fx.line(crowded, label=f"Gone {index}", status=fx.LINE_REMOVED, removed_by=actor)
        cases = [(full.public_id, fx.LINE_LIMIT_TEXT), (crowded.public_id, fx.ROW_LIMIT_TEXT)]
    for ip, text in cases:
        before = fx.record(app)
        response = client.get(fx.line_new_url(w, ip))
        assert response.status_code == 302
        assert text in fx.followed(client, response)
        edit_page = fx.page(client, fx.edit_url(w, ip))
        assert text in edit_page and f'href="{fx.line_new_url(w, ip)}"' not in edit_page
        token = tokens.make_token(tokens.PURPOSE_ITEM_CREATE, **_state(w, ip, 1))
        response = fx.add_line(client, w, ip, token=token, label="Extra")
        assert text in fx.followed(client, response)
        assert fx.record(app) == before


def test_a_draft_edit_carries_no_reason_even_if_one_is_posted(app, client):
    w = fx.login_world(app, client)
    ip = fx.create_draft(client, w)
    assert 'name="reason"' not in fx.page(client, fx.line_new_url(w, ip))
    assert fx.add_line(client, w, ip, label="Books", amount="10", reason="Ignored").status_code == 302
    with app.app_context():
        entry = fx.stored_events(ip)[-1]
        assert (entry.kind, entry.reason) == ("invoice_draft_edited", None)


# ===========================================================================
# Issue, and editing after issue
# ===========================================================================


def test_issuing_allocates_one_permanent_yearly_number(app, client):
    w = fx.login_world(app, client)
    ip = fx.create_draft(client, w)
    detail = fx.page(client, fx.detail_url(w, ip))
    assert "Draft invoice" in detail and "INV-" not in detail and fx.issue_url(w, ip) in detail
    before = fx.record(app)
    response = fx.issue(client, w, ip, confirm=False)
    assert response.status_code == 302
    assert fx.ISSUE_CONFIRM_TEXT in fx.followed(client, response)
    assert fx.record(app) == before

    response = fx.issue(client, w, ip)
    assert response.status_code == 302
    assert fx.ISSUED_OK_TEXT in fx.followed(client, response)
    with app.app_context():
        row = fx.stored_invoice(ip)
        year = fx.center_year(app, row.issued_at)
        number = f"INV-{year:04d}-000001"
        assert (row.status, row.invoice_number, row.version, row.issued_by_id) == (
            "issued", number, 2, w["admin_id"])
        assert row.issued_at == row.updated_at and row.issued_at.microsecond == 0
        assert row.cancelled_at is None
        counter = InvoiceNumberSequence.query.one()
        assert (counter.calendar_year, counter.last_number) == (year, 1)
        entry = fx.stored_events(ip)[-1]
        assert (entry.kind, entry.invoice_version_before, entry.invoice_version_after,
                entry.reason, entry.occurred_at) == ("invoice_issued", 1, 2, None, row.issued_at)
        assert (entry.before_snapshot["status"], entry.before_snapshot["invoice_number"]) == (
            "draft", None)
        assert (entry.after_snapshot["status"], entry.after_snapshot["invoice_number"]) == (
            "issued", number)
        state = [[line.public_id, line.version] for line in fx.stored_lines(ip)]
        sibling = fees.enrollment(db.session.get(Group, w["group_id"]))
        sibling_assignment = fees.assignment(sibling, db.session.get(FeePlan, w["plan_id"]),
                                             db.session.get(User, w["admin_id"]))
        other = dict(w, ep=sibling.public_id, ap=sibling_assignment.public_id,
                     assignment_id=sibling_assignment.id)
    html = fx.page(client, fx.detail_url(w, ip))
    assert f"Invoice {number}" in html and fx.issue_url(w, ip) not in html

    other_ip = fx.create_draft(client, other)
    assert fx.issue(client, other, other_ip).status_code == 302
    with app.app_context():
        assert fx.stored_invoice(other_ip).invoice_number == f"INV-{year:04d}-000002"
        assert InvoiceNumberSequence.query.one().last_number == 2

    before = fx.record(app)
    token = tokens.make_token(tokens.PURPOSE_ISSUE, active_items=state, **_state(w, ip, 2))
    response = fx.issue(client, w, ip, token=token)
    assert fx.NOT_ISSUABLE_TEXT in fx.followed(client, response)
    assert fx.record(app) == before


def test_an_issued_invoice_changes_only_with_a_reason_and_keeps_its_number(app, client):
    w = fx.login_world(app, client)
    ip = fx.create_draft(client, w)
    assert fx.issue(client, w, ip).status_code == 302
    with app.app_context():
        row = fx.stored_invoice(ip)
        issue_record = (row.invoice_number, row.issued_at, row.issued_by_id)
    registration = fx.line_ids(app, ip)[0]
    assert 'name="reason"' in fx.page(client, fx.line_edit_url(w, ip, registration))
    assert "Every change needs a reason" in fx.page(client, fx.edit_url(w, ip))

    before = fx.record(app)
    for reason in (None, "   "):
        response = fx.edit_line(client, w, ip, registration, kind="registration",
                                label="Registration", amount="60", reason=reason)
        assert response.status_code == 200
        assert fx.REASON_REQUIRED_TEXT in response.get_data(as_text=True)
    assert fx.record(app) == before

    response = fx.edit_line(client, w, ip, registration, kind="registration",
                            label="Registration", amount="60",
                            reason="  Corrected\r\nregistration fee  ")
    assert fx.LINE_SAVED_TEXT in fx.followed(client, response)
    with app.app_context():
        entry = fx.stored_events(ip)[-1]
        assert (entry.kind, entry.reason, entry.invoice_version_before,
                entry.invoice_version_after) == (
            "invoice_issued_edited", "Corrected\nregistration fee", 2, 3)
        assert (entry.before_snapshot["total"], entry.after_snapshot["total"]) == (
            "1250.5000", "1260.5000")
        assert entry.before_snapshot["invoice_number"] == entry.after_snapshot["invoice_number"]

    response = fx.add_line(client, w, ip, label="Books", amount="10")
    assert response.status_code == 200
    assert fx.REASON_REQUIRED_TEXT in response.get_data(as_text=True)
    assert fx.add_line(client, w, ip, label="Books", amount="10",
                       reason="Books were missing").status_code == 302
    books = fx.line_ids(app, ip)[-1]
    response = fx.remove_line(client, w, ip, books)
    assert response.status_code == 200
    assert fx.REASON_REQUIRED_TEXT in response.get_data(as_text=True)
    response = fx.remove_line(client, w, ip, books, reason="Books returned")
    assert fx.LINE_REMOVED_TEXT in fx.followed(client, response)

    with app.app_context():
        row = fx.stored_invoice(ip)
        assert (row.invoice_number, row.issued_at, row.issued_by_id) == issue_record
        assert (row.status, row.version) == ("issued", 5)
        events = fx.stored_events(ip)
        assert [e.kind for e in events] == ["invoice_draft_created", "invoice_issued"] + [
            "invoice_issued_edited"] * 3
        assert [e.reason for e in events[2:]] == [
            "Corrected\nregistration fee", "Books were missing", "Books returned"]


# ===========================================================================
# Cancellation
# ===========================================================================


def test_cancellation_needs_a_reason_and_confirmation_and_keeps_every_row(app, client):
    w = fx.login_world(app, client)
    ip = fx.create_draft(client, w)
    before = fx.record(app)
    for kwargs, error in (({"reason": None}, fx.CANCEL_REASON_TEXT),
                          ({"reason": "   "}, fx.CANCEL_REASON_TEXT),
                          ({"confirm": False}, fx.CANCEL_CONFIRM_TEXT)):
        response = fx.cancel(client, w, ip, **kwargs)
        assert response.status_code == 200, kwargs
        assert error in response.get_data(as_text=True), kwargs
    assert fx.record(app) == before

    response = fx.cancel(client, w, ip, reason="Duplicate charge")
    assert fx.CANCELLED_OK_TEXT in fx.followed(client, response)
    with app.app_context():
        row = fx.stored_invoice(ip)
        assert (row.status, row.invoice_number, row.issued_at, row.cancelled_by_id,
                row.version) == ("cancelled", None, None, w["admin_id"], 2)
        assert row.cancelled_at == row.updated_at
        assert fx.financial_record()[1] == before[1]
        entry = fx.stored_events(ip)[-1]
        assert (entry.kind, entry.reason, entry.invoice_version_before,
                entry.invoice_version_after) == ("invoice_cancelled", "Duplicate charge", 1, 2)
        assert (entry.before_snapshot["status"], entry.after_snapshot["status"]) == (
            "draft", "cancelled")
    html = fx.page(client, fx.detail_url(w, ip))
    assert "It and its lines are read-only" in html
    for url in (fx.edit_url(w, ip), fx.cancel_url(w, ip), fx.issue_url(w, ip)):
        assert url not in html, url

    second = fx.create_draft(client, w)
    assert fx.issue(client, w, second).status_code == 302
    assert fx.cancel(client, w, second, reason="Wrong plan").status_code == 302
    third = fx.create_draft(client, w)
    assert fx.issue(client, w, third).status_code == 302
    with app.app_context():
        cancelled, issued = fx.stored_invoice(second), fx.stored_invoice(third)
        year = cancelled.invoice_number[4:8]
        assert (cancelled.status, cancelled.invoice_number) == ("cancelled", f"INV-{year}-000001")
        assert (issued.status, issued.invoice_number) == ("issued", f"INV-{year}-000002")
        assert InvoiceNumberSequence.query.one().last_number == 2


def test_a_cancelled_invoice_is_read_only_everywhere(app, client):
    w = fx.login_world(app, client)
    ip = fx.create_draft(client, w)
    assert fx.issue(client, w, ip).status_code == 302
    assert fx.cancel(client, w, ip).status_code == 302
    lp = fx.line_ids(app, ip)[0]
    before = fx.record(app)

    for url in (fx.edit_url(w, ip), fx.line_new_url(w, ip), fx.line_edit_url(w, ip, lp),
                fx.line_remove_url(w, ip, lp)):
        response = client.get(url)
        assert response.status_code == 302, url
        assert fx.READ_ONLY_TEXT in fx.followed(client, response), url
    response = client.get(fx.cancel_url(w, ip))
    assert fx.ALREADY_CANCELLED_TEXT in fx.followed(client, response)

    with app.app_context():
        version = fx.stored_invoice(ip).version
        active = [[line.public_id, line.version] for line in fx.stored_lines(ip)]
    state = _state(w, ip, version)
    attempts = [
        (fx.line_new_url(w, ip), fx.line_form(reason="x", token=tokens.make_token(
            tokens.PURPOSE_ITEM_CREATE, **state)), fx.READ_ONLY_TEXT),
        (fx.line_edit_url(w, ip, lp), fx.line_form(label="Changed", reason="x",
                                                   token=tokens.make_token(
            tokens.PURPOSE_ITEM_EDIT, item_public_id=lp, **state)), fx.READ_ONLY_TEXT),
        (fx.line_remove_url(w, ip, lp), {fx.STATE_FIELD: tokens.make_token(
            tokens.PURPOSE_ITEM_REMOVE, item_public_id=lp, **state), "reason": "x"},
         fx.READ_ONLY_TEXT),
        (fx.issue_url(w, ip), {fx.STATE_FIELD: tokens.make_token(
            tokens.PURPOSE_ISSUE, active_items=active, **state), "confirm": "yes"},
         fx.NOT_ISSUABLE_TEXT),
        (fx.cancel_url(w, ip), {fx.STATE_FIELD: tokens.make_token(tokens.PURPOSE_CANCEL, **state),
                                "reason": "x", "confirm": "yes"}, fx.ALREADY_CANCELLED_TEXT),
    ]
    for url, data, text in attempts:
        response = client.post(url, data=data)
        assert response.status_code == 302, url
        assert text in fx.followed(client, response), url
    assert fx.record(app) == before


# ===========================================================================
# Fee assignment cancellation and Enrollment withdrawal
# ===========================================================================


@pytest.mark.parametrize("state", ["draft", "issued"])
def test_a_fee_assignment_with_an_open_invoice_cannot_be_cancelled(app, client, state):
    w = fx.login_world(app, client)
    ip = fx.create_draft(client, w)
    if state == "issued":
        assert fx.issue(client, w, ip).status_code == 302
    gp, ep, ap = w["gp"], w["ep"], w["ap"]
    history = fx.page(client, fees.history_url(gp, ep))
    assert fx.ASSIGNMENT_CANCEL_NOTE_TEXT in history
    assert fees.cancel_url(gp, ep, ap) not in history
    assert f'href="{fx.invoices_url(w)}">Invoices</a>' in history

    token = assignment_tokens.make_token(
        assignment_tokens.PURPOSE_CANCEL, actor_public_id=w["admin_public_id"],
        enrollment_public_id=ep, assignment_public_id=ap, assignment_version=1)
    before = fx.record(app)
    assignments_before = fees.assignments_snapshot(app, ep)
    response = client.post(fees.cancel_url(gp, ep, ap), data={fx.STATE_FIELD: token})
    assert response.status_code == 302
    assert fx.ASSIGNMENT_CANCEL_BLOCKED_TEXT in fx.followed(client, response)
    assert fx.record(app) == before
    assert fees.assignments_snapshot(app, ep) == assignments_before


def test_a_fee_assignment_can_be_cancelled_once_its_invoice_is_cancelled(app, client):
    w = fx.login_world(app, client)
    gp, ep, ap = w["gp"], w["ep"], w["ap"]
    ip = fx.create_draft(client, w)
    assert fx.issue(client, w, ip).status_code == 302
    assert fx.cancel(client, w, ip).status_code == 302
    history = fx.page(client, fees.history_url(gp, ep))
    assert fees.cancel_url(gp, ep, ap) in history
    assert fx.ASSIGNMENT_CANCEL_NOTE_TEXT not in history

    response = fees.cancel(client, gp, ep, ap)
    assert fees.CANCELLED_OK_TEXT in fx.followed(client, response)
    with app.app_context():
        assert db.session.get(StudentFeeAssignment, w["assignment_id"]).status == "cancelled"
        row = fx.stored_invoice(ip)
        assert row.status == "cancelled" and row.invoice_number is not None
    invoices = fx.page(client, fx.invoices_url(w))
    assert fx.ASSIGNMENT_CANCELLED_TEXT in invoices and f'href="{fx.new_url(w)}"' not in invoices
    assert client.get(fx.detail_url(w, ip)).status_code == 200
    assert fx.ASSIGNMENT_CANCELLED_TEXT in fx.followed(client, client.get(fx.new_url(w)))


def test_withdrawal_still_needs_the_fee_assignment_cancelled_first(app, client):
    w = fx.login_world(app, client)
    fx.create_draft(client, w)
    before = fx.record(app)
    response = fees.withdraw(client, w["gp"], w["ep"])
    assert response.status_code == 302
    assert fees.WITHDRAWAL_BLOCKED_TEXT in fx.followed(client, response)
    with app.app_context():
        assert db.session.get(Enrollment, w["enrollment_id"]).status == fees.ENROLLED
    assert fx.record(app) == before


# ===========================================================================
# Corrections after the context becomes inactive
# ===========================================================================


@pytest.mark.parametrize(
    "condition",
    ["plan_archived", "student_suspended", "enrollment_withdrawn", "group_archived",
     "course_archived", "level_archived", "term_archived", "assignment_cancelled"],
)
def test_an_existing_invoice_stays_correctable_when_its_context_becomes_inactive(
    app, client, condition
):
    w = fx.login_world(app, client)
    ip = fx.create_draft(client, w)
    with app.app_context():
        _make_inactive(w, condition)

    assert fx.LINE_ADDED_TEXT in fx.followed(
        client, fx.add_line(client, w, ip, label="Books", amount="10"))
    assert fx.ISSUED_OK_TEXT in fx.followed(client, fx.issue(client, w, ip))
    registration = fx.line_ids(app, ip)[0]
    assert fx.LINE_SAVED_TEXT in fx.followed(client, fx.edit_line(
        client, w, ip, registration, kind="registration", label="Registration", amount="55",
        reason="Correction"))
    assert fx.CANCELLED_OK_TEXT in fx.followed(client, fx.cancel(client, w, ip))
    with app.app_context():
        assert [e.kind for e in fx.stored_events(ip)] == [
            "invoice_draft_created", "invoice_draft_edited", "invoice_issued",
            "invoice_issued_edited", "invoice_cancelled"]
    history = fx.page(client, fx.invoices_url(w))
    assert (f'href="{fx.new_url(w)}"' in history) == (condition == "plan_archived")


# ===========================================================================
# Navigation, ordering and bounds
# ===========================================================================


def test_fee_assignments_and_invoices_link_to_each_other(app, client):
    w = fx.login_world(app, client)
    gp, ep = w["gp"], w["ep"]
    with app.app_context():
        cancelled_ap = fees.assignment(
            db.session.get(Enrollment, w["enrollment_id"]), db.session.get(FeePlan, w["plan_id"]),
            db.session.get(User, w["admin_id"]), status=fees.CANCELLED).public_id
    html = fx.page(client, fees.history_url(gp, ep))
    for ap in (w["ap"], cancelled_ap):
        assert f'href="{fx.base_url(gp, ep, ap)}">Invoices</a>' in html

    ip = fx.create_draft(client, w)
    history = fx.page(client, fx.invoices_url(w))
    assert f'href="{fees.history_url(gp, ep)}"' in history
    assert f'href="{fx.detail_url(w, ip)}"' in history
    plan_link = f'href="{plans.detail_url(w["pp"])}"'
    detail = fx.page(client, fx.detail_url(w, ip))
    for link in (fx.invoices_url(w), fx.edit_url(w, ip), fx.cancel_url(w, ip)):
        assert f'href="{link}"' in detail, link
    for url in (fx.invoices_url(w), fx.detail_url(w, ip), fx.edit_url(w, ip), fx.cancel_url(w, ip)):
        assert plan_link in fx.page(client, url), url
    for url in (fx.edit_url(w, ip), fx.cancel_url(w, ip)):
        assert f'href="{fx.detail_url(w, ip)}"' in fx.page(client, url), url
    cancelled = dict(w, ap=cancelled_ap)
    assert fx.ASSIGNMENT_CANCELLED_TEXT in fx.page(client, fx.invoices_url(cancelled))


def test_invoice_history_is_newest_first_and_paginated_without_a_count(app, client):
    w = fx.login_world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        assignment = db.session.get(StudentFeeAssignment, w["assignment_id"])
        for index in range(45):
            fx.invoice(assignment, actor, status=fx.ISSUED if index == 44 else fx.CANCELLED,
                       number=f"INV-2026-{index + 1:06d}", lines=(("course", "Course", "10"),))
    url = fx.invoices_url(w)
    pattern = r'<a href="[^"]+/invoices/[^"]+">(INV-2026-\d{6})</a>'
    first, statements = _record_statements(client, url)
    assert re.findall(pattern, first) == [f"INV-2026-{n:06d}" for n in range(45, 25, -1)]
    assert "page=2" in first and "Previous" not in first
    assert not any("count(" in s.lower() for s in statements)
    assert any("FROM invoices" in s and "LIMIT" in s for s in statements)
    second = fx.page(client, url + "?page=2")
    assert re.findall(pattern, second) == [f"INV-2026-{n:06d}" for n in range(25, 5, -1)]
    third = fx.page(client, url + "?page=3")
    assert re.findall(pattern, third) == [f"INV-2026-{n:06d}" for n in range(5, 0, -1)]
    assert "page=4" not in third and "Previous" in third
    for bad in ("?page=99", "?page=abc", "?page=-1", "?page=0"):
        assert re.findall(pattern, fx.page(client, url + bad))[0] == "INV-2026-000045", bad


def test_the_audit_timeline_is_newest_first_and_paginated_without_a_count(app, client):
    w = fx.login_world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        owner = fx.invoice(db.session.get(StudentFeeAssignment, w["assignment_id"]), actor,
                           version=45)
        for version in range(1, 46):
            fx.event(owner, actor, version)
        ip = owner.public_id
    url = fx.detail_url(w, ip)

    def versions(html):
        return [int(v) for v in re.findall(r"\(version (?:\d+ &rarr; )?(\d+)\)", html)]

    first, statements = _record_statements(client, url)
    assert versions(first) == list(range(45, 25, -1))
    assert "page=2" in first
    assert not any("count(" in s.lower() for s in statements)
    assert any("FROM payment_audit_events" in s and "LIMIT" in s for s in statements)
    assert versions(fx.page(client, url + "?page=2")) == list(range(25, 5, -1))
    assert versions(fx.page(client, url + "?page=3")) == list(range(5, 0, -1))
    for bad in ("?page=99", "?page=abc", "?page=0"):
        assert versions(fx.page(client, url + bad))[0] == 45, bad


def test_page_costs_do_not_grow_with_lines_events_or_history(app, client):
    w = fx.login_world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        assignment = db.session.get(StudentFeeAssignment, w["assignment_id"])
        others = [fx.admin(f"actor{index}@example.com") for index in range(3)]
        small = fx.invoice(assignment, actor)
        fx.line(small, label="Gone", status=fx.LINE_REMOVED, removed_by=actor)
        fx.event(small, actor, 1)
        large = fx.invoice(assignment, actor, version=30,
                           lines=[("course", f"Line {index}", "10") for index in range(20)])
        for index in range(30):
            fx.line(large, label=f"Gone {index}", status=fx.LINE_REMOVED,
                    removed_by=others[index % 3])
        for version in range(1, 31):
            fx.event(large, others[version % 3], version)
        sip, lip = small.public_id, large.public_id
    for view in (fx.detail_url, fx.edit_url, fx.cancel_url):
        assert _select_count(client, view(w, sip)) == _select_count(client, view(w, lip)), view
    history = _select_count(client, fx.invoices_url(w))
    with app.app_context():
        assignment = db.session.get(StudentFeeAssignment, w["assignment_id"])
        others = User.query.filter(User.email.like("actor%")).all()
        for index in range(30):
            fx.invoice(assignment, others[index % 3], status=fx.CANCELLED,
                       number=f"INV-2026-{index + 1:06d}")
    assert _select_count(client, fx.invoices_url(w)) == history


def test_every_response_is_private_and_no_store(app, client):
    w = fx.login_world(app, client)
    responses = [client.get(url) for url in (fx.invoices_url(w), fx.new_url(w),
                                             fx.invoices_url(w) + "?page=9")]
    responses.append(fx.create(client, w, token="forged"))
    created = fx.create(client, w)
    responses.append(created)
    ip = fx.ip_from(created)
    lp = fx.line_ids(app, ip)[0]
    responses += [client.get(url) for url in (
        fx.detail_url(w, ip), fx.detail_url(w, ip) + "?page=2", fx.edit_url(w, ip),
        fx.line_new_url(w, ip), fx.line_edit_url(w, ip, lp), fx.line_remove_url(w, ip, lp),
        fx.cancel_url(w, ip), fx.new_url(w))]
    responses += [
        fx.add_line(client, w, ip, amount="1,0"),
        fx.add_line(client, w, ip),
        fx.edit_line(client, w, ip, lp, kind="registration", label="Registration", amount="51"),
    ]
    responses += [
        fx.remove_line(client, w, ip, fx.line_ids(app, ip)[-1]),
        fx.issue(client, w, ip, confirm=False),
        fx.issue(client, w, ip),
        fx.cancel(client, w, ip, reason=None),
        fx.cancel(client, w, ip),
        fx.cancel(client, w, ip),
        client.get(fx.edit_url(w, ip)),
    ]
    for response in responses:
        assert response.headers.get("Cache-Control") == "private, no-store"
        assert "Cookie" in response.headers.get("Vary", "")


def test_markup_in_labels_and_reasons_is_escaped_everywhere(app, client):
    w = fx.login_world(app, client)
    ip = fx.create_draft(client, w)
    assert fx.add_line(client, w, ip, label="<b>Bold</b>", amount="5").status_code == 302
    assert fx.issue(client, w, ip).status_code == 302
    registration, _course, bold = fx.line_ids(app, ip)
    assert fx.edit_line(client, w, ip, registration, kind="registration", label="Registration",
                        amount="51", reason="<script>alert(1)</script>").status_code == 302
    for url in (fx.detail_url(w, ip), fx.edit_url(w, ip), fx.cancel_url(w, ip),
                fx.line_remove_url(w, ip, bold), fx.line_edit_url(w, ip, bold)):
        html = fx.page(client, url)
        assert "<b>Bold</b>" not in html, url
        assert "<script>alert(1)</script>" not in html, url
    detail = fx.page(client, fx.detail_url(w, ip))
    assert "&lt;b&gt;Bold&lt;/b&gt;" in detail
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in detail


def test_no_internal_identifier_or_out_of_scope_control_reaches_a_page(app, client):
    with app.app_context():
        filler = fx.admin("filler@example.com")
        for index in range(6):
            fx.invoice(fees.assignment(fees.enrollment(),
                                       fees.active_plan(filler, name=f"Filler {index}"), filler),
                       filler)
    w = fx.login_world(app, client)
    ip = fx.create_draft(client, w)
    assert fx.add_line(client, w, ip, label="Books", amount="3").status_code == 302
    with app.app_context():
        row = fx.stored_invoice(ip)
        lines = fx.stored_lines(ip)
        internal = {str(value) for value in (
            row.id, w["assignment_id"], w["enrollment_id"], w["group_id"], w["plan_id"],
            w["admin_id"], w["student_id"], *[line.id for line in lines])}
        lp = lines[0].public_id
    for url in (fx.invoices_url(w), fx.detail_url(w, ip), fx.edit_url(w, ip),
                fx.line_new_url(w, ip), fx.line_edit_url(w, ip, lp),
                fx.line_remove_url(w, ip, lp), fx.cancel_url(w, ip)):
        html = fx.page(client, url)
        assert not re.search(r"/(groups|enrollments|fee-assignments|invoices|items|fee-plans)/\d+[/\"?]",
                             html), url
        for name, value in re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html):
            assert name not in ("id", "invoice_id", "item_id", "student_fee_assignment_id",
                                "fee_plan_id"), (url, name)
            if name not in ("csrf_token", "state_token", "label", "amount"):
                assert value not in internal, (url, name, value)
        for leaked in ("invoice_id", "student_fee_assignment_id", "calendar_year", "last_number",
                       "invoice_number_sequences"):
            assert leaked not in html, (url, leaked)
        body = html.split('class="admin-main"', 1)[1]
        for forbidden in ("Record payment", "Receipt", "Refund", "Pay now", "Delete", "Restore",
                          "Reissue", "Discount", "Due date", "Quantity", 'name="status"',
                          'name="currency', 'name="invoice_number"', 'name="total"'):
            assert forbidden not in body, (url, forbidden)
