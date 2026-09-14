import re
from datetime import date, datetime

import pytest

import app.blueprints.admin.routes as admin_routes
from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    Schedule,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login, make_user

NOW = datetime(2026, 9, 7, 8, 0)  # a Monday morning inside every fixture's 2026 term


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    """Freeze the admin dashboard route's clock to ``NOW`` so the
    operational-class-list assertions are deterministic and stay valid
    after 2026. Patches the route module's ``app_now`` binding."""
    monkeypatch.setattr(admin_routes, "app_now", lambda *a, **k: NOW)


def _user(email, role, status=UserStatus.ACTIVE.value):
    u = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name=email.split("@")[0],
        role=role,
        status=status,
    )
    db.session.add(u)
    db.session.commit()
    return u


def _active_group(name="Group A"):
    term = AcademicTerm(name=f"Term {name}", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    db.session.add(term)
    level = Level(name=f"Level {name}", display_order=0)
    db.session.add(level)
    db.session.commit()
    course = Course(title=f"Course {name}", level_id=level.id, display_order=0)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=name, capacity=20)
    db.session.add(group)
    db.session.commit()
    return group


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


def test_teachers_nav_item_is_now_a_clickable_link(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/dashboard")
    html = resp.get_data(as_text=True)
    assert 'href="/admin/teachers"' in html
    assert client.get("/admin/teachers").status_code == 200


def test_teachers_nav_link_is_active_on_its_own_page(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/teachers")
    html = resp.get_data(as_text=True)
    assert 'admin-nav__link--active' in html
    assert re.search(
        r'<a class="admin-nav__link admin-nav__link--active"[^>]*href="/admin/teachers"',
        html,
    )


def test_no_standalone_enrollments_nav_link(app, client):
    """Part 6D moved Student Enrollment management entirely onto each
    Group's Manage Members page -- the standalone Enrollments nav entry
    and page are gone, and the old flat route must not exist.
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/dashboard")
    html = resp.get_data(as_text=True)
    assert ">Enrollments<" not in html
    assert 'href="/admin/enrollments"' not in html
    assert client.get("/admin/enrollments").status_code == 404


def test_other_disabled_nav_items_remain_disabled(app, client):
    """Phase 4 / M07 enabled **Attendance** and M08 enabled **Grades**.

    Both review surfaces are real now, so their nav entries link to them;
    Payments and Research remain deferred with no endpoint at all. The
    assertion is updated explicitly rather than loosened, so a future
    milestone enabling one of the two by accident still fails here.
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/dashboard")
    html = resp.get_data(as_text=True)
    assert "Attendance" in html
    assert "Grades" in html
    assert "Payments" in html
    assert "Research" in html
    assert "Soon" in html
    assert 'href="/admin/attendance"' in html
    assert 'href="/admin/grades"' in html
    # Phase 5 / M02 added the Fee Plans catalogue beside them. Payments stays
    # disabled -- visibly, with no endpoint -- until the manual-payment Part.
    assert "Fee Plans" in html
    assert 'href="/admin/fee-plans"' in html
    assert re.search(r'Payments <span class="badge badge--neutral">Soon</span>', html)
    assert 'href="/admin/payments"' not in html
    assert 'href="/admin/research"' not in html
    assert client.get("/admin/payments").status_code == 404
    assert client.get("/admin/research").status_code == 404


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


# ======================================================================
# M09 -- expanded people / structure counts, setup indicators, classes
# ======================================================================


def test_people_and_structure_counts_present(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _user("s1@example.com", UserRole.STUDENT.value)
        _user("s2@example.com", UserRole.STUDENT.value, status=UserStatus.SUSPENDED.value)
        _user("t1@example.com", UserRole.TEACHER.value)
        group = _active_group("G")
        st = _user("s3@example.com", UserRole.STUDENT.value)
        te = _user("t2@example.com", UserRole.TEACHER.value)
        db.session.add(Enrollment(student_id=st.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=te.id, status=GroupTeacherAssignmentStatus.ACTIVE.value))
        db.session.commit()
    login(client, "admin@example.com")

    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "Students" in html and "Teachers" in html
    # students: s1, s2(suspended), s3 -> total 3, active 2, suspended 1
    assert "2 active &middot; 1 suspended" in html
    assert "Active enrollment rows" in html
    assert "Active teacher-assignment rows" in html
    assert "Groups" in html


def test_setup_indicator_flags_group_without_teacher_or_schedule(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        needs = _active_group("Needs Setup")
    login(client, "admin@example.com")

    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "no eligible active teacher" in html
    assert "no active schedule" in html
    assert "Needs Setup" in html


def test_setup_indicator_success_only_when_configured_groups_exist(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _active_group("Ready")
        teacher = _user("t@example.com", UserRole.TEACHER.value)
        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id, status=GroupTeacherAssignmentStatus.ACTIVE.value))
        db.session.add(Schedule(
            group_id=group.id, day_of_week=0,
            start_time=datetime(2026, 1, 1, 9, 0).time(), end_time=datetime(2026, 1, 1, 10, 0).time(),
            effective_start_date=date(2026, 1, 1), effective_end_date=date(2026, 12, 31),
            location="R1", status=AcademicStatus.ACTIVE.value,
        ))
        db.session.commit()
    login(client, "admin@example.com")

    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "Every active group has an eligible active teacher" in html
    assert "No operational active groups yet" not in html


def test_setup_indicator_neutral_when_no_operational_groups(app, client):
    """An empty center (or one whose only groups sit under archived
    ancestors) must NOT be described as 'every active group is
    configured' -- it gets a neutral empty state instead."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        # a group whose Course is archived -> not an operational active group
        g = _active_group("Under Archived Course")
        g.course.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    login(client, "admin@example.com")

    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "No operational active groups yet" in html
    assert "Every active group has an eligible active teacher" not in html
    assert "no eligible active teacher" not in html


def test_operational_classes_exclude_archived_hierarchy(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        # operational group + weekday-covering schedule
        good = _active_group("Good Group")
        for d in range(7):
            db.session.add(Schedule(
                group_id=good.id, day_of_week=d,
                start_time=datetime(2026, 1, 1, 9, 0).time(), end_time=datetime(2026, 1, 1, 10, 0).time(),
                effective_start_date=date(2026, 1, 1), effective_end_date=date(2026, 12, 31),
                location="Good Room", status=AcademicStatus.ACTIVE.value,
            ))
        # archived-term group with a schedule -- must not appear
        bad = _active_group("Bad Group")
        bad.academic_term.status = AcademicStatus.ARCHIVED.value
        db.session.add(Schedule(
            group_id=bad.id, day_of_week=0,
            start_time=datetime(2026, 1, 1, 9, 0).time(), end_time=datetime(2026, 1, 1, 10, 0).time(),
            effective_start_date=date(2026, 1, 1), effective_end_date=date(2026, 12, 31),
            location="Bad Room", status=AcademicStatus.ACTIVE.value,
        ))
        db.session.commit()
    login(client, "admin@example.com")

    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "Good Room" in html
    assert "Bad Room" not in html


def test_dashboard_shows_timezone(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "timezone" in html.lower()


def test_dashboard_links_only_to_implemented_pages(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/dashboard").get_data(as_text=True)
    # Phase 4 / M07 implemented the Administrator attendance review surface
    # and M08 the gradebook report, so /admin/attendance and /admin/grades
    # both moved from the "not implemented" list to the linked one.
    # Phase 5 / M02 implemented /admin/fee-plans. Payments and Research remain
    # unimplemented, with no endpoint at all.
    for path in ("/admin/academic-terms", "/admin/levels", "/admin/courses",
                 "/admin/groups", "/admin/schedules", "/admin/students",
                 "/admin/teachers", "/admin/attendance", "/admin/grades",
                 "/admin/fee-plans"):
        assert f'href="{path}"' in html
    for missing in ("/admin/payments", "/admin/research"):
        assert f'href="{missing}"' not in html


def test_dashboard_empty_states(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "No classes in the next 7 days" in html
    # an empty center is neutral, not "fully configured"
    assert "No operational active groups yet" in html
    assert "Every active group has an eligible active teacher" not in html


def test_dashboard_query_count_bounded(app, client):
    from sqlalchemy import event

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        for i in range(6):
            g = _active_group(f"G{i}")
            db.session.add(Schedule(
                group_id=g.id, day_of_week=i % 5,
                start_time=datetime(2026, 1, 1, 9, 0).time(), end_time=datetime(2026, 1, 1, 10, 0).time(),
                effective_start_date=date(2026, 1, 1), effective_end_date=date(2026, 12, 31),
                location=f"R{i}", status=AcademicStatus.ACTIVE.value,
            ))
        db.session.commit()
    login(client, "admin@example.com")

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        resp = client.get("/admin/dashboard")
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)

    assert resp.status_code == 200
    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    assert len(selects) <= 25, (len(selects), selects)
