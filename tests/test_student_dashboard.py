"""Student dashboard (M09): login redirect, own-enrollment-only scoping,
active/withdrawn behavior, non-operational ancestors, upcoming classes,
empty state, no leaked internal IDs, and query-count protection.
"""

from datetime import date, datetime

import pytest
from sqlalchemy import event

import app.blueprints.student.routes as student_routes
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
from app.services.dashboard_queries import student_dashboard
from tests.conftest import login, make_user

NOW = datetime(2026, 9, 7, 8, 0)  # Monday morning inside the term
TERM_START = date(2026, 1, 1)
TERM_END = date(2026, 12, 31)


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    """Freeze the route's clock to ``NOW`` -- see the identical fixture in
    test_teacher_dashboard.py. Patches the route module's ``app_now``
    binding, not just the service."""
    monkeypatch.setattr(student_routes, "app_now", lambda *a, **k: NOW)


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


def _hierarchy(term_status=AcademicStatus.ACTIVE.value, level_status=AcademicStatus.ACTIVE.value,
               course_status=AcademicStatus.ACTIVE.value, group_status=AcademicStatus.ACTIVE.value,
               group_name="Group A", course_title="English"):
    term = AcademicTerm(name=f"Term {group_name}", start_date=TERM_START, end_date=TERM_END, status=term_status)
    db.session.add(term)
    level = Level(name=f"Level {group_name}", display_order=0, status=level_status)
    db.session.add(level)
    db.session.commit()
    course = Course(title=course_title, level_id=level.id, display_order=0, status=course_status)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=group_name, capacity=20, status=group_status)
    db.session.add(group)
    db.session.commit()
    return group


