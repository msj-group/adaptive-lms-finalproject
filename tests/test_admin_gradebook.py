"""Administrator review of the center's gradebooks (Phase 4 / M08).

An Administrator reads how each gradebook is **configured** and how far
along it is, and can write **nothing**: M08 adds no administrator
mutation endpoint at all, and this suite proves the absence rather than a
disabled control. It equally proves what an Administrator is *not* shown
-- no Student's score, percentage or overall grade, and above all no
Teacher's private comment.
"""

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import AcademicStatus, GradeItem, UserRole
from app.services.grade_queries import PAGE_SIZE
from tests import grade_fixtures as fx
from tests.conftest import make_user

OVERVIEW = "/admin/grades"


def _setup(label="A", released=True, weights=(6000, 4000)):
    teacher, group = fx.setup_group(label, teacher_email=f"t-{label}@example.com")
    alice = fx.enroll(group, f"alice-{label}@example.com", name="Alice Example")
    bob = fx.enroll(group, f"bob-{label}@example.com", name="Bob Example")
    homework = fx.category(group, "Homework", weights[0])
    speaking = fx.category(group, "Speaking", weights[1])
    item, records = fx.item_with_roster(
        homework,
        [alice, bob],
        scores=("18.00", "15.50"),
        released=released,
        title="HW 1",
        max_points="20.00",
    )
    records[0].comment = "A private note to Alice."
    db.session.commit()
    fx.item_with_roster(speaking, [alice, bob], title="Talk 1", max_points="40.00")
    return {
        "group_public_id": group.public_id,
        "group_id": group.id,
        "group_name": group.name,
        "term_id": group.academic_term_id,
        "item_id": item.id,
        "student_names": ["Alice Example", "Bob Example"],
    }


# ===========================================================================
# Access control
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    with app.app_context():
        data = _setup()
    for url in (OVERVIEW, fx.admin_detail(data["group_public_id"])):
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


def test_a_teacher_and_a_student_are_refused(app, client):
    with app.app_context():
        data = _setup()
        gpid = data["group_public_id"]
    for email in ("t-a@example.com", "alice-a@example.com"):
        fx.login_as(client, email)
        assert client.get(OVERVIEW).status_code == 403
        assert client.get(fx.admin_detail(gpid)).status_code == 403


def test_there_is_no_administrator_mutation_endpoint_anywhere(app, client):
    """The endpoints do not exist, so a POST returns 405 or 404 rather
    than being refused by a check somebody could later relax."""
    with app.app_context():
        data = _setup()
        gpid = data["group_public_id"]
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    for method in ("post", "put", "patch", "delete"):
        for url in (OVERVIEW, fx.admin_detail(gpid)):
            assert getattr(client, method)(url).status_code == 405, (method, url)
    for url in (
        f"{fx.admin_detail(gpid)}/release",
        f"{fx.admin_detail(gpid)}/scores",
        f"{fx.admin_detail(gpid)}/categories/new",
        f"{fx.admin_detail(gpid)}/items/new",
        f"{OVERVIEW}/recalculate",
    ):
        assert client.post(url, data={}).status_code in (404, 405), url


def test_an_unknown_group_404s(app, client):
    with app.app_context():
        _setup()
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    assert client.get(
        fx.admin_detail("00000000-0000-0000-0000-000000000000")
    ).status_code == 404


def test_every_report_page_is_private_no_store_and_varies_on_cookie(app, client):
    with app.app_context():
        data = _setup()
        gpid = data["group_public_id"]
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    for url in (OVERVIEW, fx.admin_detail(gpid)):
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert resp.headers["Cache-Control"] == "private, no-store", url
        assert "Cookie" in resp.headers.get("Vary", ""), url


# ===========================================================================
# What the report shows -- and what it deliberately does not
# ===========================================================================


def test_the_overview_reports_configuration_and_progress(app, client):
    with app.app_context():
        data = _setup()
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get(OVERVIEW).get_data(as_text=True)
    assert data["group_name"] in html
    assert "Weights total 100.00%" in html
    assert "1 released" in html
    assert "1 draft" in html
    # Two active enrolled students.
    assert ">2<" in html


