from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AcademicTerm, Course, Group, Level


def _make_term(name="Fall 2026"):
    term = AcademicTerm(name=name, start_date=date(2026, 9, 1), end_date=date(2026, 12, 31))
    db.session.add(term)
    db.session.commit()
    return term


def _make_course(name="Level 1", title="General English"):
    level = Level(name=name, display_order=0)
    db.session.add(level)
    db.session.commit()
    course = Course(title=title, level_id=level.id, display_order=0)
    db.session.add(course)
    db.session.commit()
    return course


def test_group_can_be_created_with_valid_term_and_course(app):
    with app.app_context():
        term = _make_term()
        course = _make_course()

        group = Group(academic_term_id=term.id, course_id=course.id, name="Group A", capacity=20)
        db.session.add(group)
        db.session.commit()

        assert group.id is not None
        assert group.public_id is not None
        assert group.status == "active"


def test_group_belongs_to_correct_academic_term(app):
    with app.app_context():
        term = _make_term()
        course = _make_course()
        group = Group(academic_term_id=term.id, course_id=course.id, name="Group A", capacity=20)
        db.session.add(group)
        db.session.commit()

        assert group.academic_term_id == term.id
        assert group.academic_term.name == "Fall 2026"
        assert term.groups[0].name == "Group A"


def test_group_belongs_to_correct_course(app):
    with app.app_context():
        term = _make_term()
        course = _make_course()
        group = Group(academic_term_id=term.id, course_id=course.id, name="Group A", capacity=20)
        db.session.add(group)
        db.session.commit()

        assert group.course_id == course.id
        assert group.course.title == "General English"
        assert course.groups[0].name == "Group A"


def test_group_can_access_level_through_course(app):
    with app.app_context():
        term = _make_term()
        course = _make_course(name="Level 5", title="Business English")
        group = Group(academic_term_id=term.id, course_id=course.id, name="Group A", capacity=20)
        db.session.add(group)
        db.session.commit()

        assert group.course.level.name == "Level 5"
        assert not hasattr(Group, "level_id")


def test_invalid_academic_term_is_rejected(app):
    with app.app_context():
        course = _make_course()
        group = Group(academic_term_id=999999, course_id=course.id, name="Group A", capacity=20)
        db.session.add(group)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_invalid_course_is_rejected(app):
    with app.app_context():
        term = _make_term()
        group = Group(academic_term_id=term.id, course_id=999999, name="Group A", capacity=20)
        db.session.add(group)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_invalid_capacity_is_rejected(app):
    with app.app_context():
        term = _make_term()
        course = _make_course()
        group = Group(academic_term_id=term.id, course_id=course.id, name="Group A", capacity=0)
        db.session.add(group)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_negative_capacity_is_rejected(app):
    with app.app_context():
        term = _make_term()
        course = _make_course()
        group = Group(academic_term_id=term.id, course_id=course.id, name="Group A", capacity=-5)
        db.session.add(group)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_invalid_status_is_rejected(app):
    with app.app_context():
        term = _make_term()
        course = _make_course()
        with pytest.raises(ValueError):
            Group(
                academic_term_id=term.id,
                course_id=course.id,
                name="Group A",
                capacity=20,
                status="not-a-status",
            )


def test_group_name_uniqueness_scoped_to_term_and_course(app):
    with app.app_context():
        term = _make_term()
        course = _make_course()

        db.session.add(Group(academic_term_id=term.id, course_id=course.id, name="Group A", capacity=20))
        db.session.commit()

        db.session.add(Group(academic_term_id=term.id, course_id=course.id, name="Group A", capacity=20))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_same_group_name_allowed_in_different_academic_term(app):
    with app.app_context():
        term_a = _make_term(name="Fall 2026")
        term_b = _make_term(name="Spring 2027")
        course = _make_course()

        db.session.add(Group(academic_term_id=term_a.id, course_id=course.id, name="Group A", capacity=20))
        db.session.commit()

        db.session.add(Group(academic_term_id=term_b.id, course_id=course.id, name="Group A", capacity=20))
        db.session.commit()

        assert Group.query.filter_by(name="Group A").count() == 2


def test_same_group_name_allowed_in_different_course(app):
    with app.app_context():
        term = _make_term()
        course_a = _make_course(name="Level 1", title="General English")
        course_b = _make_course(name="Level 2", title="Business English")

        db.session.add(Group(academic_term_id=term.id, course_id=course_a.id, name="Group A", capacity=20))
        db.session.commit()

        db.session.add(Group(academic_term_id=term.id, course_id=course_b.id, name="Group A", capacity=20))
        db.session.commit()

        assert Group.query.filter_by(name="Group A").count() == 2
