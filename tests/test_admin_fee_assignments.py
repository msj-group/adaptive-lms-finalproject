"""Phase 5 / M03 -- the Administrator fee assignment routes.

Authorization, POST-only and CSRF, nested 404s, the assignment lifecycle
(assign, one plan at a time, cancel, replay, assign again), every eligibility
denial, the absence of any automatic change, the Enrollment withdrawal
interaction, the Manage Members link, and the pages' ordering, bounds and cache
headers.
"""

import re

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import update
from sqlalchemy.orm import Query

import tests.fee_assignment_fixtures as fx
import tests.fee_plan_fixtures as plans
from app import create_app
from app.extensions import db
from app.models import (
    AcademicTerm,
    Course,
    Enrollment,
    FeePlan,
    FeePlanItem,
    Group,
    Level,
    StudentFeeAssignment,
    User,
    UserRole,
    UserStatus,
)
from app.services import student_fee_assignment_tokens as tokens


def _world(app, client):
    w = fx.world(app)
    fx.login_as(client, "admin@example.com")
    return w


def _rows(w):
    return fx.stored_assignments(w["ep"])


def _assign_through_route(app, client, w, pp=None):
    response = fx.assign(client, w["gp"], w["ep"], pp or w["pp"])
    assert response.status_code == 302
    with app.app_context():
        row = _rows(w)[-1]
        assert row.status == fx.ASSIGNED
        return row.public_id


def _existing_assignment(app, w, status=fx.ASSIGNED):
    with app.app_context():
        return fx.assignment(
            db.session.get(Enrollment, w["enrollment_id"]),
            db.session.get(FeePlan, w["plan_id"]),
            db.session.get(User, w["admin_id"]),
            status=status,
        ).public_id


def _plan_state(app, pp):
    with app.app_context():
        db.session.expire_all()
        row = FeePlan.query.filter_by(public_id=pp).one()
        items = FeePlanItem.query.filter_by(fee_plan_id=row.id).order_by(FeePlanItem.id).all()
        return (row.status, row.version, row.updated_at, row.first_activated_at,
                [(i.id, i.kind, i.label, i.amount, i.status, i.version, i.updated_at)
                 for i in items])


def _call(client, method, url):
    if method == "post":
        return client.post(url, data={fx.STATE_FIELD: "anything"})
    return client.get(url)


def _every_route(w, ap):
    gp, ep, pp = w["gp"], w["ep"], w["pp"]
    return [
        ("get", fx.history_url(gp, ep)),
        ("get", fx.choices_url(gp, ep)),
        ("get", fx.assign_url(gp, ep, pp)),
        ("post", fx.assign_url(gp, ep, pp)),
        ("post", fx.cancel_url(gp, ep, ap)),
    ]


# ===========================================================================
# Authorization
# ===========================================================================


def test_an_administrator_opens_every_page(app, client):
    w = _world(app, client)
    for url in (fx.history_url(w["gp"], w["ep"]), fx.choices_url(w["gp"], w["ep"]),
                fx.assign_url(w["gp"], w["ep"], w["pp"])):
        assert client.get(url).status_code == 200, url


@pytest.mark.parametrize(
    "role", [UserRole.STUDENT.value, UserRole.TEACHER.value, UserRole.RESEARCHER.value]
)
def test_every_other_role_is_forbidden_everywhere(app, client, role):
    w = fx.world(app)
    ap = _existing_assignment(app, w)
    before = fx.assignments_snapshot(app, w["ep"])
    with app.app_context():
        fx.user(f"other-{role}@example.com", role)
    fx.login_as(client, f"other-{role}@example.com")
    for method, url in _every_route(w, ap):
        assert _call(client, method, url).status_code == 403, url
    assert fx.assignments_snapshot(app, w["ep"]) == before


def test_anonymous_requests_are_sent_to_login(app, client):
    w = fx.world(app)
    ap = _existing_assignment(app, w)
    for method, url in _every_route(w, ap):
        response = _call(client, method, url)
        assert response.status_code == 302, url
        assert "/auth/login" in response.headers["Location"]


def test_a_suspended_administrator_reaches_nothing(app, client):
    w = _world(app, client)
    ap = _existing_assignment(app, w)
    before = fx.assignments_snapshot(app, w["ep"])
    with app.app_context():
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
        db.session.commit()
    fx.fresh_identity()
    for method, url in _every_route(w, ap):
        response = _call(client, method, url)
        assert response.status_code == 302, url
        assert "/auth/login" in response.headers["Location"]
    assert fx.assignments_snapshot(app, w["ep"]) == before