def test_no_score_percentage_or_comment_appears_on_either_report_page(app, client):
    with app.app_context():
        data = _setup()
        gpid = data["group_public_id"]
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    for url in (OVERVIEW, fx.admin_detail(gpid)):
        html = client.get(url).get_data(as_text=True)
        assert "A private note to Alice." not in html, url
        assert "18.00" not in html, url
        assert "15.50" not in html, url
        assert "90.00%" not in html, url
        for name in data["student_names"]:
            assert name not in html, (url, name)


def test_the_detail_page_lists_categories_items_and_score_counts(app, client):
    with app.app_context():
        data = _setup()
        gpid = data["group_public_id"]
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get(fx.admin_detail(gpid)).get_data(as_text=True)
    assert "Homework" in html and "60.00%" in html
    assert "Speaking" in html and "40.00%" in html
    assert "HW 1" in html and "Talk 1" in html
    assert "2 of 2 scored" in html  # the released item
    assert "0 of 2 scored" in html  # the untouched draft
    assert "Released" in html and "Draft" in html


def test_overall_availability_is_reported_as_a_group_level_verdict(app, client):
    with app.app_context():
        data = _setup()
        gpid = data["group_public_id"]
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    assert "Not available yet" in client.get(OVERVIEW).get_data(as_text=True)
    assert "no released grade item yet" in client.get(fx.admin_detail(gpid)).get_data(
        as_text=True
    )
    with app.app_context():
        for item in GradeItem.query.all():
            item.released_at = fx.LATER
        db.session.commit()
    assert "Can be calculated" in client.get(OVERVIEW).get_data(as_text=True)


def test_incomplete_weights_are_reported(app, client):
    with app.app_context():
        data = _setup(weights=(6000, 3000))
        gpid = data["group_public_id"]
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    assert "Weights total 90.00%" in client.get(OVERVIEW).get_data(as_text=True)
    assert "do not add up to exactly 100%" in client.get(fx.admin_detail(gpid)).get_data(
        as_text=True
    )


def test_only_groups_that_own_a_gradebook_are_listed(app, client):
    with app.app_context():
        data = _setup("A")
        _, bare = fx.setup_group("B", teacher_email="t-b@example.com")
        bare_public_id, bare_name = bare.public_id, bare.name
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get(OVERVIEW).get_data(as_text=True)
    assert data["group_name"] in html
    assert bare_public_id not in html
    assert bare_name not in html


def test_an_archived_group_stays_readable_and_is_marked(app, client):
    with app.app_context():
        data = _setup()
        gpid = data["group_public_id"]
        from app.models import Group

        db.session.get(Group, data["group_id"]).status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    assert "Archived" in client.get(OVERVIEW).get_data(as_text=True)
    assert client.get(fx.admin_detail(gpid)).status_code == 200


# ===========================================================================
# Filters -- validated into known shapes before they reach SQL
# ===========================================================================


def test_the_group_filter_narrows_to_one_group(app, client):
    with app.app_context():
        a = _setup("A")
        b = _setup("B")
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get(f"{OVERVIEW}?group={a['group_public_id']}").get_data(as_text=True)
    assert a["group_name"] in html
    assert b["group_public_id"] not in html


def test_an_unknown_group_filter_narrows_to_nothing_rather_than_widening(app, client):
    with app.app_context():
        data = _setup()
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get(f"{OVERVIEW}?group=not-a-real-id").get_data(as_text=True)
    assert "No group has that public ID" in html
    assert data["group_public_id"] not in html


def test_the_configured_filter_splits_complete_from_incomplete(app, client):
    with app.app_context():
        complete = _setup("A", weights=(6000, 4000))
        partial = _setup("B", weights=(6000, 3000))
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get(f"{OVERVIEW}?configured=complete").get_data(as_text=True)
    assert complete["group_public_id"] in html
    assert partial["group_public_id"] not in html
    html = client.get(f"{OVERVIEW}?configured=incomplete").get_data(as_text=True)
    assert partial["group_public_id"] in html
    assert complete["group_public_id"] not in html


