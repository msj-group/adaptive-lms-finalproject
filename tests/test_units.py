"""Unit model (M10): constraints, relationship, lifecycle, ordering
column, and the migration's structural shape.

SQLite enforces CHECK and UNIQUE and (with the project's
`PRAGMA foreign_keys=ON`) foreign keys, so the model-level constraint
tests are meaningful for application logic. They do not prove
MySQL/InnoDB behaviour -- the M10 report covers the real-MySQL migration
verification separately.
"""

from datetime import date

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AcademicStatus, AcademicTerm, Course, Group, Level, Unit


def _group(name="Group A"):
    term = AcademicTerm(name=f"Term {name}", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    db.session.add(term)
    level = Level(name=f"Level {name}", display_order=0)
    db.session.add(level)
    db.session.commit()
    course = Course(title=f"Course {name}", level_id=level.id, display_order=0)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=name, capacity=20)
    db.session.add(group)
    db.session.commit()
    return group


def _unit(group, title="Unit 1", display_order=0, status=AcademicStatus.ACTIVE.value, commit=True):
    u = Unit(group_id=group.id, title=title, display_order=display_order, status=status)
    db.session.add(u)
    if commit:
        db.session.commit()
    return u


def test_unit_defaults_and_relationship(app):
    with app.app_context():
        group = _group()
        u = _unit(group, title="Intro", display_order=0)
        assert u.id is not None
        assert u.public_id is not None
        assert u.status == "active"
        assert u.created_at is not None and u.updated_at is not None
        assert u.group.id == group.id
        assert group.units[0].id == u.id


def test_unit_invalid_status_rejected(app):
    with app.app_context():
        group = _group()
        with pytest.raises(ValueError):
            Unit(group_id=group.id, title="X", status="not-a-status")


def test_unit_title_unique_within_group_including_archived(app):
    with app.app_context():
        group = _group()
        _unit(group, title="Same Title", status=AcademicStatus.ARCHIVED.value)
        _unit(group, title="Same Title", commit=False)  # active, same title -> reject
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_same_unit_title_allowed_in_different_groups(app):
    with app.app_context():
        g1 = _group("G1")
        g2 = _group("G2")
        _unit(g1, title="Shared")
        _unit(g2, title="Shared")
        assert Unit.query.filter_by(title="Shared").count() == 2


def test_unit_display_order_non_negative_check(app):
    with app.app_context():
        group = _group()
        _unit(group, title="Bad", display_order=-1, commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_unit_public_id_unique(app):
    with app.app_context():
        group = _group()
        first = _unit(group, title="A")
        dup = _unit(group, title="B", commit=False)
        dup.public_id = first.public_id
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_unit_fk_to_group_is_non_cascading(app):
    with app.app_context():
        group = _group()
        _unit(group, title="A")
        db.session.delete(group)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_unit_requires_group(app):
    with app.app_context():
        u = Unit(group_id=999999, title="Orphan")
        db.session.add(u)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_units_table_shape(app):
    """The migration/model produce exactly the M10 units table: FK to
    groups, the display_order CHECK, the (group_id, title) + public_id
    unique constraints, and group/status/order indexes -- and nothing
    that would attach a Unit to Course/Level/Term/Teacher."""
    with app.app_context():
        insp = inspect(db.engine)
        columns = {c["name"] for c in insp.get_columns("units")}
        assert columns == {
            "id", "public_id", "group_id", "title", "description",
            "search_keywords",  # M13
            "display_order", "status", "created_at", "updated_at",
        }
        for forbidden in ("teacher_id", "course_id", "level_id", "academic_term_id", "created_by"):
            assert forbidden not in columns

        fks = insp.get_foreign_keys("units")
        assert len(fks) == 1
        assert fks[0]["referred_table"] == "groups"
        assert fks[0]["constrained_columns"] == ["group_id"]

        uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("units")}
        assert ("group_id", "title") in uniques

        index_cols = {tuple(i["column_names"]) for i in insp.get_indexes("units")}
        assert ("group_id",) in index_cols
        assert ("status",) in index_cols
        assert ("display_order",) in index_cols

        checks = " ".join(c["sqltext"] for c in insp.get_check_constraints("units"))
        assert "display_order" in checks
