import re
from datetime import date

from app import create_app
from app.extensions import db
from app.models import AcademicTerm, UserRole
from tests.conftest import login, make_user


def _create_term(name="Fall 2026", start=date(2026, 9, 1), end=date(2026, 12, 31)):
    term = AcademicTerm(name=name, start_date=start, end_date=end)
    db.session.add(term)
    db.session.commit()
    return term


def test_administrator_can_list_terms(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _create_term()
    login(client, "admin@example.com")

    resp = client.get("/admin/academic-terms")
    assert resp.status_code == 200
    assert b"Fall 2026" in resp.data


def test_administrator_can_create_a_valid_term(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/academic-terms/new",
        data={"name": "Spring 2027", "start_date": "2027-01-10", "end_date": "2027-05-20"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"created" in resp.data.lower()

    with app.app_context():
        term = AcademicTerm.query.filter_by(name="Spring 2027").first()
        assert term is not None
        assert term.status == "active"


def test_administrator_can_edit_a_term(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _create_term(name="Old Name")
        public_id = term.public_id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/academic-terms/{public_id}/edit",
        data={"name": "New Name", "start_date": "2026-09-01", "end_date": "2026-12-31"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated" in resp.data.lower()

    with app.app_context():
        term = AcademicTerm.query.filter_by(public_id=public_id).first()
        assert term.name == "New Name"


def test_administrator_can_deactivate_a_term(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _create_term()
        public_id = term.public_id
        assert term.status == "active"

    login(client, "admin@example.com")
    resp = client.post(f"/admin/academic-terms/{public_id}/toggle-status", follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        term = AcademicTerm.query.filter_by(public_id=public_id).first()
        assert term.status == "archived"

    resp = client.post(f"/admin/academic-terms/{public_id}/toggle-status", follow_redirects=True)
    with app.app_context():
        term = AcademicTerm.query.filter_by(public_id=public_id).first()
        assert term.status == "active"


def test_invalid_date_range_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/academic-terms/new",
        data={"name": "Broken Term", "start_date": "2026-12-31", "end_date": "2026-09-01"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"must be after the start date" in resp.data.lower()

    with app.app_context():
        assert AcademicTerm.query.filter_by(name="Broken Term").first() is None


def test_required_fields_are_enforced(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/academic-terms/new",
        data={"name": "", "start_date": "", "end_date": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"this field is required" in resp.data.lower()

    with app.app_context():
        assert AcademicTerm.query.count() == 0


def test_anonymous_visitor_is_redirected(client):
    resp = client.get("/admin/academic-terms")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_student_cannot_access(app, client):
    with app.app_context():
        make_user("student@example.com", UserRole.STUDENT.value)
    login(client, "student@example.com")
    resp = client.get("/admin/academic-terms")
    assert resp.status_code == 403


def test_teacher_cannot_access(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
    login(client, "teacher@example.com")
    resp = client.get("/admin/academic-terms")
    assert resp.status_code == 403


def test_researcher_cannot_access(app, client):
    with app.app_context():
        make_user("researcher@example.com", UserRole.RESEARCHER.value)
    login(client, "researcher@example.com")
    resp = client.get("/admin/academic-terms")
    assert resp.status_code == 403


def test_csrf_remains_enforced_on_create():
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)

            login_page = client.get("/auth/login")
            token = re.search(
                r'name="csrf_token" type="hidden" value="([^"]+)"', login_page.get_data(as_text=True)
            ).group(1)
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )

            resp = client.post(
                "/admin/academic-terms/new",
                data={"name": "No CSRF Term", "start_date": "2027-01-01", "end_date": "2027-06-01"},
            )
            assert resp.status_code == 400
            assert AcademicTerm.query.filter_by(name="No CSRF Term").first() is None
        finally:
            db.session.remove()
            db.drop_all()
