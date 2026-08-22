from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AcademicTerm, Course, Enrollment, Group, Level, User, UserRole, UserStatus
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


def _make_student(email="student@example.com", full_name="Student One"):
    student = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name=full_name,
        role=UserRole.STUDENT.value,
        status=UserStatus.ACTIVE.value,
    )
    db.session.add(student)
    db.session.commit()
    return student


# ======================================================================
# CREATION AND BASIC RELATIONSHIPS
# ======================================================================


def test_enrollment_can_be_created_with_valid_student_and_group(app):
    with app.app_context():
        student = _make_student()
        group = _make_group()

        enrollment = Enrollment(student_id=student.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()

        assert enrollment.id is not None
        assert enrollment.public_id is not None
        assert enrollment.status == "active"


def test_enrollment_public_id_is_unique(app):
    with app.app_context():
        student1 = _make_student(email="a@example.com")
        student2 = _make_student(email="b@example.com")
        group = _make_group()

        e1 = Enrollment(student_id=student1.id, group_id=group.id)
        e2 = Enrollment(student_id=student2.id, group_id=group.id)
        db.session.add_all([e1, e2])
        db.session.commit()

        assert e1.public_id != e2.public_id
        assert len(e1.public_id) == 36


def test_enrollment_has_timestamps(app):
    with app.app_context():
        student = _make_student()
        group = _make_group()
        enrollment = Enrollment(student_id=student.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()

        assert enrollment.created_at is not None
        assert enrollment.updated_at is not None


def test_enrollment_student_relationship(app):
    with app.app_context():
        student = _make_student(email="rel@example.com", full_name="Relationship Student")
        group = _make_group()
        enrollment = Enrollment(student_id=student.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()

        assert enrollment.student_id == student.id
        assert enrollment.student.full_name == "Relationship Student"


def test_enrollment_group_relationship(app):
    with app.app_context():
        student = _make_student()
        group = _make_group(name="Relationship Group")
        enrollment = Enrollment(student_id=student.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()

        assert enrollment.group_id == group.id
        assert enrollment.group.name == "Relationship Group"


def test_reverse_relationship_student_student_enrollments(app):
    with app.app_context():
        student = _make_student()
        group = _make_group()
        enrollment = Enrollment(student_id=student.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()

        assert student.student_enrollments[0].id == enrollment.id


def test_reverse_relationship_group_enrollments(app):
    with app.app_context():
        student = _make_student()
        group = _make_group()
        enrollment = Enrollment(student_id=student.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()

        assert group.enrollments[0].id == enrollment.id


# ======================================================================
# TRAVERSAL TO COURSE / LEVEL / ACADEMIC TERM (through Group)
# ======================================================================


def test_enrollment_traverses_to_course_through_group(app):
    with app.app_context():
        student = _make_student()
        term = _make_term()
        level = Level(name="Level X", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="Course X", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group = _make_group(term=term, course=course)
        enrollment = Enrollment(student_id=student.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()

        assert enrollment.group.course.title == "Course X"


def test_enrollment_traverses_to_level_through_group_course(app):
    with app.app_context():
        student = _make_student()
        term = _make_term()
        level = Level(name="Level Y", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="Course Y", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group = _make_group(term=term, course=course)
        enrollment = Enrollment(student_id=student.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()

        assert enrollment.group.course.level.name == "Level Y"


def test_enrollment_traverses_to_academic_term_through_group(app):
    with app.app_context():
        student = _make_student()
        term = _make_term(name="Winter 2027")
        group = _make_group(term=term)
        enrollment = Enrollment(student_id=student.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()

        assert enrollment.group.academic_term.name == "Winter 2027"


def test_enrollment_does_not_duplicate_course_level_term_columns(app):
    with app.app_context():
        assert not hasattr(Enrollment, "course_id")
        assert not hasattr(Enrollment, "level_id")
        assert not hasattr(Enrollment, "academic_term_id")


# ======================================================================
# INTEGRITY CONSTRAINTS
# ======================================================================


def test_duplicate_student_group_pair_is_rejected(app):
    with app.app_context():
        student = _make_student()
        group = _make_group()

        db.session.add(Enrollment(student_id=student.id, group_id=group.id))
        db.session.commit()

        db.session.add(Enrollment(student_id=student.id, group_id=group.id))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_invalid_student_id_is_rejected(app):
    with app.app_context():
        group = _make_group()
        enrollment = Enrollment(student_id=999999, group_id=group.id)
        db.session.add(enrollment)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_invalid_group_id_is_rejected(app):
    with app.app_context():
        student = _make_student()
        enrollment = Enrollment(student_id=student.id, group_id=999999)
        db.session.add(enrollment)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_missing_student_id_is_rejected(app):
    with app.app_context():
        group = _make_group()
        enrollment = Enrollment(group_id=group.id)
        db.session.add(enrollment)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_missing_group_id_is_rejected(app):
    with app.app_context():
        student = _make_student()
        enrollment = Enrollment(student_id=student.id)
        db.session.add(enrollment)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_invalid_status_is_rejected(app):
    with app.app_context():
        student = _make_student()
        group = _make_group()
        with pytest.raises(ValueError):
            Enrollment(student_id=student.id, group_id=group.id, status="not-a-status")


def test_valid_statuses_are_accepted(app):
    with app.app_context():
        term = _make_term()
        for status_value in ("active", "withdrawn"):
            student = _make_student(email=f"{status_value}@example.com")
            group = _make_group(term=term, name=f"Group for {status_value}")
            enrollment = Enrollment(student_id=student.id, group_id=group.id, status=status_value)
            db.session.add(enrollment)
            db.session.commit()
            assert enrollment.status == status_value


# ======================================================================
# MULTIPLE ENROLLMENTS ARE LEGITIMATE (not one-group-per-student/term)
# ======================================================================


def test_same_student_may_enroll_in_different_groups(app):
    with app.app_context():
        student = _make_student()
        term = _make_term()
        group_a = _make_group(term=term, name="Group A")
        group_b = _make_group(term=term, name="Group B")

        db.session.add(Enrollment(student_id=student.id, group_id=group_a.id))
        db.session.add(Enrollment(student_id=student.id, group_id=group_b.id))
        db.session.commit()

        assert len(student.student_enrollments) == 2


def test_different_students_may_enroll_in_same_group(app):
    with app.app_context():
        student_a = _make_student(email="a2@example.com")
        student_b = _make_student(email="b2@example.com")
        group = _make_group()

        db.session.add(Enrollment(student_id=student_a.id, group_id=group.id))
        db.session.add(Enrollment(student_id=student_b.id, group_id=group.id))
        db.session.commit()

        assert len(group.enrollments) == 2


# ======================================================================
# ROLE-INTEGRITY BOUNDARY (documented, database-level limitation)
# ======================================================================


def test_database_fk_does_not_enforce_student_role(app):
    """The `student_id` foreign key only guarantees the referenced User
    row exists -- it cannot, at the database level, restrict that row to
    role=student. This test documents and proves that boundary directly:
    a non-Student user id is accepted by the FK constraint. Enforcing the
    role is the responsibility of the (not-yet-built) Enrollment write
    path, not the schema -- see the Enrollment model's docstring.
    """
    with app.app_context():
        teacher = User(
            email="teacher.notstudent@example.com",
            password_hash=hash_password("Sup3rSecret!123"),
            full_name="Not A Student",
            role=UserRole.TEACHER.value,
            status=UserStatus.ACTIVE.value,
        )
        db.session.add(teacher)
        db.session.commit()
        group = _make_group()

        enrollment = Enrollment(student_id=teacher.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()  # the FK alone does not reject this

        assert enrollment.student.role == UserRole.TEACHER.value
