"""Phase 4 / M04B QuizQuestion and QuestionOption models and database
invariants.

The test backend is SQLite in memory built with ``db.create_all()``, so
the schema checks here prove the **models** are internally consistent and
that the application rules and the declared constraints agree. SQLite does
enforce CHECK constraints and (via the project's
``PRAGMA foreign_keys=ON`` hook in ``app/extensions.py``) foreign keys, so
those integrity assertions are real.

M04C adds the Alembic revision for these accepted models and their parent
Quiz. Migration structure, isolated execution, and offline MySQL compilation
live in ``tests/test_quiz_migration.py``; the checks here remain focused on
model and application invariants.
"""

from datetime import date, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    MAX_ACTIVE_OPTIONS,
    MIN_ACTIVE_OPTIONS,
    OPTION_TEXT_MAX_LENGTH,
    QUESTION_PROMPT_MAX_LENGTH,
    AcademicTerm,
    Course,
    Group,
    Level,
    QuestionAnswerMode,
    QuestionOption,
    Quiz,
    QuizQuestion,
)

MOMENT = datetime(2026, 5, 10, 9, 0, 0)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _quiz(name="Group A", title="Unit 1 check"):
    term = AcademicTerm(
        name=f"Term {name}", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
    )
    level = Level(name=f"Level {name}", display_order=0)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title=f"Course {name}", level_id=level.id, display_order=0)
    db.session.add(course)
    db.session.commit()
    group = Group(
        academic_term_id=term.id, course_id=course.id, name=name, capacity=20
    )
    db.session.add(group)
    db.session.commit()
    quiz = Quiz(
        group_id=group.id,
        title=title,
        instructions="Answer every question.",
        version=1,
        created_at=MOMENT,
        updated_at=MOMENT,
    )
    db.session.add(quiz)
    db.session.commit()
    return quiz