def test_no_other_portal_has_a_fee_endpoint(app):
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/admin"):
            # "feedback" routes exist elsewhere; a fee route would say fee- or fee_.
            assert not re.search(r"fee[-_]|/fees?(/|$)", rule.rule), rule.rule
            assert "fee_" not in rule.endpoint, rule.endpoint


# ===========================================================================
# POST-only, and CSRF
# ===========================================================================


def test_mutations_are_post_only_and_no_route_accepts_delete(app, client):
    w = _world(app, client)
    ap = _existing_assignment(app, w)
    gp, ep, pp = w["gp"], w["ep"], w["pp"]
    assert client.get(fx.cancel_url(gp, ep, ap)).status_code == 405
    assert client.put(fx.cancel_url(gp, ep, ap)).status_code == 405
    for url in (fx.history_url(gp, ep), fx.choices_url(gp, ep)):
        for method in ("post", "put", "delete"):
            assert getattr(client, method)(url).status_code == 405, (method, url)
    assert client.delete(fx.assign_url(gp, ep, pp)).status_code == 405

    base = "/admin/groups/<group_public_id>/enrollments/<enrollment_public_id>"
    # Phase 5 / M04's invoice routes nest below an assignment; they are
    # inventoried by tests/test_admin_invoices.py.
    rules = {
        (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith(base + "/fee-") and "/invoices" not in rule.rule
    }
    assert rules == {
        (base + "/fee-assignments", frozenset({"GET"})),
        (base + "/fee-plans", frozenset({"GET"})),
        (base + "/fee-plans/<plan_public_id>/assign", frozenset({"GET", "POST"})),
        (base + "/fee-assignments/<assignment_public_id>/cancel", frozenset({"POST"})),
    }
    with app.app_context():
        assert [(row.status, row.version) for row in _rows(w)] == [(fx.ASSIGNED, 1)]


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


def test_csrf_is_enforced_on_assignment_and_cancellation(csrf_app):
    client = csrf_app.test_client()
    w = fx.world(csrf_app)
    gp, ep, pp = w["gp"], w["ep"], w["pp"]
    login_page = client.get("/auth/login").get_data(as_text=True)
    client.post("/auth/login", data={"email": "admin@example.com", "password": fx.PW,
                                     "csrf_token": _csrf(login_page)})

    confirm = client.get(fx.assign_url(gp, ep, pp)).get_data(as_text=True)
    token = fx.state_in(confirm, fx.assign_url(gp, ep, pp))
    assert token
    assert client.post(fx.assign_url(gp, ep, pp), data={fx.STATE_FIELD: token}).status_code == 400
    with csrf_app.app_context():
        assert _rows(w) == []
    response = client.post(fx.assign_url(gp, ep, pp),
                           data={fx.STATE_FIELD: token, "csrf_token": _csrf(confirm)})
    assert response.status_code == 302
    with csrf_app.app_context():
        ap = _rows(w)[0].public_id

    history = client.get(fx.history_url(gp, ep)).get_data(as_text=True)
    cancel_token = fx.state_in(history, fx.cancel_url(gp, ep, ap))
    assert cancel_token
    response = client.post(fx.cancel_url(gp, ep, ap), data={fx.STATE_FIELD: cancel_token})
    assert response.status_code == 400
    with csrf_app.app_context():
        assert [(row.status, row.version) for row in _rows(w)] == [(fx.ASSIGNED, 1)]
    response = client.post(fx.cancel_url(gp, ep, ap),
                           data={fx.STATE_FIELD: cancel_token, "csrf_token": _csrf(history)})
    assert response.status_code == 302
    with csrf_app.app_context():
        assert [(row.status, row.version) for row in _rows(w)] == [(fx.CANCELLED, 2)]


# ===========================================================================
# Nested identifiers
# ===========================================================================


def test_foreign_unknown_numeric_and_unavailable_identifiers_are_404(app, client):
    w = _world(app, client)
    gp, ep, pp = w["gp"], w["ep"], w["pp"]
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        world_plan = db.session.get(FeePlan, w["plan_id"])
        other = fx.enrollment()
        other_gp, other_ep = db.session.get(Group, other.group_id).public_id, other.public_id
        teacher = fx.user("corrupt@example.com", UserRole.TEACHER.value)
        corrupted = Enrollment(student_id=teacher.id, group_id=w["group_id"], status=fx.ENROLLED)
        db.session.add(corrupted)
        db.session.commit()
        corrupted_ep = corrupted.public_id
        draft = plans.plan(actor, name="Draft plan")
        plans.item(draft, label="Course")
        archived = plans.plan(actor, name="Archived plan", status=plans.ARCHIVED_STATUS)
        plans.item(archived, label="Course")
        archived_draft = plans.plan(actor, name="Archived draft", status=plans.ARCHIVED_STATUS,
                                    ever_activated=False)
        plans.item(archived_draft, label="Course")
        foreign_ap = fx.assignment(other, world_plan, actor).public_id
        own = fx.assignment(db.session.get(Enrollment, w["enrollment_id"]), world_plan, actor,
                            status=fx.CANCELLED)
        own_ap, own_id = own.public_id, own.id
        unavailable = [draft.public_id, archived.public_id, archived_draft.public_id]
        token = tokens.make_token(tokens.PURPOSE_ASSIGN, actor_public_id=w["admin_public_id"],
                                  enrollment_public_id=ep, plan_public_id=draft.public_id,
                                  plan_version=1)
        before = fx.snapshot(StudentFeeAssignment.query.order_by(StudentFeeAssignment.id).all())

    missing = fx.MISSING
    assign_targets = [
        (gp, ep, missing), *[(gp, ep, plan_pp) for plan_pp in unavailable],
        (gp, ep, str(w["plan_id"])), (gp, ep, "x" * 300), (other_gp, ep, pp),
        (gp, other_ep, pp), (gp, corrupted_ep, pp), (missing, ep, pp),
    ]
    pages = [
        fx.history_url(missing, ep), fx.history_url(gp, missing), fx.history_url(other_gp, ep),
        fx.history_url(gp, other_ep), fx.history_url(gp, corrupted_ep),
        fx.history_url(str(w["group_id"]), ep), fx.history_url(gp, str(w["enrollment_id"])),
        fx.history_url(gp, "x" * 300), fx.choices_url(other_gp, ep),
        fx.choices_url(gp, corrupted_ep),
    ] + [fx.assign_url(*target) for target in assign_targets]
    for url in pages:
        assert client.get(url).status_code == 404, url

    posts = [fx.assign_url(*target) for target in assign_targets] + [
        fx.cancel_url(gp, ep, foreign_ap), fx.cancel_url(gp, ep, missing),
        fx.cancel_url(gp, ep, str(own_id)), fx.cancel_url(other_gp, ep, own_ap),
        fx.cancel_url(gp, other_ep, own_ap), fx.cancel_url(gp, corrupted_ep, own_ap),
    ]
    for url in posts:
        assert client.post(url, data={fx.STATE_FIELD: token}).status_code == 404, url
    with app.app_context():
        db.session.expire_all()
        after = fx.snapshot(StudentFeeAssignment.query.order_by(StudentFeeAssignment.id).all())
        assert after == before


# ===========================================================================
# Assigning, one at a time, cancelling and assigning again
# ===========================================================================


def test_the_confirmation_page_shows_the_exact_frozen_plan(app, client):
    w = _world(app, client)
    url = fx.assign_url(w["gp"], w["ep"], w["pp"])
    html = fx.page(client, url)
    for text in ("Standard plan", "Registration", "50.000", "1,200.500", "1,250.500",
                 "Student One", "LYD"):
        assert text in html, text
    assert fx.state_in(html, url)
    assert not re.search(r"/(groups|enrollments|fee-plans|fee-assignments)/\d+[/\"?]", html)
    with app.app_context():
        assert _rows(w) == []


def test_an_administrator_assigns_an_active_plan_to_an_active_enrollment(app, client):
    w = _world(app, client)
    plan_before = _plan_state(app, w["pp"])
    response = fx.assign(client, w["gp"], w["ep"], w["pp"])
    assert response.status_code == 302
    assert response.headers["Location"].endswith(fx.history_url(w["gp"], w["ep"]))
    html = fx.followed(client, response)
    assert fx.ASSIGNED_OK_TEXT in html
    assert "Standard plan" in html and "1,250.500" in html and ">Assigned<" in html

    with app.app_context():
        (row,) = _rows(w)
        assert (row.enrollment_id, row.fee_plan_id) == (w["enrollment_id"], w["plan_id"])
        assert row.status == fx.ASSIGNED and row.version == 1
        assert row.assigned_by_id == w["admin_id"]
        assert row.cancelled_at is None and row.cancelled_by_id is None
        assert row.assigned_at == row.created_at == row.updated_at
        assert row.assigned_at.microsecond == 0 and len(row.public_id) == 36
    assert _plan_state(app, w["pp"]) == plan_before


def test_an_enrollment_has_one_assigned_plan_at_a_time(app, client):
    w = _world(app, client)
    gp, ep = w["gp"], w["ep"]
    with app.app_context():
        second_pp = fx.active_plan(db.session.get(User, w["admin_id"]), name="Second").public_id
    early_token = fx.assign_token(client, gp, ep, second_pp)
    assert early_token
    _assign_through_route(app, client, w)

    for url in (fx.choices_url(gp, ep), fx.assign_url(gp, ep, second_pp)):
        response = client.get(url)
        assert response.status_code == 302, url
        assert response.headers["Location"].endswith(fx.history_url(gp, ep))
        assert fx.ALREADY_ASSIGNED_TEXT in fx.followed(client, response)

    response = fx.assign(client, gp, ep, second_pp, token=early_token)
    assert response.status_code == 302
    html = fx.followed(client, response)
    assert fx.ALREADY_ASSIGNED_TEXT in html or fx.STALE_TEXT in html
    assert "Assign a fee plan" not in html
    with app.app_context():
        assert [(row.status, row.fee_plan_id) for row in _rows(w)] == [
            (fx.ASSIGNED, w["plan_id"])
        ]


def test_cancellation_keeps_the_row_and_records_who_and_when(app, client):
    w = _world(app, client)
    ap = _assign_through_route(app, client, w)
    with app.app_context():
        (assigned,) = fx.snapshot(_rows(w))
    plan_before = _plan_state(app, w["pp"])

    response = fx.cancel(client, w["gp"], w["ep"], ap)
    assert response.status_code == 302
    html = fx.followed(client, response)
    assert fx.CANCELLED_OK_TEXT in html and ">Cancelled<" in html
    assert fx.cancel_url(w["gp"], w["ep"], ap) not in html
    with app.app_context():
        (row,) = _rows(w)
        assert row.public_id == ap and row.status == fx.CANCELLED and row.version == 2
        assert row.cancelled_by_id == w["admin_id"]
        assert row.cancelled_at >= row.assigned_at and row.updated_at == row.cancelled_at
        assert row.cancelled_at.microsecond == 0
        assert (row.enrollment_id, row.fee_plan_id, row.assigned_at, row.assigned_by_id,
                row.created_at) == (assigned[1], assigned[2], assigned[5], assigned[6],
                                    assigned[9])
    assert _plan_state(app, w["pp"]) == plan_before


def test_a_replayed_cancellation_changes_nothing(app, client):
    w = _world(app, client)
    ap = _assign_through_route(app, client, w)
    token = fx.cancel_token(client, w["gp"], w["ep"], ap)
    assert fx.cancel(client, w["gp"], w["ep"], ap, token=token).status_code == 302
    before = fx.assignments_snapshot(app, w["ep"])
    for replay in (token, "forged", ""):
        response = fx.cancel(client, w["gp"], w["ep"], ap, token=replay)
        assert response.status_code == 302
        assert fx.ALREADY_CANCELLED_TEXT in fx.followed(client, response)
        assert fx.assignments_snapshot(app, w["ep"]) == before


def test_the_same_plan_can_be_assigned_again_as_a_new_row(app, client):
    w = _world(app, client)
    first = _assign_through_route(app, client, w)
    assert fx.cancel(client, w["gp"], w["ep"], first).status_code == 302
    cancelled = fx.assignments_snapshot(app, w["ep"])
    second = _assign_through_route(app, client, w)
    assert second != first
    with app.app_context():
        rows = _rows(w)
        assert fx.snapshot(rows[:1]) == cancelled
        assert [(row.status, row.fee_plan_id, row.version) for row in rows] == [
            (fx.CANCELLED, w["plan_id"], 2), (fx.ASSIGNED, w["plan_id"], 1)
        ]
    html = fx.page(client, fx.history_url(w["gp"], w["ep"]))
    assert html.index(">Assigned<") < html.index(">Cancelled<")
    assert fx.cancel_url(w["gp"], w["ep"], second) in html
    assert fx.cancel_url(w["gp"], w["ep"], first) not in html


def test_a_different_plan_can_be_assigned_after_cancellation(app, client):
    w = _world(app, client)
    first = _assign_through_route(app, client, w)
    assert fx.cancel(client, w["gp"], w["ep"], first).status_code == 302
    with app.app_context():
        other = fx.active_plan(db.session.get(User, w["admin_id"]), name="Other plan")
        other_pp, other_id = other.public_id, other.id
    _assign_through_route(app, client, w, pp=other_pp)
    with app.app_context():
        assert [(row.status, row.fee_plan_id) for row in _rows(w)] == [
            (fx.CANCELLED, w["plan_id"]), (fx.ASSIGNED, other_id)
        ]


# ===========================================================================
# Denials
# ===========================================================================


def _make_ineligible(w, condition):
    if condition == "student_suspended":
        db.session.execute(update(User).where(User.id == w["student_id"]).values(
            status=UserStatus.SUSPENDED.value))
    elif condition == "enrollment_withdrawn":
        db.session.execute(update(Enrollment).where(Enrollment.id == w["enrollment_id"]).values(
            status=fx.WITHDRAWN))
    else:
        model, key = {
            "group_archived": (Group, "group_id"),
            "course_archived": (Course, "course_id"),
            "level_archived": (Level, "level_id"),
            "term_archived": (AcademicTerm, "term_id"),
        }[condition]
        db.session.execute(update(model).where(model.id == w[key]).values(status=fx.ARCHIVED))
    db.session.commit()


_DENIAL_TEXT = {
    "student_suspended": fx.STUDENT_INACTIVE_TEXT,
    "enrollment_withdrawn": fx.ENROLLMENT_INACTIVE_TEXT,
    "group_archived": fx.ACADEMIC_INACTIVE_TEXT,
    "course_archived": fx.ACADEMIC_INACTIVE_TEXT,
    "level_archived": fx.ACADEMIC_INACTIVE_TEXT,
    "term_archived": fx.ACADEMIC_INACTIVE_TEXT,
}


@pytest.mark.parametrize("condition", sorted(_DENIAL_TEXT))
def test_an_ineligible_enrollment_cannot_receive_a_plan(app, client, condition):
    w = _world(app, client)
    gp, ep, pp = w["gp"], w["ep"], w["pp"]
    token = fx.assign_token(client, gp, ep, pp)
    assert token
    with app.app_context():
        _make_ineligible(w, condition)

    for url in (fx.choices_url(gp, ep), fx.assign_url(gp, ep, pp)):
        response = client.get(url)
        assert response.status_code == 302, url
        assert _DENIAL_TEXT[condition] in fx.followed(client, response)
    response = fx.assign(client, gp, ep, pp, token=token)
    assert response.status_code == 302
    assert _DENIAL_TEXT[condition] in fx.followed(client, response)
    history = fx.page(client, fx.history_url(gp, ep))
    assert _DENIAL_TEXT[condition] in history and "Assign a fee plan" not in history
    with app.app_context():
        assert _rows(w) == []


@pytest.mark.parametrize("state", ["draft", "archived", "archived_draft"])
def test_a_plan_that_is_not_active_is_neither_listed_nor_assignable(app, client, state):
    w = _world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        row = plans.plan(actor, name=f"Unavailable {state}",
                         status=plans.DRAFT if state == "draft" else plans.ARCHIVED_STATUS,
                         ever_activated=None if state != "archived_draft" else False)
        plans.item(row, label="Course")
        token = tokens.make_token(tokens.PURPOSE_ASSIGN, actor_public_id=w["admin_public_id"],
                                  enrollment_public_id=w["ep"], plan_public_id=row.public_id,
                                  plan_version=row.version)
        upp = row.public_id
    assert f"Unavailable {state}" not in fx.page(client, fx.choices_url(w["gp"], w["ep"]))
    assert client.get(fx.assign_url(w["gp"], w["ep"], upp)).status_code == 404
    assert fx.assign(client, w["gp"], w["ep"], upp, token=token).status_code == 404
    with app.app_context():
        assert _rows(w) == []


def test_a_plan_archived_after_the_page_opened_is_not_assigned(app, client):
    w = _world(app, client)
    token = fx.assign_token(client, w["gp"], w["ep"], w["pp"])
    assert plans.archive(client, w["pp"]).status_code == 302
    assert fx.assign(client, w["gp"], w["ep"], w["pp"], token=token).status_code == 404
    with app.app_context():
        assert _rows(w) == []


def test_a_plan_archived_and_reactivated_since_the_page_opened_is_stale(app, client):
    w = _world(app, client)
    token = fx.assign_token(client, w["gp"], w["ep"], w["pp"])
    assert plans.archive(client, w["pp"]).status_code == 302
    assert plans.reactivate(client, w["pp"]).status_code == 302
    response = fx.assign(client, w["gp"], w["ep"], w["pp"], token=token)
    assert response.status_code == 302
    assert fx.STALE_TEXT in fx.followed(client, response)
    with app.app_context():
        assert _rows(w) == []
    _assign_through_route(app, client, w)


@pytest.mark.parametrize("defect", ["empty", "duplicate_labels", "all_removed", "too_many"])
def test_an_active_plan_without_a_valid_item_set_cannot_be_assigned(app, client, defect):
    w = _world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        row = plans.plan(actor, name="Defective", status=plans.ACTIVE, version=2)
        if defect == "duplicate_labels":
            plans.item(row, label="Fee")
            plans.item(row, label="fee", kind="registration")
        elif defect == "all_removed":
            plans.item(row, label="Gone", status=plans.ITEM_REMOVED)
        elif defect == "too_many":
            for index in range(21):
                plans.item(row, label=f"Item {index}")
        token = tokens.make_token(tokens.PURPOSE_ASSIGN, actor_public_id=w["admin_public_id"],
                                  enrollment_public_id=w["ep"], plan_public_id=row.public_id,
                                  plan_version=2)
        dpp = row.public_id

    response = client.get(fx.assign_url(w["gp"], w["ep"], dpp))
    assert response.status_code == 302
    assert response.headers["Location"].endswith(fx.choices_url(w["gp"], w["ep"]))
    assert fx.ITEMS_INVALID_TEXT in fx.followed(client, response)
    response = fx.assign(client, w["gp"], w["ep"], dpp, token=token)
    assert response.status_code == 302
    assert fx.ITEMS_INVALID_TEXT in fx.followed(client, response)
    with app.app_context():
        assert _rows(w) == []


# ===========================================================================
# Nothing changes an assignment automatically
# ===========================================================================


def test_archiving_the_assigned_plan_changes_no_assignment(app, client):
    w = _world(app, client)
    ap = _assign_through_route(app, client, w)
    before = fx.assignments_snapshot(app, w["ep"])
    assert plans.archive(client, w["pp"]).status_code == 302
    assert fx.assignments_snapshot(app, w["ep"]) == before
    history = fx.page(client, fx.history_url(w["gp"], w["ep"]))
    assert ">Assigned<" in history and ">Archived<" in history
    assert fx.cancel(client, w["gp"], w["ep"], ap).status_code == 302
    with app.app_context():
        assert [row.status for row in _rows(w)] == [fx.CANCELLED]


def test_reactivating_an_enrollment_restores_copies_and_creates_nothing(app, client):
    w = _world(app, client)
    with app.app_context():
        fx.teacher_for(db.session.get(Group, w["group_id"]))
    ap = _assign_through_route(app, client, w)
    assert fx.cancel(client, w["gp"], w["ep"], ap).status_code == 302
    assert fx.withdraw(client, w["gp"], w["ep"]).status_code == 302
    before = fx.assignments_snapshot(app, w["ep"])
    assert fx.reactivate(client, w["gp"], w["ep"]).status_code == 302
    with app.app_context():
        assert db.session.get(Enrollment, w["enrollment_id"]).status == fx.ENROLLED
        assert StudentFeeAssignment.query.count() == 1
    assert fx.assignments_snapshot(app, w["ep"]) == before


def test_suspending_the_student_or_archiving_the_group_changes_no_assignment(app, client):
    w = _world(app, client)
    ap = _assign_through_route(app, client, w)
    before = fx.assignments_snapshot(app, w["ep"])
    assert client.post(f"/admin/students/{w['student_public_id']}/toggle-status").status_code == 302
    assert client.post(f"/admin/groups/{w['gp']}/toggle-status").status_code == 302
    with app.app_context():
        assert db.session.get(User, w["student_id"]).status == UserStatus.SUSPENDED.value
        assert db.session.get(Group, w["group_id"]).status == fx.ARCHIVED
    assert fx.assignments_snapshot(app, w["ep"]) == before
    # Cancellation needs no active Student or chain: it corrects a charge.
    history = fx.page(client, fx.history_url(w["gp"], w["ep"]))
    assert fx.cancel_url(w["gp"], w["ep"], ap) in history
    assert fx.cancel(client, w["gp"], w["ep"], ap).status_code == 302
    with app.app_context():
        assert [row.status for row in _rows(w)] == [fx.CANCELLED]


# ===========================================================================
# Enrollment withdrawal
# ===========================================================================


def _record_lock_requests(monkeypatch):
    requested = []
    original = Query.with_for_update

    def spy(self, *args, **kwargs):
        requested.append(self.column_descriptions[0]["entity"].__tablename__)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", spy)
    return requested


def test_withdrawal_is_refused_while_a_plan_is_assigned(app, client, monkeypatch):
    w = _world(app, client)
    _assign_through_route(app, client, w)
    before = fx.assignments_snapshot(app, w["ep"])
    requested = _record_lock_requests(monkeypatch)
    response = fx.withdraw(client, w["gp"], w["ep"])
    locked = requested[:]
    monkeypatch.undo()
    assert response.status_code == 302
    assert response.headers["Location"].endswith(fx.members_url(w["gp"]))
    assert fx.WITHDRAWAL_BLOCKED_TEXT in fx.followed(client, response)
    assert locked == ["groups", "users", "enrollments"]
    with app.app_context():
        assert db.session.get(Enrollment, w["enrollment_id"]).status == fx.ENROLLED
    assert fx.assignments_snapshot(app, w["ep"]) == before


def test_withdrawal_is_allowed_after_explicit_cancellation(app, client, monkeypatch):
    w = _world(app, client)
    ap = _assign_through_route(app, client, w)
    assert fx.cancel(client, w["gp"], w["ep"], ap).status_code == 302
    before = fx.assignments_snapshot(app, w["ep"])
    requested = _record_lock_requests(monkeypatch)
    response = fx.withdraw(client, w["gp"], w["ep"])
    locked = requested[:]
    monkeypatch.undo()
    assert response.status_code == 302
    assert "withdrawn" in fx.followed(client, response)
    assert locked == ["groups", "users", "enrollments"]
    with app.app_context():
        assert db.session.get(Enrollment, w["enrollment_id"]).status == fx.WITHDRAWN
    assert fx.assignments_snapshot(app, w["ep"]) == before


def test_withdrawal_with_only_cancelled_history_is_unaffected(app, client):
    w = _world(app, client)
    _existing_assignment(app, w, status=fx.CANCELLED)
    assert fx.withdraw(client, w["gp"], w["ep"]).status_code == 302
    with app.app_context():
        assert db.session.get(Enrollment, w["enrollment_id"]).status == fx.WITHDRAWN


def test_an_assigned_plan_in_another_enrollment_does_not_block_withdrawal(app, client):
    w = _world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        fx.assignment(fx.enrollment(enrolled=db.session.get(User, w["student_id"])),
                      db.session.get(FeePlan, w["plan_id"]), actor)
    assert fx.withdraw(client, w["gp"], w["ep"]).status_code == 302
    with app.app_context():
        assert db.session.get(Enrollment, w["enrollment_id"]).status == fx.WITHDRAWN


# ===========================================================================
# Manage Members navigation
# ===========================================================================


def test_manage_members_links_every_enrollment_to_its_fee_assignments(app, client):
    w = _world(app, client)
    with app.app_context():
        withdrawn = fx.enrollment(db.session.get(Group, w["group_id"]), status=fx.WITHDRAWN)
        teacher = fx.user("notastudent@example.com", UserRole.TEACHER.value)
        corrupted = Enrollment(student_id=teacher.id, group_id=w["group_id"], status=fx.ENROLLED)
        db.session.add(corrupted)
        db.session.commit()
        withdrawn_ep, corrupted_ep = withdrawn.public_id, corrupted.public_id

    html = fx.page(client, fx.members_url(w["gp"]))
    for ep in (w["ep"], withdrawn_ep):
        assert f'href="{fx.history_url(w["gp"], ep)}">Fee assignments</a>' in html
    assert fx.history_url(w["gp"], corrupted_ep) not in html

    assert client.post(f"/admin/groups/{w['gp']}/toggle-status").status_code == 302
    archived = fx.page(client, fx.members_url(w["gp"]))
    assert f'href="{fx.history_url(w["gp"], w["ep"])}"' in archived
    history = fx.page(client, fx.history_url(w["gp"], w["ep"]))
    assert f'href="{fx.members_url(w["gp"])}"' in history


# ===========================================================================
# Ordering, bounds and cache headers
# ===========================================================================


def _plan_links(html):
    return re.findall(r'<a href="/admin/fee-plans/[^"]+">([^<]+)</a>', html)


def _record_statements(app, client, url):
    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    sa_event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        html = fx.page(client, url)
    finally:
        sa_event.remove(db.engine, "before_cursor_execute", _rec)
    return html, statements


def test_history_is_newest_first_and_paginated_without_a_count(app, client):
    w = _world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        owner = db.session.get(Enrollment, w["enrollment_id"])
        for index in range(45):
            fee_plan = fx.active_plan(actor, name=f"Plan {index:02d}",
                                      items=(("course", "Course", "10"),))
            fx.assignment(owner, fee_plan, actor,
                          status=fx.ASSIGNED if index == 44 else fx.CANCELLED)
    url = fx.history_url(w["gp"], w["ep"])
    first, statements = _record_statements(app, client, url)
    assert _plan_links(first) == [f"Plan {index:02d}" for index in range(44, 24, -1)]
    assert "page=2" in first and "Previous" not in first
    assert first.count("Cancel assignment") == 1
    assert not any("count(" in s.lower() for s in statements)
    assert any("FROM student_fee_assignments" in s and "LIMIT" in s for s in statements)

    second = fx.page(client, url + "?page=2")
    assert _plan_links(second) == [f"Plan {index:02d}" for index in range(24, 4, -1)]
    third = fx.page(client, url + "?page=3")
    assert _plan_links(third) == [f"Plan {index:02d}" for index in range(4, -1, -1)]
    assert "page=4" not in third and "Previous" in third
    for bad in ("?page=99", "?page=abc", "?page=-1", "?page=0"):
        assert _plan_links(fx.page(client, url + bad))[0] == "Plan 44", bad


def test_the_choice_page_lists_active_plans_only(app, client):
    w = _world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        for name, status, ever in (("Draft plan", plans.DRAFT, None),
                                   ("Archived plan", plans.ARCHIVED_STATUS, None),
                                   ("Archived draft", plans.ARCHIVED_STATUS, False)):
            plans.item(plans.plan(actor, name=name, status=status, ever_activated=ever))
        empty_pp = plans.plan(actor, name="Empty active", status=plans.ACTIVE).public_id
    html = fx.page(client, fx.choices_url(w["gp"], w["ep"]))
    names = re.findall(r'<td style="padding: var\(--space-3\)"><strong>([^<]+)</strong>', html)
    assert names == ["Empty active", "Standard plan"]
    assert fx.assign_url(w["gp"], w["ep"], w["pp"]) in html
    assert fx.assign_url(w["gp"], w["ep"], empty_pp) not in html
    assert "Cannot be assigned" in html and "1,250.500" in html


def test_the_choice_page_is_paginated_without_a_count(app, client):
    w = _world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        for index in range(21):
            fx.active_plan(actor, name=f"Extra {index:02d}", items=(("course", "Course", "5"),))
    url = fx.choices_url(w["gp"], w["ep"])
    first, statements = _record_statements(app, client, url)
    pattern = r'<td style="padding: var\(--space-3\)"><strong>([^<]+)</strong>'
    assert re.findall(pattern, first) == [f"Extra {index:02d}" for index in range(20, 0, -1)]
    assert "page=2" in first
    assert not any("count(" in s.lower() for s in statements)
    assert re.findall(pattern, fx.page(client, url + "?page=2")) == ["Extra 00", "Standard plan"]
    assert re.findall(pattern, fx.page(client, url + "?page=7"))[0] == "Extra 20"


def _select_count(app, client, url):
    _, statements = _record_statements(app, client, url)
    return len([s for s in statements if s.upper().startswith("SELECT")])


def test_page_costs_do_not_grow_with_history_or_catalogue_size(app, client):
    w = _world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        owner = db.session.get(Enrollment, w["enrollment_id"])
        fx.assignment(owner, db.session.get(FeePlan, w["plan_id"]), actor, status=fx.CANCELLED)
    history_url, choices_url = fx.history_url(w["gp"], w["ep"]), fx.choices_url(w["gp"], w["ep"])
    small = (_select_count(app, client, history_url), _select_count(app, client, choices_url))
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        owner = db.session.get(Enrollment, w["enrollment_id"])
        others = [fx.admin(f"canceller{index}@example.com") for index in range(3)]
        for index in range(30):
            fee_plan = fx.active_plan(actor, name=f"Bulk {index}")
            fx.assignment(owner, fee_plan, others[index % 3], status=fx.CANCELLED)
    large = (_select_count(app, client, history_url), _select_count(app, client, choices_url))
    assert large == small


def test_every_response_is_private_and_no_store(app, client):
    w = _world(app, client)
    gp, ep, pp = w["gp"], w["ep"], w["pp"]
    responses = [client.get(url) for url in (
        fx.history_url(gp, ep), fx.history_url(gp, ep) + "?page=3", fx.choices_url(gp, ep),
        fx.assign_url(gp, ep, pp))]
    responses.append(fx.assign(client, gp, ep, pp, token="forged"))
    responses.append(fx.assign(client, gp, ep, pp))
    with app.app_context():
        ap = _rows(w)[0].public_id
    responses += [
        client.get(fx.choices_url(gp, ep)),
        client.get(fx.assign_url(gp, ep, pp)),
        fx.cancel(client, gp, ep, ap, token="forged"),
        fx.cancel(client, gp, ep, ap),
        fx.cancel(client, gp, ep, ap),
    ]
    for response in responses:
        assert response.headers.get("Cache-Control") == "private, no-store"
        assert "Cookie" in response.headers.get("Vary", "")
