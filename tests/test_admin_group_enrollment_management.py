import itertools
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

import re

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Query, Session

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
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login, make_user

_unique_counter = itertools.count(1)


def _make_term(name=None, status=AcademicStatus.ACTIVE.value):
    name = name or f"Term {next(_unique_counter)}"
    term = AcademicTerm(name=name, start_date=date(2020, 1, 1), end_date=date(2099, 1, 1), status=status)
    db.session.add(term)
    db.session.commit()
    return term


def _make_group(
    term=None, course=None, name=None, capacity=20, status=AcademicStatus.ACTIVE.value, with_teacher=True
):
    """`with_teacher=True` by default: every Group in this file gets an
    eligible active Teacher unless the test is specifically about the
    Teacher-prerequisite rule, so unrelated tests do not need to know
    about that precondition.
    """
    name = name or f"Group {next(_unique_counter)}"
    term = term or _make_term()
    if course is None:
        n = next(_unique_counter)
        level = Level(name=f"Level {n}", display_order=n)
        db.session.add(level)
        db.session.commit()
        course = Course(title=f"Course {n}", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=name, capacity=capacity, status=status)
    db.session.add(group)
    db.session.commit()
    if with_teacher:
        _make_assignment(group)
    return group


def _make_teacher(email=None, full_name="Teacher One", status=UserStatus.ACTIVE.value):
    email = email or f"teacher{next(_unique_counter)}@example.com"
    teacher = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name=full_name,
        role=UserRole.TEACHER.value,
        status=status,
    )
    db.session.add(teacher)
    db.session.commit()
    return teacher


def _make_student(email=None, full_name="Student One", status=UserStatus.ACTIVE.value):
    email = email or f"student{next(_unique_counter)}@example.com"
    student = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name=full_name,
        role=UserRole.STUDENT.value,
        status=status,
    )
    db.session.add(student)
    db.session.commit()
    return student


def _make_assignment(group=None, teacher=None, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    group = group or _make_group(with_teacher=False)
    teacher = teacher or _make_teacher()
    assignment = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id, status=status)
    db.session.add(assignment)
    db.session.commit()
    return assignment


def _make_enrollment(student=None, group=None, status=EnrollmentStatus.ACTIVE.value):
    student = student or _make_student()
    group = group or _make_group()
    enrollment = Enrollment(student_id=student.id, group_id=group.id, status=status)
    db.session.add(enrollment)
    db.session.commit()
    return enrollment


def _active_count(group_id):
    return Enrollment.query.filter_by(group_id=group_id, status=EnrollmentStatus.ACTIVE.value).count()


@contextmanager
def _isolated_app():
    from app import create_app

    flask_app = create_app("testing")
    with flask_app.app_context():
        db.create_all()
    try:
        yield flask_app
    finally:
        with flask_app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def _get_csrf_token(client, path):
    html = client.get(path).get_data(as_text=True)
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return match.group(1) if match else None


NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000"


# ======================================================================
# AUTHORIZATION
# ======================================================================


def test_create_administrator_allowed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": student_pid})
    assert resp.status_code == 302


def test_create_anonymous_denied(app, client):
    with app.app_context():
        group = _make_group()
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    resp = client.post(f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": student_pid})
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_create_student_denied(app, client):
    with app.app_context():
        _make_student(email="denied1@example.com")
        group = _make_group()
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "denied1@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": student_pid})
    assert resp.status_code == 403


def test_create_teacher_denied(app, client):
    with app.app_context():
        make_user("denied2@example.com", UserRole.TEACHER.value)
        group = _make_group()
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "denied2@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": student_pid})
    assert resp.status_code == 403


def test_create_researcher_denied(app, client):
    with app.app_context():
        make_user("denied3@example.com", UserRole.RESEARCHER.value)
        group = _make_group()
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "denied3@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": student_pid})
    assert resp.status_code == 403


def test_withdraw_administrator_allowed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw")
    assert resp.status_code == 302


def test_withdraw_anonymous_denied(app, client):
    with app.app_context():
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_withdraw_student_denied(app, client):
    with app.app_context():
        _make_student(email="denied4@example.com")
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "denied4@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw")
    assert resp.status_code == 403


def test_withdraw_teacher_denied(app, client):
    with app.app_context():
        make_user("denied5@example.com", UserRole.TEACHER.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "denied5@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw")
    assert resp.status_code == 403


def test_withdraw_researcher_denied(app, client):
    with app.app_context():
        make_user("denied6@example.com", UserRole.RESEARCHER.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "denied6@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw")
    assert resp.status_code == 403


def test_reactivate_administrator_allowed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate")
    assert resp.status_code == 302


