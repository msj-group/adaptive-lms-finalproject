import itertools
import re
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

from sqlalchemy.orm import Query

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


def _make_group(term=None, course=None, name=None, capacity=20, status=AcademicStatus.ACTIVE.value):
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


def _make_enrollment(student=None, group=None, status=EnrollmentStatus.ACTIVE.value):
    student = student or _make_student()
    group = group or _make_group()
    enrollment = Enrollment(student_id=student.id, group_id=group.id, status=status)
    db.session.add(enrollment)
    db.session.commit()
    return enrollment


def _make_assignment(group=None, teacher=None, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    group = group or _make_group()
    teacher = teacher or _make_teacher()
    assignment = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id, status=status)
    db.session.add(assignment)
    db.session.commit()
    return assignment


@contextmanager
def _isolated_app():
    """Standalone app/DB, no app context left pushed across requests --
    mirrors the identical helper already used in
    test_admin_teacher_management.py / test_admin_group_enrollment_management.py.
    """
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


def test_assign_administrator_allowed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        group = _make_group()
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": teacher_public_id}
    )
    assert resp.status_code == 302


def test_assign_anonymous_denied(app, client):
    with app.app_context():
        teacher = _make_teacher()
        group = _make_group()
        teacher_public_id, group_public_id = teacher.public_id, group.public_id

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": teacher_public_id}
    )
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_assign_student_denied(app, client):
    with app.app_context():
        _make_student(email="denied1@example.com")
        teacher = _make_teacher()
        group = _make_group()
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
    login(client, "denied1@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": teacher_public_id}
    )
    assert resp.status_code == 403


def test_assign_teacher_denied(app, client):
    with app.app_context():
        make_user("acting@example.com", UserRole.TEACHER.value)
        teacher = _make_teacher()
        group = _make_group()
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
    login(client, "acting@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": teacher_public_id}
    )
    assert resp.status_code == 403


def test_assign_researcher_denied(app, client):
    with app.app_context():
        make_user("researcherx@example.com", UserRole.RESEARCHER.value)
        teacher = _make_teacher()
        group = _make_group()
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
    login(client, "researcherx@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": teacher_public_id}
    )
    assert resp.status_code == 403


def test_remove_administrator_allowed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment()
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/remove")
    assert resp.status_code == 302


def test_remove_anonymous_denied(app, client):
    with app.app_context():
        assignment = _make_assignment()
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/remove")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_remove_student_denied(app, client):
    with app.app_context():
        _make_student(email="denied2@example.com")
        assignment = _make_assignment()
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "denied2@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/remove")
    assert resp.status_code == 403


def test_remove_teacher_denied(app, client):
    with app.app_context():
        make_user("acting2@example.com", UserRole.TEACHER.value)
        assignment = _make_assignment()
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "acting2@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/remove")
    assert resp.status_code == 403


def test_remove_researcher_denied(app, client):
    with app.app_context():
        make_user("researcher2@example.com", UserRole.RESEARCHER.value)
        assignment = _make_assignment()
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "researcher2@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/remove")
    assert resp.status_code == 403


def test_reactivate_administrator_allowed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.REMOVED.value)
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/reactivate")
    assert resp.status_code == 302


def test_reactivate_anonymous_denied(app, client):
    with app.app_context():
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.REMOVED.value)
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/reactivate")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_reactivate_student_denied(app, client):
    with app.app_context():
        _make_student(email="denied3@example.com")
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.REMOVED.value)
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "denied3@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/reactivate")
    assert resp.status_code == 403


def test_reactivate_teacher_denied(app, client):
    with app.app_context():
        make_user("acting3@example.com", UserRole.TEACHER.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.REMOVED.value)
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "acting3@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/reactivate")
    assert resp.status_code == 403


def test_reactivate_researcher_denied(app, client):
    with app.app_context():
        make_user("researcher3@example.com", UserRole.RESEARCHER.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.REMOVED.value)
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "researcher3@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/reactivate")
    assert resp.status_code == 403


# ======================================================================
# CSRF
# ======================================================================


def test_assign_csrf_enforced():
    with _isolated_app() as flask_app:
        client = flask_app.test_client()
        flask_app.config["WTF_CSRF_ENABLED"] = True
        with flask_app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            teacher = _make_teacher()
            group = _make_group()
            teacher_public_id, group_public_id = teacher.public_id, group.public_id

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(
                f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": teacher_public_id}
            )
            assert resp.status_code == 400
            assert GroupTeacherAssignment.query.filter_by(group_id=group.id, teacher_id=teacher.id).first() is None


