"""The Gradebook aggregate's model-level contract (Phase 4 / M08).

Rows are written directly here, so what is exercised is the *schema and
the model validators* -- the final defense behind every route -- rather
than any write path. Every constraint this milestone relies on is
asserted to actually fire, and every one it deliberately does **not**
declare (a cascade, a hard delete, a score column on somebody else's
table) is asserted to be absent.
"""

from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    BASIS_POINTS_TOTAL,
    GRADE_COMMENT_MAX_LENGTH,
    GradeCategory,
    GradeItem,
    GradeRecord,
    GradeSourceKind,
    MAX_POINTS_CEILING,
    MIN_CATEGORY_BASIS_POINTS,
    MIN_POINTS,
    POINTS_PRECISION,
    POINTS_SCALE,
)
from tests import grade_fixtures as fx


# ===========================================================================
# GradeCategory
# ===========================================================================


def test_a_category_gets_a_public_id_and_starts_at_version_one(app):
    with app.app_context():
        _, group = fx.setup_group()
        row = fx.category(group, "Homework", 6000)
        assert row.public_id and len(row.public_id) == 36
        assert row.version == 1
        assert row.weight_basis_points == 6000
        assert row.created_at == row.updated_at == fx.NOW


def test_two_categories_in_one_group_cannot_share_a_title(app):
    with app.app_context():
        _, group = fx.setup_group()
        fx.category(group, "Homework", 5000)
        with pytest.raises(IntegrityError):
            fx.category(group, "Homework", 3000)
        db.session.rollback()


def test_the_same_title_is_fine_in_another_group(app):
    with app.app_context():
        _, group_a = fx.setup_group("A")
        _, group_b = fx.setup_group("B", teacher_email="t-b@example.com")
        fx.category(group_a, "Homework", 5000)
        fx.category(group_b, "Homework", 5000)
        assert GradeCategory.query.count() == 2


@pytest.mark.parametrize("weight", [0, -1, BASIS_POINTS_TOTAL + 1, 99999])
def test_the_model_validator_refuses_a_weight_outside_one_to_ten_thousand(app, weight):
    """The first of two defenses. The CHECK constraint behind it is
    exercised separately, on a raw insert that bypasses this validator."""
    with app.app_context():
        _, group = fx.setup_group()
        with pytest.raises(ValueError):
            GradeCategory(group_id=group.id, title="X", weight_basis_points=weight)


def test_the_weight_check_constraint_fires_on_a_raw_insert(app):
    """Bypassing the model validator entirely, which is what a manual row
    or a future code path would do."""
    with app.app_context():
        _, group = fx.setup_group()
        for weight in (0, BASIS_POINTS_TOTAL + 1):
            with pytest.raises(IntegrityError):
                db.session.execute(
                    db.text(
                        "INSERT INTO grade_categories (public_id, group_id, title,"
                        " weight_basis_points, version, created_at, updated_at)"
                        " VALUES (:p, :g, :t, :w, 1, :n, :n)"
                    ).bindparams(
                        p=f"cat-{weight}", g=group.id, t=f"T{weight}", w=weight, n=fx.NOW
                    )
                )
                db.session.commit()
            db.session.rollback()


def test_a_category_version_must_be_a_positive_integer(app):
    with app.app_context():
        _, group = fx.setup_group()
        for bad in (0, -1, True, "2", 1.5):
            with pytest.raises(ValueError):
                GradeCategory(
                    group_id=group.id, title="X", weight_basis_points=100, version=bad
                )


def test_there_is_no_status_or_deleted_at_column_on_a_category(app):
    """"The Group's active categories" is exactly "the Group's
    categories" -- one answer, not two that could disagree."""
    columns = {c.name for c in GradeCategory.__table__.columns}
    assert "status" not in columns
    assert "deleted_at" not in columns
    assert "archived_at" not in columns
    assert "is_active" not in columns


# ===========================================================================
# GradeItem -- the source-link rule
# ===========================================================================


