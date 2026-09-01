"""Lesson model (M11): defaults, the Lesson-specific status enum, the
relationship to Unit, the ordering column, the draft/published +
published_at database invariants, title uniqueness within a Unit, the
non-cascading FK, and the migration's structural shape.

SQLite enforces CHECK and UNIQUE and (with the project's
``PRAGMA foreign_keys=ON``) foreign keys, so the model-level constraint
tests are meaningful for application logic. They do not prove
MySQL/InnoDB behaviour -- the M11 report covers the real-MySQL migration
verification separately.
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Group,
    Lesson,
    LessonStatus,
    Level,
    Unit,
)


def _unit(title="Unit 1", group_name="Group A", status=AcademicStatus.ACTIVE.value):
    term = AcademicTerm(
        name=f"Term {group_name}", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
    )
    db.session.add(term)
    level = Level(name=f"Level {group_name}", display_order=0)
    db.session.add(level)
    db.session.commit()
    course = Course(title=f"Course {group_name}", level_id=level.id, display_order=0)
    db.session.add(course)
    db.session.commit()
    group = Group(
        academic_term_id=term.id, course_id=course.id, name=group_name, capacity=20
    )
    db.session.add(group)
    db.session.commit()
    unit = Unit(group_id=group.id, title=title, display_order=0)
    db.session.add(unit)
    db.session.commit()
    return unit


def _lesson(unit, title="Lesson 1", display_order=0, status=LessonStatus.DRAFT.value,
            published_at=None, commit=True):
    lesson = Lesson(
        unit_id=unit.id, title=title, display_order=display_order,
        status=status, published_at=published_at,
    )
    db.session.add(lesson)
    if commit:
        db.session.commit()
    return lesson


def test_lesson_defaults_and_relationship(app):
    with app.app_context():
        unit = _unit()
        lesson = _lesson(unit, title="Intro")
        assert lesson.id is not None
        assert lesson.public_id is not None
        assert lesson.status == "draft"
        assert lesson.published_at is None
        assert lesson.created_at is not None and lesson.updated_at is not None
        assert lesson.unit.id == unit.id
        assert unit.lessons[0].id == lesson.id


def test_lesson_invalid_status_rejected(app):
    with app.app_context():
        unit = _unit()
        with pytest.raises(ValueError):
            Lesson(unit_id=unit.id, title="X", status="archived")


def test_lesson_title_unique_within_unit_including_draft(app):
    with app.app_context():
        unit = _unit()
        _lesson(unit, title="Same Title", status=LessonStatus.DRAFT.value)
        _lesson(unit, title="Same Title", commit=False)  # another draft, same title
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_lesson_title_unique_within_unit_against_published(app):
    with app.app_context():
        unit = _unit()
        _lesson(unit, title="Grammar", status=LessonStatus.PUBLISHED.value,
                published_at=datetime.now(timezone.utc))
        _lesson(unit, title="Grammar", commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_same_lesson_title_allowed_in_different_units(app):
    with app.app_context():
        u1 = _unit(title="U1", group_name="G1")
        u2 = _unit(title="U2", group_name="G2")
        _lesson(u1, title="Shared")
        _lesson(u2, title="Shared")
        assert Lesson.query.filter_by(title="Shared").count() == 2


def test_lesson_display_order_non_negative_check(app):
    with app.app_context():
        unit = _unit()
        _lesson(unit, title="Bad", display_order=-1, commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_draft_with_published_at_set_is_rejected(app):
    with app.app_context():
        unit = _unit()
        _lesson(unit, title="Bad Draft", status=LessonStatus.DRAFT.value,
                published_at=datetime.now(timezone.utc), commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_published_without_published_at_is_rejected(app):
    with app.app_context():
        unit = _unit()
        _lesson(unit, title="Bad Published", status=LessonStatus.PUBLISHED.value,
                published_at=None, commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_published_with_published_at_is_accepted(app):
    with app.app_context():
        unit = _unit()
        lesson = _lesson(unit, title="Good Published", status=LessonStatus.PUBLISHED.value,
                         published_at=datetime.now(timezone.utc))
        assert lesson.status == "published"
        assert lesson.published_at is not None


def test_lesson_public_id_unique(app):
    with app.app_context():
        unit = _unit()
        first = _lesson(unit, title="A")
        dup = _lesson(unit, title="B", commit=False)
        dup.public_id = first.public_id
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_lesson_fk_to_unit_is_non_cascading(app):
    with app.app_context():
        unit = _unit()
        _lesson(unit, title="A")
        db.session.delete(unit)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_lesson_requires_unit(app):
    with app.app_context():
        _unit()
        orphan = Lesson(unit_id=999999, title="Orphan")
        db.session.add(orphan)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_lessons_table_shape(app):
    """The migration/model produce exactly the M11 lessons table: FK to
    units, the display_order + status + status/published_at CHECKs, the
    (unit_id, title) + public_id unique constraints, and
    unit/status/order indexes -- and nothing that would attach a Lesson
    to Group/Course/Level/Term/Teacher or add progress/completion."""
    with app.app_context():
        insp = inspect(db.engine)
        columns = {c["name"] for c in insp.get_columns("lessons")}
        assert columns == {
            "id", "public_id", "unit_id", "title", "description",
            "search_keywords",  # M13
            "display_order", "status", "published_at", "created_at", "updated_at",
        }
        for forbidden in (
            "group_id", "course_id", "level_id", "academic_term_id",
            "teacher_id", "created_by", "content_html", "material_id",
            "file_path", "completed", "progress",
        ):
            assert forbidden not in columns

        fks = insp.get_foreign_keys("lessons")
        assert len(fks) == 1
        assert fks[0]["referred_table"] == "units"
        assert fks[0]["constrained_columns"] == ["unit_id"]

        uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("lessons")}
        assert ("unit_id", "title") in uniques

        index_cols = {tuple(i["column_names"]) for i in insp.get_indexes("lessons")}
        assert ("unit_id",) in index_cols
        assert ("status",) in index_cols
        assert ("display_order",) in index_cols

        checks = " ".join(c["sqltext"] for c in insp.get_check_constraints("lessons"))
        assert "display_order" in checks
        assert "published_at" in checks
