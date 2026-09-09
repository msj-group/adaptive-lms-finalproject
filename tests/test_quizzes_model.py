"""Phase 4 / M04A Quiz model and database invariants.

The test backend is SQLite in memory built with ``db.create_all()``, so
the schema checks here prove the **model** is internally consistent and
that the application-level rules and the declared constraints agree.
SQLite does enforce CHECK constraints and (via the project's
``PRAGMA foreign_keys=ON`` hook in ``app/extensions.py``) foreign keys, so
those integrity assertions are real.

M04C adds the Alembic revision for this accepted model together with its
Question and Option children. Migration structure, isolated execution, and
offline MySQL compilation live in ``tests/test_quiz_migration.py``; the
checks here remain focused on model and application invariants.
"""

from datetime import date, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    QUIZ_INSTRUCTIONS_MAX_LENGTH,
    QUIZ_TITLE_MAX_LENGTH,
    AcademicTerm,
    Course,
    Group,
    Level,
    Quiz,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _group(name="Group A", course_title="English"):
    term = AcademicTerm(
        name=f"Term {name}", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
    )
    level = Level(name=f"Level {name}", display_order=0)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title=course_title, level_id=level.id, display_order=0)
    db.session.add(course)
    db.session.commit()
    group = Group(
        academic_term_id=term.id, course_id=course.id, name=name, capacity=20
    )
    db.session.add(group)
    db.session.commit()
    return group


def _quiz(group, title="Unit 1 check", instructions="Answer every question.", **kwargs):
    row = Quiz(group_id=group.id, title=title, instructions=instructions, **kwargs)
    db.session.add(row)
    db.session.commit()
    return row


# ---------------------------------------------------------------------------
# registration and defaults
# ---------------------------------------------------------------------------


def test_table_is_registered_under_the_expected_name(app):
    with app.app_context():
        assert Quiz.__tablename__ == "quizzes"
        assert "quizzes" in inspect(db.engine).get_table_names()


def test_defaults_are_version_one_with_matching_whole_second_timestamps(app):
    with app.app_context():
        quiz = _quiz(_group())
        assert quiz.version == 1
        assert quiz.created_at is not None and quiz.updated_at is not None
        # Naive UTC, truncated to whole seconds: MySQL DATETIME(0) rounds a
        # fraction rather than truncating it, so the column default must
        # not carry one.
        assert quiz.created_at.tzinfo is None
        assert quiz.created_at.microsecond == 0
        assert quiz.updated_at.microsecond == 0


def test_public_id_is_a_generated_uuid_string(app):
    import uuid

    with app.app_context():
        quiz = _quiz(_group())
        assert isinstance(quiz.public_id, str)
        # Parses as a real UUID and round-trips to the same text.
        assert str(uuid.UUID(quiz.public_id)) == quiz.public_id
        assert quiz.public_id != str(quiz.id)


def test_group_relationship_both_directions(app):
    with app.app_context():
        group = _group()
        quiz = _quiz(group)
        assert quiz.group.id == group.id
        assert [q.id for q in group.quizzes] == [quiz.id]


def test_no_duplicated_hierarchy_owner_lifecycle_or_future_columns(app):
    """Course / Level / AcademicTerm are reachable through ``quiz.group``
    and must not be duplicated; there is no creator-owner column; and no
    placeholder is left for publication, timing, attempts or grading.

    Phase 4 / M04B added questions as their own normalized table, so this
    list still forbids a denormalized ``questions`` / ``question_count``
    column on ``quizzes`` -- a stored counter would be a second source of
    truth that every question write would have to keep in step.
    """
    with app.app_context():
        columns = {c.name for c in Quiz.__table__.columns}
        assert columns == {
            "id",
            "public_id",
            "group_id",
            "title",
            "instructions",
            "version",
            "created_at",
            "updated_at",
        }
        for forbidden in (
            "course_id", "level_id", "academic_term_id", "unit_id", "lesson_id",
            "teacher_id", "created_by", "owner_id",
            "status", "published_at", "opens_at", "due_at", "closes_at",
            "time_limit", "time_limit_minutes", "duration_minutes",
            "question_count", "questions", "max_score", "total_points",
            "pass_mark", "attempts_allowed", "shuffle", "display_order",
            "is_deleted", "deleted_at", "archived_at",
        ):
            assert forbidden not in columns, forbidden


def test_the_model_declares_no_cascade_in_any_direction(app):
    """No lifecycle change anywhere may delete a Quiz, deleting a Quiz may
    not reach back into a Group, and (since Phase 4 / M04B) neither may it
    reach down into the draft's questions."""
    with app.app_context():
        for relationship in (Quiz.group, Group.quizzes, Quiz.questions):
            cascade = relationship.property.cascade
            assert "delete" not in cascade, relationship
            assert "delete-orphan" not in cascade, relationship


def test_input_boundary_constants_match_the_column(app):
    with app.app_context():
        assert QUIZ_TITLE_MAX_LENGTH == 150
        assert Quiz.__table__.c.title.type.length == QUIZ_TITLE_MAX_LENGTH
        assert QUIZ_INSTRUCTIONS_MAX_LENGTH == 10000
        # `instructions` is an unbounded Text column; the finite boundary
        # is the form's, which imports the constant above.
        assert Quiz.__table__.c.instructions.type.length is None


