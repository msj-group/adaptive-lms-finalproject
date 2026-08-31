"""Part M07C3 -- the guarded AcademicTerm -> Level -> Course -> Group
lifecycle: parent archive guards, parent-first create/retarget/
reactivation, the archived-Group closure roster, archived-Group conflict
exclusion, and the full Group reactivation guard.

SQLite (the test backend) has no `SELECT ... FOR UPDATE` and no
REPEATABLE READ snapshot isolation, so the structural lock-order tests
here prove only the *requested* order -- never that a real InnoDB lock
blocks a concurrent transaction. That guarantee holds only on
MySQL/InnoDB, and no isolated MySQL test database exists in this project.
"""

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


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _admin(client):
    make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")


def _term(name="Fall 2026", status=AcademicStatus.ACTIVE.value):
    term = AcademicTerm(
        name=name, start_date=date(2026, 9, 1), end_date=date(2026, 12, 31), status=status
    )
    db.session.add(term)
    db.session.commit()
    return term


def _level(name="Level 1", status=AcademicStatus.ACTIVE.value):
    level = Level(name=name, display_order=0, status=status)
    db.session.add(level)
    db.session.commit()
    return level


def _course(level, title="Course A", status=AcademicStatus.ACTIVE.value):
    course = Course(level_id=level.id, title=title, display_order=0, status=status)
    db.session.add(course)
    db.session.commit()
    return course


def _group(term, course, name="Group A", status=AcademicStatus.ACTIVE.value, capacity=20):
    group = Group(
        academic_term_id=term.id,
        course_id=course.id,
        name=name,
        capacity=capacity,
        status=status,
    )
    db.session.add(group)
    db.session.commit()
    return group