def _enroll(group, student, status=EnrollmentStatus.ACTIVE.value):
    row = Enrollment(student_id=student.id, group_id=group.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def _schedule(group, day=0, start="09:00", end="10:00", eff_start=TERM_START, eff_end=TERM_END,
              location="Room 1", status=AcademicStatus.ACTIVE.value):
    row = Schedule(
        group_id=group.id,
        day_of_week=day,
        start_time=datetime.strptime(start, "%H:%M").time(),
        end_time=datetime.strptime(end, "%H:%M").time(),
        effective_start_date=eff_start,
        effective_end_date=eff_end,
        location=location,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


# ===========================================================================
# Auth / routing
# ===========================================================================


def test_student_login_redirects_to_student_dashboard(app, client):
    with app.app_context():
        make_user("stud@example.com", UserRole.STUDENT.value)

    resp = client.post(
        "/auth/login",
        data={"email": "stud@example.com", "password": "Sup3rSecret!123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/student/dashboard"


def test_anonymous_redirected_to_login(app, client):
    resp = client.get("/student/dashboard")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_teacher_forbidden(app, client):
    with app.app_context():
        make_user("t@example.com", UserRole.TEACHER.value)
    login(client, "t@example.com")
    assert client.get("/student/dashboard").status_code == 403


def test_admin_forbidden(app, client):
    with app.app_context():
        make_user("a@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "a@example.com")
    assert client.get("/student/dashboard").status_code == 403


def test_researcher_forbidden(app, client):
    with app.app_context():
        make_user("r@example.com", UserRole.RESEARCHER.value)
    login(client, "r@example.com")
    assert client.get("/student/dashboard").status_code == 403


# ===========================================================================
# Route behavior
# ===========================================================================


def test_empty_state_when_no_active_enrollment(app, client):
    with app.app_context():
        student = _user("stud@example.com", UserRole.STUDENT.value)
        group = _hierarchy(group_name="Group A")
        _enroll(group, student, status=EnrollmentStatus.WITHDRAWN.value)
    login(client, "stud@example.com")

    resp = client.get("/student/dashboard")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "No active enrollments" in html
    assert "Group A" not in html


def test_shows_only_own_active_enrollment_via_route(app, client):
    with app.app_context():
        mine = _user("mine@example.com", UserRole.STUDENT.value)
        other = _user("other@example.com", UserRole.STUDENT.value)
        my_group = _hierarchy(group_name="My Group", course_title="My Course")
        other_group = _hierarchy(group_name="Other Group", course_title="Other Course")
        _enroll(my_group, mine)
        _enroll(other_group, other)
        _schedule(my_group)
        _schedule(other_group)
    login(client, "mine@example.com")

    html = client.get("/student/dashboard").get_data(as_text=True)
    assert "My Group" in html
    assert "Other Group" not in html
    assert "Other Course" not in html


def test_withdrawn_enrollment_excluded(app, client):
    with app.app_context():
        student = _user("stud@example.com", UserRole.STUDENT.value)
        active_group = _hierarchy(group_name="Active Group")
        withdrawn_group = _hierarchy(group_name="Withdrawn Group")
        _enroll(active_group, student)
        _enroll(withdrawn_group, student, status=EnrollmentStatus.WITHDRAWN.value)
    login(client, "stud@example.com")

    html = client.get("/student/dashboard").get_data(as_text=True)
    assert "Active Group" in html
    assert "Withdrawn Group" not in html


def test_non_operational_ancestor_visible_but_no_upcoming(app, client):
    with app.app_context():
        student = _user("stud@example.com", UserRole.STUDENT.value)
        group = _hierarchy(course_status=AcademicStatus.ARCHIVED.value, group_name="Archived Course Group")
        _enroll(group, student)
        _schedule(group)
    login(client, "stud@example.com")

    html = client.get("/student/dashboard").get_data(as_text=True)
    assert "Archived Course Group" in html
    assert "Not operational" in html
    assert "No classes in the next 7 days" in html


def test_upcoming_classes_listed_when_operational(app, client):
    with app.app_context():
        student = _user("stud@example.com", UserRole.STUDENT.value)
        group = _hierarchy(group_name="Meets Weekly")
        _enroll(group, student)
        for d in range(7):
            _schedule(group, day=d, location=f"R{d}")
    login(client, "stud@example.com")

    html = client.get("/student/dashboard").get_data(as_text=True)
    assert "Upcoming classes (next 7 days)" in html
    assert "No classes in the next 7 days" not in html


def test_archived_schedule_excluded_from_dashboard(app, client):
    with app.app_context():
        student = _user("stud@example.com", UserRole.STUDENT.value)
        group = _hierarchy(group_name="G")
        _enroll(group, student)
        _schedule(group, day=0, location="Active Slot")
        _schedule(group, day=2, location="Archived Slot", status=AcademicStatus.ARCHIVED.value)
    login(client, "stud@example.com")

    html = client.get("/student/dashboard").get_data(as_text=True)
    assert "Active Slot" in html
    assert "Archived Slot" not in html


def test_no_other_students_or_internal_ids_exposed(app, client):
    with app.app_context():
        student = _user("stud@example.com", UserRole.STUDENT.value)
        classmate = _user("classmate@example.com", UserRole.STUDENT.value)
        group = _hierarchy(group_name="G")
        _enroll(group, student)
        _enroll(group, classmate)
        schedule = _schedule(group)
        gid, sid, uid = group.id, schedule.id, student.id
    login(client, "stud@example.com")

    html = client.get("/student/dashboard").get_data(as_text=True)
    # no classmate identity on the dashboard
    assert "classmate@example.com" not in html
    assert "classmate" not in html
    # no internal numeric object ids in URLs / markup. The trailing slash
    # keeps these from matching a random UUID public_id that happens to
    # begin with the same digits (e.g. "/groups/1" inside
    # "/groups/1a2b-...."): a real numeric id in a path is always a whole
    # segment.
    for leaked in (f"/groups/{gid}/", f"/schedules/{sid}/", f'href="/student/{uid}"'):
        assert leaked not in html


def test_dashboard_query_count_is_bounded(app, client):
    with app.app_context():
        student = _user("stud@example.com", UserRole.STUDENT.value)
        for i in range(6):
            group = _hierarchy(group_name=f"G{i}", course_title=f"C{i}")
            _enroll(group, student)
            _schedule(group, day=i % 5, location=f"R{i}")
    login(client, "stud@example.com")

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        resp = client.get("/student/dashboard")
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)

    assert resp.status_code == 200
    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    # Phase 4 / M13 adds exactly three fixed queries -- Group progress,
    # Continue Learning and Recently Opened -- none of which grows with the
    # number of groups (tests/test_student_dashboard_progress.py).
    assert len(selects) <= 10, (len(selects), selects)


# ===========================================================================
# Query-function level
# ===========================================================================


def test_student_dashboard_query_scoped_to_student(app):
    with app.app_context():
        mine = _user("mine@example.com", UserRole.STUDENT.value)
        other = _user("other@example.com", UserRole.STUDENT.value)
        my_group = _hierarchy(group_name="Mine")
        other_group = _hierarchy(group_name="Theirs")
        _enroll(my_group, mine)
        _enroll(other_group, other)

        data = student_dashboard(mine.id, NOW)
        assert {c["group_name"] for c in data["cards"]} == {"Mine"}


def test_query_marks_non_operational_and_excludes_from_upcoming(app):
    with app.app_context():
        student = _user("s@example.com", UserRole.STUDENT.value)
        active_group = _hierarchy(group_name="Active")
        archived_level_group = _hierarchy(group_name="ArchLevel", level_status=AcademicStatus.ARCHIVED.value)
        _enroll(active_group, student)
        _enroll(archived_level_group, student)
        _schedule(active_group)
        _schedule(archived_level_group)

        data = student_dashboard(student.id, NOW)
        by_name = {c["group_name"]: c for c in data["cards"]}
        assert by_name["Active"]["operational"] is True
        assert by_name["ArchLevel"]["operational"] is False
        assert "Level" in by_name["ArchLevel"]["archived_labels"]
        assert all(o.ref["group_name"] == "Active" for o in data["upcoming"])


def test_query_next_class_uses_injected_now(app):
    with app.app_context():
        student = _user("s@example.com", UserRole.STUDENT.value)
        group = _hierarchy(group_name="G")
        _enroll(group, student)
        _schedule(group, day=0, start="09:00", end="10:00")  # Monday

        before = student_dashboard(student.id, datetime(2026, 9, 7, 8, 0))
        during = student_dashboard(student.id, datetime(2026, 9, 7, 9, 30))
        after = student_dashboard(student.id, datetime(2026, 9, 7, 10, 30))

        assert before["next_class"].start == datetime(2026, 9, 7, 9, 0)
        assert during["next_class"].start == datetime(2026, 9, 7, 9, 0)
        assert after["next_class"].start == datetime(2026, 9, 14, 9, 0)