def test_the_term_filter_narrows_by_academic_term(app, client):
    with app.app_context():
        a = _setup("A")
        b = _setup("B")
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get(f"{OVERVIEW}?term={a['term_id']}").get_data(as_text=True)
    assert a["group_public_id"] in html
    assert b["group_public_id"] not in html


@pytest.mark.parametrize(
    "query",
    [
        "configured=COMPLETE",
        "configured=maybe",
        "configured=",
        "term=0",
        "term=-1",
        "term=abc",
        "term=99999999999999999999",
        "page=abc",
        "page=0",
    ],
)
def test_an_unrecognised_filter_value_is_dropped_rather_than_guessed_at(
    app, client, query
):
    with app.app_context():
        data = _setup()
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    resp = client.get(f"{OVERVIEW}?{query}")
    assert resp.status_code == 200, query
    assert data["group_public_id"] in resp.get_data(as_text=True), query


def test_a_page_past_the_end_falls_back_to_page_one(app, client):
    with app.app_context():
        data = _setup()
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    resp = client.get(f"{OVERVIEW}?page=999")
    assert resp.status_code == 200
    assert data["group_public_id"] in resp.get_data(as_text=True)


# ===========================================================================
# Bounded, and free of N+1
# ===========================================================================


def test_the_overview_is_limited_and_never_counts(app, client):
    with app.app_context():
        for index in range(PAGE_SIZE + 3):
            _setup(f"G{index}")
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")

    recorded = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        recorded.append((statement, parameters))

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        assert client.get(OVERVIEW).status_code == 200
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", _rec)

    listing = [
        (statement, parameters)
        for statement, parameters in recorded
        if "FROM grade_categories" in statement and "GROUP BY" in statement
    ]
    assert listing
    assert any(
        "LIMIT" in statement.upper() and PAGE_SIZE + 1 in tuple(parameters or ())
        for statement, parameters in listing
    )


def test_the_overview_cost_does_not_grow_with_the_number_of_groups(app, client):
    def query_count(groups):
        with app.app_context():
            db.drop_all()
            db.create_all()
            for index in range(groups):
                _setup(f"G{index}")
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        fx.login_as(client, "admin@example.com")

        recorded = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            assert client.get(OVERVIEW).status_code == 200
        finally:
            with app.app_context():
                event.remove(db.engine, "before_cursor_execute", _rec)
        return len([s for s in recorded if s.strip().upper().startswith("SELECT")])

    assert query_count(1) == query_count(PAGE_SIZE)


def test_the_detail_page_cost_does_not_grow_with_the_gradebook(app, client):
    def query_count(items, students):
        with app.app_context():
            db.drop_all()
            db.create_all()
            teacher, group = fx.setup_group()
            roster = [
                fx.enroll(group, f"s{i}@example.com", name=f"S{i}") for i in range(students)
            ]
            cat = fx.category(group, "Homework", 10000)
            for n in range(items):
                fx.item_with_roster(
                    cat, roster, scores=("5.00",) * students, released=True, title=f"I{n}"
                )
            gpid = group.public_id
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        fx.login_as(client, "admin@example.com")

        recorded = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            assert client.get(fx.admin_detail(gpid)).status_code == 200
        finally:
            with app.app_context():
                event.remove(db.engine, "before_cursor_execute", _rec)
        return len([s for s in recorded if s.strip().upper().startswith("SELECT")])

    assert query_count(1, 1) == query_count(5, 5)


# ===========================================================================
# Navigation
# ===========================================================================


def test_the_admin_navigation_now_links_grades(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert 'href="/admin/grades"' in html
    # Phase 5 / M05 enabled Payments; Research remains deferred with no
    # endpoint.
    assert 'href="/admin/payments"' in html
    assert 'href="/admin/research"' not in html