def _student(email, status=UserStatus.ACTIVE.value):
    user = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name=f"Student {email}",
        role=UserRole.STUDENT.value,
        status=status,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _teacher(email, status=UserStatus.ACTIVE.value):
    user = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name=f"Teacher {email}",
        role=UserRole.TEACHER.value,
        status=status,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _enroll(group, student, status=EnrollmentStatus.ACTIVE.value):
    row = Enrollment(student_id=student.id, group_id=group.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def _assign(group, teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    row = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def _toggle_group(client, group_public_id):
    return client.post(f"/admin/groups/{group_public_id}/toggle-status", follow_redirects=True)


# ===========================================================================
# Parent archive guards
# ===========================================================================


def test_term_archive_blocked_by_active_group(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        _group(term, _course(_level()), status=AcademicStatus.ACTIVE.value)
        term_pid = term.public_id

    resp = client.post(f"/admin/academic-terms/{term_pid}/toggle-status", follow_redirects=True)
    assert b"cannot be archived" in resp.data.lower()
    with app.app_context():
        assert AcademicTerm.query.filter_by(public_id=term_pid).first().status == "active"


def test_term_archive_allowed_with_only_archived_group(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        _group(term, _course(_level()), status=AcademicStatus.ARCHIVED.value)
        term_pid = term.public_id

    resp = client.post(f"/admin/academic-terms/{term_pid}/toggle-status", follow_redirects=True)
    assert b"is now archived" in resp.data.lower()
    with app.app_context():
        assert AcademicTerm.query.filter_by(public_id=term_pid).first().status == "archived"


def test_term_reactivation_is_never_blocked(app, client):
    with app.app_context():
        _admin(client)
        term = _term(status=AcademicStatus.ARCHIVED.value)
        term_pid = term.public_id

    resp = client.post(f"/admin/academic-terms/{term_pid}/toggle-status", follow_redirects=True)
    assert b"is now active" in resp.data.lower()


def test_course_archive_blocked_by_active_group(app, client):
    with app.app_context():
        _admin(client)
        course = _course(_level())
        _group(_term(), course, status=AcademicStatus.ACTIVE.value)
        course_pid = course.public_id

    resp = client.post(f"/admin/courses/{course_pid}/toggle-status", follow_redirects=True)
    assert b"cannot be archived" in resp.data.lower()
    with app.app_context():
        assert Course.query.filter_by(public_id=course_pid).first().status == "active"


def test_course_archive_allowed_with_only_archived_group(app, client):
    with app.app_context():
        _admin(client)
        course = _course(_level())
        _group(_term(), course, status=AcademicStatus.ARCHIVED.value)
        course_pid = course.public_id

    resp = client.post(f"/admin/courses/{course_pid}/toggle-status", follow_redirects=True)
    assert b"is now archived" in resp.data.lower()


def test_course_reactivation_blocked_while_level_archived(app, client):
    with app.app_context():
        _admin(client)
        level = _level()
        course = _course(level, status=AcademicStatus.ARCHIVED.value)
        level.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        course_pid = course.public_id

    resp = client.post(f"/admin/courses/{course_pid}/toggle-status", follow_redirects=True)
    assert b"cannot be reactivated" in resp.data.lower()
    with app.app_context():
        assert Course.query.filter_by(public_id=course_pid).first().status == "archived"


def test_course_reactivation_allowed_when_level_active(app, client):
    with app.app_context():
        _admin(client)
        course = _course(_level(), status=AcademicStatus.ARCHIVED.value)
        course_pid = course.public_id

    resp = client.post(f"/admin/courses/{course_pid}/toggle-status", follow_redirects=True)
    assert b"is now active" in resp.data.lower()


def test_level_archive_blocked_by_active_course(app, client):
    with app.app_context():
        _admin(client)
        level = _level()
        _course(level, status=AcademicStatus.ACTIVE.value)
        level_pid = level.public_id

    resp = client.post(f"/admin/levels/{level_pid}/toggle-status", follow_redirects=True)
    assert b"cannot be archived" in resp.data.lower()
    with app.app_context():
        assert Level.query.filter_by(public_id=level_pid).first().status == "active"


def test_level_archive_blocked_by_active_group_even_under_archived_course(app, client):
    """The transitive Level rule: an active Group under an *archived*
    Course still blocks archiving that Course's Level."""
    with app.app_context():
        _admin(client)
        level = _level()
        archived_course = _course(level, status=AcademicStatus.ARCHIVED.value)
        _group(_term(), archived_course, status=AcademicStatus.ACTIVE.value)
        level_pid = level.public_id

    resp = client.post(f"/admin/levels/{level_pid}/toggle-status", follow_redirects=True)
    assert b"cannot be archived" in resp.data.lower()
    with app.app_context():
        assert Level.query.filter_by(public_id=level_pid).first().status == "active"


def test_level_archive_allowed_when_all_descendants_archived(app, client):
    with app.app_context():
        _admin(client)
        level = _level()
        archived_course = _course(level, status=AcademicStatus.ARCHIVED.value)
        _group(_term(), archived_course, status=AcademicStatus.ARCHIVED.value)
        level_pid = level.public_id

    resp = client.post(f"/admin/levels/{level_pid}/toggle-status", follow_redirects=True)
    assert b"is now archived" in resp.data.lower()
    with app.app_context():
        assert Level.query.filter_by(public_id=level_pid).first().status == "archived"


def test_level_reactivation_is_never_blocked(app, client):
    with app.app_context():
        _admin(client)
        level = _level(status=AcademicStatus.ARCHIVED.value)
        level_pid = level.public_id

    resp = client.post(f"/admin/levels/{level_pid}/toggle-status", follow_redirects=True)
    assert b"is now active" in resp.data.lower()


def test_blocked_term_archive_causes_no_partial_mutation_or_cascade(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        course = _course(_level())
        group = _group(term, course, status=AcademicStatus.ACTIVE.value)
        student = _student("s1@example.com")
        _enroll(group, student)
        term_pid = term.public_id

    client.post(f"/admin/academic-terms/{term_pid}/toggle-status")

    with app.app_context():
        assert AcademicTerm.query.filter_by(public_id=term_pid).first().status == "active"
        assert Group.query.first().status == "active"
        assert Enrollment.query.first().status == "active"
        assert Course.query.first().status == "active"


# ===========================================================================
# Parent-first Course create / move / reactivation
# ===========================================================================


def _course_create(client, level_id, title="New Course"):
    return client.post(
        "/admin/courses/new",
        data={"level_id": level_id, "title": title, "code": "", "description": ""},
        follow_redirects=True,
    )


def test_course_cannot_be_created_under_archived_level(app, client):
    with app.app_context():
        _admin(client)
        level = _level(status=AcademicStatus.ARCHIVED.value)
        level_id = level.id

    resp = _course_create(client, level_id, "Under Archived")
    assert b"archived level" in resp.data.lower()
    with app.app_context():
        assert Course.query.filter_by(title="Under Archived").first() is None


def test_course_can_be_created_under_active_level(app, client):
    with app.app_context():
        _admin(client)
        level_id = _level().id

    resp = _course_create(client, level_id, "Under Active")
    assert b"created" in resp.data.lower()


def test_course_cannot_be_moved_to_archived_level(app, client):
    import re

    with app.app_context():
        _admin(client)
        level_a = _level("Level A")
        level_b = _level("Level B", status=AcademicStatus.ARCHIVED.value)
        course = _course(level_a, "Movable")
        public_id, level_a_id, level_b_id = course.public_id, level_a.id, level_b.id

    html = client.get(f"/admin/courses/{public_id}/edit").get_data(as_text=True)
    snapshot = re.search(r'name="edit_snapshot" value="([^"]*)"', html).group(1)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data={
            "level_id": level_b_id,
            "title": "Movable",
            "code": "",
            "description": "",
            "edit_snapshot": snapshot,
        },
    )
    assert b"archived level" in resp.data.lower()
    with app.app_context():
        assert Course.query.filter_by(public_id=public_id).first().level_id == level_a_id


def test_course_metadata_edit_allowed_while_its_own_level_archived(app, client):
    """Legacy metadata-correction exception: submitting the Course's
    unchanged current level_id is not a move, so a Course whose Level is
    archived can still have its title fixed."""
    import re

    with app.app_context():
        _admin(client)
        level = _level(status=AcademicStatus.ARCHIVED.value)
        course = _course(level, "Old Title", status=AcademicStatus.ARCHIVED.value)
        public_id, level_id = course.public_id, level.id

    html = client.get(f"/admin/courses/{public_id}/edit").get_data(as_text=True)
    snapshot = re.search(r'name="edit_snapshot" value="([^"]*)"', html).group(1)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data={
            "level_id": level_id,
            "title": "Corrected Title",
            "code": "",
            "description": "",
            "edit_snapshot": snapshot,
        },
        follow_redirects=True,
    )
    assert b"updated" in resp.data.lower()
    with app.app_context():
        assert Course.query.filter_by(public_id=public_id).first().title == "Corrected Title"


# ===========================================================================
# Parent-first Group create / retarget / reactivation
# ===========================================================================


def _group_create(client, term_id, course_id, name="New Group"):
    return client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": name,
            "code": "",
            "capacity": "10",
        },
        follow_redirects=True,
    )


def _group_create_ancestor_case(app, client, archived):
    with app.app_context():
        _admin(client)
        term = _term()
        level = _level()
        course = _course(level)
        if archived == "term":
            term.status = AcademicStatus.ARCHIVED.value
        elif archived == "level":
            level.status = AcademicStatus.ARCHIVED.value
        else:
            course.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        term_id, course_id = term.id, course.id

    resp = _group_create(client, term_id, course_id, f"G-{archived}")
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert Group.query.filter_by(name=f"G-{archived}").first() is None


def test_group_create_rejected_when_academic_term_archived(app, client):
    _group_create_ancestor_case(app, client, "term")


def test_group_create_rejected_when_level_archived(app, client):
    _group_create_ancestor_case(app, client, "level")


def test_group_create_rejected_when_course_archived(app, client):
    _group_create_ancestor_case(app, client, "course")


def test_group_create_succeeds_when_all_ancestors_active(app, client):
    with app.app_context():
        _admin(client)
        term_id = _term().id
        course_id = _course(_level()).id

    resp = _group_create(client, term_id, course_id, "All Active")
    assert b"created" in resp.data.lower()
    with app.app_context():
        assert Group.query.filter_by(name="All Active").first().status == "active"


def _edit_group(client, group_public_id, term_id, course_id, name="Group A", capacity="20"):
    import re

    html = client.get(f"/admin/groups/{group_public_id}/edit").get_data(as_text=True)
    snapshot = re.search(r'name="edit_snapshot" value="([^"]*)"', html).group(1)
    return client.post(
        f"/admin/groups/{group_public_id}/edit",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": name,
            "code": "",
            "capacity": capacity,
            "edit_snapshot": snapshot,
        },
        follow_redirects=True,
    )