def test_every_approved_source_kind_is_accepted(app):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "All kinds", 10000)
        assignment = fx.assignment_for(group)
        quiz = fx.quiz_for(group)
        speaking = fx.speaking_for(group, title="Talk")
        fx.grade_item(cat, "a", "assignment", assignment=assignment)
        fx.grade_item(cat, "q", "quiz", quiz=quiz)
        fx.grade_item(cat, "s", "speaking", speaking=speaking)
        fx.grade_item(cat, "c", "activity")
        fx.grade_item(cat, "m", "manual")
        assert GradeItem.query.count() == 5


def test_an_unknown_source_kind_is_refused_by_the_model_validator(app):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        for bad in ("attendance", "listening", "exam", "", "MANUAL"):
            with pytest.raises(ValueError):
                GradeItem(
                    category_id=cat.id,
                    title="X",
                    source_kind=bad,
                    max_points=Decimal("10.00"),
                )


def test_the_source_kind_check_constraint_fires_on_a_raw_insert(app):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        with pytest.raises(IntegrityError):
            db.session.execute(
                db.text(
                    "INSERT INTO grade_items (public_id, category_id, title, source_kind,"
                    " max_points, version, created_at, updated_at)"
                    " VALUES ('gi-bad', :c, 'X', 'attendance', 10.00, 1, :n, :n)"
                ).bindparams(c=cat.id, n=fx.NOW)
            )
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize(
    "kind,columns",
    [
        ("assignment", ("quiz_id",)),
        ("assignment", ("speaking_activity_id",)),
        ("quiz", ("assignment_id",)),
        ("speaking", ("quiz_id",)),
    ],
)
def test_a_linked_item_may_not_carry_a_second_link(app, kind, columns):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        assignment = fx.assignment_for(group)
        quiz = fx.quiz_for(group)
        speaking = fx.speaking_for(group, title="Talk")
        ids = {
            "assignment_id": assignment.id,
            "quiz_id": quiz.id,
            "speaking_activity_id": speaking.id,
        }
        own = {
            "assignment": "assignment_id",
            "quiz": "quiz_id",
            "speaking": "speaking_activity_id",
        }[kind]
        values = {own: ids[own]}
        for extra in columns:
            values[extra] = ids[extra]
        with pytest.raises(IntegrityError):
            db.session.add(
                GradeItem(
                    category_id=cat.id,
                    title="X",
                    source_kind=kind,
                    max_points=Decimal("10.00"),
                    version=1,
                    created_at=fx.NOW,
                    updated_at=fx.NOW,
                    **values,
                )
            )
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize("kind", ["assignment", "quiz", "speaking"])
def test_a_linked_kind_may_not_carry_no_link_at_all(app, kind):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        with pytest.raises(IntegrityError):
            db.session.add(
                GradeItem(
                    category_id=cat.id,
                    title="X",
                    source_kind=kind,
                    max_points=Decimal("10.00"),
                    version=1,
                    created_at=fx.NOW,
                    updated_at=fx.NOW,
                )
            )
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize("kind", ["activity", "manual"])
def test_an_unlinked_kind_may_not_carry_a_link(app, kind):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        quiz = fx.quiz_for(group)
        with pytest.raises(IntegrityError):
            db.session.add(
                GradeItem(
                    category_id=cat.id,
                    title="X",
                    source_kind=kind,
                    max_points=Decimal("10.00"),
                    quiz_id=quiz.id,
                    version=1,
                    created_at=fx.NOW,
                    updated_at=fx.NOW,
                )
            )
            db.session.commit()
        db.session.rollback()


def test_there_is_no_generic_polymorphic_source_column(app):
    """Three real, typed foreign keys -- never a ``source_type`` /
    ``source_id`` pair the database cannot check."""
    columns = {c.name for c in GradeItem.__table__.columns}
    assert "source_id" not in columns
    assert "source_table" not in columns
    assert "source_type" not in columns
    assert {"assignment_id", "quiz_id", "speaking_activity_id"} <= columns


