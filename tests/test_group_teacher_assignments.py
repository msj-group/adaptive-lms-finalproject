from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AcademicTerm,
    Course,
    Enrollment,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password


def _make_term(name="Fall 2026"):
    term = AcademicTerm(name=name, start_date=date(2026, 9, 1), end_date=date(2026, 12, 31))
    db.session.add(term)
    db.session.commit()
    return term


def _make_group(term=None, course=None, name="Group A"):
    term = term or _make_term()
    if course is None:
        level = Level(name=f"Level for {name}", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title=f"Course for {name}", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=name, capacity=20)
    db.session.add(group)
    db.session.commit()
    return group


def _make_teacher(email="teacher@example.com", full_name="Teacher One"):
    teacher = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name=full_name,
        role=UserRole.TEACHER.value,
        status=UserStatus.ACTIVE.value,
    )
    db.session.add(teacher)
    db.session.commit()
    return teacher


# ======================================================================
# CREATION AND BASIC RELATIONSHIPS
# ======================================================================


def test_assignment_can_be_created_with_valid_group_and_teacher(app):
    with app.app_context():
        teacher = _make_teacher()
        group = _make_group()

        assignment = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id)
        db.session.add(assignment)
        db.session.commit()

        assert assignment.id is not None
        assert assignment.public_id is not None


def test_default_status_is_active(app):
    with app.app_context():
        teacher = _make_teacher()
        group = _make_group()
        assignment = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id)
        db.session.add(assignment)
        db.session.commit()

        assert assignment.status == GroupTeacherAssignmentStatus.ACTIVE.value


def test_public_id_is_generated_and_unique(app):
    with app.app_context():
        teacher1 = _make_teacher(email="a@example.com")
        teacher2 = _make_teacher(email="b@example.com")
        group = _make_group()

        a1 = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher1.id)
        a2 = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher2.id)
        db.session.add_all([a1, a2])
        db.session.commit()

        assert a1.public_id is not None
        assert a2.public_id is not None
        assert a1.public_id != a2.public_id
        assert len(a1.public_id) == 36


def test_group_relationship_works(app):
    with app.app_context():
        teacher = _make_teacher()
        group = _make_group(name="Relationship Group")
        assignment = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id)
        db.session.add(assignment)
        db.session.commit()

        assert assignment.group_id == group.id
        assert assignment.group.name == "Relationship Group"
        assert assignment in group.teacher_assignments


def test_user_teaching_assignments_relationship_works(app):
    with app.app_context():
        teacher = _make_teacher(full_name="Relationship Teacher")
        group = _make_group()
        assignment = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id)
        db.session.add(assignment)
        db.session.commit()

        assert assignment.teacher_id == teacher.id
        assert assignment.teacher.full_name == "Relationship Teacher"
        assert assignment in teacher.teaching_assignments


# ======================================================================
# MULTIPLE ASSIGNMENTS ARE LEGITIMATE
# ======================================================================


def test_one_teacher_may_be_assigned_to_multiple_groups(app):
    with app.app_context():
        teacher = _make_teacher()
        term = _make_term()
        group_a = _make_group(term=term, name="Group A")
        group_b = _make_group(term=term, name="Group B")

        db.session.add(GroupTeacherAssignment(group_id=group_a.id, teacher_id=teacher.id))
        db.session.add(GroupTeacherAssignment(group_id=group_b.id, teacher_id=teacher.id))
        db.session.commit()

        assert len(teacher.teaching_assignments) == 2


def test_multiple_teachers_may_be_assigned_to_one_group(app):
    with app.app_context():
        teacher_a = _make_teacher(email="ta@example.com")
        teacher_b = _make_teacher(email="tb@example.com")
        group = _make_group()

        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher_a.id))
        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher_b.id))
        db.session.commit()

        assert len(group.teacher_assignments) == 2


# ======================================================================
# INTEGRITY CONSTRAINTS
# ======================================================================


