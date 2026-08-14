import re
from datetime import date

from app.extensions import db
from app.models import AcademicStatus, AcademicTerm, Course, Level, UserRole
from tests.conftest import login, make_user


def test_admin_login_redirects_to_dashboard(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)

    resp = client.post(
        "/auth/login",
        data={"email": "admin@example.com", "password": "Sup3rSecret!123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/dashboard")


def test_dashboard_loads(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/dashboard")
    assert resp.status_code == 200
    assert b"Welcome" in resp.data


def test_dashboard_database_counts_are_correct(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        db.session.add(AcademicTerm(name="Term A", start_date=date(2026, 1, 1), end_date=date(2026, 5, 1)))
        db.session.add(
            AcademicTerm(
                name="Term B",
                start_date=date(2026, 6, 1),
                end_date=date(2026, 9, 1),
                status=AcademicStatus.ARCHIVED.value,
            )
        )
        level = Level(name="Level 1", display_order=0)
        db.session.add(level)
        db.session.commit()
        db.session.add(Course(title="Course A", level_id=level.id, display_order=0))
        db.session.commit()

    login(client, "admin@example.com")
    resp = client.get("/admin/dashboard")
    html = resp.get_data(as_text=True)

    with app.app_context():
        assert f">{AcademicTerm.query.count()}<" in html
        assert f">{AcademicTerm.query.filter_by(status=AcademicStatus.ACTIVE.value).count()}<" in html
        assert f">{Level.query.count()}<" in html
        assert f">{Course.query.count()}<" in html


def test_anonymous_users_redirected(client):
    resp = client.get("/admin/dashboard")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_student_denied(app, client):
    with app.app_context():
        make_user("student@example.com", UserRole.STUDENT.value)
    login(client, "student@example.com")
    assert client.get("/admin/dashboard").status_code == 403


def test_teacher_denied(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
    login(client, "teacher@example.com")
    assert client.get("/admin/dashboard").status_code == 403


def test_researcher_denied(app, client):
    with app.app_context():
        make_user("researcher@example.com", UserRole.RESEARCHER.value)
    login(client, "researcher@example.com")
    assert client.get("/admin/dashboard").status_code == 403


def test_academic_navigation_links_present_and_working(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/dashboard")
    html = resp.get_data(as_text=True)
    assert '/admin/academic-terms' in html
    assert '/admin/levels' in html
    assert '/admin/courses' in html

    for path in ["/admin/academic-terms", "/admin/levels", "/admin/courses"]:
        assert client.get(path).status_code == 200


def test_disabled_nav_items_are_not_clickable_links(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/dashboard")
    html = resp.get_data(as_text=True)
    assert "Teachers" in html
    assert 'href="/admin/teachers"' not in html
    assert "Soon" in html


def test_logout_works(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    assert client.get("/admin/dashboard").status_code == 200

    dashboard_html = client.get("/admin/dashboard").get_data(as_text=True)
    token = re.search(r'csrf_token[^>]*value="([^"]+)"', dashboard_html).group(1)

    resp = client.post("/auth/logout", data={"csrf_token": token}, follow_redirects=True)
    assert resp.status_code == 200

    resp = client.get("/admin/dashboard")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]