def test_grade_items_carries_no_group_id_of_its_own(app):
    """Ownership runs through the category, so the two can never
    disagree."""
    assert "group_id" not in {c.name for c in GradeItem.__table__.columns}


# ===========================================================================
# GradeItem -- exact points, never a float
# ===========================================================================


def test_max_points_is_stored_and_read_back_as_an_exact_decimal(app):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        for text in ("0.01", "0.10", "12.34", "20.00", "99999.99"):
            item = fx.grade_item(cat, f"item-{text}", max_points=text)
            db.session.expire(item)
            reread = db.session.get(GradeItem, item.id)
            assert isinstance(reread.max_points, Decimal)
            assert reread.max_points == Decimal(text)


def test_a_binary_float_is_refused_outright_rather_than_converted(app):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        with pytest.raises(ValueError, match="binary float"):
            GradeItem(
                category_id=cat.id, title="X", source_kind="manual", max_points=0.1
            )


def test_max_points_rejects_a_third_decimal_place(app):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        with pytest.raises(ValueError, match="decimal places"):
            GradeItem(
                category_id=cat.id,
                title="X",
                source_kind="manual",
                max_points=Decimal("7.005"),
            )


def test_max_points_must_be_positive_and_within_the_column(app):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        for bad in (Decimal("0"), Decimal("-1.00"), MAX_POINTS_CEILING + 1):
            with pytest.raises(ValueError):
                GradeItem(
                    category_id=cat.id,
                    title="X",
                    source_kind="manual",
                    max_points=bad,
                )
        assert MIN_POINTS == Decimal("0.01")


def test_the_max_points_check_constraint_fires_on_a_raw_insert(app):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        with pytest.raises(IntegrityError):
            db.session.execute(
                db.text(
                    "INSERT INTO grade_items (public_id, category_id, title, source_kind,"
                    " max_points, version, created_at, updated_at)"
                    " VALUES ('gi-zero', :c, 'X', 'manual', 0, 1, :n, :n)"
                ).bindparams(c=cat.id, n=fx.NOW)
            )
            db.session.commit()
        db.session.rollback()


def test_the_points_columns_are_fixed_point_not_floating_point(app):
    for column in (GradeItem.__table__.c.max_points, GradeRecord.__table__.c.score):
        assert column.type.precision == POINTS_PRECISION
        assert column.type.scale == POINTS_SCALE
        assert column.type.asdecimal is True
        assert column.type.python_type is Decimal
        assert "FLOAT" not in type(column.type).__name__.upper()


def test_no_existing_table_gained_a_score_column(app):
    """The gradebook is the single source of truth for Teacher-entered
    grades, so no submission, attempt, recording, attendance or feedback
    table carries one."""
    with app.app_context():
        inspector = inspect(db.engine)
        for table in (
            "submissions",
            "submission_feedback",
            "quiz_attempts",
            "speaking_submissions",
            "speaking_feedback",
            "attendance_records",
            "assignments",
            "quizzes",
        ):
            names = {c["name"] for c in inspector.get_columns(table)}
            assert "score" not in names, table
            assert "grade" not in names, table
            assert "points" not in names, table
            assert "max_points" not in names, table


# ===========================================================================
# GradeItem lifecycle
# ===========================================================================


def test_an_item_starts_as_a_draft(app):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        item = fx.grade_item(cat)
        assert item.released_at is None
        assert item.is_released() is False


def test_released_at_is_the_only_release_state(app):
    columns = {c.name for c in GradeItem.__table__.columns}
    assert "status" not in columns
    assert "is_released" not in columns
    assert "released" not in columns
    assert "released_at" in columns


def test_an_item_version_must_be_a_positive_integer(app):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        for bad in (0, -1, True, "2"):
            with pytest.raises(ValueError):
                GradeItem(
                    category_id=cat.id,
                    title="X",
                    source_kind="manual",
                    max_points=Decimal("1.00"),
                    version=bad,
                )


