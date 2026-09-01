"""Student lesson navigation (M11): SQL-scoped active-Enrollment
authorization, the effective-visibility formula (active hierarchy +
active Unit + published Lesson), non-disclosure 404s for every failure
mode, independence from Teacher assignment / Schedule, the bounded query
count for the outline, and no leaked internal ids or Teacher identities.
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import event

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
    Lesson,
    LessonStatus,
    Level,
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login, make_user

PW = "Sup3rSecret!123"


def _user(email, role, status=UserStatus.ACTIVE.value):
    u = User(email=email, password_hash=hash_password(PW),
             full_name=email.split("@")[0], role=role, status=status)
    db.session.add(u)
    db.session.commit()
    return u


def _hierarchy(term_status=AcademicStatus.ACTIVE.value, level_status=AcademicStatus.ACTIVE.value,
               course_status=AcademicStatus.ACTIVE.value, group_status=AcademicStatus.ACTIVE.value,
               group_name="Group A", course_title="English"):
    term = AcademicTerm(name=f"Term {group_name}", start_date=date(2026, 1, 1),
                        end_date=date(2026, 12, 31), status=term_status)
    db.session.add(term)
    level = Level(name=f"Level {group_name}", display_order=0, status=level_status)
    db.session.add(level)
    db.session.commit()
    course = Course(title=course_title, level_id=level.id, display_order=0, status=course_status)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=group_name,
                  capacity=20, status=group_status)
    db.session.add(group)
    db.session.commit()
    return group


def _enroll(group, student, status=EnrollmentStatus.ACTIVE.value):
    row = Enrollment(student_id=student.id, group_id=group.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def _unit(group, title="Unit 1", display_order=0, status=AcademicStatus.ACTIVE.value):
    u = Unit(group_id=group.id, title=title, display_order=display_order, status=status)
    db.session.add(u)
    db.session.commit()
    return u


def _lesson(unit, title="Lesson 1", display_order=0, status=LessonStatus.DRAFT.value):
    published_at = datetime.now(timezone.utc) if status == LessonStatus.PUBLISHED.value else None
    lsn = Lesson(unit_id=unit.id, title=title, display_order=display_order,
                 status=status, published_at=published_at)
    db.session.add(lsn)
    db.session.commit()
    return lsn


def _setup_enrolled(student_email="stud@example.com"):
    student = _user(student_email, UserRole.STUDENT.value)
    group = _hierarchy()
    _enroll(group, student)
    unit = _unit(group)
    return student, group, unit


def _outline_url(gpid):
    return f"/student/groups/{gpid}/units"


def _lesson_url(gpid, upid, lpid):
    return f"/student/groups/{gpid}/units/{upid}/lessons/{lpid}"


# ===========================================================================
# Auth / role
# ===========================================================================


def test_anonymous_redirected_to_login(app, client):
    with app.app_context():
        _, group, _ = _setup_enrolled()
        gpid = group.public_id
    resp = client.get(_outline_url(gpid))
    assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


def test_teacher_admin_researcher_forbidden(app, client):
    with app.app_context():
        _, group, _ = _setup_enrolled()
        gpid = group.public_id
        make_user("t@example.com", UserRole.TEACHER.value)
        make_user("a@example.com", UserRole.ADMINISTRATOR.value)
        make_user("r@example.com", UserRole.RESEARCHER.value)
    for email in ("t@example.com", "a@example.com", "r@example.com"):
        login(client, email)
        assert client.get(_outline_url(gpid)).status_code == 403


# ===========================================================================
# Outline access
# ===========================================================================


def test_enrolled_student_sees_published_lessons_only(app, client):
    with app.app_context():
        student, group, unit = _setup_enrolled()
        _lesson(unit, title="Published One", display_order=0, status=LessonStatus.PUBLISHED.value)
        _lesson(unit, title="Draft Two", display_order=1)
        gpid = group.public_id
    login(client, "stud@example.com")
    html = client.get(_outline_url(gpid)).get_data(as_text=True)
    assert "Published One" in html
    assert "Draft Two" not in html


def test_active_unit_with_no_published_lessons_has_empty_state(app, client):
    with app.app_context():
        student, group, unit = _setup_enrolled()
        _lesson(unit, title="Only A Draft", display_order=0)
        gpid = group.public_id
    login(client, "stud@example.com")
    html = client.get(_outline_url(gpid)).get_data(as_text=True)
    assert "Unit 1" in html
    assert "Only A Draft" not in html
    assert "No lessons in this unit yet" in html


def test_archived_unit_hidden_from_outline(app, client):
    with app.app_context():
        student, group, active_unit = _setup_enrolled()
        _lesson(active_unit, title="Visible", status=LessonStatus.PUBLISHED.value)
        archived_unit = _unit(group, title="Archived Unit", display_order=1,
                              status=AcademicStatus.ARCHIVED.value)
        _lesson(archived_unit, title="Hidden Lesson", status=LessonStatus.PUBLISHED.value)
        gpid = group.public_id
    login(client, "stud@example.com")
    html = client.get(_outline_url(gpid)).get_data(as_text=True)
    assert "Visible" in html
    assert "Archived Unit" not in html
    assert "Hidden Lesson" not in html


def test_withdrawn_enrollment_denied(app, client):
    with app.app_context():
        student = _user("stud@example.com", UserRole.STUDENT.value)
        group = _hierarchy()
        _enroll(group, student, status=EnrollmentStatus.WITHDRAWN.value)
        unit = _unit(group)
        _lesson(unit, title="X", status=LessonStatus.PUBLISHED.value)
        gpid = group.public_id
    login(client, "stud@example.com")
    assert client.get(_outline_url(gpid)).status_code == 404


def test_non_enrolled_student_denied(app, client):
    with app.app_context():
        _setup_enrolled("enrolled@example.com")
        _user("outsider@example.com", UserRole.STUDENT.value)
        group = Group.query.first()
        gpid = group.public_id
    login(client, "outsider@example.com")
    assert client.get(_outline_url(gpid)).status_code == 404


def test_cross_group_denied(app, client):
    with app.app_context():
        student = _user("stud@example.com", UserRole.STUDENT.value)
        mine = _hierarchy(group_name="Mine")
        theirs = _hierarchy(group_name="Theirs")
        _enroll(mine, student)
        _unit(theirs, title="Their Unit")
        theirs_pid = theirs.public_id
    login(client, "stud@example.com")
    assert client.get(_outline_url(theirs_pid)).status_code == 404


@pytest.mark.parametrize(
    "archived", ["term_status", "level_status", "course_status", "group_status"]
)
def test_inactive_hierarchy_hides_outline(app, client, archived):
    with app.app_context():
        student = _user("stud@example.com", UserRole.STUDENT.value)
        group = _hierarchy(**{archived: AcademicStatus.ARCHIVED.value})
        _enroll(group, student)
        unit = _unit(group)
        _lesson(unit, title="P", status=LessonStatus.PUBLISHED.value)
        gpid = group.public_id
    login(client, "stud@example.com")
    assert client.get(_outline_url(gpid)).status_code == 404


# ===========================================================================
# Lesson detail access
# ===========================================================================


def test_published_lesson_detail_renders(app, client):
    with app.app_context():
        student, group, unit = _setup_enrolled()
        lsn = _lesson(unit, title="Reading 1", status=LessonStatus.PUBLISHED.value)
        lsn.description = "Line one\nLine two"
        db.session.commit()
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "stud@example.com")
    resp = client.get(_lesson_url(gpid, upid, lpid))
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Reading 1" in html
    assert "Line one" in html and "Line two" in html
    assert "white-space: pre-wrap" in html


def test_draft_lesson_detail_returns_404(app, client):
    with app.app_context():
        student, group, unit = _setup_enrolled()
        lsn = _lesson(unit, title="Secret Draft")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "stud@example.com")
    resp = client.get(_lesson_url(gpid, upid, lpid))
    assert resp.status_code == 404
    assert b"Secret Draft" not in resp.data


def test_lesson_under_archived_unit_returns_404(app, client):
    with app.app_context():
        student, group, _ = _setup_enrolled()
        unit = _unit(group, title="Archived", display_order=1,
                     status=AcademicStatus.ARCHIVED.value)
        lsn = _lesson(unit, title="P", status=LessonStatus.PUBLISHED.value)
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "stud@example.com")
    assert client.get(_lesson_url(gpid, upid, lpid)).status_code == 404


def test_lesson_under_archived_ancestor_returns_404_status_preserved(app, client):
    with app.app_context():
        student = _user("stud@example.com", UserRole.STUDENT.value)
        group = _hierarchy(course_status=AcademicStatus.ARCHIVED.value)
        _enroll(group, student)
        unit = _unit(group)
        lsn = _lesson(unit, title="P", status=LessonStatus.PUBLISHED.value)
        gpid, upid, lpid, lid = group.public_id, unit.public_id, lsn.public_id, lsn.id
    login(client, "stud@example.com")
    assert client.get(_lesson_url(gpid, upid, lpid)).status_code == 404
    with app.app_context():
        assert db.session.get(Lesson, lid).status == "published"


def test_reactivation_restores_lesson_visibility(app, client):
    with app.app_context():
        student = _user("stud@example.com", UserRole.STUDENT.value)
        group = _hierarchy(course_status=AcademicStatus.ARCHIVED.value)
        _enroll(group, student)
        unit = _unit(group)
        lsn = _lesson(unit, title="P", status=LessonStatus.PUBLISHED.value)
        gpid, upid, lpid, course_id = group.public_id, unit.public_id, lsn.public_id, group.course_id
    login(client, "stud@example.com")
    assert client.get(_lesson_url(gpid, upid, lpid)).status_code == 404
    with app.app_context():
        db.session.get(Course, course_id).status = AcademicStatus.ACTIVE.value
        db.session.commit()
    assert client.get(_lesson_url(gpid, upid, lpid)).status_code == 200


def test_mismatched_nested_ids_return_404(app, client):
    with app.app_context():
        student, group, unit = _setup_enrolled()
        other_unit = _unit(group, title="Other", display_order=1)
        lsn = _lesson(unit, title="P", status=LessonStatus.PUBLISHED.value)
        gpid, other_pid, lpid = group.public_id, other_unit.public_id, lsn.public_id
    login(client, "stud@example.com")
    # lesson belongs to `unit`, not `other_unit`
    assert client.get(_lesson_url(gpid, other_pid, lpid)).status_code == 404


def test_nonexistent_objects_return_404(app, client):
    with app.app_context():
        student, group, unit = _setup_enrolled()
        gpid, upid = group.public_id, unit.public_id
    login(client, "stud@example.com")
    assert client.get(_lesson_url(gpid, upid, "no-such-lesson")).status_code == 404
    assert client.get(_outline_url("no-such-group")).status_code == 404


# ===========================================================================
# Independence from Teacher assignment / Schedule
# ===========================================================================


def test_access_independent_of_teacher_assignment(app, client):
    with app.app_context():
        student, group, unit = _setup_enrolled()
        lsn = _lesson(unit, title="P", status=LessonStatus.PUBLISHED.value)
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        assignment = GroupTeacherAssignment(
            group_id=group.id, teacher_id=teacher.id,
            status=GroupTeacherAssignmentStatus.REMOVED.value,
        )
        db.session.add(assignment)
        db.session.commit()
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "stud@example.com")
    assert client.get(_outline_url(gpid)).status_code == 200
    assert client.get(_lesson_url(gpid, upid, lpid)).status_code == 200


# ===========================================================================
# Query count / leakage
# ===========================================================================


def test_outline_query_count_bounded_no_n_plus_1(app, client):
    with app.app_context():
        student, group, _ = _setup_enrolled()
        for i in range(6):
            unit = _unit(group, title=f"U{i}", display_order=i)
            for j in range(4):
                _lesson(unit, title=f"U{i}L{j}", display_order=j,
                        status=LessonStatus.PUBLISHED.value)
        gpid = group.public_id
    login(client, "stud@example.com")

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        resp = client.get(_outline_url(gpid))
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)

    assert resp.status_code == 200
    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    assert len(selects) <= 6, (len(selects), selects)


def test_no_internal_ids_or_teacher_identity_leaked(app, client):
    with app.app_context():
        student, group, unit = _setup_enrolled()
        teacher = _user("secretteacher@example.com", UserRole.TEACHER.value)
        db.session.add(GroupTeacherAssignment(
            group_id=group.id, teacher_id=teacher.id,
            status=GroupTeacherAssignmentStatus.ACTIVE.value))
        db.session.commit()
        lsn = _lesson(unit, title="P", status=LessonStatus.PUBLISHED.value)
        gpid, gid, upid, uid, lid = group.public_id, group.id, unit.public_id, unit.id, lsn.id
        lpid = lsn.public_id
    login(client, "stud@example.com")
    for path in (_outline_url(gpid), _lesson_url(gpid, upid, lpid)):
        html = client.get(path).get_data(as_text=True)
        assert "secretteacher" not in html
        assert f"/groups/{gid}/" not in html
        assert f"/units/{uid}/" not in html
        assert f'value="{lid}"' not in html


def test_dashboard_open_lessons_link_only_on_operational_cards(app, client):
    with app.app_context():
        student = _user("stud@example.com", UserRole.STUDENT.value)
        operational = _hierarchy(group_name="Operational")
        archived = _hierarchy(group_name="Archived Group", group_status=AcademicStatus.ARCHIVED.value)
        _enroll(operational, student)
        _enroll(archived, student)
        op_pid, arch_pid = operational.public_id, archived.public_id
    login(client, "stud@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    assert f"/student/groups/{op_pid}/units" in html
    assert f"/student/groups/{arch_pid}/units" not in html
