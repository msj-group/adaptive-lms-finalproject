from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AcademicTerm, Course, Level


def test_academic_term_model_defaults(app):
    with app.app_context():
        term = AcademicTerm(name="Fall 2026", start_date=date(2026, 9, 1), end_date=date(2026, 12, 31))
        db.session.add(term)
        db.session.commit()
        assert term.id is not None
        assert term.public_id is not None
        assert term.status == "active"


def test_academic_term_rejects_invalid_date_range(app):
    with app.app_context():
        term = AcademicTerm(name="Broken Term", start_date=date(2026, 12, 31), end_date=date(2026, 9, 1))
        db.session.add(term)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_level_model_defaults(app):
    with app.app_context():
        level = Level(name="Level 1", display_order=1)
        db.session.add(level)
        db.session.commit()
        assert level.id is not None
        assert level.public_id is not None
        assert level.status == "active"


def test_level_name_uniqueness_enforced(app):
    with app.app_context():
        db.session.add(Level(name="Level X", display_order=1))
        db.session.commit()
        db.session.add(Level(name="Level X", display_order=2))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_invalid_status_rejected_on_level(app):
    with app.app_context():
        with pytest.raises(ValueError):
            Level(name="Bad Level", display_order=1, status="not-a-status")


def test_course_belongs_to_level(app):
    with app.app_context():
        level = Level(name="Level 2", display_order=2)
        db.session.add(level)
        db.session.commit()

        course = Course(title="Speaking Practice", level_id=level.id)
        db.session.add(course)
        db.session.commit()

        assert course.level_id == level.id
        assert course.level.name == "Level 2"
        assert level.courses[0].title == "Speaking Practice"


def test_course_requires_valid_level(app):
    with app.app_context():
        course = Course(title="Grammar Basics", level_id=999999)
        db.session.add(course)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_course_title_unique_within_level(app):
    with app.app_context():
        level = Level(name="Level Y", display_order=1)
        db.session.add(level)
        db.session.commit()

        db.session.add(Course(title="Same Title", level_id=level.id))
        db.session.commit()

        db.session.add(Course(title="Same Title", level_id=level.id))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