# ===========================================================================
# GradeRecord
# ===========================================================================


def test_a_record_starts_ungraded_rather_than_zeroed(app):
    """A captured roster is a list of people to grade, not a list of
    zeros: a zero would be a claim nobody has made."""
    with app.app_context():
        _, group = fx.setup_group()
        student = fx.enroll(group)
        cat = fx.category(group, "Homework", 10000)
        item, records = fx.item_with_roster(cat, [student])
        assert records[0].score is None
        assert records[0].comment is None
        assert records[0].graded_by_id is None
        assert records[0].graded_at is None
        assert records[0].is_graded() is False


def test_one_record_per_item_and_student(app):
    with app.app_context():
        _, group = fx.setup_group()
        student = fx.enroll(group)
        cat = fx.category(group, "Homework", 10000)
        item = fx.grade_item(cat)
        fx.grade_record(item, student)
        with pytest.raises(IntegrityError):
            fx.grade_record(item, student, score="5.00")
        db.session.rollback()


def test_a_negative_score_is_refused_by_the_validator_and_by_the_check(app):
    with app.app_context():
        _, group = fx.setup_group()
        student = fx.enroll(group)
        cat = fx.category(group, "Homework", 10000)
        item = fx.grade_item(cat)
        with pytest.raises(ValueError):
            GradeRecord(
                grade_item_id=item.id,
                student_id=student.id,
                score=Decimal("-0.01"),
            )
        with pytest.raises(IntegrityError):
            db.session.execute(
                db.text(
                    "INSERT INTO grade_records (public_id, grade_item_id, student_id,"
                    " score, version, created_at, updated_at)"
                    " VALUES ('gr-neg', :i, :s, -1.00, 1, :n, :n)"
                ).bindparams(i=item.id, s=student.id, n=fx.NOW)
            )
            db.session.commit()
        db.session.rollback()


def test_a_null_score_is_legitimate_and_means_not_graded_yet(app):
    with app.app_context():
        _, group = fx.setup_group()
        student = fx.enroll(group)
        cat = fx.category(group, "Homework", 10000)
        item = fx.grade_item(cat)
        record = fx.grade_record(item, student, score=None)
        assert record.score is None


def test_a_score_is_stored_and_read_back_as_an_exact_decimal(app):
    with app.app_context():
        _, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        item = fx.grade_item(cat, max_points="99999.99")
        for index, text in enumerate(("0.00", "0.01", "0.10", "12.34", "99999.99")):
            student = fx.enroll(group, f"s{index}@example.com")
            record = fx.grade_record(item, student, score=text)
            db.session.expire(record)
            reread = db.session.get(GradeRecord, record.id)
            assert isinstance(reread.score, Decimal)
            assert reread.score == Decimal(text)


def test_a_score_float_is_refused_outright(app):
    with app.app_context():
        _, group = fx.setup_group()
        student = fx.enroll(group)
        cat = fx.category(group, "Homework", 10000)
        item = fx.grade_item(cat)
        with pytest.raises(ValueError, match="binary float"):
            GradeRecord(grade_item_id=item.id, student_id=student.id, score=18.5)


def test_graded_by_and_graded_at_are_both_null_or_both_set(app):
    with app.app_context():
        teacher, group = fx.setup_group()
        student = fx.enroll(group)
        cat = fx.category(group, "Homework", 10000)
        item = fx.grade_item(cat)
        record = fx.grade_record(item, student)
        with pytest.raises(IntegrityError):
            db.session.execute(
                db.text(
                    "UPDATE grade_records SET graded_by_id = :t WHERE id = :i"
                ).bindparams(t=teacher.id, i=record.id)
            )
            db.session.commit()
        db.session.rollback()


