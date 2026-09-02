"""Teacher dashboard (M09): login redirect, own-assignment-only scoping,
active/removed assignment behavior, non-operational ancestors, upcoming
classes, empty state, and query-count protection.

Cross-user scoping is asserted at the query-function level
(``teacher_dashboard``) as well as through the route, because two
independent authenticated sessions cannot be exercised inside one test
(Flask-Login caches ``current_user`` on the app-context ``g`` and the
test ``app`` fixture holds that context open).
"""

import re
from datetime import date, datetime, time

import pytest
from sqlalchemy import event

import app.blueprints.teacher.routes as teacher_routes
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
from app.services.dashboard_queries import teacher_dashboard
from tests.conftest import login, make_user

NOW = datetime(2026, 9, 7, 8, 0)  # a Monday morning, inside the term
TERM_START = date(2026, 1, 1)
TERM_END = date(2026, 12, 31)


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    """Freeze the route's clock to ``NOW`` so every date-dependent route
    assertion is deterministic (and still valid after 2026). The route
    module imports ``app_now`` by name, so patch that binding -- not just
    the service function. Pure/query tests that call the dashboard
    services with an explicit ``now`` are unaffected.
    """
    monkeypatch.setattr(teacher_routes, "app_now", lambda *a, **k: NOW)


def _user(email, role, status=UserStatus.ACTIVE.value, name=None):
    u = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name=name or email.split("@")[0],
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