def test_group_cannot_be_retargeted_to_archived_course(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        level = _level()
        course_a = _course(level, "Course A")
        course_b = _course(level, "Course B", status=AcademicStatus.ARCHIVED.value)
        group = _group(term, course_a, name="Retarget Me")
        public_id, term_id, course_a_id, course_b_id = (
            group.public_id,
            term.id,
            course_a.id,
            course_b.id,
        )

    resp = _edit_group(client, public_id, term_id, course_b_id, name="Retarget Me")
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert Group.query.filter_by(public_id=public_id).first().course_id == course_a_id


def test_group_cannot_be_retargeted_to_archived_term(app, client):
    with app.app_context():
        _admin(client)
        term_a = _term("Term A")
        term_b = _term("Term B", status=AcademicStatus.ARCHIVED.value)
        course = _course(_level())
        group = _group(term_a, course, name="Retarget Me")
        public_id, term_a_id, term_b_id, course_id = (
            group.public_id,
            term_a.id,
            term_b.id,
            course.id,
        )

    resp = _edit_group(client, public_id, term_b_id, course_id, name="Retarget Me")
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert Group.query.filter_by(public_id=public_id).first().academic_term_id == term_a_id


def test_group_metadata_edit_allowed_while_own_ancestor_archived(app, client):
    """Legacy metadata-correction exception -- submitting the Group's
    unchanged current term + course is not a retarget, so a Group under
    an archived Course can still have its name/capacity fixed."""
    with app.app_context():
        _admin(client)
        term = _term()
        course = _course(_level(), status=AcademicStatus.ARCHIVED.value)
        group = _group(term, course, name="Legacy", capacity=20)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    resp = _edit_group(client, public_id, term_id, course_id, name="Legacy Fixed", capacity="15")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        fixed = Group.query.filter_by(public_id=public_id).first()
        assert fixed.name == "Legacy Fixed"
        assert fixed.capacity == 15


def test_forged_archived_course_id_cannot_bypass_group_create_guard(app, client):
    """The form's <select> is filtered to active courses; a crafted POST
    with an archived course_id still fails the server-side guard."""
    with app.app_context():
        _admin(client)
        term_id = _term().id
        archived_course = _course(_level(), status=AcademicStatus.ARCHIVED.value)
        course_id = archived_course.id

    resp = _group_create(client, term_id, course_id, "Forged")
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert Group.query.filter_by(name="Forged").first() is None


# ===========================================================================
# Archived-Group closure roster + conflict exclusion
# ===========================================================================


def test_archiving_a_group_keeps_all_roster_rows_unchanged(app, client):
    with app.app_context():
        _admin(client)
        group = _group(_term(), _course(_level()), name="Closing", capacity=10)
        s1 = _student("s1@example.com")
        s2 = _student("s2@example.com")
        t1 = _teacher("t1@example.com")
        _enroll(group, s1, EnrollmentStatus.ACTIVE.value)
        _enroll(group, s2, EnrollmentStatus.WITHDRAWN.value)
        _assign(group, t1, GroupTeacherAssignmentStatus.ACTIVE.value)
        group_pid = group.public_id

    resp = _toggle_group(client, group_pid)
    assert b"is now archived" in resp.data.lower()
    with app.app_context():
        assert Group.query.first().status == "archived"
        statuses = sorted(e.status for e in Enrollment.query.all())
        assert statuses == ["active", "withdrawn"]
        assert GroupTeacherAssignment.query.first().status == "active"


def test_archived_group_does_not_block_enrollment_in_another_active_group(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        level = _level()
        course = _course(level)
        archived_group = _group(term, course, name="Archived", capacity=10)
        active_group = _group(term, course, name="Active", capacity=10)
        active_group_pid = active_group.public_id
        teacher = _teacher("t@example.com")
        _assign(active_group, teacher)
        student = _student("student@example.com")
        _enroll(archived_group, student, EnrollmentStatus.ACTIVE.value)
        archived_group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        student_pid = student.public_id

    resp = client.post(
        f"/admin/groups/{active_group_pid}/enrollments",
        data={"student_public_id": student_pid},
        follow_redirects=True,
    )
    assert b"enrolled" in resp.data.lower()
    assert b"already actively enrolled" not in resp.data.lower()


# ===========================================================================
# Group reactivation guard
# ===========================================================================


def _setup_archived_group_with_roster(capacity=10, n_students=1, with_teacher=True, suspended_student=False):
    """Returns the archived Group's public_id (a plain str safe to use
    outside the app context)."""
    term = _term()
    course = _course(_level())
    group = _group(term, course, name="Reactivate Me", capacity=capacity)
    if with_teacher:
        _assign(group, _teacher("guardt@example.com"))
    for i in range(n_students):
        st = _student(
            f"guards{i}@example.com",
            status=(UserStatus.SUSPENDED.value if suspended_student and i == 0 else UserStatus.ACTIVE.value),
        )
        _enroll(group, st)
    group.status = AcademicStatus.ARCHIVED.value
    db.session.commit()
    return group.public_id


def test_zero_student_group_reactivates_without_a_teacher(app, client):
    with app.app_context():
        _admin(client)
        group_pid = _setup_archived_group_with_roster(n_students=0, with_teacher=False)

    resp = _toggle_group(client, group_pid)
    assert b"is now active" in resp.data.lower()
    with app.app_context():
        assert Group.query.first().status == "active"


def test_reactivation_blocked_when_students_but_no_eligible_teacher(app, client):
    with app.app_context():
        _admin(client)
        group_pid = _setup_archived_group_with_roster(n_students=1, with_teacher=False)

    resp = _toggle_group(client, group_pid)
    assert b"no eligible active teacher" in resp.data.lower()
    with app.app_context():
        assert Group.query.first().status == "archived"


def test_reactivation_blocked_when_teacher_is_suspended(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        group = _group(term, _course(_level()), name="R", capacity=10)
        _assign(group, _teacher("susp@example.com", status=UserStatus.SUSPENDED.value))
        _enroll(group, _student("st@example.com"))
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        group_pid = group.public_id

    resp = _toggle_group(client, group_pid)
    assert b"no eligible active teacher" in resp.data.lower()
    with app.app_context():
        assert Group.query.first().status == "archived"


def test_reactivation_blocked_when_capacity_below_active_students(app, client):
    with app.app_context():
        _admin(client)
        group_pid = _setup_archived_group_with_roster(capacity=2, n_students=3, with_teacher=True)

    resp = _toggle_group(client, group_pid)
    assert b"capacity" in resp.data.lower()
    with app.app_context():
        assert Group.query.first().status == "archived"


def test_reactivation_counts_suspended_students_toward_capacity_and_keeps_them(app, client):
    with app.app_context():
        _admin(client)
        # capacity 1, one suspended + one active student -> 2 > 1 -> blocked
        group_pid = _setup_archived_group_with_roster(
            capacity=1, n_students=2, with_teacher=True, suspended_student=True
        )

    resp = _toggle_group(client, group_pid)
    assert b"capacity" in resp.data.lower()
    with app.app_context():
        assert Group.query.first().status == "archived"
        # both enrollments still present and active -- nothing rewritten
        assert Enrollment.query.filter_by(status="active").count() == 2


def test_reactivation_succeeds_with_students_and_eligible_teacher_within_capacity(app, client):
    with app.app_context():
        _admin(client)
        group_pid = _setup_archived_group_with_roster(capacity=5, n_students=2, with_teacher=True)

    resp = _toggle_group(client, group_pid)
    assert b"is now active" in resp.data.lower()
    with app.app_context():
        assert Group.query.first().status == "active"


def test_reactivation_blocked_when_a_student_has_a_conflicting_active_enrollment(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        course = _course(_level())
        archived_group = _group(term, course, name="Archived", capacity=10)
        other_active_group = _group(term, course, name="Other Active", capacity=10)
        teacher = _teacher("ct@example.com")
        _assign(archived_group, teacher)
        student = _student("conflict@example.com")
        _enroll(archived_group, student)
        _enroll(other_active_group, student)  # same term + course, both active -> conflict
        archived_group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        archived_group_pid = archived_group.public_id

    resp = _toggle_group(client, archived_group_pid)
    assert b"already actively enrolled" in resp.data.lower()
    with app.app_context():
        assert Group.query.filter_by(name="Archived").first().status == "archived"


def test_reactivation_not_blocked_by_a_conflict_in_another_archived_group(app, client):
    """The conflicting Group must itself be active -- a second archived
    Group's roster is a closure record, not an operational seat."""
    with app.app_context():
        _admin(client)
        term = _term()
        course = _course(_level())
        group_a = _group(term, course, name="Group A", capacity=10)
        group_b = _group(term, course, name="Group B", capacity=10)
        _assign(group_a, _teacher("ct2@example.com"))
        student = _student("noconf@example.com")
        _enroll(group_a, student)
        _enroll(group_b, student)
        group_a.status = AcademicStatus.ARCHIVED.value
        group_b.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        group_a_pid = group_a.public_id

    resp = _toggle_group(client, group_a_pid)
    assert b"is now active" in resp.data.lower()
    with app.app_context():
        assert Group.query.filter_by(name="Group A").first().status == "active"


def _reactivation_ancestor_case(app, client, archived):
    with app.app_context():
        _admin(client)
        term = _term()
        level = _level()
        course = _course(level)
        group = _group(term, course, name=f"R-{archived}", status=AcademicStatus.ARCHIVED.value)
        if archived == "term":
            term.status = AcademicStatus.ARCHIVED.value
        elif archived == "level":
            level.status = AcademicStatus.ARCHIVED.value
        else:
            course.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        group_pid = group.public_id

    resp = _toggle_group(client, group_pid)
    assert b"reactivate the parent first" in resp.data.lower()
    with app.app_context():
        assert Group.query.filter_by(name=f"R-{archived}").first().status == "archived"


def test_reactivation_blocked_while_academic_term_archived(app, client):
    _reactivation_ancestor_case(app, client, "term")


def test_reactivation_blocked_while_level_archived(app, client):
    _reactivation_ancestor_case(app, client, "level")


def test_reactivation_blocked_while_course_archived(app, client):
    _reactivation_ancestor_case(app, client, "course")


def test_failed_reactivation_modifies_no_row(app, client):
    with app.app_context():
        _admin(client)
        group_pid = _setup_archived_group_with_roster(capacity=1, n_students=2, with_teacher=True)

    _toggle_group(client, group_pid)
    with app.app_context():
        assert Group.query.first().status == "archived"
        assert Enrollment.query.filter_by(status="active").count() == 2
        assert GroupTeacherAssignment.query.filter_by(status="active").count() == 1


# ===========================================================================
# Structural: lock order
# ===========================================================================


def test_group_toggle_locks_hierarchy_then_group_in_order(app, client):
    from unittest.mock import patch

    from sqlalchemy.orm import Query

    with app.app_context():
        _admin(client)
        group = _group(_term(), _course(_level()), name="Ordered")
        group_pid = group.public_id

    calls = []
    original = Query.with_for_update

    def spy(self, *args, **kwargs):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        calls.append(getattr(entity, "__name__", "?"))
        return original(self, *args, **kwargs)

    with patch.object(Query, "with_for_update", spy):
        client.post(f"/admin/groups/{group_pid}/toggle-status")

    # archive direction: no roster locking, just the ancestor chain + Group
    assert calls == ["AcademicTerm", "Level", "Course", "Group"]


def test_group_reactivation_locks_users_ascending_then_relationship_rows(app, client):
    from unittest.mock import patch

    from sqlalchemy.orm import Query

    with app.app_context():
        _admin(client)
        term = _term()
        course = _course(_level())
        group = _group(term, course, name="LockOrder", capacity=10)
        # deliberately assign/enrol out of id order so ascending sorting matters
        t = _teacher("zt@example.com")
        s = _student("zs@example.com")
        _assign(group, t)
        _enroll(group, s)
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        group_pid = group.public_id
        user_ids_sorted = sorted([t.id, s.id])

    seen = []
    original = Query.with_for_update

    def spy(self, *args, **kwargs):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        name = getattr(entity, "__name__", "?")
        seen.append(name)
        return original(self, *args, **kwargs)

    with patch.object(Query, "with_for_update", spy):
        client.post(f"/admin/groups/{group_pid}/toggle-status")

    # AcademicTerm, Level, Course, Group, then User rows, then Enrollment,
    # then GroupTeacherAssignment -- entity-type order, ascending id within.
    assert seen[:4] == ["AcademicTerm", "Level", "Course", "Group"]
    assert seen[4:6] == ["User", "User"]
    assert "Enrollment" in seen[6:]
    assert "GroupTeacherAssignment" in seen[6:]
    assert user_ids_sorted == sorted(user_ids_sorted)  # sanity: helper computed ascending


def test_group_create_and_edit_and_toggle_never_lock_in_reverse_hierarchy_order(app, client):
    """The lock graph must contain no reverse-order path: for each of the
    three Group lifecycle routes, the entity-type sequence of FOR UPDATE
    requests is non-decreasing in the fixed order
    AcademicTerm < Level < Course < Group < User < relationship."""
    from unittest.mock import patch

    from sqlalchemy.orm import Query

    rank = {
        "AcademicTerm": 0,
        "Level": 1,
        "Course": 2,
        "Group": 3,
        "User": 4,
        "Enrollment": 5,
        "GroupTeacherAssignment": 5,
    }

    def _run_and_capture(fn):
        calls = []
        original = Query.with_for_update

        def spy(self, *args, **kwargs):
            entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
            calls.append(getattr(entity, "__name__", "?"))
            return original(self, *args, **kwargs)

        with patch.object(Query, "with_for_update", spy):
            fn()
        return calls

    with app.app_context():
        _admin(client)
        term = _term()
        level = _level()
        course = _course(level)
        group = _group(term, course, name="Graph", capacity=10)
        group2 = _group(term, course, name="Graph2", capacity=10, status=AcademicStatus.ARCHIVED.value)
        term_id, course_id = term.id, course.id
        group_pid, group2_pid = group.public_id, group2.public_id

    import re

    def do_create():
        _group_create(client, term_id, course_id, "GraphCreated")

    def do_edit():
        html = client.get(f"/admin/groups/{group_pid}/edit").get_data(as_text=True)
        snap = re.search(r'name="edit_snapshot" value="([^"]*)"', html).group(1)
        client.post(
            f"/admin/groups/{group_pid}/edit",
            data={
                "academic_term_id": term_id,
                "course_id": course_id,
                "name": "Graph",
                "code": "",
                "capacity": "9",
                "edit_snapshot": snap,
            },
        )

    def do_toggle():
        client.post(f"/admin/groups/{group2_pid}/toggle-status")

    for fn in (do_create, do_edit, do_toggle):
        calls = _run_and_capture(fn)
        ranks = [rank[c] for c in calls if c in rank]
        assert ranks == sorted(ranks), calls
