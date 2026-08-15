import re

from app.models import UserRole, UserStatus
from tests.conftest import login, make_user


def _make_student(email, full_name="Student", status=UserStatus.ACTIVE.value):
    return make_user(email, UserRole.STUDENT.value, status=status, full_name=full_name)


def _assert_no_clear_or_filter_controls(html):
    """There is no custom Clear control at all any more -- the search
    input's native browser-provided "x" (type=search) covers that job --
    and no visible Filter button either; it only exists inside <noscript>
    as a no-JS fallback.
    """
    assert 'id="student-clear-filters"' not in html
    assert re.search(r"<noscript>\s*<button[^>]*>Filter</button>\s*</noscript>", html) is not None
    outside_noscript = re.sub(r"<noscript>.*?</noscript>", "", html, flags=re.DOTALL)
    assert ">Filter<" not in outside_noscript


def test_administrator_can_access_student_list(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/students")
    assert resp.status_code == 200


def test_anonymous_user_is_redirected_to_login(client):
    resp = client.get("/admin/students")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_teacher_cannot_access(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
    login(client, "teacher@example.com")
    assert client.get("/admin/students").status_code == 403


def test_student_cannot_access(app, client):
    with app.app_context():
        _make_student("student@example.com")
    login(client, "student@example.com")
    assert client.get("/admin/students").status_code == 403


def test_researcher_cannot_access(app, client):
    with app.app_context():
        make_user("researcher@example.com", UserRole.RESEARCHER.value)
    login(client, "researcher@example.com")
    assert client.get("/admin/students").status_code == 403


def test_only_student_role_users_appear(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_student("alice.student@example.com", full_name="Alice Student")
    login(client, "admin@example.com")

    resp = client.get("/admin/students")
    html = resp.get_data(as_text=True)
    assert "Alice Student" in html


def test_other_roles_do_not_appear(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        make_user("teacher2@example.com", UserRole.TEACHER.value, full_name="Teacher Person")
        make_user("researcher2@example.com", UserRole.RESEARCHER.value, full_name="Researcher Person")
        _make_student("student2@example.com", full_name="Student Person")
    login(client, "admin@example.com")

    html = client.get("/admin/students").get_data(as_text=True)
    assert "Student Person" in html
    assert "Teacher Person" not in html
    assert "Researcher Person" not in html


def test_student_fields_displayed_correctly(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(
            "jane.doe@example.com", full_name="Jane Doe", status=UserStatus.SUSPENDED.value
        )
        created_date = student.created_at.strftime("%Y-%m-%d")
    login(client, "admin@example.com")

    html = client.get("/admin/students").get_data(as_text=True)
    assert "Jane Doe" in html
    assert "jane.doe@example.com" in html
    assert "Suspended" in html
    assert created_date in html


def test_search_by_full_name(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_student("s1@example.com", full_name="Amina Khalid")
        _make_student("s2@example.com", full_name="Youssef Omar")
    login(client, "admin@example.com")

    html = client.get("/admin/students?q=Amina").get_data(as_text=True)
    assert "Amina Khalid" in html
    assert "Youssef Omar" not in html


def test_search_by_email(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_student("unique.email@example.com", full_name="Student One")
        _make_student("other@example.com", full_name="Student Two")
    login(client, "admin@example.com")

    html = client.get("/admin/students?q=unique.email").get_data(as_text=True)
    assert "Student One" in html
    assert "Student Two" not in html


def test_status_filtering(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_student("active1@example.com", full_name="Active Student", status=UserStatus.ACTIVE.value)
        _make_student(
            "suspended1@example.com", full_name="Suspended Student", status=UserStatus.SUSPENDED.value
        )
    login(client, "admin@example.com")

    html = client.get("/admin/students?status=suspended").get_data(as_text=True)
    assert "Suspended Student" in html
    assert "Active Student" not in html

    html = client.get("/admin/students?status=active").get_data(as_text=True)
    assert "Active Student" in html
    assert "Suspended Student" not in html


def test_search_and_status_filter_work_together(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_student("a1@example.com", full_name="Karim Active", status=UserStatus.ACTIVE.value)
        _make_student("a2@example.com", full_name="Karim Suspended", status=UserStatus.SUSPENDED.value)
        _make_student("a3@example.com", full_name="Other Active", status=UserStatus.ACTIVE.value)
    login(client, "admin@example.com")

    html = client.get("/admin/students?q=Karim&status=active").get_data(as_text=True)
    assert "Karim Active" in html
    assert "Karim Suspended" not in html
    assert "Other Active" not in html


def test_invalid_status_value_does_not_crash_or_bypass_filtering(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_student("s1@example.com", full_name="Some Student", status=UserStatus.ACTIVE.value)
    login(client, "admin@example.com")

    for bad_status in ["deleted", "'; DROP TABLE users; --", "<script>alert(1)</script>", "ACTIVE"]:
        resp = client.get(f"/admin/students?status={bad_status}")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        # An unrecognized status must be ignored, not crash and not silently
        # exclude every student either -- the valid student still shows.
        assert "Some Student" in html
        # It must not be treated as an active filter, and a "no results"
        # page must never be mistaken for "no students yet".
        _assert_no_clear_or_filter_controls(html)


def test_invalid_status_does_not_produce_misleading_empty_state(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/students?status=deleted")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "No students yet" in html
    assert "No students match your filters" not in html
    _assert_no_clear_or_filter_controls(html)


def test_empty_database_state(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/students").get_data(as_text=True)
    assert "No students yet" in html


def test_no_match_filter_state(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_student("s1@example.com", full_name="Real Student")
    login(client, "admin@example.com")

    html = client.get("/admin/students?q=NoSuchPerson").get_data(as_text=True)
    assert "No students match your filters" in html
    assert "No students yet" not in html


def test_students_navigation_is_active_and_correct(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert 'href="/admin/students"' in html
    assert client.get("/admin/students").status_code == 200


def test_password_hash_and_internal_id_not_rendered(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student("secret.holder@example.com", full_name="Secret Holder")
        student_id = student.id
        password_hash = student.password_hash
    login(client, "admin@example.com")

    html = client.get("/admin/students").get_data(as_text=True)
    assert password_hash not in html
    assert "$argon2" not in html
    assert f">{student_id}<" not in html


def test_html_in_student_name_is_escaped(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_student("xss@example.com", full_name="<script>alert('xss')</script>")
    login(client, "admin@example.com")

    html = client.get("/admin/students").get_data(as_text=True)
    assert "<script>alert('xss')</script>" not in html
    assert "&lt;script&gt;" in html