def test_duplicate_group_teacher_pair_is_rejected(app):
    with app.app_context():
        teacher = _make_teacher()
        group = _make_group()

        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id))
        db.session.commit()

        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_removed_assignment_remains_stored(app):
    with app.app_context():
        teacher = _make_teacher()
        group = _make_group()
        assignment = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id)
        db.session.add(assignment)
        db.session.commit()

        assignment.status = GroupTeacherAssignmentStatus.REMOVED.value
        db.session.commit()

        reloaded = GroupTeacherAssignment.query.filter_by(
            group_id=group.id, teacher_id=teacher.id
        ).first()
        assert reloaded is not None
        assert reloaded.status == GroupTeacherAssignmentStatus.REMOVED.value


def test_removed_assignment_can_be_reactivated_without_a_second_row(app):
    with app.app_context():
        teacher = _make_teacher()
        group = _make_group()
        assignment = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id)
        db.session.add(assignment)
        db.session.commit()
        assignment_id = assignment.id

        assignment.status = GroupTeacherAssignmentStatus.REMOVED.value
        db.session.commit()

        assignment.status = GroupTeacherAssignmentStatus.ACTIVE.value
        db.session.commit()

        rows = GroupTeacherAssignment.query.filter_by(group_id=group.id, teacher_id=teacher.id).all()
        assert len(rows) == 1
        assert rows[0].id == assignment_id
        assert rows[0].status == GroupTeacherAssignmentStatus.ACTIVE.value


def test_invalid_status_is_rejected(app):
    with app.app_context():
        teacher = _make_teacher()
        group = _make_group()
        with pytest.raises(ValueError):
            GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id, status="not-a-status")


def test_invalid_group_id_is_rejected(app):
    with app.app_context():
        teacher = _make_teacher()
        assignment = GroupTeacherAssignment(group_id=999999, teacher_id=teacher.id)
        db.session.add(assignment)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_invalid_teacher_id_is_rejected(app):
    with app.app_context():
        group = _make_group()
        assignment = GroupTeacherAssignment(group_id=group.id, teacher_id=999999)
        db.session.add(assignment)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


# ======================================================================
# ROLE-INTEGRITY BOUNDARY (documented, database-level limitation)
# ======================================================================


def test_database_fk_does_not_enforce_teacher_role(app):
    """The `teacher_id` foreign key only guarantees the referenced User
    row exists -- it cannot, at the database level, restrict that row to
    role=teacher (or to an active account). This test documents and
    proves that boundary directly, exactly mirroring how Enrollment's
    student_id FK is documented and tested: a non-Teacher user id is
    accepted by the FK constraint. Role and account-status integrity at
    write time are the responsibility of the application write path in
    `app/blueprints/admin/group_members.py` (assign/reactivate), not the
    schema -- see tests/test_admin_group_teacher_assignments.py for the
    route-level tests proving that path actually rejects this case.
    """
    with app.app_context():
        student = User(
            email="student.notteacher@example.com",
            password_hash=hash_password("Sup3rSecret!123"),
            full_name="Not A Teacher",
            role=UserRole.STUDENT.value,
            status=UserStatus.ACTIVE.value,
        )
        db.session.add(student)
        db.session.commit()
        group = _make_group()

        assignment = GroupTeacherAssignment(group_id=group.id, teacher_id=student.id)
        db.session.add(assignment)
        db.session.commit()  # the FK alone does not reject this

        assert assignment.teacher.role == UserRole.STUDENT.value


# ======================================================================
# NO REGRESSION ON ENROLLMENT
# ======================================================================


def test_enrollment_behavior_and_relationships_unchanged(app):
    """Adding GroupTeacherAssignment must not alter Enrollment's own
    creation, relationships, or reverse relationships in any way.
    """
    with app.app_context():
        student = User(
            email="enrollmentstudent@example.com",
            password_hash=hash_password("Sup3rSecret!123"),
            full_name="Enrollment Student",
            role=UserRole.STUDENT.value,
            status=UserStatus.ACTIVE.value,
        )
        db.session.add(student)
        db.session.commit()
        group = _make_group(name="Unaffected Group")

        enrollment = Enrollment(student_id=student.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()

        assert enrollment.status == "active"
        assert enrollment.student.full_name == "Enrollment Student"
        assert enrollment.group.name == "Unaffected Group"
        assert student.student_enrollments[0].id == enrollment.id
        assert group.enrollments[0].id == enrollment.id
