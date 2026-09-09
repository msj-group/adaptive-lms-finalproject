"""Phase 4 / M04D Quiz lifecycle fields and the attempt aggregate.

Model defaults, validators, CHECK / UNIQUE / foreign-key constraints and
the absence of delete cascades. The test backend is SQLite in memory built
with ``db.create_all()``; SQLite enforces CHECK constraints and (via the
project's ``PRAGMA foreign_keys=ON`` hook) foreign keys, so these
integrity assertions are real. MySQL/InnoDB behaviour -- collation,
engine, index plans -- is out of reach here and is not claimed.
"""

from datetime import date, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    MAX_ATTEMPT_LIMIT,
    MAX_QUIZ_QUESTIONS,
    MAX_TIME_LIMIT_MINUTES,
    MIN_ATTEMPT_LIMIT,
    MIN_TIME_LIMIT_MINUTES,
    AcademicTerm,
    Course,
    Group,
    Level,
    QuestionAnswerMode,
    QuestionOption,
    Quiz,
    QuizAnswer,
    QuizAnswerSelection,
    QuizAttempt,
    QuizAttemptStatus,
    QuizQuestion,
    QuizStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password

NOW = datetime(2026, 5, 10, 9, 0, 0)
LATER = datetime(2026, 5, 10, 10, 0, 0)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _group(name="Group A"):
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
    return group


def _quiz(group=None, title="Unit 1 check", **kwargs):
    group = group or _group()
    quiz = Quiz(
        group_id=group.id,
        title=title,
        instructions="Answer every question.",
        version=kwargs.pop("version", 1),
        created_at=NOW,
        updated_at=NOW,
        **kwargs,
    )
    db.session.add(quiz)
    db.session.commit()
    return quiz