def test_the_comment_boundary_is_declared_beside_the_column(app):
    assert GRADE_COMMENT_MAX_LENGTH == 1000
    from app.blueprints.teacher.grade_forms import COMMENT_MAX

    assert COMMENT_MAX == GRADE_COMMENT_MAX_LENGTH


def test_a_record_version_must_be_a_positive_integer(app):
    with app.app_context():
        _, group = fx.setup_group()
        student = fx.enroll(group)
        cat = fx.category(group, "Homework", 10000)
        item = fx.grade_item(cat)
        for bad in (0, -1, True, "2"):
            with pytest.raises(ValueError):
                GradeRecord(
                    grade_item_id=item.id, student_id=student.id, version=bad
                )


# ===========================================================================
# No cascade, anywhere
# ===========================================================================


def test_no_foreign_key_declares_a_cascade(app):
    """No Group, Assignment, Quiz, Speaking, account or academic
    lifecycle change may remove a grade."""
    for model in (GradeCategory, GradeItem, GradeRecord):
        for fk in model.__table__.foreign_keys:
            assert fk.ondelete is None, (model.__tablename__, fk.parent.name)
            assert fk.onupdate is None, (model.__tablename__, fk.parent.name)


def test_no_orm_relationship_is_declared_in_either_direction(app):
    """Every read is an explicit join returning plain presentation dicts,
    so rendering a gradebook can never trigger a lazy load or an
    ORM-driven authorization decision -- and no ``cascade`` /
    ``delete-orphan`` configuration exists that could remove history."""
    for model in (GradeCategory, GradeItem, GradeRecord):
        assert not inspect(model).relationships.keys(), model.__tablename__
    from app.models import Assignment, Group, Quiz, SpeakingActivity, User

    for model in (Group, Assignment, Quiz, SpeakingActivity, User):
        for name in inspect(model).relationships.keys():
            assert "grade" not in name.lower(), (model.__tablename__, name)


def test_deleting_a_group_is_refused_while_it_owns_a_gradebook(app):
    """There is no hard-delete route anywhere in M08; this proves the
    schema would refuse one too, rather than quietly taking the grades
    with it."""
    with app.app_context():
        _, group = fx.setup_group()
        student = fx.enroll(group)
        cat = fx.category(group, "Homework", 10000)
        fx.item_with_roster(cat, [student], scores=("10.00",))
        db.session.execute(db.text("PRAGMA foreign_keys=ON"))
        with pytest.raises(IntegrityError):
            db.session.execute(
                db.text("DELETE FROM groups WHERE id = :i").bindparams(i=group.id)
            )
            db.session.commit()
        db.session.rollback()


def test_deleting_a_quiz_is_refused_while_a_grade_item_links_to_it(app):
    with app.app_context():
        _, group = fx.setup_group()
        quiz = fx.quiz_for(group)
        cat = fx.category(group, "Quizzes", 10000)
        fx.grade_item(cat, "Q grade", "quiz", quiz=quiz)
        db.session.execute(db.text("PRAGMA foreign_keys=ON"))
        with pytest.raises(IntegrityError):
            db.session.execute(
                db.text("DELETE FROM quizzes WHERE id = :i").bindparams(i=quiz.id)
            )
            db.session.commit()
        db.session.rollback()


# ===========================================================================
# The enum
# ===========================================================================


def test_the_source_kind_enum_is_exactly_the_five_approved_members(app):
    assert [kind.value for kind in GradeSourceKind] == [
        "assignment",
        "quiz",
        "speaking",
        "activity",
        "manual",
    ]


def test_there_is_no_attendance_source_kind(app):
    """M07 states that no attendance mark carries a weight, a score or a
    pass/fail meaning. M08 does not quietly reverse that."""
    values = {kind.value for kind in GradeSourceKind}
    for absent in ("attendance", "listening", "submission", "exam", "bonus"):
        assert absent not in values


def test_basis_points_constants_are_the_approved_ones(app):
    assert BASIS_POINTS_TOTAL == 10000
    assert MIN_CATEGORY_BASIS_POINTS == 1