def _question(quiz, prompt="What is the capital of France?", mode=None, order=0, **kwargs):
    row = QuizQuestion(
        quiz_id=quiz.id,
        prompt=prompt,
        answer_mode=mode or QuestionAnswerMode.SINGLE.value,
        display_order=order,
        version=kwargs.pop("version", 1),
        created_at=kwargs.pop("created_at", MOMENT),
        updated_at=kwargs.pop("updated_at", MOMENT),
        **kwargs,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _option(question, text="Paris", order=0, is_correct=False, **kwargs):
    row = QuestionOption(
        question_id=question.id,
        option_text=text,
        display_order=order,
        is_correct=is_correct,
        is_active=kwargs.pop("is_active", True),
        retired_at=kwargs.pop("retired_at", None),
        created_at=kwargs.pop("created_at", MOMENT),
        updated_at=kwargs.pop("updated_at", MOMENT),
        **kwargs,
    )
    db.session.add(row)
    db.session.commit()
    return row


# ===========================================================================
# Registration, columns, relationships
# ===========================================================================


def test_tables_are_registered_under_the_expected_names(app):
    with app.app_context():
        assert QuizQuestion.__tablename__ == "quiz_questions"
        assert QuestionOption.__tablename__ == "question_options"
        tables = inspect(db.engine).get_table_names()
        assert "quiz_questions" in tables
        assert "question_options" in tables


def test_question_columns_are_exactly_what_m04b_approved(app):
    """Quiz, Group, Course, Level and AcademicTerm are reachable through
    ``question.quiz`` and must not be duplicated; there is no
    creator-owner column; and no placeholder is left for scoring, timing,
    publication or attempts."""
    with app.app_context():
        columns = {c.name for c in QuizQuestion.__table__.columns}
        assert columns == {
            "id",
            "public_id",
            "quiz_id",
            "prompt",
            "answer_mode",
            "display_order",
            "version",
            "created_at",
            "updated_at",
        }
        for forbidden in (
            "group_id", "course_id", "level_id", "academic_term_id",
            "teacher_id", "created_by", "owner_id",
            "points", "score", "weight", "max_score", "partial_credit",
            "status", "published_at", "time_limit", "seconds",
            "question_type", "media_url", "explanation",
            "is_active", "is_deleted", "retired_at", "deleted_at",
        ):
            assert forbidden not in columns, forbidden


def test_option_columns_are_exactly_what_m04b_approved(app):
    with app.app_context():
        columns = {c.name for c in QuestionOption.__table__.columns}
        assert columns == {
            "id",
            "public_id",
            "question_id",
            "option_text",
            "display_order",
            "is_correct",
            "is_active",
            "retired_at",
            "created_at",
            "updated_at",
        }
        for forbidden in (
            "quiz_id", "group_id", "teacher_id",
            "points", "score", "weight", "partial_credit", "feedback",
            "selected_count", "chosen_by", "media_url",
        ):
            assert forbidden not in columns, forbidden


def test_relationships_exist_in_both_directions(app):
    with app.app_context():
        quiz = _quiz()
        question = _question(quiz)
        option = _option(question)
        assert question.quiz.id == quiz.id
        assert [q.id for q in quiz.questions] == [question.id]
        assert option.question.id == question.id
        assert [o.id for o in question.options] == [option.id]


def test_no_orm_cascade_in_any_direction(app):
    """No lifecycle change anywhere may delete a question or an option,
    and deleting one may not reach back up the chain."""
    with app.app_context():
        for relationship in (
            Quiz.questions,
            QuizQuestion.quiz,
            QuizQuestion.options,
            QuestionOption.question,
        ):
            cascade = relationship.property.cascade
            assert "delete" not in cascade, relationship
            assert "delete-orphan" not in cascade, relationship


def test_input_boundary_constants_match_the_columns(app):
    with app.app_context():
        assert QUESTION_PROMPT_MAX_LENGTH == 5000
        assert OPTION_TEXT_MAX_LENGTH == 1000
        # Both are unbounded Text columns; the finite boundaries are the
        # form's, which imports these constants.
        assert QuizQuestion.__table__.c.prompt.type.length is None
        assert QuestionOption.__table__.c.option_text.type.length is None
        assert (MIN_ACTIVE_OPTIONS, MAX_ACTIVE_OPTIONS) == (2, 8)


# ===========================================================================
# public_id, required columns, foreign keys
# ===========================================================================


def test_public_ids_are_generated_uuid_strings(app):
    import uuid

    with app.app_context():
        question = _question(_quiz())
        option = _option(question)
        for row in (question, option):
            assert isinstance(row.public_id, str)
            assert str(uuid.UUID(row.public_id)) == row.public_id
            assert row.public_id != str(row.id)


@pytest.mark.parametrize(
    "model,column",
    [
        (QuizQuestion, "public_id"),
        (QuizQuestion, "quiz_id"),
        (QuizQuestion, "prompt"),
        (QuizQuestion, "answer_mode"),
        (QuizQuestion, "display_order"),
        (QuizQuestion, "version"),
        (QuizQuestion, "created_at"),
        (QuizQuestion, "updated_at"),
        (QuestionOption, "public_id"),
        (QuestionOption, "question_id"),
        (QuestionOption, "option_text"),
        (QuestionOption, "display_order"),
        (QuestionOption, "is_correct"),
        (QuestionOption, "is_active"),
        (QuestionOption, "created_at"),
        (QuestionOption, "updated_at"),
    ],
)
def test_required_columns_are_not_nullable(app, model, column):
    with app.app_context():
        assert model.__table__.c[column].nullable is False


def test_retired_at_is_the_only_nullable_option_column(app):
    with app.app_context():
        assert QuestionOption.__table__.c.retired_at.nullable is True


def test_public_ids_are_unique(app):
    with app.app_context():
        quiz = _quiz()
        first = _question(quiz, prompt="A")
        db.session.add(
            QuizQuestion(
                quiz_id=quiz.id, prompt="B",
                answer_mode=QuestionAnswerMode.SINGLE.value,
                display_order=1, public_id=first.public_id,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        option = _option(first)
        db.session.add(
            QuestionOption(
                question_id=first.id, option_text="Other", display_order=1,
                is_correct=False, is_active=True, public_id=option.public_id,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_foreign_keys_point_where_documented_and_carry_no_ondelete(app):
    with app.app_context():
        insp = inspect(db.engine)
        question_fks = {
            tuple(fk["constrained_columns"]): fk
            for fk in insp.get_foreign_keys("quiz_questions")
        }
        assert question_fks[("quiz_id",)]["referred_table"] == "quizzes"
        assert question_fks[("quiz_id",)]["referred_columns"] == ["id"]
        assert not (question_fks[("quiz_id",)].get("options") or {}).get("ondelete")

        option_fks = {
            tuple(fk["constrained_columns"]): fk
            for fk in insp.get_foreign_keys("question_options")
        }
        assert option_fks[("question_id",)]["referred_table"] == "quiz_questions"
        assert option_fks[("question_id",)]["referred_columns"] == ["id"]
        assert not (option_fks[("question_id",)].get("options") or {}).get("ondelete")


def test_an_unknown_quiz_or_question_is_rejected(app):
    with app.app_context():
        quiz = _quiz()
        db.session.add(
            QuizQuestion(
                quiz_id=quiz.id + 9999, prompt="Orphan",
                answer_mode=QuestionAnswerMode.SINGLE.value, display_order=0,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        question = _question(quiz)
        db.session.add(
            QuestionOption(
                question_id=question.id + 9999, option_text="Orphan",
                display_order=0, is_correct=False, is_active=True,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


# ===========================================================================
# CHECK constraints
# ===========================================================================


def test_answer_mode_is_a_closed_set_in_the_application(app):
    with app.app_context():
        quiz = _quiz()
        for invalid in ("true_false", "short_answer", "SINGLE", "", "multiple_choice"):
            with pytest.raises(ValueError):
                QuizQuestion(
                    quiz_id=quiz.id, prompt="P", answer_mode=invalid, display_order=0
                )


def test_answer_mode_is_also_a_database_check(app):
    """The application ``@validates`` guard and the schema are rendered
    from the same enum, so a row that bypasses the ORM validator still
    cannot be inserted."""
    with app.app_context():
        quiz = _quiz()
        checks = {
            c["name"] for c in inspect(db.engine).get_check_constraints("quiz_questions")
        }
        assert "ck_quiz_questions_answer_mode_valid" in checks

        # Bypass the @validates hook with a direct INSERT.
        with pytest.raises(IntegrityError):
            db.session.execute(
                QuizQuestion.__table__.insert().values(
                    public_id="11111111-2222-3333-4444-555555555555",
                    quiz_id=quiz.id,
                    prompt="P",
                    answer_mode="true_false",
                    display_order=0,
                    version=1,
                    created_at=MOMENT,
                    updated_at=MOMENT,
                )
            )
        db.session.rollback()


@pytest.mark.parametrize("mode", ["single", "multiple"])
def test_both_approved_answer_modes_are_accepted(app, mode):
    with app.app_context():
        question = _question(_quiz(), mode=mode)
        assert question.answer_mode == mode


@pytest.mark.parametrize("order", [-1, -5])
def test_a_negative_question_order_is_rejected(app, order):
    with app.app_context():
        quiz = _quiz()
        db.session.add(
            QuizQuestion(
                quiz_id=quiz.id, prompt="P",
                answer_mode=QuestionAnswerMode.SINGLE.value, display_order=order,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize("order", [-1, -5])
def test_a_negative_option_order_is_rejected(app, order):
    with app.app_context():
        question = _question(_quiz())
        db.session.add(
            QuestionOption(
                question_id=question.id, option_text="X", display_order=order,
                is_correct=False, is_active=True,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize("version", [0, -1])
def test_a_non_positive_question_version_is_rejected(app, version):
    with app.app_context():
        quiz = _quiz()
        db.session.add(
            QuizQuestion(
                quiz_id=quiz.id, prompt="P",
                answer_mode=QuestionAnswerMode.SINGLE.value, display_order=0,
                version=version,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_the_named_checks_are_declared(app):
    with app.app_context():
        insp = inspect(db.engine)
        question_checks = {
            c["name"] for c in insp.get_check_constraints("quiz_questions")
        }
        assert "ck_quiz_questions_answer_mode_valid" in question_checks
        assert "ck_quiz_questions_display_order_non_negative" in question_checks
        assert "ck_quiz_questions_version_positive" in question_checks

        option_checks = {
            c["name"] for c in insp.get_check_constraints("question_options")
        }
        assert "ck_question_options_display_order_non_negative" in option_checks
        assert "ck_question_options_active_retired_consistency" in option_checks


# ---------------------------------------------------------------------------
# active / retired consistency
# ---------------------------------------------------------------------------


def test_an_active_option_with_a_retirement_time_is_rejected(app):
    with app.app_context():
        question = _question(_quiz())
        db.session.add(
            QuestionOption(
                question_id=question.id, option_text="X", display_order=0,
                is_correct=False, is_active=True, retired_at=MOMENT,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_an_inactive_option_without_a_retirement_time_is_rejected(app):
    with app.app_context():
        question = _question(_quiz())
        db.session.add(
            QuestionOption(
                question_id=question.id, option_text="X", display_order=0,
                is_correct=False, is_active=False, retired_at=None,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_both_consistent_states_are_accepted(app):
    with app.app_context():
        question = _question(_quiz())
        active = _option(question, text="Live", order=0)
        retired = _option(
            question, text="Gone", order=1, is_active=False, retired_at=MOMENT
        )
        assert active.is_active is True and active.retired_at is None
        assert retired.is_active is False and retired.retired_at == MOMENT


def test_retirement_preserves_the_option_as_history(app):
    """A retired option keeps its text, its answer-key value, its public
    id, its creation time and its stored order. It is never deleted."""
    with app.app_context():
        question = _question(_quiz())
        option = _option(question, text="Was correct", order=3, is_correct=True)
        public_id, created = option.public_id, option.created_at

        option.is_active = False
        option.retired_at = MOMENT
        db.session.commit()

        stored = QuestionOption.query.one()
        assert stored.option_text == "Was correct"
        assert stored.is_correct is True
        assert stored.display_order == 3
        assert stored.public_id == public_id
        assert stored.created_at == created
        assert QuestionOption.query.count() == 1


# ===========================================================================
# Deliberate non-constraints
# ===========================================================================


def test_question_prompt_text_is_not_unique(app):
    """Two questions may legitimately use identical wording."""
    with app.app_context():
        quiz = _quiz()
        _question(quiz, prompt="Same wording", order=0)
        _question(quiz, prompt="Same wording", order=1)
        assert QuizQuestion.query.filter_by(prompt="Same wording").count() == 2


def test_option_text_is_not_unique_in_the_database(app):
    """The duplicate-active-option rule is enforced by the application
    against the locked aggregate, deliberately NOT by a constraint: a
    retired row must be allowed to share wording with a live one, which is
    exactly the history the model keeps."""
    with app.app_context():
        question = _question(_quiz())
        _option(question, text="Paris", order=0, is_active=False, retired_at=MOMENT)
        _option(question, text="Paris", order=0)
        assert QuestionOption.query.filter_by(option_text="Paris").count() == 2


def test_display_order_is_not_unique_and_gaps_are_allowed(app):
    with app.app_context():
        quiz = _quiz()
        _question(quiz, prompt="A", order=0)
        _question(quiz, prompt="B", order=0)
        _question(quiz, prompt="C", order=90)
        assert QuizQuestion.query.count() == 3


def test_the_two_to_eight_rule_is_not_a_database_constraint(app):
    """It counts rows, which a CHECK cannot do. The application enforces
    it against the locked aggregate instead -- see the Teacher tests."""
    with app.app_context():
        question = _question(_quiz())
        _option(question, text="Only one", order=0, is_correct=True)
        assert QuestionOption.query.count() == 1


# ===========================================================================
# Indexes
# ===========================================================================


def test_only_the_one_query_driven_question_index_exists(app):
    with app.app_context():
        indexes = {
            i["name"]: list(i["column_names"])
            for i in inspect(db.engine).get_indexes("quiz_questions")
        }
        assert indexes == {
            "ix_quiz_questions_quiz_order_id": ["quiz_id", "display_order", "id"]
        }


def test_only_the_one_query_driven_option_index_exists(app):
    with app.app_context():
        indexes = {
            i["name"]: list(i["column_names"])
            for i in inspect(db.engine).get_indexes("question_options")
        }
        assert indexes == {
            "ix_question_options_question_active_order_id": [
                "question_id", "is_active", "display_order", "id"
            ]
        }


def test_no_redundant_single_column_foreign_key_indexes(app):
    """Each table's one index already starts with its foreign-key column,
    so the key has a usable leftmost prefix and must not carry a duplicate
    of its own."""
    with app.app_context():
        insp = inspect(db.engine)
        for table, column in (
            ("quiz_questions", "quiz_id"),
            ("question_options", "question_id"),
        ):
            for index in insp.get_indexes(table):
                assert list(index["column_names"]) != [column], (table, index["name"])
            assert insp.get_indexes(table)[0]["column_names"][0] == column


# ===========================================================================
# Timestamps
# ===========================================================================


def test_neither_model_has_an_implicit_onupdate_hook(app):
    """The write path samples one authoritative post-lock whole-second
    moment and assigns it explicitly to every row it changes. An implicit
    ``onupdate`` would bypass that truncation and fire on writes this Part
    defines as no-ops."""
    with app.app_context():
        for table in (QuizQuestion.__table__, QuestionOption.__table__):
            for column in ("created_at", "updated_at"):
                assert table.c[column].onupdate is None, (table.name, column)
        assert QuestionOption.__table__.c.retired_at.onupdate is None


def test_default_timestamps_are_naive_whole_second_utc(app):
    with app.app_context():
        question = QuizQuestion(
            quiz_id=_quiz().id, prompt="P",
            answer_mode=QuestionAnswerMode.SINGLE.value, display_order=0,
        )
        db.session.add(question)
        db.session.commit()
        assert question.created_at.tzinfo is None
        assert question.created_at.microsecond == 0
        assert question.updated_at.microsecond == 0


def test_explicit_timestamps_are_persisted_unchanged(app):
    with app.app_context():
        moment = datetime(2026, 5, 1, 6, 30, 15)
        question = _question(_quiz(), created_at=moment, updated_at=moment)
        db.session.expire_all()
        stored = db.session.get(QuizQuestion, question.id)
        assert stored.created_at == moment and stored.updated_at == moment


def test_default_question_version_is_one(app):
    with app.app_context():
        question = QuizQuestion(
            quiz_id=_quiz().id, prompt="P",
            answer_mode=QuestionAnswerMode.SINGLE.value, display_order=0,
        )
        db.session.add(question)
        db.session.commit()
        assert question.version == 1


def test_option_defaults_are_active_and_not_correct(app):
    with app.app_context():
        question = _question(_quiz())
        option = QuestionOption(
            question_id=question.id, option_text="X", display_order=0
        )
        db.session.add(option)
        db.session.commit()
        assert option.is_active is True
        assert option.is_correct is False
        assert option.retired_at is None
