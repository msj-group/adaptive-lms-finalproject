from datetime import date

from app.extensions import db
from app.models import AcademicTerm, Course, Group, Level, UserRole
from tests.conftest import login, make_user


def _make_term(name="Fall 2026"):
    term = AcademicTerm(name=name, start_date=date(2026, 9, 1), end_date=date(2026, 12, 31))
    db.session.add(term)
    db.session.commit()
    return term


def _make_course(level_name="Level 1", title="General English"):
    level = Level(name=level_name, display_order=0)
    db.session.add(level)
    db.session.commit()
    course = Course(title=title, level_id=level.id, display_order=0)
    db.session.add(course)
    db.session.commit()
    return course


def _make_group(term=None, course=None, name="Group A", code=None, capacity=20):
    term = term or _make_term()
    course = course or _make_course()
    group = Group(academic_term_id=term.id, course_id=course.id, name=name, code=code, capacity=capacity)
    db.session.add(group)
    db.session.commit()
    return group


def test_administrator_can_access_groups_list(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    assert resp.status_code == 200


def test_unauthenticated_user_cannot_access(client):
    resp = client.get("/admin/groups")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_non_administrator_cannot_access(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
    login(client, "teacher@example.com")
    assert client.get("/admin/groups").status_code == 403


def test_existing_groups_are_displayed_correctly(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_group(name="Morning Batch", code="MB1", capacity=25)
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    html = resp.get_data(as_text=True)
    assert "Morning Batch" in html
    assert "MB1" in html
    assert ">25<" in html


def test_course_is_displayed_correctly(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        course = _make_course(title="Business English")
        _make_group(course=course, name="Group A")
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    assert "Business English" in resp.get_data(as_text=True)


def test_level_is_displayed_through_course_relationship(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        course = _make_course(level_name="Level 7")
        _make_group(course=course, name="Group A")
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    assert "Level 7" in resp.get_data(as_text=True)


def test_academic_term_is_displayed_correctly(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term(name="Spring 2027")
        _make_group(term=term, name="Group A")
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    assert "Spring 2027" in resp.get_data(as_text=True)


def test_empty_state_when_no_groups_exist(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    html = resp.get_data(as_text=True)
    assert "No groups yet" in html
    assert "disabled" in html


def test_search_filters_by_name_or_code(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        _make_group(term=term, course=course, name="Alpha Group", code="ALP")
        _make_group(term=term, course=course, name="Beta Group", code="BET")
    login(client, "admin@example.com")

    resp = client.get("/admin/groups?q=Alpha")
    html = resp.get_data(as_text=True)
    assert "Alpha Group" in html
    assert "Beta Group" not in html

    resp = client.get("/admin/groups?q=BET")
    html = resp.get_data(as_text=True)
    assert "Beta Group" in html
    assert "Alpha Group" not in html


def test_admin_can_access_create_page(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_term()
        _make_course()
    login(client, "admin@example.com")

    resp = client.get("/admin/groups/new")
    assert resp.status_code == 200
    assert b"New Group" in resp.data


def test_create_page_redirects_when_no_term_or_course_exists(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/groups/new", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Create at least one Academic Term and one Course" in resp.data


def test_unauthenticated_user_cannot_access_create_page(client):
    resp = client.get("/admin/groups/new")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_non_admin_user_cannot_access_create_page(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
    login(client, "teacher@example.com")
    assert client.get("/admin/groups/new").status_code == 403


def test_successful_group_creation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "GA1",
            "capacity": "25",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"created" in resp.data.lower()

    with app.app_context():
        group = Group.query.filter_by(name="Group A").first()
        assert group is not None
        assert group.code == "GA1"
        assert group.capacity == 25
        assert group.status == "active"


def test_correct_database_relationships_after_creation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term(name="Winter 2027")
        course = _make_course(level_name="Level 9", title="Advanced English")
        term_id, course_id = term.id, course.id
    login(client, "admin@example.com")

    client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )

    with app.app_context():
        group = Group.query.filter_by(name="Group A").first()
        assert group.academic_term_id == term_id
        assert group.course_id == course_id
        assert group.academic_term.name == "Winter 2027"
        assert group.course.title == "Advanced English"
        assert group.course.level.name == "Level 9"


def test_correct_status_handling(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Archived Group",
            "code": "",
            "capacity": "10",
            "status": "archived",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        group = Group.query.filter_by(name="Archived Group").first()
        assert group.status == "archived"


def test_required_field_validation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_term()
        _make_course()
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={"academic_term_id": "", "course_id": "", "name": "", "code": "", "capacity": "", "status": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"this field is required" in resp.data.lower()

    with app.app_context():
        assert Group.query.count() == 0


def test_invalid_capacity_zero_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "0",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert Group.query.filter_by(name="Group A").first() is None


def test_invalid_capacity_negative_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "-3",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert Group.query.filter_by(name="Group A").first() is None


def test_duplicate_group_name_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
        _make_group(term=term, course=course, name="Group A")
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data.lower()

    with app.app_context():
        assert Group.query.filter_by(name="Group A").count() == 1


def test_duplicate_group_code_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
        _make_group(term=term, course=course, name="Existing Group", code="DUP")
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Another Group",
            "code": "DUP",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data.lower()

    with app.app_context():
        assert Group.query.filter_by(name="Another Group").first() is None


def test_same_name_allowed_in_different_term_or_course(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
        _make_group(term=term, course=course, name="Group A")
        other_term = _make_term(name="Spring 2027")
        other_term_id = other_term.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": other_term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"created" in resp.data.lower()

    with app.app_context():
        assert Group.query.filter_by(name="Group A").count() == 2


def test_invalid_nonexistent_course_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        term_id = term.id
        _make_course()
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": "999999",
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert Group.query.filter_by(name="Group A").first() is None


def test_invalid_nonexistent_academic_term_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_term()
        course = _make_course()
        course_id = course.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": "999999",
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert Group.query.filter_by(name="Group A").first() is None


def test_csrf_protection_on_create():
    import re

    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            term = _make_term()
            course = _make_course()
            term_id, course_id = term.id, course.id

            login_page = client.get("/auth/login")
            token = re.search(
                r'name="csrf_token" type="hidden" value="([^"]+)"', login_page.get_data(as_text=True)
            ).group(1)
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )

            resp = client.post(
                "/admin/groups/new",
                data={
                    "academic_term_id": term_id,
                    "course_id": course_id,
                    "name": "No CSRF Group",
                    "code": "",
                    "capacity": "10",
                    "status": "active",
                },
            )
            assert resp.status_code == 400
            assert Group.query.filter_by(name="No CSRF Group").first() is None
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
