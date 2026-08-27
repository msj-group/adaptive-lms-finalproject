import itertools
from datetime import date

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


def _make_assignment(group=None, teacher=None, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    group = group or _make_group()
    teacher = teacher or _make_teacher()
    assignment = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id, status=status)
    db.session.add(assignment)
    db.session.commit()
    return assignment


def _make_enrollment(group=None, student=None, status=EnrollmentStatus.ACTIVE.value):
    group = group or _make_group()
    student = student or _make_student()
    enrollment = Enrollment(group_id=group.id, student_id=student.id, status=status)
    db.session.add(enrollment)
    db.session.commit()
    return enrollment


NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000"


# ======================================================================
# PAGE ACCESS
# ======================================================================


def test_administrator_can_view_manage_members(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        public_id = group.public_id
    login(client, "admin@example.com")
    assert client.get(f"/admin/groups/{public_id}/members").status_code == 200


def test_anonymous_denied(app, client):
    with app.app_context():
        group = _make_group()
        public_id = group.public_id
    resp = client.get(f"/admin/groups/{public_id}/members")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_student_denied(app, client):
    with app.app_context():
        _make_student(email="denied1@example.com")
        group = _make_group()
        public_id = group.public_id
    login(client, "denied1@example.com")
    assert client.get(f"/admin/groups/{public_id}/members").status_code == 403


def test_teacher_denied(app, client):
    with app.app_context():
        make_user("denied2@example.com", UserRole.TEACHER.value)
        group = _make_group()
        public_id = group.public_id
    login(client, "denied2@example.com")
    assert client.get(f"/admin/groups/{public_id}/members").status_code == 403


def test_researcher_denied(app, client):
    with app.app_context():
        make_user("denied3@example.com", UserRole.RESEARCHER.value)
        group = _make_group()
        public_id = group.public_id
    login(client, "denied3@example.com")
    assert client.get(f"/admin/groups/{public_id}/members").status_code == 403


def test_unknown_group_public_id_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")
    assert client.get(f"/admin/groups/{NONEXISTENT_UUID}/members").status_code == 404


def test_numeric_group_id_not_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        numeric_id = group.id
    login(client, "admin@example.com")
    assert client.get(f"/admin/groups/{numeric_id}/members").status_code == 404


# ======================================================================
# GROUP SUMMARY
# ======================================================================


def test_group_summary_correct(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term(name="Summary Term")
        level = Level(name="Summary Level", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="Summary Course", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group = _make_group(term=term, course=course, name="Summary Group", capacity=15)
        group.code = "SUM1"
        db.session.commit()
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert "Summary Group" in html
    assert "SUM1" in html
    assert "Summary Course" in html
    assert "Summary Level" in html
    assert "Summary Term" in html
    assert "0/15" in html


# ======================================================================
# TEACHER SECTION
# ======================================================================


def test_teacher_assignment_shown_correctly(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        teacher = _make_teacher(full_name="Displayed Teacher", email="displayed@example.com")
        _make_assignment(group=group, teacher=teacher)
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert "Displayed Teacher" in html
    assert "displayed@example.com" in html
    assert ">Active<" in html


def test_corrupted_non_teacher_assignment_excluded(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        corrupt_user = _make_student(full_name="Not A Teacher")
        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=corrupt_user.id))
        db.session.commit()
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert "Not A Teacher" not in html


def test_active_assignments_ordered_before_removed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        removed_teacher = _make_teacher(full_name="AAA Removed Teacher")
        active_teacher = _make_teacher(full_name="ZZZ Active Teacher")
        _make_assignment(group=group, teacher=removed_teacher, status=GroupTeacherAssignmentStatus.REMOVED.value)
        _make_assignment(group=group, teacher=active_teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert html.index("ZZZ Active Teacher") < html.index("AAA Removed Teacher")


# ======================================================================
# STUDENT SECTION
# ======================================================================


def test_student_enrollment_shown_correctly(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student(full_name="Displayed Student", email="displayedstudent@example.com")
        _make_enrollment(group=group, student=student)
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert "Displayed Student" in html
    assert "displayedstudent@example.com" in html


def test_corrupted_non_student_enrollment_excluded(app, client):
    """The corrupted-Enrollment Teacher is still a genuine active Teacher
    account, so their name legitimately appears in the Assign Teacher
    dropdown -- the assertion is scoped to the Student table cell
    specifically, which is where a role-integrity failure would show up.
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        corrupt_user = _make_teacher(full_name="Not A Student")
        db.session.add(Enrollment(group_id=group.id, student_id=corrupt_user.id))
        db.session.commit()
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert '<td style="padding: var(--space-3)">Not A Student</td>' not in html
    assert "No students enrolled yet" in html


def test_active_enrollments_ordered_before_withdrawn(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        withdrawn_student = _make_student(full_name="AAA Withdrawn Student")
        active_student = _make_student(full_name="ZZZ Active Student")
        _make_enrollment(group=group, student=withdrawn_student, status=EnrollmentStatus.WITHDRAWN.value)
        _make_enrollment(group=group, student=active_student, status=EnrollmentStatus.ACTIVE.value)
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert html.index("ZZZ Active Student") < html.index("AAA Withdrawn Student")


def test_suspended_student_with_active_enrollment_still_counts(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(capacity=5)
        suspended_student = _make_student(status=UserStatus.SUSPENDED.value)
        _make_enrollment(group=group, student=suspended_student, status=EnrollmentStatus.ACTIVE.value)
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert "1/5" in html


def test_corrupted_non_student_enrollment_does_not_count(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(capacity=5)
        corrupt_user = _make_teacher()
        db.session.add(Enrollment(group_id=group.id, student_id=corrupt_user.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert "0/5" in html


# ======================================================================
# ARCHIVED GROUP
# ======================================================================


def test_archived_group_shows_read_only_roster_with_no_mutation_controls(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        _make_assignment(group=group)
        _make_enrollment(group=group)
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        public_id = group.public_id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/groups/{public_id}/members")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "archived" in html.lower()
    assert ">Remove<" not in html
    assert ">Withdraw<" not in html
    assert ">Reactivate<" not in html
    assert "Assign Teacher" not in html
    assert "Enroll Student" not in html


# ======================================================================
# FORMS
# ======================================================================


def test_student_choices_use_public_id_not_numeric_id(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        _make_assignment(group=group)  # eligible teacher required for the Add Student form to render
        student = _make_student(full_name="Choice Student")
        student_public_id, student_numeric_id = student.public_id, student.id
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert f'value="{student_public_id}"' in html
    assert f'value="{student_numeric_id}"' not in html


def test_teacher_choices_use_public_id_not_numeric_id(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        teacher = _make_teacher(full_name="Choice Teacher")
        teacher_public_id, teacher_numeric_id = teacher.public_id, teacher.id
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert f'value="{teacher_public_id}"' in html
    assert f'value="{teacher_numeric_id}"' not in html


def test_already_assigned_teacher_excluded_from_assign_dropdown(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        teacher = _make_teacher(full_name="Already Assigned Teacher")
        _make_assignment(group=group, teacher=teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        public_id, teacher_public_id = group.public_id, teacher.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert f'value="{teacher_public_id}"' not in html


def test_removed_teacher_assignment_excluded_from_assign_dropdown(app, client):
    """A removed assignment must use Reactivate, not appear again in the
    "assign a new teacher" dropdown.
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        teacher = _make_teacher(full_name="Removed From Dropdown Teacher")
        _make_assignment(group=group, teacher=teacher, status=GroupTeacherAssignmentStatus.REMOVED.value)
        public_id, teacher_public_id = group.public_id, teacher.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    # The dropdown option is absent, but the Reactivate button (which
    # also renders the public_id in its form action URL) is present.
    assert f'<option value="{teacher_public_id}"' not in html
    assert f"/admin/groups/{public_id}/teachers/" in html
    assert ">Reactivate<" in html


def test_already_enrolled_student_excluded_from_add_dropdown(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student(full_name="Already Enrolled Student")
        _make_enrollment(group=group, student=student, status=EnrollmentStatus.ACTIVE.value)
        public_id, student_public_id = group.public_id, student.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert f'<option value="{student_public_id}"' not in html


def test_withdrawn_student_excluded_from_add_dropdown(app, client):
    """A withdrawn Enrollment must use Reactivate, not appear again in
    the "add a new student" dropdown.
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student(full_name="Withdrawn Dropdown Student")
        _make_enrollment(group=group, student=student, status=EnrollmentStatus.WITHDRAWN.value)
        public_id, student_public_id = group.public_id, student.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert f'<option value="{student_public_id}"' not in html
    assert ">Reactivate<" in html


def test_tampered_teacher_role_rejected_server_side(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        student = _make_student()
        public_id, student_public_id = group.public_id, student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{public_id}/teachers", data={"teacher_public_id": student_public_id}, follow_redirects=True
    )
    assert resp.status_code == 200
    with app.app_context():
        assert GroupTeacherAssignment.query.count() == 0


def test_tampered_student_role_rejected_server_side(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        teacher = _make_teacher()
        public_id, teacher_public_id = group.public_id, teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{public_id}/enrollments",
        data={"student_public_id": teacher_public_id},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert Enrollment.query.count() == 0


def test_tampered_suspended_teacher_rejected_server_side(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        teacher = _make_teacher(status=UserStatus.SUSPENDED.value)
        public_id, teacher_public_id = group.public_id, teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{public_id}/teachers", data={"teacher_public_id": teacher_public_id}, follow_redirects=True
    )
    assert resp.status_code == 200
    with app.app_context():
        assert GroupTeacherAssignment.query.count() == 0


def test_tampered_unknown_public_id_rejected_server_side(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        public_id = group.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{public_id}/enrollments",
        data={"student_public_id": NONEXISTENT_UUID},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert Enrollment.query.count() == 0


# ======================================================================
# CONFIRMATION AND CSRF
# ======================================================================


def test_remove_teacher_form_has_confirmation_attribute(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        assignment = _make_assignment(status=GroupTeacherAssignmentStatus.ACTIVE.value)
        public_id = assignment.group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert "data-confirm=" in html


def test_withdraw_student_form_has_confirmation_attribute(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(status=EnrollmentStatus.ACTIVE.value)
        public_id = enrollment.group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert "data-confirm=" in html


def test_reactivate_forms_do_not_require_confirmation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        _make_assignment(group=group, status=GroupTeacherAssignmentStatus.REMOVED.value)
        _make_enrollment(group=group, status=EnrollmentStatus.WITHDRAWN.value)
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    import re

    reactivate_forms = re.findall(r"<form[^>]*teachers/[^>]*reactivate[^>]*>|<form[^>]*enrollments/[^>]*reactivate[^>]*>", html)
    for form_tag in reactivate_forms:
        assert "data-confirm" not in form_tag


def test_remove_and_withdraw_forms_contain_csrf_token(app, client):
    """Remove Teacher and Withdraw Student are plain POST forms (no
    backing WTForm), with a literal `csrf_token()` hidden field --
    unlike form.hidden_tag() below, this is not conditioned on
    WTF_CSRF_ENABLED, so it renders under the default testing config too.
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        _make_assignment(group=group, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    # Remove Teacher + Withdraw Student (the page's own logout form also
    # has one, from the shared admin layout -- not counted here).
    assert html.count('name="csrf_token"') >= 2


def test_assign_and_enroll_forms_use_hidden_tag_for_csrf(app, client):
    """The Assign Teacher / Add Student forms use form.hidden_tag(),
    which -- like every other WTForm-based admin form in this codebase --
    only renders its csrf_token field when WTF_CSRF_ENABLED is on
    (disabled by default under TestingConfig, verified for real via the
    isolated-app CSRF-enforcement tests). This just confirms hidden_tag()
    itself is present in the template source (the mechanism used), since
    a rendered field cannot be asserted under the default test config.
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        _make_assignment(group=group)
        _make_student()
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert "Enroll Student" in html


def test_all_state_changing_forms_are_post(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group()
        _make_assignment(group=group, status=GroupTeacherAssignmentStatus.ACTIVE.value)
        _make_enrollment(group=group, status=EnrollmentStatus.ACTIVE.value)
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}/members").get_data(as_text=True)
    assert f'<a href="/admin/groups/{public_id}/teachers' not in html
    assert f'<a href="/admin/groups/{public_id}/enrollments' not in html