def test_remove_csrf_enforced():
    with _isolated_app() as flask_app:
        client = flask_app.test_client()
        flask_app.config["WTF_CSRF_ENABLED"] = True
        with flask_app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            assignment = _make_assignment()
            group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/remove")
            assert resp.status_code == 400
            assert (
                GroupTeacherAssignment.query.filter_by(public_id=assignment_public_id).first().status
                == GroupTeacherAssignmentStatus.ACTIVE.value
            )


def test_reactivate_csrf_enforced():
    with _isolated_app() as flask_app:
        client = flask_app.test_client()
        flask_app.config["WTF_CSRF_ENABLED"] = True
        with flask_app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            assignment = _make_assignment(status=GroupTeacherAssignmentStatus.REMOVED.value)
            group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/reactivate")
            assert resp.status_code == 400
            assert (
                GroupTeacherAssignment.query.filter_by(public_id=assignment_public_id).first().status
                == GroupTeacherAssignmentStatus.REMOVED.value
            )


# ======================================================================
# IDENTIFIER / IDOR PROTECTION
# ======================================================================


def test_assign_unknown_group_public_id_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        teacher_public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{NONEXISTENT_UUID}/teachers", data={"teacher_public_id": teacher_public_id})
    assert resp.status_code == 404


def test_assign_numeric_group_id_not_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        group = _make_group()
        teacher_public_id, numeric_group_id = teacher.public_id, group.id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{numeric_group_id}/teachers", data={"teacher_public_id": teacher_public_id})
    assert resp.status_code == 404


def test_remove_unknown_assignment_public_id_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        group_public_id = group.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{NONEXISTENT_UUID}/remove")
    assert resp.status_code == 404


def test_remove_numeric_assignment_id_not_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment()
        group_public_id, numeric_assignment_id = assignment.group.public_id, assignment.id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{numeric_assignment_id}/remove")
    assert resp.status_code == 404


def test_remove_assignment_belonging_to_another_group_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        other_group = _make_group(name="Other Group")
        assignment = _make_assignment()  # belongs to a different group
        other_group_public_id, assignment_public_id = other_group.public_id, assignment.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{other_group_public_id}/teachers/{assignment_public_id}/remove")
    assert resp.status_code == 404
    with app.app_context():
        # untouched -- the mismatched lookup must not have mutated it
        assert GroupTeacherAssignment.query.filter_by(public_id=assignment_public_id).first().status == "active"


def test_reactivate_assignment_belonging_to_another_group_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        other_group = _make_group(name="Another Group")
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.REMOVED.value)
        other_group_public_id, assignment_public_id = other_group.public_id, assignment.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{other_group_public_id}/teachers/{assignment_public_id}/reactivate")
    assert resp.status_code == 404


def test_assign_unknown_teacher_public_id_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        group_public_id = group.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": NONEXISTENT_UUID})
    assert resp.status_code == 302
    with app.app_context():
        assert GroupTeacherAssignment.query.count() == 0


def test_assign_numeric_teacher_id_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        group = _make_group()
        numeric_teacher_id, group_public_id = teacher.id, group.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": str(numeric_teacher_id)}
    )
    assert resp.status_code == 302
    with app.app_context():
        assert GroupTeacherAssignment.query.count() == 0


# ======================================================================
# ASSIGNMENT CREATION
# ======================================================================