def _student(email="s@example.com"):
    row = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name="Student",
        role=UserRole.STUDENT.value,
        status=UserStatus.ACTIVE.value,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _attempt(quiz, student, number=1, **kwargs):
    row = QuizAttempt(
        quiz_id=quiz.id,
        student_id=student.id,
        attempt_number=number,
        status=kwargs.pop("status", QuizAttemptStatus.IN_PROGRESS.value),
        quiz_version=kwargs.pop("quiz_version", quiz.version),
        started_at=kwargs.pop("started_at", NOW),
        deadline_at=kwargs.pop("deadline_at", LATER),
        **kwargs,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _question(quiz, order=0):
    row = QuizQuestion(
        quiz_id=quiz.id,
        prompt="P",
        answer_mode=QuestionAnswerMode.SINGLE.value,
        display_order=order,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _option(question, text="A", order=0, correct=False):
    row = QuestionOption(
        question_id=question.id,
        option_text=text,
        display_order=order,
        is_correct=correct,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    db.session.add(row)
    db.session.commit()
    return row


# ===========================================================================
# Quiz lifecycle columns
# ===========================================================================


def test_quiz_lifecycle_columns_exist_and_default_to_draft(app):
    with app.app_context():
        quiz = _quiz()
        assert quiz.status == QuizStatus.DRAFT.value
        assert quiz.published_at is None
        assert quiz.opens_at is None and quiz.closes_at is None
        assert quiz.time_limit_minutes is None
        assert quiz.attempt_limit == MIN_ATTEMPT_LIMIT


def test_quiz_columns_are_exactly_what_m04d_approved(app):
    with app.app_context():
        assert {c.name for c in Quiz.__table__.columns} == {
            "id", "public_id", "group_id", "title", "instructions", "version",
            "created_at", "updated_at",
            "status", "opens_at", "closes_at", "time_limit_minutes",
            "attempt_limit", "published_at",
        }
        # No stored score, no derived-state column, no question counter.
        for forbidden in (
            "score", "max_score", "pass_mark", "question_count", "state",
            "is_open", "randomize", "shuffle",
        ):
            assert forbidden not in {c.name for c in Quiz.__table__.columns}


def test_quiz_status_is_a_closed_set_in_the_application(app):
    with app.app_context():
        group = _group()
        for invalid in ("archived", "closed", "PUBLISHED", "", "graded"):
            with pytest.raises(ValueError):
                Quiz(group_id=group.id, title="T", instructions="I", status=invalid)


def test_quiz_status_is_also_a_database_check(app):
    with app.app_context():
        group = _group()
        with pytest.raises(IntegrityError):
            db.session.execute(
                Quiz.__table__.insert().values(
                    public_id="11111111-2222-3333-4444-555555555555",
                    group_id=group.id, title="T", instructions="I", version=1,
                    status="archived", attempt_limit=1,
                    created_at=NOW, updated_at=NOW,
                )
            )
        db.session.rollback()


@pytest.mark.parametrize(
    "opens,closes",
    [
        (NOW, None),
        (None, LATER),
        (LATER, NOW),
        (NOW, NOW),
    ],
)
def test_a_half_or_reversed_availability_window_is_rejected(app, opens, closes):
    with app.app_context():
        group = _group()
        db.session.add(
            Quiz(group_id=group.id, title="T", instructions="I", version=1,
                 opens_at=opens, closes_at=closes, created_at=NOW, updated_at=NOW)
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_a_complete_ordered_window_and_no_window_are_both_accepted(app):
    with app.app_context():
        group = _group()
        _quiz(group, title="Windowed", opens_at=NOW, closes_at=LATER)
        _quiz(group, title="Unset")
        assert Quiz.query.count() == 2


@pytest.mark.parametrize("minutes", [0, -1, MAX_TIME_LIMIT_MINUTES + 1, 5000])
def test_a_time_limit_outside_the_approved_range_is_rejected(app, minutes):
    with app.app_context():
        group = _group()
        db.session.add(
            Quiz(group_id=group.id, title="T", instructions="I", version=1,
                 time_limit_minutes=minutes, created_at=NOW, updated_at=NOW)
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize(
    "minutes", [MIN_TIME_LIMIT_MINUTES, 45, MAX_TIME_LIMIT_MINUTES, None]
)
def test_time_limits_inside_the_range_and_none_are_accepted(app, minutes):
    with app.app_context():
        quiz = _quiz(time_limit_minutes=minutes)
        assert quiz.time_limit_minutes == minutes


@pytest.mark.parametrize("limit", [0, -1, MAX_ATTEMPT_LIMIT + 1, 99])
def test_an_attempt_limit_outside_the_approved_range_is_rejected(app, limit):
    with app.app_context():
        group = _group()
        db.session.add(
            Quiz(group_id=group.id, title="T", instructions="I", version=1,
                 attempt_limit=limit, created_at=NOW, updated_at=NOW)
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize(
    "status,published_at,ok",
    [
        (QuizStatus.DRAFT.value, None, True),
        (QuizStatus.DRAFT.value, NOW, False),
        (QuizStatus.PUBLISHED.value, NOW, True),
        (QuizStatus.PUBLISHED.value, None, False),
    ],
)
def test_status_and_published_at_must_agree(app, status, published_at, ok):
    with app.app_context():
        group = _group()
        db.session.add(
            Quiz(group_id=group.id, title="T", instructions="I", version=1,
                 status=status, published_at=published_at,
                 opens_at=NOW, closes_at=LATER, created_at=NOW, updated_at=NOW)
        )
        if ok:
            db.session.commit()
            assert Quiz.query.count() == 1
        else:
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()


def test_the_named_quiz_checks_and_index_are_declared(app):
    with app.app_context():
        insp = inspect(db.engine)
        checks = {c["name"] for c in insp.get_check_constraints("quizzes")}
        for name in (
            "ck_quizzes_status_valid",
            "ck_quizzes_availability_window",
            "ck_quizzes_time_limit_range",
            "ck_quizzes_attempt_limit_range",
            "ck_quizzes_status_published_at_consistency",
            "ck_quizzes_version_positive",
        ):
            assert name in checks, name
        indexes = {
            i["name"]: list(i["column_names"]) for i in insp.get_indexes("quizzes")
        }
        assert indexes["ix_quizzes_group_status_opens_id"] == [
            "group_id", "status", "opens_at", "id"
        ]


def test_the_question_maximum_is_a_stated_constant_not_a_column(app):
    """A row-count rule cannot be a CHECK, so it lives in the locked
    application transaction -- and a denormalized counter column would be
    a second source of truth."""
    with app.app_context():
        assert MAX_QUIZ_QUESTIONS == 100
        assert "question_count" not in {c.name for c in Quiz.__table__.columns}


# ===========================================================================
# QuizAttempt
# ===========================================================================


def test_attempt_columns_are_exactly_what_m04d_approved(app):
    with app.app_context():
        assert {c.name for c in QuizAttempt.__table__.columns} == {
            "id", "public_id", "quiz_id", "student_id", "attempt_number",
            "status", "quiz_version", "started_at", "deadline_at",
            "submitted_at", "correct_count", "total_questions",
        }
        for forbidden in (
            "score", "percentage", "points", "grade", "passed", "partial_credit",
            "group_id", "graded_by", "reviewed_at", "is_deleted",
        ):
            assert forbidden not in {c.name for c in QuizAttempt.__table__.columns}


def test_attempt_defaults_and_public_id(app):
    import uuid

    with app.app_context():
        attempt = _attempt(_quiz(), _student())
        assert attempt.status == QuizAttemptStatus.IN_PROGRESS.value
        assert attempt.submitted_at is None
        assert attempt.correct_count is None and attempt.total_questions is None
        assert str(uuid.UUID(attempt.public_id)) == attempt.public_id
        assert attempt.is_finalized is False


def test_attempt_status_is_a_closed_set_in_the_application(app):
    with app.app_context():
        quiz, student = _quiz(), _student()
        for invalid in ("abandoned", "graded", "IN_PROGRESS", "", "paused"):
            with pytest.raises(ValueError):
                QuizAttempt(
                    quiz_id=quiz.id, student_id=student.id, attempt_number=1,
                    status=invalid, quiz_version=1,
                    started_at=NOW, deadline_at=LATER,
                )


def test_attempt_status_is_also_a_database_check(app):
    with app.app_context():
        quiz, student = _quiz(), _student()
        with pytest.raises(IntegrityError):
            db.session.execute(
                QuizAttempt.__table__.insert().values(
                    public_id="22222222-3333-4444-5555-666666666666",
                    quiz_id=quiz.id, student_id=student.id, attempt_number=1,
                    status="abandoned", quiz_version=1,
                    started_at=NOW, deadline_at=LATER,
                )
            )
        db.session.rollback()


def test_one_attempt_number_per_quiz_and_student(app):
    with app.app_context():
        quiz, student = _quiz(), _student()
        _attempt(quiz, student, number=1)
        db.session.add(
            QuizAttempt(
                quiz_id=quiz.id, student_id=student.id, attempt_number=1,
                status=QuizAttemptStatus.IN_PROGRESS.value, quiz_version=1,
                started_at=NOW, deadline_at=LATER,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_the_same_attempt_number_is_fine_for_another_student_or_quiz(app):
    with app.app_context():
        group = _group()
        quiz_a = _quiz(group, title="A")
        quiz_b = _quiz(group, title="B")
        first = _student("a@example.com")
        second = _student("b@example.com")
        _attempt(quiz_a, first, number=1)
        _attempt(quiz_a, second, number=1)
        _attempt(quiz_b, first, number=1)
        assert QuizAttempt.query.count() == 3


@pytest.mark.parametrize("number", [0, -1])
def test_a_non_positive_attempt_number_is_rejected(app, number):
    with app.app_context():
        quiz, student = _quiz(), _student()
        db.session.add(
            QuizAttempt(
                quiz_id=quiz.id, student_id=student.id, attempt_number=number,
                status=QuizAttemptStatus.IN_PROGRESS.value, quiz_version=1,
                started_at=NOW, deadline_at=LATER,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize(
    "status,submitted_at,correct,total,ok",
    [
        ("in_progress", None, None, None, True),
        ("in_progress", NOW, None, None, False),
        ("in_progress", None, 1, 2, False),
        ("submitted", NOW, 1, 2, True),
        ("submitted", None, 1, 2, False),
        ("submitted", NOW, None, None, False),
        ("expired", None, 0, 2, True),
        ("expired", NOW, 0, 2, False),
        ("expired", None, None, None, False),
    ],
)
def test_finalization_consistency_is_enforced(
    app, status, submitted_at, correct, total, ok
):
    with app.app_context():
        quiz, student = _quiz(), _student()
        db.session.add(
            QuizAttempt(
                quiz_id=quiz.id, student_id=student.id, attempt_number=1,
                status=status, quiz_version=1, started_at=NOW, deadline_at=LATER,
                submitted_at=submitted_at, correct_count=correct,
                total_questions=total,
            )
        )
        if ok:
            db.session.commit()
            assert QuizAttempt.query.count() == 1
        else:
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()


@pytest.mark.parametrize("correct,total", [(-1, 5), (6, 5), (3, 2)])
def test_a_score_outside_its_own_total_is_rejected(app, correct, total):
    with app.app_context():
        quiz, student = _quiz(), _student()
        db.session.add(
            QuizAttempt(
                quiz_id=quiz.id, student_id=student.id, attempt_number=1,
                status=QuizAttemptStatus.SUBMITTED.value, quiz_version=1,
                started_at=NOW, deadline_at=LATER, submitted_at=NOW,
                correct_count=correct, total_questions=total,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_attempt_indexes_and_foreign_keys(app):
    with app.app_context():
        insp = inspect(db.engine)
        indexes = {
            i["name"]: list(i["column_names"])
            for i in insp.get_indexes("quiz_attempts")
        }
        assert indexes["ix_quiz_attempts_quiz_started_id"] == [
            "quiz_id", "started_at", "id"
        ]
        # `student_id` leads nothing above, so it carries its own index.
        assert ["student_id"] in indexes.values()
        keys = {
            tuple(fk["constrained_columns"]): fk
            for fk in insp.get_foreign_keys("quiz_attempts")
        }
        assert keys[("quiz_id",)]["referred_table"] == "quizzes"
        assert keys[("student_id",)]["referred_table"] == "users"
        for fk in keys.values():
            assert not (fk.get("options") or {}).get("ondelete")


# ===========================================================================
# QuizAnswer and QuizAnswerSelection
# ===========================================================================


def test_answer_and_selection_columns(app):
    with app.app_context():
        assert {c.name for c in QuizAnswer.__table__.columns} == {
            "id", "public_id", "attempt_id", "question_id", "created_at", "updated_at",
        }
        assert {c.name for c in QuizAnswerSelection.__table__.columns} == {
            "id", "public_id", "answer_id", "option_id", "created_at",
        }
        for forbidden in ("is_correct", "points", "score", "graded_at", "feedback"):
            assert forbidden not in {c.name for c in QuizAnswer.__table__.columns}
            assert forbidden not in {
                c.name for c in QuizAnswerSelection.__table__.columns
            }


def test_one_answer_per_attempt_and_question(app):
    with app.app_context():
        quiz = _quiz()
        question = _question(quiz)
        attempt = _attempt(quiz, _student())
        db.session.add(
            QuizAnswer(attempt_id=attempt.id, question_id=question.id,
                       created_at=NOW, updated_at=NOW)
        )
        db.session.commit()
        db.session.add(
            QuizAnswer(attempt_id=attempt.id, question_id=question.id,
                       created_at=NOW, updated_at=NOW)
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_one_selection_per_answer_and_option(app):
    with app.app_context():
        quiz = _quiz()
        question = _question(quiz)
        option = _option(question)
        attempt = _attempt(quiz, _student())
        answer = QuizAnswer(attempt_id=attempt.id, question_id=question.id,
                            created_at=NOW, updated_at=NOW)
        db.session.add(answer)
        db.session.commit()
        db.session.add(
            QuizAnswerSelection(answer_id=answer.id, option_id=option.id, created_at=NOW)
        )
        db.session.commit()
        db.session.add(
            QuizAnswerSelection(answer_id=answer.id, option_id=option.id, created_at=NOW)
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_answer_and_selection_foreign_keys_carry_no_ondelete(app):
    with app.app_context():
        insp = inspect(db.engine)
        for table, expected in (
            ("quiz_answers", {("attempt_id",): "quiz_attempts",
                              ("question_id",): "quiz_questions"}),
            ("quiz_answer_selections", {("answer_id",): "quiz_answers",
                                        ("option_id",): "question_options"}),
        ):
            keys = {
                tuple(fk["constrained_columns"]): fk
                for fk in insp.get_foreign_keys(table)
            }
            for columns, referred in expected.items():
                assert keys[columns]["referred_table"] == referred
                assert not (keys[columns].get("options") or {}).get("ondelete")


def test_no_orm_cascade_anywhere_in_the_attempt_aggregate(app):
    """No lifecycle change may delete an attempt, an answer or a
    selection, and deleting one may not reach back up the chain."""
    with app.app_context():
        for relationship in (
            Quiz.attempts,
            QuizAttempt.quiz,
            QuizAttempt.answers,
            QuizAnswer.attempt,
            QuizAnswer.selections,
            QuizAnswerSelection.answer,
            QuizAnswerSelection.option,
            QuizAnswer.question,
            QuizAttempt.student,
        ):
            cascade = relationship.property.cascade
            assert "delete" not in cascade, relationship
            assert "delete-orphan" not in cascade, relationship


def test_no_implicit_onupdate_hooks(app):
    """The write path samples one authoritative post-lock whole-second
    moment and assigns it explicitly to every row it changes."""
    with app.app_context():
        for table in (
            QuizAttempt.__table__, QuizAnswer.__table__,
            QuizAnswerSelection.__table__, Quiz.__table__,
        ):
            for column in table.columns:
                assert column.onupdate is None, (table.name, column.name)