def test_reactivate_anonymous_denied(app, client):
    with app.app_context():
        enrollment = _make_enrollment(status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_reactivate_student_denied(app, client):
    with app.app_context():
        _make_student(email="denied7@example.com")
        enrollment = _make_enrollment(status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "denied7@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate")
    assert resp.status_code == 403


def test_reactivate_teacher_denied(app, client):
    with app.app_context():
        make_user("denied8@example.com", UserRole.TEACHER.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "denied8@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate")
    assert resp.status_code == 403


def test_reactivate_researcher_denied(app, client):
    with app.app_context():
        make_user("denied9@example.com", UserRole.RESEARCHER.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "denied9@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate")
    assert resp.status_code == 403


# ======================================================================
# CSRF
# ======================================================================


def test_create_csrf_enforced():
    with _isolated_app() as flask_app:
        client = flask_app.test_client()
        flask_app.config["WTF_CSRF_ENABLED"] = True
        with flask_app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            group = _make_group()
            student = _make_student()
            group_pid, student_pid = group.public_id, student.public_id

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(
                f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": student_pid}
            )
            assert resp.status_code == 400
            assert Enrollment.query.filter_by(group_id=group.id, student_id=student.id).first() is None


def test_withdraw_csrf_enforced():
    with _isolated_app() as flask_app:
        client = flask_app.test_client()
        flask_app.config["WTF_CSRF_ENABLED"] = True
        with flask_app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
            group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw")
            assert resp.status_code == 400
            assert (
                Enrollment.query.filter_by(public_id=enrollment_pid).first().status
                == EnrollmentStatus.ACTIVE.value
            )


def test_reactivate_csrf_enforced():
    with _isolated_app() as flask_app:
        client = flask_app.test_client()
        flask_app.config["WTF_CSRF_ENABLED"] = True
        with flask_app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            enrollment = _make_enrollment(status=EnrollmentStatus.WITHDRAWN.value)
            group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate")
            assert resp.status_code == 400
            assert (
                Enrollment.query.filter_by(public_id=enrollment_pid).first().status
                == EnrollmentStatus.WITHDRAWN.value
            )


# ======================================================================
# NESTED IDENTIFIER / IDOR PROTECTION
# ======================================================================


def test_create_unknown_group_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        student_pid = student.public_id
    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{NONEXISTENT_UUID}/enrollments", data={"student_public_id": student_pid})
    assert resp.status_code == 404


def test_create_numeric_group_id_not_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        numeric_group_id, student_pid = group.id, student.public_id
    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{numeric_group_id}/enrollments", data={"student_public_id": student_pid})
    assert resp.status_code == 404


def test_withdraw_unknown_enrollment_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        group_pid = group.public_id
    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{NONEXISTENT_UUID}/withdraw")
    assert resp.status_code == 404


def test_withdraw_numeric_enrollment_id_not_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        group_pid, numeric_enrollment_id = enrollment.group.public_id, enrollment.id
    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{numeric_enrollment_id}/withdraw")
    assert resp.status_code == 404


def test_withdraw_enrollment_from_another_group_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        other_group = _make_group(name="Other Group")
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)  # belongs to a different group
        other_group_pid, enrollment_pid = other_group.public_id, enrollment.public_id
    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{other_group_pid}/enrollments/{enrollment_pid}/withdraw")
    assert resp.status_code == 404
    with app.app_context():
        assert Enrollment.query.filter_by(public_id=enrollment_pid).first().status == EnrollmentStatus.ACTIVE.value


def test_reactivate_enrollment_from_another_group_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        other_group = _make_group(name="Another Group")
        enrollment = _make_enrollment(status=EnrollmentStatus.WITHDRAWN.value)
        other_group_pid, enrollment_pid = other_group.public_id, enrollment.public_id
    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{other_group_pid}/enrollments/{enrollment_pid}/reactivate")
    assert resp.status_code == 404


def test_create_unknown_student_public_id_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        group_pid = group.public_id
    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": NONEXISTENT_UUID})
    assert resp.status_code == 302
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group.id).first() is None


def test_create_numeric_student_id_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        group_pid, numeric_student_id = group.public_id, student.id
    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": str(numeric_student_id)}
    )
    assert resp.status_code == 302
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group.id).first() is None


# ======================================================================
# CREATION -- BUSINESS RULES
# ======================================================================


def test_create_active_student_succeeds(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Target Group")
        student = _make_student(full_name="New Student")
        group_pid, student_pid = group.public_id, student.public_id
        group_id, student_id = group.id, student.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"enrolled" in resp.data.lower()
    with app.app_context():
        enrollment = Enrollment.query.filter_by(group_id=group_id, student_id=student_id).first()
        assert enrollment is not None
        assert enrollment.status == EnrollmentStatus.ACTIVE.value
        assert enrollment.public_id is not None


def test_create_suspended_student_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student(status=UserStatus.SUSPENDED.value)
        group_pid, student_pid = group.public_id, student.public_id
        group_id, student_id = group.id, student.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"not active" in resp.data.lower()
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group_id, student_id=student_id).first() is None
        assert db.session.get(User, student_id).status == UserStatus.SUSPENDED.value


def test_create_teacher_rejected_as_student(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        teacher = make_user("nonstudentt@example.com", UserRole.TEACHER.value)
        group_pid, teacher_pid = group.public_id, teacher.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": teacher_pid})
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group.id, student_id=teacher.id).first() is None


def test_create_administrator_rejected_as_student(app, client):
    with app.app_context():
        admin = make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        group_pid, admin_pid = group.public_id, admin.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": admin_pid})
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group.id, student_id=admin.id).first() is None


def test_create_researcher_rejected_as_student(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        researcher = make_user("nonstudentr@example.com", UserRole.RESEARCHER.value)
        group_pid, researcher_pid = group.public_id, researcher.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": researcher_pid})
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group.id, student_id=researcher.id).first() is None


def test_create_archived_group_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(status=AcademicStatus.ARCHIVED.value, with_teacher=False)
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
        group_id, student_id = group.id, student.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group_id, student_id=student_id).first() is None


def test_create_duplicate_active_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        _make_enrollment(student=student, group=group, status=EnrollmentStatus.ACTIVE.value)
        group_pid, student_pid = group.public_id, student.public_id
        group_id, student_id = group.id, student.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"already enrolled" in resp.data.lower()
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group_id, student_id=student_id).count() == 1