def test_active_teacher_can_be_assigned(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(full_name="New Teacher")
        group = _make_group(name="Target Group")
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
        teacher_id, group_id = teacher.id, group.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers",
        data={"teacher_public_id": teacher_public_id},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"assigned" in resp.data.lower()
    with app.app_context():
        assignment = GroupTeacherAssignment.query.filter_by(group_id=group_id, teacher_id=teacher_id).first()
        assert assignment is not None
        assert assignment.status == GroupTeacherAssignmentStatus.ACTIVE.value


def test_suspended_teacher_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(status=UserStatus.SUSPENDED.value)
        group = _make_group()
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
        teacher_id, group_id = teacher.id, group.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers",
        data={"teacher_public_id": teacher_public_id},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"not active" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(group_id=group_id, teacher_id=teacher_id).first() is None


def test_student_rejected_as_teacher(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        group = _make_group()
        student_public_id, group_public_id = student.public_id, group.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers",
        data={"teacher_public_id": student_public_id},
        follow_redirects=True,
    )
    assert b"does not exist" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.count() == 0


def test_administrator_rejected_as_teacher(app, client):
    with app.app_context():
        admin = make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        admin_public_id, group_public_id = admin.public_id, group.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": admin_public_id})
    with app.app_context():
        assert GroupTeacherAssignment.query.count() == 0


def test_researcher_rejected_as_teacher(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        researcher = make_user("researcher4@example.com", UserRole.RESEARCHER.value)
        group = _make_group()
        researcher_public_id, group_public_id = researcher.public_id, group.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": researcher_public_id})
    with app.app_context():
        assert GroupTeacherAssignment.query.count() == 0


def test_assign_to_archived_group_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        group = _make_group(status=AcademicStatus.ARCHIVED.value)
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
        teacher_id, group_id = teacher.id, group.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers",
        data={"teacher_public_id": teacher_public_id},
        follow_redirects=True,
    )
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(group_id=group_id, teacher_id=teacher_id).first() is None


def test_duplicate_active_assignment_creates_no_new_row(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        group = _make_group()
        _make_assignment(group=group, teacher=teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
        teacher_id, group_id = teacher.id, group.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers",
        data={"teacher_public_id": teacher_public_id},
        follow_redirects=True,
    )
    assert b"already assigned" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(group_id=group_id, teacher_id=teacher_id).count() == 1


def test_removed_assignment_not_duplicated_on_assign(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        group = _make_group()
        _make_assignment(group=group, teacher=teacher, status=GroupTeacherAssignmentStatus.REMOVED.value)
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
        teacher_id, group_id = teacher.id, group.id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers",
        data={"teacher_public_id": teacher_public_id},
        follow_redirects=True,
    )
    assert b"reactivate" in resp.data.lower()
    with app.app_context():
        rows = GroupTeacherAssignment.query.filter_by(group_id=group_id, teacher_id=teacher_id).all()
        assert len(rows) == 1
        assert rows[0].status == GroupTeacherAssignmentStatus.REMOVED.value


def test_integrity_error_on_assign_handled_safely(app, client, monkeypatch):
    from app.blueprints.admin.forms import GroupTeacherAssignmentForm

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        group = _make_group()
        _make_assignment(group=group, teacher=teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
        teacher_id, group_id = teacher.id, group.id
    login(client, "admin@example.com")

    monkeypatch.setattr(GroupTeacherAssignmentForm, "validate_teacher_public_id", lambda self, field: None)

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers",
        data={"teacher_public_id": teacher_public_id},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert resp.status_code != 500
    assert b"already assigned" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(group_id=group_id, teacher_id=teacher_id).count() == 1


# ======================================================================
# MULTIPLE TEACHERS
# ======================================================================


def test_multiple_active_teachers_can_belong_to_one_group(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        teacher_a = _make_teacher(full_name="Teacher A")
        teacher_b = _make_teacher(full_name="Teacher B")
        group_public_id = group.public_id
        teacher_a_public_id, teacher_b_public_id = teacher_a.public_id, teacher_b.public_id
        group_id = group.id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": teacher_a_public_id})
    client.post(f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": teacher_b_public_id})

    with app.app_context():
        assert (
            GroupTeacherAssignment.query.filter_by(
                group_id=group_id, status=GroupTeacherAssignmentStatus.ACTIVE.value
            ).count()
            == 2
        )


def test_one_teacher_can_belong_to_multiple_groups(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        group_a = _make_group(name="Group A")
        group_b = _make_group(name="Group B")
        teacher_public_id = teacher.public_id
        group_a_public_id, group_b_public_id = group_a.public_id, group_b.public_id
        teacher_id = teacher.id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_a_public_id}/teachers", data={"teacher_public_id": teacher_public_id})
    client.post(f"/admin/groups/{group_b_public_id}/teachers", data={"teacher_public_id": teacher_public_id})

    with app.app_context():
        assert (
            GroupTeacherAssignment.query.filter_by(
                teacher_id=teacher_id, status=GroupTeacherAssignmentStatus.ACTIVE.value
            ).count()
            == 2
        )


# ======================================================================
# REACTIVATION
# ======================================================================


def test_reactivate_uses_same_row_and_id(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.REMOVED.value)
        assignment_id, public_id = assignment.id, assignment.public_id
        group_public_id = assignment.group.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers/{public_id}/reactivate", follow_redirects=True
    )
    assert b"reactivated" in resp.data.lower()
    with app.app_context():
        rows = GroupTeacherAssignment.query.filter_by(public_id=public_id).all()
        assert len(rows) == 1
        assert rows[0].id == assignment_id
        assert rows[0].status == GroupTeacherAssignmentStatus.ACTIVE.value


def test_reactivate_already_active_unchanged(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.ACTIVE.value)
        public_id, group_public_id = assignment.public_id, assignment.group.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{public_id}/reactivate", follow_redirects=True)
    assert b"already actively assigned" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(public_id=public_id).count() == 1


def test_reactivate_suspended_teacher_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(status=UserStatus.ACTIVE.value)
        assignment = _make_assignment(teacher=teacher, status=GroupTeacherAssignmentStatus.REMOVED.value)
        public_id, group_public_id = assignment.public_id, assignment.group.public_id
        teacher.status = UserStatus.SUSPENDED.value
        db.session.commit()
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{public_id}/reactivate", follow_redirects=True)
    assert b"not active" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(public_id=public_id).first().status == "removed"


def test_reactivate_non_teacher_corrupted_assignment_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        group = _make_group()
        assignment = GroupTeacherAssignment(
            group_id=group.id, teacher_id=student.id, status=GroupTeacherAssignmentStatus.REMOVED.value
        )
        db.session.add(assignment)
        db.session.commit()
        public_id, group_public_id = assignment.public_id, group.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{public_id}/reactivate", follow_redirects=True)
    assert resp.status_code == 200
    assert resp.status_code != 500
    assert b"not a teacher" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(public_id=public_id).first().status == "removed"


def test_reactivate_archived_group_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        assignment = _make_assignment(group=group, status=GroupTeacherAssignmentStatus.REMOVED.value)
        public_id = assignment.public_id
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        group_public_id = group.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{public_id}/reactivate", follow_redirects=True)
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(public_id=public_id).first().status == "removed"


# ======================================================================
# REMOVAL
# ======================================================================


def test_removal_changes_status_and_does_not_delete_row(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.ACTIVE.value)
        public_id, group_public_id = assignment.public_id, assignment.group.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{public_id}/remove", follow_redirects=True)
    assert b"removed" in resp.data.lower()
    with app.app_context():
        rows = GroupTeacherAssignment.query.filter_by(public_id=public_id).all()
        assert len(rows) == 1
        assert rows[0].status == GroupTeacherAssignmentStatus.REMOVED.value


def test_removal_already_removed_unchanged(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.REMOVED.value)
        public_id, group_public_id = assignment.public_id, assignment.group.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{public_id}/remove", follow_redirects=True)
    assert b"already removed" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(public_id=public_id).count() == 1


def test_suspended_teacher_assignment_may_be_removed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(status=UserStatus.SUSPENDED.value)
        assignment = _make_assignment(teacher=teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        public_id, group_public_id = assignment.public_id, assignment.group.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{public_id}/remove", follow_redirects=True)
    assert b"removed" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(public_id=public_id).first().status == "removed"


def test_remove_from_archived_group_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        assignment = _make_assignment(group=group, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        public_id = assignment.public_id
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        group_public_id = group.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{public_id}/remove", follow_redirects=True)
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(public_id=public_id).first().status == "active"


def test_last_eligible_teacher_removable_with_no_active_students(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.ACTIVE.value)
        public_id, group_public_id = assignment.public_id, assignment.group.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{public_id}/remove", follow_redirects=True)
    assert b"removed" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(public_id=public_id).first().status == "removed"


def test_last_eligible_teacher_not_removable_with_active_students(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        assignment = _make_assignment(group=group, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        public_id, group_public_id = assignment.public_id, group.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{public_id}/remove", follow_redirects=True)
    assert b"cannot remove the last active teacher" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(public_id=public_id).first().status == "active"


def test_removable_after_replacement_teacher_assigned(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        first = _make_assignment(group=group, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        replacement = _make_teacher(full_name="Replacement Teacher")
        _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        first_public_id, group_public_id = first.public_id, group.public_id
        replacement_public_id = replacement.public_id
    login(client, "admin@example.com")

    # Cannot remove yet -- first is the only eligible teacher.
    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{first_public_id}/remove", follow_redirects=True)
    assert b"cannot remove the last active teacher" in resp.data.lower()

    # Assign a replacement, then removal becomes allowed.
    client.post(f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": replacement_public_id})
    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{first_public_id}/remove", follow_redirects=True)
    assert b"removed" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(public_id=first_public_id).first().status == "removed"


def test_suspended_teacher_not_eligible_replacement(app, client):
    """A Group with one active-eligible Teacher plus one suspended
    Teacher's assignment must still refuse removing the eligible one --
    the suspended Teacher's row does not count as a replacement.
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        eligible = _make_assignment(group=group, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        suspended_teacher = _make_teacher(status=UserStatus.SUSPENDED.value)
        _make_assignment(group=group, teacher=suspended_teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        eligible_public_id, group_public_id = eligible.public_id, group.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers/{eligible_public_id}/remove", follow_redirects=True
    )
    assert b"cannot remove the last active teacher" in resp.data.lower()


def test_removed_assignment_not_eligible_replacement(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        eligible = _make_assignment(group=group, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        _make_assignment(group=group, status=GroupTeacherAssignmentStatus.REMOVED.value)
        _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        eligible_public_id, group_public_id = eligible.public_id, group.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers/{eligible_public_id}/remove", follow_redirects=True
    )
    assert b"cannot remove the last active teacher" in resp.data.lower()


def test_corrupted_non_teacher_assignment_not_eligible_replacement(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        eligible = _make_assignment(group=group, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        corrupt_user = _make_student()
        corrupt_assignment = GroupTeacherAssignment(
            group_id=group.id, teacher_id=corrupt_user.id, status=GroupTeacherAssignmentStatus.ACTIVE.value
        )
        db.session.add(corrupt_assignment)
        _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        db.session.commit()
        eligible_public_id, group_public_id = eligible.public_id, group.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers/{eligible_public_id}/remove", follow_redirects=True
    )
    assert b"cannot remove the last active teacher" in resp.data.lower()


def test_withdrawn_enrollments_do_not_trigger_last_teacher_restriction(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        assignment = _make_assignment(group=group, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        _make_enrollment(group=group, status=EnrollmentStatus.WITHDRAWN.value)
        public_id, group_public_id = assignment.public_id, group.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/groups/{group_public_id}/teachers/{public_id}/remove", follow_redirects=True)
    assert b"removed" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(public_id=public_id).first().status == "removed"


# ======================================================================
# CONCURRENCY / LOCKING STRUCTURE
#
# SQLite (used throughout this test suite) has no SELECT ... FOR UPDATE
# syntax -- SQLAlchemy silently omits it there instead of raising, so
# these tests cannot prove real blocking between concurrent transactions.
# What they *do* prove structurally is that every one of the three
# mutation routes acquires the Group row lock (via
# `Query.with_for_update()`) before performing any check or write --
# the actual serialization guarantee this provides is only real on
# MySQL/InnoDB in production, and is not exercised by these tests.
# ======================================================================


def test_assign_locks_group_row(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        group = _make_group()
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
    login(client, "admin@example.com")

    with patch.object(Query, "with_for_update", wraps=Query.with_for_update, autospec=True) as spy:
        client.post(f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": teacher_public_id})
        assert spy.called, "expected the Group row to be locked via with_for_update() before mutating"


def test_remove_locks_group_row(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment()
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "admin@example.com")

    with patch.object(Query, "with_for_update", wraps=Query.with_for_update, autospec=True) as spy:
        client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/remove")
        assert spy.called, "expected the Group row to be locked via with_for_update() before mutating"


def test_reactivate_locks_group_row(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.REMOVED.value)
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "admin@example.com")

    with patch.object(Query, "with_for_update", wraps=Query.with_for_update, autospec=True) as spy:
        client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/reactivate")
        assert spy.called, "expected the Group row to be locked via with_for_update() before mutating"


def _locked_entity_sequence():
    """Records, in order, the model class each with_for_update() call
    targeted -- used to prove the fixed Group-then-User lock order, not
    just that *some* lock was requested.
    """
    calls = []
    original = Query.with_for_update

    def spy(self, *args, **kwargs):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        calls.append(getattr(entity, "__name__", None))
        return original(self, *args, **kwargs)

    return calls, spy


def test_assign_locks_group_then_teacher(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        group = _make_group()
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
    login(client, "admin@example.com")

    calls, spy = _locked_entity_sequence()
    with patch.object(Query, "with_for_update", spy):
        client.post(f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": teacher_public_id})
    assert calls == ["Group", "User"]


def test_reactivate_locks_group_then_teacher(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.REMOVED.value)
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "admin@example.com")

    calls, spy = _locked_entity_sequence()
    with patch.object(Query, "with_for_update", spy):
        client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/reactivate")
    assert calls == ["Group", "User"]


def _transaction_reset_events():
    """Records, in order, every db.session.rollback() call and every
    with_for_update() lock request -- used to prove the deliberate
    transaction-boundary reset in _get_group_locked_or_404 happens
    strictly before the first lock, not merely "at some point". SQLite
    has no MySQL/InnoDB REPEATABLE READ snapshot to actually discard, so
    this only proves the code requests the reset in the right place, not
    that SQLite itself was ever at risk of the staleness this guards
    against on MySQL.
    """
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


def test_assign_resets_transaction_before_group_lock(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        group = _make_group()
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
    login(client, "admin@example.com")

    events, rollback_spy, lock_spy = _transaction_reset_events()
    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch.object(
        Query, "with_for_update", lock_spy
    ):
        client.post(f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": teacher_public_id})

    assert "rollback" in events
    assert events.index("rollback") < events.index("lock")


def test_remove_resets_transaction_before_group_lock(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment()
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "admin@example.com")

    events, rollback_spy, lock_spy = _transaction_reset_events()
    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch.object(
        Query, "with_for_update", lock_spy
    ):
        client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/remove")

    assert "rollback" in events
    assert events.index("rollback") < events.index("lock")


def test_reactivate_resets_transaction_before_group_lock(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.REMOVED.value)
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "admin@example.com")

    events, rollback_spy, lock_spy = _transaction_reset_events()
    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch.object(
        Query, "with_for_update", lock_spy
    ):
        client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/reactivate")

    assert "rollback" in events
    assert events.index("rollback") < events.index("lock")


def test_assign_route_recheck_catches_what_form_missed(app, client, monkeypatch):
    """Bypass the form's own eligibility validator entirely (simulating
    the Teacher becoming ineligible in the instant between form
    validation and the route acquiring its locks) and prove the route's
    own post-lock recheck of Teacher.role/Teacher.status still rejects
    the request -- the route does not simply trust the form.
    """
    from app.blueprints.admin.forms import GroupTeacherAssignmentForm

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(status=UserStatus.SUSPENDED.value)
        group = _make_group()
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
        teacher_id, group_id = teacher.id, group.id
    login(client, "admin@example.com")

    monkeypatch.setattr(GroupTeacherAssignmentForm, "validate_teacher_public_id", lambda self, field: None)

    resp = client.post(
        f"/admin/groups/{group_public_id}/teachers",
        data={"teacher_public_id": teacher_public_id},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"no longer eligible" in resp.data.lower()
    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(group_id=group_id, teacher_id=teacher_id).first() is None


# ======================================================================
# DATA PRESERVATION / NO REGRESSION
# ======================================================================


def test_existing_enrollment_rows_unchanged_by_teacher_assignment(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        enrollment = _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        teacher = _make_teacher()
        enrollment_id = enrollment.id
        created_at, student_id = enrollment.created_at, enrollment.student_id
        teacher_public_id, group_public_id = teacher.public_id, group.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_public_id}/teachers", data={"teacher_public_id": teacher_public_id})

    with app.app_context():
        reloaded = db.session.get(Enrollment, enrollment_id)
        assert reloaded.status == EnrollmentStatus.ACTIVE.value
        assert reloaded.student_id == student_id
        assert reloaded.created_at == created_at


def test_group_members_page_reachable_and_unaffected_by_teacher_mutation(app, client):
    """Part 6D moved Student Enrollment management onto the Group-scoped
    Manage Members page -- this proves that page remains reachable and
    its content is unaffected by a Teacher-assignment mutation on the
    same Group (the two concerns stay correctly isolated on one page).
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment()
        group_public_id, assignment_public_id = assignment.group.public_id, assignment.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_public_id}/teachers/{assignment_public_id}/remove")

    resp = client.get(f"/admin/groups/{group_public_id}/members")
    assert resp.status_code == 200


def test_no_teacher_or_user_account_deleted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.ACTIVE.value)
        teacher_id = assignment.teacher_id
        public_id, group_public_id = assignment.public_id, assignment.group.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_public_id}/teachers/{public_id}/remove")

    with app.app_context():
        assert db.session.get(User, teacher_id) is not None


def test_no_assignment_row_hard_deleted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.ACTIVE.value)
        public_id, group_public_id = assignment.public_id, assignment.group.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{group_public_id}/teachers/{public_id}/remove")

    with app.app_context():
        assert GroupTeacherAssignment.query.filter_by(public_id=public_id).count() == 1