def _assign(group, teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    row = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


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


def test_teacher_login_redirects_to_teacher_dashboard(app, client):
    with app.app_context():
        make_user("teach@example.com", UserRole.TEACHER.value)

    resp = client.post(
        "/auth/login",
        data={"email": "teach@example.com", "password": "Sup3rSecret!123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/teacher/dashboard"


def test_anonymous_redirected_to_login(app, client):
    resp = client.get("/teacher/dashboard")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_student_forbidden(app, client):
    with app.app_context():
        make_user("s@example.com", UserRole.STUDENT.value)
    login(client, "s@example.com")
    assert client.get("/teacher/dashboard").status_code == 403


def test_admin_forbidden(app, client):
    with app.app_context():
        make_user("a@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "a@example.com")
    assert client.get("/teacher/dashboard").status_code == 403


def test_researcher_forbidden(app, client):
    with app.app_context():
        make_user("r@example.com", UserRole.RESEARCHER.value)
    login(client, "r@example.com")
    assert client.get("/teacher/dashboard").status_code == 403


# ===========================================================================
# Route behavior
# ===========================================================================


def test_empty_state_when_no_active_assignment(app, client):
    with app.app_context():
        teacher = _user("teach@example.com", UserRole.TEACHER.value)
        # a removed assignment must NOT count as "has assignment"
        group = _hierarchy(group_name="Group A")
        _assign(group, teacher, status=GroupTeacherAssignmentStatus.REMOVED.value)
    login(client, "teach@example.com")

    resp = client.get("/teacher/dashboard")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "No active teaching assignments" in html
    assert "Group A" not in html


def test_shows_only_own_active_assignment_via_route(app, client):
    with app.app_context():
        mine = _user("mine@example.com", UserRole.TEACHER.value)
        other = _user("other@example.com", UserRole.TEACHER.value)
        my_group = _hierarchy(group_name="My Group", course_title="My Course")
        other_group = _hierarchy(group_name="Other Group", course_title="Other Course")
        _assign(my_group, mine)
        _assign(other_group, other)
        _schedule(my_group)
        _schedule(other_group)
    login(client, "mine@example.com")

    html = client.get("/teacher/dashboard").get_data(as_text=True)
    assert "My Group" in html
    assert "Other Group" not in html
    assert "Other Course" not in html


def test_removed_assignment_excluded_from_dashboard(app, client):
    with app.app_context():
        teacher = _user("teach@example.com", UserRole.TEACHER.value)
        active_group = _hierarchy(group_name="Active Group")
        removed_group = _hierarchy(group_name="Removed Group")
        _assign(active_group, teacher)
        _assign(removed_group, teacher, status=GroupTeacherAssignmentStatus.REMOVED.value)
    login(client, "teach@example.com")

    html = client.get("/teacher/dashboard").get_data(as_text=True)
    assert "Active Group" in html
    assert "Removed Group" not in html


def test_non_operational_ancestor_visible_but_no_upcoming(app, client):
    with app.app_context():
        teacher = _user("teach@example.com", UserRole.TEACHER.value)
        group = _hierarchy(term_status=AcademicStatus.ARCHIVED.value, group_name="Archived Term Group")
        _assign(group, teacher)
        _schedule(group, day=0, eff_start=TERM_START, eff_end=TERM_END)
    login(client, "teach@example.com")

    html = client.get("/teacher/dashboard").get_data(as_text=True)
    assert "Archived Term Group" in html
    assert "Not operational" in html
    assert "No classes in the next 7 days" in html


def test_upcoming_classes_listed_when_operational(app, client):
    with app.app_context():
        teacher = _user("teach@example.com", UserRole.TEACHER.value)
        group = _hierarchy(group_name="Meets Weekly")
        _assign(group, teacher)
        # a slot on every weekday guarantees at least one occurrence within 7 days
        for d in range(7):
            _schedule(group, day=d, start="09:00", end="10:00", location=f"R{d}")
    login(client, "teach@example.com")

    html = client.get("/teacher/dashboard").get_data(as_text=True)
    assert "Upcoming classes (next 7 days)" in html
    assert "No classes in the next 7 days" not in html
    assert "Meets Weekly" in html


def test_archived_schedule_excluded_from_dashboard(app, client):
    with app.app_context():
        teacher = _user("teach@example.com", UserRole.TEACHER.value)
        group = _hierarchy(group_name="G")
        _assign(group, teacher)
        _schedule(group, day=0, start="09:00", end="10:00", location="Active Slot")
        _schedule(group, day=2, start="09:00", end="10:00", location="Archived Slot",
                  status=AcademicStatus.ARCHIVED.value)
    login(client, "teach@example.com")

    html = client.get("/teacher/dashboard").get_data(as_text=True)
    assert "Active Slot" in html
    assert "Archived Slot" not in html


def test_portal_logout_requires_csrf_and_post():
    import re

    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    with app.app_context():
        db.create_all()
        try:
            make_user("teach@example.com", UserRole.TEACHER.value)
            login_page = client.get("/auth/login")
            token = re.search(
                r'name="csrf_token" type="hidden" value="([^"]+)"', login_page.get_data(as_text=True)
            ).group(1)
            client.post(
                "/auth/login",
                data={"email": "teach@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            # GET logout is not allowed; POST without a token is rejected
            assert client.get("/auth/logout").status_code == 405
            assert client.post("/auth/logout").status_code == 400
            # still authenticated
            assert client.get("/teacher/dashboard").status_code == 200
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def _leaks_id_as_path_segment(html, prefix, numeric_id):
    """True when `numeric_id` appears in `html` right after `prefix` as a
    **complete** URL path segment.

    A plain substring test is a false positive here: internal ids start at
    1, and every object in a URL is addressed by a random UUID
    ``public_id``, so ``"/groups/1"`` matches the perfectly legitimate
    ``"/groups/1100f4bf-1b3f-..."`` roughly one run in sixteen. The
    negative lookahead requires the id to end the segment -- followed by
    ``/``, a quote, ``?``, ``#``, or end of string -- which is exactly how
    a real leaked numeric id would appear, and never how a UUID continues
    (UUIDs continue with hex digits or ``-``).

    This is the same whole-segment intent as the neighbouring Student
    dashboard test, expressed so it also catches an id at the very end of
    a URL, which a trailing-slash check would miss.
    """
    pattern = re.escape(prefix) + re.escape(str(numeric_id)) + r"(?![0-9A-Za-z_-])"
    return re.search(pattern, html) is not None


def test_no_internal_numeric_ids_in_html(app, client):
    with app.app_context():
        teacher = _user("teach@example.com", UserRole.TEACHER.value)
        group = _hierarchy(group_name="G")
        _assign(group, teacher)
        _schedule(group)
        # capture the internal ids we must never leak into markup/URLs
        gid, sid, tid = group.id, Schedule.query.first().id, teacher.id
    login(client, "teach@example.com")

    html = client.get("/teacher/dashboard").get_data(as_text=True)

    # The detector must still be able to fail: a genuine whole-segment
    # leak is caught, and a UUID that merely starts with the same digits
    # is not. Without this, a broken matcher would silently turn the
    # assertions below into a no-op.
    assert _leaks_id_as_path_segment(f'href="/groups/{gid}/units"', "/groups/", gid)
    assert _leaks_id_as_path_segment(f'href="/groups/{gid}"', "/groups/", gid)
    assert not _leaks_id_as_path_segment(
        f'href="/groups/{gid}a2b3c4d5-0000-0000-0000-000000000000/units"', "/groups/", gid
    )

    for prefix, internal_id in (
        ("/groups/", gid),
        ("/schedules/", sid),
        ("/teacher/", tid),
    ):
        assert not _leaks_id_as_path_segment(html, prefix, internal_id), (prefix, internal_id)
    # Form values are already exactly delimited by their quotes.
    assert f'value="{gid}"' not in html


def test_dashboard_query_count_is_bounded(app, client):
    """The teacher dashboard must not run per-group / per-schedule
    queries: adding more assigned groups must not add SELECTs."""
    with app.app_context():
        teacher = _user("teach@example.com", UserRole.TEACHER.value)
        for i in range(6):
            group = _hierarchy(group_name=f"G{i}", course_title=f"C{i}")
            _assign(group, teacher)
            _schedule(group, day=i % 5, location=f"R{i}")
            st = _user(f"st{i}@example.com", UserRole.STUDENT.value)
            _enroll(group, st)
    login(client, "teach@example.com")

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        resp = client.get("/teacher/dashboard")
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)

    assert resp.status_code == 200
    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    assert len(selects) <= 8, (len(selects), selects)


# ===========================================================================
# Query-function level: scoping and eligibility
# ===========================================================================


def test_teacher_dashboard_query_scoped_to_teacher(app):
    with app.app_context():
        mine = _user("mine@example.com", UserRole.TEACHER.value)
        other = _user("other@example.com", UserRole.TEACHER.value)
        my_group = _hierarchy(group_name="Mine")
        other_group = _hierarchy(group_name="Theirs")
        _assign(my_group, mine)
        _assign(other_group, other)

        data = teacher_dashboard(mine.id, NOW)
        names = {c["group_name"] for c in data["cards"]}
        assert names == {"Mine"}


def test_active_student_count_is_role_and_account_eligible_only(app):
    with app.app_context():
        teacher = _user("t@example.com", UserRole.TEACHER.value)
        group = _hierarchy(group_name="G")
        _assign(group, teacher)

        active_student = _user("as@example.com", UserRole.STUDENT.value)
        suspended_student = _user("ss@example.com", UserRole.STUDENT.value, status=UserStatus.SUSPENDED.value)
        not_a_student = _user("nas@example.com", UserRole.TEACHER.value)
        _enroll(group, active_student)
        _enroll(group, suspended_student)  # counts against eligibility (suspended)
        _enroll(group, not_a_student)  # corrupted row -- wrong role
        _enroll(group, _user("wd@example.com", UserRole.STUDENT.value), status=EnrollmentStatus.WITHDRAWN.value)

        data = teacher_dashboard(teacher.id, NOW)
        assert data["cards"][0]["student_count"] == 1


def test_query_excludes_removed_assignment_and_marks_non_operational(app):
    with app.app_context():
        teacher = _user("t@example.com", UserRole.TEACHER.value)
        active_group = _hierarchy(group_name="Active")
        archived_group = _hierarchy(group_name="ArchivedGroup", group_status=AcademicStatus.ARCHIVED.value)
        removed_group = _hierarchy(group_name="Removed")
        _assign(active_group, teacher)
        _assign(archived_group, teacher)
        _assign(removed_group, teacher, status=GroupTeacherAssignmentStatus.REMOVED.value)
        _schedule(active_group)
        _schedule(archived_group)

        data = teacher_dashboard(teacher.id, NOW)
        by_name = {c["group_name"]: c for c in data["cards"]}
        assert set(by_name) == {"Active", "ArchivedGroup"}
        assert by_name["Active"]["operational"] is True
        assert by_name["ArchivedGroup"]["operational"] is False
        assert by_name["ArchivedGroup"]["next_class"] is None
        # only the operational group contributes to the combined upcoming list
        assert all(o.ref["group_name"] == "Active" for o in data["upcoming"])