# ---------------------------------------------------------------------------
# required fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["title", "instructions", "group_id", "public_id"])
def test_required_columns_are_not_nullable(app, missing):
    with app.app_context():
        assert Quiz.__table__.c[missing].nullable is False


def test_a_null_title_or_instructions_is_rejected(app):
    with app.app_context():
        group = _group()
        for override in ({"title": None}, {"instructions": None}):
            values = {"group_id": group.id, "title": "T", "instructions": "I"}
            values.update(override)
            db.session.add(Quiz(**values))
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()


# ---------------------------------------------------------------------------
# uniqueness
# ---------------------------------------------------------------------------


def test_title_is_unique_within_one_group(app):
    with app.app_context():
        group = _group()
        _quiz(group, title="Same")
        db.session.add(Quiz(group_id=group.id, title="Same", instructions="Other"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_the_same_title_is_allowed_in_another_group(app):
    with app.app_context():
        first = _group(name="Group A")
        second = _group(name="Group B", course_title="Writing")
        _quiz(first, title="Same")
        _quiz(second, title="Same")
        assert Quiz.query.filter_by(title="Same").count() == 2


def test_public_id_is_unique(app):
    with app.app_context():
        group = _group()
        first = _quiz(group, title="A")
        db.session.add(
            Quiz(
                group_id=group.id,
                title="B",
                instructions="I",
                public_id=first.public_id,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


# ---------------------------------------------------------------------------
# positive version CHECK
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", [0, -1, -99])
def test_a_non_positive_version_is_rejected_by_the_check(app, version):
    with app.app_context():
        group = _group()
        db.session.add(
            Quiz(group_id=group.id, title="T", instructions="I", version=version)
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_the_positive_version_check_is_declared_by_name(app):
    with app.app_context():
        checks = {
            c["name"] for c in inspect(db.engine).get_check_constraints("quizzes")
        }
        assert "ck_quizzes_version_positive" in checks


def test_a_version_above_one_is_accepted(app):
    with app.app_context():
        quiz = _quiz(_group(), version=7)
        assert quiz.version == 7


# ---------------------------------------------------------------------------
# foreign key
# ---------------------------------------------------------------------------


def test_group_foreign_key_points_where_documented_and_carries_no_ondelete(app):
    with app.app_context():
        keys = {
            tuple(fk["constrained_columns"]): fk
            for fk in inspect(db.engine).get_foreign_keys("quizzes")
        }
        assert keys[("group_id",)]["referred_table"] == "groups"
        assert keys[("group_id",)]["referred_columns"] == ["id"]
        assert not (keys[("group_id",)].get("options") or {}).get("ondelete")


def test_an_unknown_group_is_rejected(app):
    with app.app_context():
        group = _group()
        db.session.add(
            Quiz(group_id=group.id + 9999, title="Orphan", instructions="I")
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


# ---------------------------------------------------------------------------
# indexes -- exactly the one query-driven index, no redundant FK index
# ---------------------------------------------------------------------------


def test_only_the_one_query_driven_index_exists(app):
    """The Teacher list is the only read shape in M04A: a single-Group
    equality ordered ``created_at DESC, id DESC``. No speculative index is
    declared for a search, a cross-Group listing or a counter, because no
    such read exists."""
    with app.app_context():
        indexes = {
            i["name"]: list(i["column_names"])
            for i in inspect(db.engine).get_indexes("quizzes")
        }
        assert indexes == {
            "ix_quizzes_group_created_id": ["group_id", "created_at", "id"]
        }


def test_no_redundant_single_column_group_id_index(app):
    """``uq_quizzes_group_title`` and ``ix_quizzes_group_created_id`` both
    start with ``group_id``, so the foreign key already has a usable
    leftmost prefix and must not carry a duplicate of its own."""
    with app.app_context():
        insp = inspect(db.engine)
        for index in insp.get_indexes("quizzes"):
            assert list(index["column_names"]) != ["group_id"], index["name"]
        uniques = {
            u["name"]: list(u["column_names"])
            for u in insp.get_unique_constraints("quizzes")
        }
        assert uniques["uq_quizzes_group_title"] == ["group_id", "title"]


# ---------------------------------------------------------------------------
# timestamps
# ---------------------------------------------------------------------------


def test_updated_at_has_no_implicit_onupdate_hook(app):
    """The write path samples one authoritative post-lock whole-second
    moment and assigns it explicitly. An implicit ``onupdate`` would both
    bypass that truncation and fire on writes this milestone does not want
    timestamped -- including a save this Part defines as a no-op."""
    with app.app_context():
        assert Quiz.__table__.c.updated_at.onupdate is None
        assert Quiz.__table__.c.created_at.onupdate is None


def test_explicit_timestamps_are_persisted_unchanged(app):
    with app.app_context():
        moment = datetime(2026, 5, 1, 6, 30, 15)
        quiz = _quiz(_group(), created_at=moment, updated_at=moment)
        db.session.expire_all()
        stored = db.session.get(Quiz, quiz.id)
        assert stored.created_at == moment
        assert stored.updated_at == moment