def test_create_withdrawn_duplicate_not_recreated(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        _make_enrollment(student=student, group=group, status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, student_pid = group.public_id, student.public_id
        group_id, student_id = group.id, student.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"reactivate" in resp.data.lower()
    with app.app_context():
        rows = Enrollment.query.filter_by(group_id=group_id, student_id=student_id).all()
        assert len(rows) == 1
        assert rows[0].status == EnrollmentStatus.WITHDRAWN.value


def test_create_integrity_error_handled_safely(app, client, monkeypatch):
    """Simulates a genuine concurrent race: the pre-insert `existing`
    check (enrollments.py, just before the INSERT) finds nothing because
    no conflicting row exists at read time, but the unique constraint
    still fires at commit time -- exactly the scenario the route's own
    IntegrityError handler is documented to guard against. Mocking
    `db.session.commit` is the only way to exercise that except block
    without genuinely running two concurrent requests.
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
        group_id, student_id = group.id, student.id

    from sqlalchemy.exc import IntegrityError

    login(client, "admin@example.com")

    original_commit = db.session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("insert", {}, Exception("duplicate"))
        return original_commit()

    with patch.object(db.session, "commit", side_effect=flaky_commit):
        resp = client.post(
            f"/admin/groups/{group_pid}/enrollments",
            data={"student_public_id": student_pid},
            follow_redirects=True,
        )
    assert resp.status_code == 200
    assert resp.status_code != 500
    assert b"already enrolled" in resp.data.lower()
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group_id, student_id=student_id).count() == 0


def test_create_route_recheck_rejects_student_removed_after_form_validation(app, client, monkeypatch):
    """Belt-and-braces net: the form already validated the submitted
    student_public_id exists, but the route re-looks-up and locks the
    student independently rather than trusting that read. Simulated here
    by bypassing the form's own existence check and submitting a
    public_id that never existed.
    """
    from app.blueprints.admin.forms import GroupEnrollmentForm

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        group_pid = group.public_id

    monkeypatch.setattr(GroupEnrollmentForm, "validate_student_public_id", lambda self, field: None)
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": NONEXISTENT_UUID},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"no longer exists" in resp.data.lower()


def test_create_route_recheck_rejects_withdrawn_duplicate_when_form_bypassed(app, client, monkeypatch):
    """Mirrors the active-duplicate belt-and-braces recheck: the route's
    own `existing` lookup before the INSERT must independently catch a
    withdrawn duplicate pair too, not only rely on the form's validator.
    """
    from app.blueprints.admin.forms import GroupEnrollmentForm

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        _make_enrollment(student=student, group=group, status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, student_pid = group.public_id, student.public_id
        group_id, student_id = group.id, student.id

    monkeypatch.setattr(GroupEnrollmentForm, "validate_student_public_id", lambda self, field: None)
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"reactivate" in resp.data.lower()
    with app.app_context():
        rows = Enrollment.query.filter_by(group_id=group_id, student_id=student_id).all()
        assert len(rows) == 1
        assert rows[0].status == EnrollmentStatus.WITHDRAWN.value


def test_create_route_recheck_rejects_active_duplicate_when_form_bypassed(app, client, monkeypatch):
    """Mirrors the withdrawn-duplicate belt-and-braces recheck above, for
    the active-duplicate branch of the same route-level `existing` check.
    """
    from app.blueprints.admin.forms import GroupEnrollmentForm

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        _make_enrollment(student=student, group=group, status=EnrollmentStatus.ACTIVE.value)
        group_pid, student_pid = group.public_id, student.public_id
        group_id, student_id = group.id, student.id

    monkeypatch.setattr(GroupEnrollmentForm, "validate_student_public_id", lambda self, field: None)
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already enrolled" in resp.data.lower()
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group_id, student_id=student_id).count() == 1


def test_reactivate_blocked_by_full_capacity(app, client):
    """A withdrawn student's old seat can be taken by someone else before
    they try to come back -- reactivation must re-check capacity just
    like creation does, not only at the time the enrollment first became
    inactive.
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(capacity=1)
        withdrawn_enrollment = _make_enrollment(group=group, status=EnrollmentStatus.WITHDRAWN.value)
        _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = group.public_id, withdrawn_enrollment.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate", follow_redirects=True
    )
    assert b"full capacity" in resp.data.lower()
    with app.app_context():
        assert (
            Enrollment.query.filter_by(public_id=enrollment_pid).first().status == EnrollmentStatus.WITHDRAWN.value
        )


# ======================================================================
# TEACHER PREREQUISITE
# ======================================================================


def test_create_blocked_with_no_assigned_teacher(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(with_teacher=False)
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
        group_id, student_id = group.id, student.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"at least one active teacher" in resp.data.lower()
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group_id, student_id=student_id).first() is None


def test_create_blocked_when_only_assignment_removed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(with_teacher=False)
        _make_assignment(group=group, status=GroupTeacherAssignmentStatus.REMOVED.value)
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"at least one active teacher" in resp.data.lower()


def test_create_blocked_when_only_teacher_suspended(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(with_teacher=False)
        suspended_teacher = _make_teacher(status=UserStatus.SUSPENDED.value)
        _make_assignment(group=group, teacher=suspended_teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"at least one active teacher" in resp.data.lower()


def test_create_blocked_when_assignment_references_non_teacher(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(with_teacher=False)
        corrupt_user = _make_student(email="corruptassign@example.com")
        db.session.add(
            GroupTeacherAssignment(
                group_id=group.id, teacher_id=corrupt_user.id, status=GroupTeacherAssignmentStatus.ACTIVE.value
            )
        )
        db.session.commit()
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"at least one active teacher" in resp.data.lower()


def test_reactivate_blocked_with_no_assigned_teacher(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(with_teacher=False)
        enrollment = _make_enrollment(group=group, status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate", follow_redirects=True
    )
    assert b"at least one active teacher" in resp.data.lower()
    with app.app_context():
        assert (
            Enrollment.query.filter_by(public_id=enrollment_pid).first().status == EnrollmentStatus.WITHDRAWN.value
        )


def test_reactivate_succeeds_with_eligible_active_teacher(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        enrollment = _make_enrollment(group=group, status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate", follow_redirects=True
    )
    assert b"reactivated" in resp.data.lower()


def test_existing_enrollment_visible_when_group_has_no_teacher(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(with_teacher=False, name="Teacherless Group")
        student = _make_student(full_name="Visible Student")
        _make_enrollment(student=student, group=group, status=EnrollmentStatus.ACTIVE.value)
        group_pid = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{group_pid}/members").get_data(as_text=True)
    assert "Visible Student" in html


# ======================================================================
# SAME COURSE / ACADEMIC TERM CONFLICT
# ======================================================================


def test_create_blocked_by_conflicting_active_enrollment(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        level = Level(name="Conflict Level", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="Conflict Course", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group_a = _make_group(term=term, course=course, name="Group Alpha")
        group_b = _make_group(term=term, course=course, name="Group Beta")
        student = _make_student()
        _make_enrollment(student=student, group=group_a, status=EnrollmentStatus.ACTIVE.value)
        group_b_pid, student_pid = group_b.public_id, student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_b_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"already actively enrolled" in resp.data.lower()


def test_reactivate_blocked_by_conflicting_active_enrollment(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        level = Level(name="Conflict Level2", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="Conflict Course2", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group_a = _make_group(term=term, course=course, name="Group Gamma")
        group_b = _make_group(term=term, course=course, name="Group Delta")
        student = _make_student()
        _make_enrollment(student=student, group=group_a, status=EnrollmentStatus.ACTIVE.value)
        enrollment_b = _make_enrollment(student=student, group=group_b, status=EnrollmentStatus.WITHDRAWN.value)
        group_b_pid, enrollment_b_pid = group_b.public_id, enrollment_b.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_b_pid}/enrollments/{enrollment_b_pid}/reactivate", follow_redirects=True
    )
    assert b"already actively enrolled" in resp.data.lower()


def test_conflict_error_identifies_the_other_group(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        level = Level(name="Conflict Level3", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="Conflict Course3", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group_a = _make_group(term=term, course=course, name="Named Group Alpha")
        group_b = _make_group(term=term, course=course, name="Named Group Beta")
        student = _make_student()
        _make_enrollment(student=student, group=group_a, status=EnrollmentStatus.ACTIVE.value)
        group_b_pid, student_pid = group_b.public_id, student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_b_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"named group alpha" in resp.data.lower()


def test_different_course_same_term_allowed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        group_a = _make_group(term=term, name="Course A Group")
        group_b = _make_group(term=term, name="Course B Group")
        student = _make_student()
        _make_enrollment(student=student, group=group_a, status=EnrollmentStatus.ACTIVE.value)
        group_b_pid, student_pid = group_b.public_id, student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_b_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"enrolled" in resp.data.lower()


def test_same_course_different_term_allowed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = Level(name="Shared Level", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="Shared Course", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        term_a = _make_term(name="Term One")
        term_b = _make_term(name="Term Two")
        group_a = _make_group(term=term_a, course=course, name="Term One Group")
        group_b = _make_group(term=term_b, course=course, name="Term Two Group")
        student = _make_student()
        _make_enrollment(student=student, group=group_a, status=EnrollmentStatus.ACTIVE.value)
        group_b_pid, student_pid = group_b.public_id, student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_b_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"enrolled" in resp.data.lower()


def test_withdraw_then_enroll_elsewhere_allowed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        level = Level(name="Switch Level", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="Switch Course", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group_a = _make_group(term=term, course=course, name="Switch Group A")
        group_b = _make_group(term=term, course=course, name="Switch Group B")
        student = _make_student()
        enrollment_a = _make_enrollment(student=student, group=group_a, status=EnrollmentStatus.ACTIVE.value)
        group_a_pid, enrollment_a_pid = group_a.public_id, enrollment_a.public_id
        group_b_pid, student_pid = group_b.public_id, student.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_a_pid}/enrollments/{enrollment_a_pid}/withdraw")
    resp = client.post(
        f"/admin/groups/{group_b_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"enrolled" in resp.data.lower()


def test_withdrawn_enrollment_in_other_group_does_not_conflict(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        level = Level(name="NoConflict Level", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="NoConflict Course", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group_a = _make_group(term=term, course=course, name="NoConflict Group A")
        group_b = _make_group(term=term, course=course, name="NoConflict Group B")
        student = _make_student()
        _make_enrollment(student=student, group=group_a, status=EnrollmentStatus.WITHDRAWN.value)
        group_b_pid, student_pid = group_b.public_id, student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_b_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"enrolled" in resp.data.lower()


# ======================================================================
# CAPACITY
# ======================================================================


def test_capacity_zero_active_creation_succeeds(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(capacity=1)
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"enrolled" in resp.data.lower()


def test_capacity_full_next_creation_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(capacity=1)
        _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        second_student = _make_student()
        group_pid, second_student_pid = group.public_id, second_student.public_id
        group_id = group.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": second_student_pid},
        follow_redirects=True,
    )
    assert b"full capacity" in resp.data.lower()
    with app.app_context():
        assert _active_count(group_id) == 1


def test_withdrawn_enrollment_does_not_consume_capacity(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(capacity=1)
        _make_enrollment(group=group, status=EnrollmentStatus.WITHDRAWN.value)
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"enrolled" in resp.data.lower()


def test_exact_final_seat_can_be_filled(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(capacity=2)
        _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
        group_id = group.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"enrolled" in resp.data.lower()
    with app.app_context():
        assert _active_count(group_id) == 2


def test_creation_after_exact_capacity_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(capacity=2)
        _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
        group_id = group.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"full capacity" in resp.data.lower()
    with app.app_context():
        assert _active_count(group_id) == 2


def test_corrupted_enrollment_does_not_count_toward_capacity(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(capacity=1)
        corrupt_user = make_user("corruptcap@example.com", UserRole.TEACHER.value)
        db.session.add(Enrollment(student_id=corrupt_user.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"enrolled" in resp.data.lower()


def test_suspended_student_active_enrollment_still_counts_toward_capacity(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(capacity=1)
        suspended_student = _make_student(status=UserStatus.SUSPENDED.value)
        _make_enrollment(student=suspended_student, group=group, status=EnrollmentStatus.ACTIVE.value)
        other_student = _make_student()
        group_pid, other_student_pid = group.public_id, other_student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": other_student_pid},
        follow_redirects=True,
    )
    assert b"full capacity" in resp.data.lower()


# ======================================================================
# ARCHIVED GROUP
# ======================================================================


def test_withdraw_rejected_for_archived_group(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        enrollment = _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = group.public_id, enrollment.public_id
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw", follow_redirects=True)
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert Enrollment.query.filter_by(public_id=enrollment_pid).first().status == EnrollmentStatus.ACTIVE.value


def test_reactivate_rejected_for_archived_group(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        enrollment = _make_enrollment(group=group, status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = group.public_id, enrollment.public_id
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate", follow_redirects=True
    )
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert (
            Enrollment.query.filter_by(public_id=enrollment_pid).first().status == EnrollmentStatus.WITHDRAWN.value
        )


# ======================================================================
# STATUS BEHAVIOR
# ======================================================================


def test_suspended_student_active_enrollment_can_be_withdrawn(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        enrollment = _make_enrollment(student=student, status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
        student.status = UserStatus.SUSPENDED.value
        db.session.commit()
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw", follow_redirects=True)
    assert b"withdrawn" in resp.data.lower()


def test_reactivate_suspended_student_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        enrollment = _make_enrollment(student=student, status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
        student.status = UserStatus.SUSPENDED.value
        db.session.commit()
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate", follow_redirects=True
    )
    assert b"not active" in resp.data.lower()


def test_already_active_withdraw_and_reactivate_idempotent(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate", follow_redirects=True
    )
    assert b"already active" in resp.data.lower()
    with app.app_context():
        assert Enrollment.query.filter_by(public_id=enrollment_pid).count() == 1


def test_already_withdrawn_double_withdraw_idempotent(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw", follow_redirects=True)
    assert b"already withdrawn" in resp.data.lower()
    with app.app_context():
        assert Enrollment.query.filter_by(public_id=enrollment_pid).count() == 1


# ======================================================================
# TRANSACTION / LOCKING STRUCTURE
#
# SQLite (used throughout this test suite) has no SELECT ... FOR UPDATE
# syntax and no REPEATABLE READ snapshot isolation to begin with --
# these tests can only prove the fixed lock order and the transaction-
# boundary reset the code *requests*, not that SQLite honours either.
# The real guarantee (current reads, actual blocking between concurrent
# transactions) is only real on MySQL/InnoDB in production.
# ======================================================================


def _locked_entity_sequence():
    calls = []
    original = Query.with_for_update

    def spy(self, *args, **kwargs):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        calls.append(getattr(entity, "__name__", None))
        return original(self, *args, **kwargs)

    return calls, spy


def _transaction_reset_events():
    events = []
    original_rollback = db.session.rollback
    original_lock = Query.with_for_update

    def rollback_spy(*args, **kwargs):
        events.append("rollback")
        return original_rollback(*args, **kwargs)

    def lock_spy(self, *args, **kwargs):
        events.append("lock")
        return original_lock(self, *args, **kwargs)

    return events, rollback_spy, lock_spy


def test_create_locks_group_then_student(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "admin@example.com")

    calls, spy = _locked_entity_sequence()
    with patch.object(Query, "with_for_update", spy):
        client.post(f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": student_pid})
    assert calls == ["Group", "User"]


def test_withdraw_locks_group_then_student_then_enrollment(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    calls, spy = _locked_entity_sequence()
    with patch.object(Query, "with_for_update", spy):
        client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw")
    assert calls == ["Group", "User", "Enrollment"]


def test_reactivate_locks_group_then_student_then_enrollment(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    calls, spy = _locked_entity_sequence()
    with patch.object(Query, "with_for_update", spy):
        client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate")
    assert calls == ["Group", "User", "Enrollment"]


def test_create_resets_transaction_before_first_lock(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "admin@example.com")

    events, rollback_spy, lock_spy = _transaction_reset_events()
    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch.object(
        Query, "with_for_update", lock_spy
    ):
        client.post(f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": student_pid})

    assert "rollback" in events
    assert events.index("rollback") < events.index("lock")


def test_withdraw_resets_transaction_before_first_lock(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    events, rollback_spy, lock_spy = _transaction_reset_events()
    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch.object(
        Query, "with_for_update", lock_spy
    ):
        client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw")

    assert "rollback" in events
    assert events.index("rollback") < events.index("lock")


def test_reactivate_resets_transaction_before_first_lock(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    events, rollback_spy, lock_spy = _transaction_reset_events()
    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch.object(
        Query, "with_for_update", lock_spy
    ):
        client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate")

    assert "rollback" in events
    assert events.index("rollback") < events.index("lock")


def test_withdraw_does_not_rely_on_session_refresh(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    with patch.object(Session, "refresh", autospec=True) as spy:
        resp = client.post(
            f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw", follow_redirects=True
        )
    assert b"withdrawn" in resp.data.lower()
    assert not spy.called


def test_reactivate_does_not_rely_on_session_refresh(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    with patch.object(Session, "refresh", autospec=True) as spy:
        resp = client.post(
            f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate", follow_redirects=True
        )
    assert b"reactivated" in resp.data.lower()
    assert not spy.called


def test_create_route_recheck_catches_what_form_missed(app, client, monkeypatch):
    from app.blueprints.admin.forms import GroupEnrollmentForm

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(with_teacher=False)
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
        group_id, student_id = group.id, student.id
    login(client, "admin@example.com")

    monkeypatch.setattr(GroupEnrollmentForm, "validate_student_public_id", lambda self, field: None)

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"at least one active teacher" in resp.data.lower()
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group_id, student_id=student_id).first() is None


def test_create_post_lock_conflict_recheck_rejects_when_form_bypassed(app, client, monkeypatch):
    from app.blueprints.admin.forms import GroupEnrollmentForm

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        level = Level(name="PostLockConflict Level", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="PostLockConflict Course", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group_a = _make_group(term=term, course=course, name="PostLock Group A")
        group_b = _make_group(term=term, course=course, name="PostLock Group B")
        student = _make_student()
        _make_enrollment(student=student, group=group_a, status=EnrollmentStatus.ACTIVE.value)
        group_b_pid, student_pid = group_b.public_id, student.public_id
        group_b_id, student_id = group_b.id, student.id
    login(client, "admin@example.com")

    monkeypatch.setattr(GroupEnrollmentForm, "validate_student_public_id", lambda self, field: None)

    resp = client.post(
        f"/admin/groups/{group_b_pid}/enrollments", data={"student_public_id": student_pid}
    )
    assert resp.status_code == 302
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group_b_id, student_id=student_id).first() is None


# ======================================================================
# NO HARD DELETE / DATA PRESERVATION
# ======================================================================


def test_withdraw_does_not_delete_row(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw")
    with app.app_context():
        assert Enrollment.query.filter_by(public_id=enrollment_pid).count() == 1


def test_withdraw_preserves_identity_and_history(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        enrollment_id, created_at = enrollment.id, enrollment.created_at
        student_id, group_id = enrollment.student_id, enrollment.group_id
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw")

    with app.app_context():
        reloaded = Enrollment.query.filter_by(public_id=enrollment_pid).first()
        assert reloaded.id == enrollment_id
        assert reloaded.student_id == student_id
        assert reloaded.group_id == group_id
        assert reloaded.created_at == created_at


def test_unrelated_enrollment_unaffected_by_another_groups_mutation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        untouched = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        untouched_id = untouched.id
        other_enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        other_group_pid, other_enrollment_pid = other_enrollment.group.public_id, other_enrollment.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{other_group_pid}/enrollments/{other_enrollment_pid}/withdraw")

    with app.app_context():
        assert db.session.get(Enrollment, untouched_id).status == EnrollmentStatus.ACTIVE.value


# ======================================================================
# TRANSACTION-ORDER CORRECTION (pre-validation vs. protected transaction)
#
# These tests prove the fix for the regression where the protected
# transaction (reset + Group lock) was started too early -- before form
# validation / the initial nested lookup -- so the first snapshot-
# establishing read inside the fresh transaction was still one of those
# ordinary, pre-lock queries rather than something issued only after the
# Student lock. The required structure is:
#
#   ordinary pre-validation work (unlocked, may run against a stale
#   snapshot -- nothing critical is decided from it)
#   -> deliberate transaction reset (`_get_group_locked_or_404`)
#   -> Group lock
#   -> Student lock                      <-- nothing ordinary in between
#   -> Enrollment lock (Withdraw/Reactivate only)
#   -> every critical business recheck, now safe to trust
#
# As throughout this suite, SQLite has no SELECT ... FOR UPDATE syntax
# and no REPEATABLE READ snapshot isolation -- these tests can only prove
# the *order* of operations the code requests (rollback before lock,
# lock before lock, no query interleaved), not that SQLite itself
# actually blocks a concurrent transaction or serves a stale snapshot.
# The real concurrency guarantee is only real on MySQL/InnoDB.
# ======================================================================


def _lock_event_spy(events):
    original_with_for_update = Query.with_for_update

    def lock_spy(self, *args, **kwargs):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        events.append(f"lock:{getattr(entity, '__name__', '?')}")
        return original_with_for_update(self, *args, **kwargs)

    return lock_spy


def _rollback_event_spy(events):
    original_rollback = db.session.rollback

    def rollback_spy(*args, **kwargs):
        events.append("reset")
        return original_rollback(*args, **kwargs)

    return rollback_spy


def test_create_event_order_is_form_validate_then_reset_then_group_then_student_lock(app, client, monkeypatch):
    """Proves requirements 1-3: GroupEnrollmentForm validation happens
    before the deliberate transaction reset, and the reset is
    immediately followed by the Group lock and then the Student lock
    with nothing else recorded in between.
    """
    from app.blueprints.admin.forms import GroupEnrollmentForm

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "admin@example.com")

    events = []

    original_validate = GroupEnrollmentForm.validate_on_submit

    def validate_spy(self, *args, **kwargs):
        result = original_validate(self, *args, **kwargs)
        events.append("form_validate")
        return result

    monkeypatch.setattr(GroupEnrollmentForm, "validate_on_submit", validate_spy)

    with patch.object(db.session, "rollback", side_effect=_rollback_event_spy(events)), patch.object(
        Query, "with_for_update", _lock_event_spy(events)
    ):
        resp = client.post(f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": student_pid})

    assert resp.status_code == 302
    assert events == ["form_validate", "reset", "lock:Group", "lock:User"]


def test_create_no_ordinary_select_between_group_lock_and_student_lock(app, client):
    """Proves requirement 3 at the raw-SQL level: the very first two
    SELECTs issued after the transaction reset are the Group lock and
    then the Student lock, back to back -- no ordinary business-check
    query (capacity, teacher count, duplicate/conflict lookups) is
    interleaved between them.
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        group_pid, student_pid = group.public_id, student.public_id
    login(client, "admin@example.com")

    statements = []
    original_rollback = db.session.rollback

    def rollback_spy(*args, **kwargs):
        statements.append(("RESET", None))
        return original_rollback(*args, **kwargs)

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        upper = statement.strip().upper()
        if not upper.startswith("SELECT"):
            return
        if "FROM GROUPS" in upper:
            statements.append(("SELECT", "groups"))
        elif "FROM USERS" in upper:
            statements.append(("SELECT", "users"))
        elif "FROM ENROLLMENTS" in upper:
            statements.append(("SELECT", "enrollments"))

    with patch.object(db.session, "rollback", side_effect=rollback_spy):
        sa_event.listen(db.engine, "before_cursor_execute", before_cursor_execute)
        try:
            client.post(f"/admin/groups/{group_pid}/enrollments", data={"student_public_id": student_pid})
        finally:
            sa_event.remove(db.engine, "before_cursor_execute", before_cursor_execute)

    reset_index = next(i for i, s in enumerate(statements) if s[0] == "RESET")
    after_reset = statements[reset_index + 1 :]
    assert after_reset[0] == ("SELECT", "groups")
    assert after_reset[1] == ("SELECT", "users")


def test_create_recheck_catches_conflict_created_between_form_validation_and_lock(app, client, monkeypatch):
    """Proves requirements 4-5, and is the direct regression test for the
    reported bug: the form's own conflict validator passes (no
    conflicting Enrollment exists yet when it runs). A conflicting active
    Enrollment for the same Student/Course/Academic Term is then created
    -- simulating a concurrent request's commit -- in the window between
    that form validation and the fresh Group lock. The route's own
    post-lock conflict recheck must still catch it and reject creation;
    nothing must be inserted.
    """
    import app.blueprints.admin.enrollments as enrollments_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        level = Level(name="RaceLevel", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="RaceCourse", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group_a = _make_group(term=term, course=course, name="Race Group A")
        group_b = _make_group(term=term, course=course, name="Race Group B")
        student = _make_student()
        group_b_pid, student_pid = group_b.public_id, student.public_id
        group_a_id, group_b_id, student_id = group_a.id, group_b.id, student.id
    login(client, "admin@example.com")

    original_get_group_locked_or_404 = enrollments_module._get_group_locked_or_404

    def create_conflict_then_lock(group_public_id):
        # By the time this runs, form validation (including its own
        # conflict check) has already passed against a read with no
        # conflict. Insert the conflicting row now, exactly like a
        # concurrent request's commit landing in this window.
        db.session.add(
            Enrollment(student_id=student_id, group_id=group_a_id, status=EnrollmentStatus.ACTIVE.value)
        )
        db.session.commit()
        return original_get_group_locked_or_404(group_public_id)

    monkeypatch.setattr(enrollments_module, "_get_group_locked_or_404", create_conflict_then_lock)

    resp = client.post(
        f"/admin/groups/{group_b_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"already actively enrolled" in resp.data.lower()
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group_b_id, student_id=student_id).first() is None


def test_withdraw_event_order_is_lookup_then_reset_then_group_student_enrollment_locks(app, client, monkeypatch):
    """Proves requirement 6: the ordinary nested Enrollment lookup runs
    first, then the reset, then locks in the order Group -> Student ->
    Enrollment, with nothing else recorded in between.
    """
    import app.blueprints.admin.enrollments as enrollments_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    events = []

    original_lookup = enrollments_module._get_enrollment_for_group_or_404

    def lookup_spy(group, enrollment_public_id):
        result = original_lookup(group, enrollment_public_id)
        events.append("ordinary_lookup")
        return result

    monkeypatch.setattr(enrollments_module, "_get_enrollment_for_group_or_404", lookup_spy)

    with patch.object(db.session, "rollback", side_effect=_rollback_event_spy(events)), patch.object(
        Query, "with_for_update", _lock_event_spy(events)
    ):
        resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw")

    assert resp.status_code == 302
    assert events == ["ordinary_lookup", "reset", "lock:Group", "lock:User", "lock:Enrollment"]


def test_reactivate_event_order_is_lookup_then_reset_then_group_student_enrollment_locks(app, client, monkeypatch):
    """Proves requirement 7: same sequence as Withdraw."""
    import app.blueprints.admin.enrollments as enrollments_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.WITHDRAWN.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    events = []

    original_lookup = enrollments_module._get_enrollment_for_group_or_404

    def lookup_spy(group, enrollment_public_id):
        result = original_lookup(group, enrollment_public_id)
        events.append("ordinary_lookup")
        return result

    monkeypatch.setattr(enrollments_module, "_get_enrollment_for_group_or_404", lookup_spy)

    with patch.object(db.session, "rollback", side_effect=_rollback_event_spy(events)), patch.object(
        Query, "with_for_update", _lock_event_spy(events)
    ):
        resp = client.post(f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/reactivate")

    assert resp.status_code == 302
    assert events == ["ordinary_lookup", "reset", "lock:Group", "lock:User", "lock:Enrollment"]


def test_reactivate_recheck_catches_conflict_created_between_lookup_and_locks(app, client, monkeypatch):
    """Proves requirement 8: Reactivate's cross-Group conflict check must
    catch a conflicting Enrollment even when it is created concurrently
    in the window between the initial ordinary nested lookup and the
    protected locks -- i.e. the check genuinely runs after all of Group,
    Student, and Enrollment are locked in the fresh transaction, not
    against the earlier read.
    """
    import app.blueprints.admin.enrollments as enrollments_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        level = Level(name="ReactRaceLevel", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="ReactRaceCourse", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group_a = _make_group(term=term, course=course, name="ReactRace Group A")
        group_b = _make_group(term=term, course=course, name="ReactRace Group B")
        student = _make_student()
        enrollment_b = _make_enrollment(student=student, group=group_b, status=EnrollmentStatus.WITHDRAWN.value)
        group_b_pid, enrollment_b_pid = group_b.public_id, enrollment_b.public_id
        group_a_id, student_id = group_a.id, student.id
    login(client, "admin@example.com")

    original_get_group_locked_or_404 = enrollments_module._get_group_locked_or_404

    def create_conflict_then_lock(group_public_id):
        db.session.add(
            Enrollment(student_id=student_id, group_id=group_a_id, status=EnrollmentStatus.ACTIVE.value)
        )
        db.session.commit()
        return original_get_group_locked_or_404(group_public_id)

    monkeypatch.setattr(enrollments_module, "_get_group_locked_or_404", create_conflict_then_lock)

    resp = client.post(
        f"/admin/groups/{group_b_pid}/enrollments/{enrollment_b_pid}/reactivate", follow_redirects=True
    )
    assert b"already actively enrolled" in resp.data.lower()
    with app.app_context():
        assert (
            Enrollment.query.filter_by(public_id=enrollment_b_pid).first().status
            == EnrollmentStatus.WITHDRAWN.value
        )


def test_create_uses_freshly_locked_group_object_not_preview_object(app, client, monkeypatch):
    """Proves requirement 9 for Create: the archived-Group check reads
    `group.status` from the object returned by `_get_group_locked_or_404`
    (the fresh, locked read) -- not from `preview_group` (the earlier,
    ordinary read used only for 404 handling and form construction).

    A DB-level UPDATE cannot cleanly simulate this in a single-session
    SQLite test: Flask-SQLAlchemy's default `expire_on_commit=True` would
    transparently refresh `preview_group.status` on next access after any
    commit, which would make the early (pre-lock) archived check catch it
    too and mask exactly the distinction this test needs to prove. So the
    freshly-locked Group object's `status` is forced directly in Python
    instead -- a Python object distinct from `preview_group`, which is
    left reporting ACTIVE. If the route's decision still read
    `preview_group`, it would incorrectly proceed to enroll the student.
    """
    import app.blueprints.admin.enrollments as enrollments_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        group_pid, student_pid, group_id = group.public_id, student.public_id, group.id
    login(client, "admin@example.com")

    original_get_group_locked_or_404 = enrollments_module._get_group_locked_or_404

    def locked_but_archived(gpid):
        locked_group = original_get_group_locked_or_404(gpid)
        locked_group.status = AcademicStatus.ARCHIVED.value
        return locked_group

    monkeypatch.setattr(enrollments_module, "_get_group_locked_or_404", locked_but_archived)

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert Enrollment.query.filter_by(group_id=group_id, student_id=student.id).first() is None


def test_withdraw_uses_freshly_locked_enrollment_object_not_initial_object(app, client, monkeypatch):
    """Proves requirement 9 for Withdraw: whether this Enrollment can be
    withdrawn is decided from the object returned by
    `_lock_student_and_enrollment_for_group` (the fresh, locked read) --
    not from `initial` (the earlier, ordinary nested lookup). The freshly
    locked Enrollment object's status is forced to WITHDRAWN in Python
    (see the comment on the Create counterpart above for why a DB-level
    UPDATE cannot cleanly simulate this); `initial` is a distinct object
    left reporting ACTIVE. If the route's decision still read `initial`,
    it would incorrectly proceed to "withdraw" it again instead of
    reporting it already withdrawn.
    """
    import app.blueprints.admin.enrollments as enrollments_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        group_pid, enrollment_pid = enrollment.group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    original_lock = enrollments_module._lock_student_and_enrollment_for_group

    def lock_but_withdrawn(group, enrollment_id, student_id):
        locked_student, locked_enrollment = original_lock(group, enrollment_id, student_id)
        locked_enrollment.status = EnrollmentStatus.WITHDRAWN.value
        return locked_student, locked_enrollment

    monkeypatch.setattr(enrollments_module, "_lock_student_and_enrollment_for_group", lock_but_withdrawn)

    resp = client.post(
        f"/admin/groups/{group_pid}/enrollments/{enrollment_pid}/withdraw", follow_redirects=True
    )
    assert b"already withdrawn" in resp.data.lower()
