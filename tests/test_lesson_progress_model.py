"""Phase 4 / M13 -- the ``LessonProgress`` model: defaults, the duplicate
defense, the version rule, immutable identity, no deletion, and no ORM
relationship in either direction."""

from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

import tests.lesson_progress_fixtures as fx
from app.extensions import db
from app.models import Group, Lesson, LessonProgress, User
from app.models.lesson_progress import LESSON_PROGRESS_MUTABLE_COLUMNS, progress_now


def _world():
    student, _teacher, group = fx.classroom()
    unit = fx.unit(group)
    lesson = fx.lesson(unit)
    return student, group, unit, lesson


def test_a_new_row_starts_at_version_one_never_completed_or_opened(app):
    with app.app_context():
        student, group, _, lesson = _world()
        row = LessonProgress(student_id=student.id, group_id=group.id, lesson_id=lesson.id)
        db.session.add(row)
        db.session.commit()
        assert row.version == 1
        assert row.completed_at is None
        assert row.last_opened_at is None
        assert row.created_at.tzinfo is None
        assert row.created_at.microsecond == 0


def test_progress_now_is_naive_utc_to_the_whole_second():
    moment = progress_now()
    assert moment.tzinfo is None
    assert moment.microsecond == 0


def test_one_row_per_student_group_and_lesson(app):
    with app.app_context():
        student, group, unit, lesson = _world()
        other_lesson = fx.lesson(unit, "Numbers", 1)
        classmate = fx.user("classmate@example.com", fx.STUDENT)
        fx.enroll(group, classmate)
        fx.progress(student, group, lesson)
        fx.progress(student, group, other_lesson)
        fx.progress(classmate, group, lesson)
        db.session.add(
            LessonProgress(student_id=student.id, group_id=group.id, lesson_id=lesson.id)
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert LessonProgress.query.count() == 3


@pytest.mark.parametrize("bad", [0, -1, True, False, "2", 1.0, None])
def test_the_version_must_be_a_positive_integer(app, bad):
    with app.app_context():
        with pytest.raises(ValueError):
            LessonProgress(version=bad)


def test_the_database_refuses_a_non_positive_version(app):
    with app.app_context():
        student, group, _, lesson = _world()
        with pytest.raises(IntegrityError):
            db.session.execute(
                sa.text(
                    "INSERT INTO lesson_progress (student_id, group_id, lesson_id, created_at,"
                    " version) VALUES (:student, :group, :lesson, :moment, 0)"
                ),
                {"student": student.id, "group": group.id, "lesson": lesson.id,
                 "moment": "2026-05-13 09:00:00"},
            )
        db.session.rollback()


@pytest.mark.parametrize("column", ["student_id", "group_id", "lesson_id", "created_at"])
def test_identity_columns_never_change(app, column):
    with app.app_context():
        student, group, unit, lesson = _world()
        other_group = fx.hierarchy("B")
        fx.enroll(other_group, student)
        other_lesson = fx.lesson(unit, "Numbers", 1)
        other_student = fx.user("other@example.com", fx.STUDENT)
        row = fx.progress(student, group, lesson)
        replacement = {
            "student_id": other_student.id,
            "group_id": other_group.id,
            "lesson_id": other_lesson.id,
            "created_at": fx.NOW + timedelta(days=1),
        }[column]
        setattr(row, column, replacement)
        with pytest.raises(ValueError, match=column):
            db.session.flush()
        db.session.rollback()


def test_completion_opening_and_version_may_change_together(app):
    with app.app_context():
        student, group, _, lesson = _world()
        row = fx.progress(student, group, lesson, last_opened_at=fx.NOW)
        row.completed_at = fx.LATER
        row.last_opened_at = fx.LATEST
        row.version = 2
        db.session.commit()
        db.session.expire_all()
        row = db.session.get(LessonProgress, row.id)
        assert (row.completed_at, row.last_opened_at, row.version) == (fx.LATER, fx.LATEST, 2)


def test_the_mutable_columns_are_exactly_completion_opening_and_version():
    assert LESSON_PROGRESS_MUTABLE_COLUMNS == {"completed_at", "last_opened_at", "version"}


def test_a_row_is_never_deleted(app):
    with app.app_context():
        student, group, _, lesson = _world()
        row = fx.progress(student, group, lesson)
        db.session.delete(row)
        with pytest.raises(ValueError, match="never deleted"):
            db.session.flush()
        db.session.rollback()
        assert LessonProgress.query.count() == 1


def test_no_relationship_is_declared_in_either_direction():
    assert list(sa_inspect(LessonProgress).relationships) == []
    for model in (User, Group, Lesson):
        assert all(
            rel.mapper.class_ is not LessonProgress for rel in sa_inspect(model).relationships
        ), model


def test_the_declared_constraints_indexes_and_foreign_keys():
    table = LessonProgress.__table__
    uniques = {
        c.name: [col.name for col in c.columns]
        for c in table.constraints
        if isinstance(c, sa.UniqueConstraint)
    }
    assert uniques == {
        "uq_lesson_progress_student_group_lesson": ["student_id", "group_id", "lesson_id"]
    }
    assert {c.name for c in table.constraints if isinstance(c, sa.CheckConstraint)} == {
        "ck_lesson_progress_version_positive"
    }
    assert {i.name: [col.name for col in i.columns] for i in table.indexes} == {
        "ix_lesson_progress_student_opened_id": ["student_id", "last_opened_at", "id"],
        "ix_lesson_progress_group_student": ["group_id", "student_id"],
        "ix_lesson_progress_lesson_group": ["lesson_id", "group_id"],
    }
    foreign_keys = {
        (fk.parent.name, fk.column.table.name, fk.ondelete, fk.onupdate)
        for fk in table.foreign_keys
    }
    assert foreign_keys == {
        ("student_id", "users", None, None),
        ("group_id", "groups", None, None),
        ("lesson_id", "lessons", None, None),
    }
    assert "public_id" not in table.columns
