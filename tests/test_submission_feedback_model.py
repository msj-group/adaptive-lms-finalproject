"""Phase 4 / M03 SubmissionFeedback model, database invariants, and the
additive ``b26b20c3d20d`` migration.

The test backend is SQLite in memory built with ``db.create_all()``, so
the schema checks here prove the **models** match what the migration is
written to produce -- never that the migration runs on MySQL. SQLite does
enforce UNIQUE, NOT NULL, CHECK and (with the project's ``PRAGMA
foreign_keys=ON``) foreign keys, so those integrity assertions are real,
but MySQL/InnoDB behaviour (engine, collation, index plans) is out of
reach and is not claimed.
"""

import importlib.util
import os
import pathlib
import re
import tempfile
import uuid
from datetime import date, datetime

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    FEEDBACK_MAX_LENGTH,
    AcademicStatus,
    AcademicTerm,
    Assignment,
    AssignmentStatus,
    Course,
    Group,
    Level,
    Submission,
    SubmissionFeedback,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "b26b20c3d20d"
_DOWN_REVISION = "6b1f0ad74c92"

OPENS = datetime(2026, 5, 1, 8, 0)
DUE = datetime(2026, 5, 8, 23, 59)
SUBMITTED = datetime(2026, 5, 3, 10, 30)
REVIEWED = datetime(2026, 5, 10, 9, 0)

PW = "Sup3rSecret!123"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _user(email, role, status=UserStatus.ACTIVE.value, full_name="Someone"):
    row = User(email=email, password_hash=hash_password(PW), full_name=full_name,
               role=role, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def _group():
    term = AcademicTerm(name="Term", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                        status=AcademicStatus.ACTIVE.value)
    level = Level(name="Level", display_order=0, status=AcademicStatus.ACTIVE.value)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title="English", level_id=level.id, display_order=0,
                    status=AcademicStatus.ACTIVE.value)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name="Group A", capacity=20,
                  status=AcademicStatus.ACTIVE.value)
    db.session.add(group)
    db.session.commit()
    return group


def _assignment(group, title="Task 1"):
    row = Assignment(group_id=group.id, title=title, instructions="Do it.", opens_at=OPENS,
                     due_at=DUE, status=AssignmentStatus.PUBLISHED.value,
                     published_at=datetime(2026, 4, 1, 9, 0))
    db.session.add(row)
    db.session.commit()
    return row


def _submission(assignment, student, answer="An answer."):
    row = Submission(assignment_id=assignment.id, student_id=student.id, answer_text=answer,
                     submitted_at=SUBMITTED)
    db.session.add(row)
    db.session.commit()
    return row


def _chain(app):
    """(submission, teacher) with a full valid hierarchy behind them."""
    group = _group()
    assignment = _assignment(group)
    student = _user("s@example.com", UserRole.STUDENT.value)
    teacher = _user("t@example.com", UserRole.TEACHER.value)
    return _submission(assignment, student), teacher


def _feedback(submission, reviewer, text="Well argued.", version=1,
              created_at=REVIEWED, updated_at=REVIEWED):
    row = SubmissionFeedback(submission_id=submission.id, reviewer_id=reviewer.id,
                             feedback_text=text, version=version,
                             created_at=created_at, updated_at=updated_at)
    db.session.add(row)
    db.session.commit()
    return row


# ---------------------------------------------------------------------------
# columns and required fields
# ---------------------------------------------------------------------------


def test_a_feedback_row_stores_exactly_the_declared_fields(app):
    with app.app_context():
        submission, teacher = _chain(app)
        row = _feedback(submission, teacher, text="Line one\nLine two")
        db.session.expire_all()
        stored = db.session.get(SubmissionFeedback, row.id)
        assert stored.submission_id == submission.id
        assert stored.reviewer_id == teacher.id
        assert stored.feedback_text == "Line one\nLine two"
        assert stored.version == 1
        assert stored.created_at == REVIEWED and stored.updated_at == REVIEWED
        assert uuid.UUID(stored.public_id)


@pytest.mark.parametrize(
    "column",
    ["public_id", "submission_id", "reviewer_id", "feedback_text", "version",
     "created_at", "updated_at"],
)
def test_every_declared_column_is_not_null(app, column):
    with app.app_context():
        columns = {c["name"]: c for c in inspect(db.engine).get_columns("submission_feedback")}
        assert columns[column]["nullable"] is False


@pytest.mark.parametrize(
    "column", ["submission_id", "reviewer_id", "feedback_text"]
)
def test_a_missing_required_value_is_rejected(app, column):
    with app.app_context():
        submission, teacher = _chain(app)
        values = {"submission_id": submission.id, "reviewer_id": teacher.id,
                  "feedback_text": "Text"}
        values[column] = None
        db.session.add(SubmissionFeedback(**values))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_public_id_is_a_generated_unique_uuid(app):
    with app.app_context():
        submission, teacher = _chain(app)
        first = _feedback(submission, teacher)
        assert uuid.UUID(first.public_id)

        second_submission = _submission(
            _assignment(Group.query.one(), title="Task 2"),
            User.query.filter_by(role=UserRole.STUDENT.value).one(),
        )
        second = _feedback(second_submission, teacher)
        assert first.public_id != second.public_id

        # The UNIQUE constraint is the final defense behind that generator.
        db.session.add(SubmissionFeedback(
            submission_id=_submission(
                _assignment(Group.query.one(), title="Task 3"),
                User.query.filter_by(role=UserRole.STUDENT.value).one(),
            ).id,
            reviewer_id=teacher.id, feedback_text="x", public_id=first.public_id,
        ))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_the_text_column_holds_the_full_documented_maximum(app):
    """The form's 10,000-character boundary really does fit the column on
    the test backend. On MySQL ``TEXT`` is 65,535 **bytes**, which is
    above 10,000 utf8mb4 characters at any encoding width -- but that
    headroom has NOT been measured against a real MySQL insert here."""
    with app.app_context():
        submission, teacher = _chain(app)
        text = "أ" * FEEDBACK_MAX_LENGTH
        row = _feedback(submission, teacher, text=text)
        db.session.expire_all()
        assert len(db.session.get(SubmissionFeedback, row.id).feedback_text) == FEEDBACK_MAX_LENGTH


# ---------------------------------------------------------------------------
# constraints
# ---------------------------------------------------------------------------


def test_one_feedback_record_per_submission(app):
    with app.app_context():
        submission, teacher = _chain(app)
        _feedback(submission, teacher)
        other_teacher = _user("t2@example.com", UserRole.TEACHER.value)
        db.session.add(SubmissionFeedback(submission_id=submission.id,
                                          reviewer_id=other_teacher.id,
                                          feedback_text="Mine now."))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert SubmissionFeedback.query.count() == 1


def test_the_uniqueness_constraint_is_named(app):
    with app.app_context():
        names = {
            c["name"] for c in inspect(db.engine).get_unique_constraints("submission_feedback")
        }
        assert "uq_submission_feedback_submission" in names


@pytest.mark.parametrize("version", [0, -1])
def test_a_non_positive_version_is_rejected(app, version):
    with app.app_context():
        submission, teacher = _chain(app)
        db.session.add(SubmissionFeedback(submission_id=submission.id, reviewer_id=teacher.id,
                                          feedback_text="Text", version=version))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert SubmissionFeedback.query.count() == 0


def test_the_positive_version_check_is_named(app):
    with app.app_context():
        checks = {
            c["name"] for c in inspect(db.engine).get_check_constraints("submission_feedback")
        }
        assert "ck_submission_feedback_version_positive" in checks


def test_both_foreign_keys_point_where_documented_and_carry_no_ondelete(app):
    with app.app_context():
        keys = {
            tuple(fk["constrained_columns"]): fk
            for fk in inspect(db.engine).get_foreign_keys("submission_feedback")
        }
        assert keys[("submission_id",)]["referred_table"] == "submissions"
        assert keys[("submission_id",)]["referred_columns"] == ["id"]
        assert keys[("reviewer_id",)]["referred_table"] == "users"
        assert keys[("reviewer_id",)]["referred_columns"] == ["id"]
        for fk in keys.values():
            assert not (fk.get("options") or {}).get("ondelete")


def test_an_unknown_submission_or_reviewer_is_rejected(app):
    with app.app_context():
        submission, teacher = _chain(app)
        for values in (
            {"submission_id": submission.id + 9999, "reviewer_id": teacher.id},
            {"submission_id": submission.id, "reviewer_id": teacher.id + 9999},
        ):
            db.session.add(SubmissionFeedback(feedback_text="Text", **values))
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()


def test_no_cascade_or_delete_orphan_relationship_exists_in_either_direction(app):
    """Neither the model nor its neighbours declare an ORM relationship to
    feedback, so no lifecycle change anywhere can cascade into it -- and
    no template can lazy-load it."""
    from sqlalchemy import inspect as sa_inspect

    with app.app_context():
        mapper = sa_inspect(SubmissionFeedback)
        assert list(mapper.relationships) == []
        for model in (Submission, Assignment, User, Group):
            for relationship in sa_inspect(model).relationships:
                assert relationship.mapper.class_ is not SubmissionFeedback
                assert "delete" not in (relationship.cascade or ())
                assert "delete-orphan" not in (relationship.cascade or ())


# ---------------------------------------------------------------------------
# timestamps
# ---------------------------------------------------------------------------


def test_the_column_defaults_are_whole_second_naive_utc(app):
    """Defense in depth only -- the route supplies its own post-lock
    moment -- but it must carry the same canonical precision."""
    with app.app_context():
        submission, teacher = _chain(app)
        row = SubmissionFeedback(submission_id=submission.id, reviewer_id=teacher.id,
                                 feedback_text="Text")
        db.session.add(row)
        db.session.commit()
        db.session.expire_all()
        stored = db.session.get(SubmissionFeedback, row.id)
        assert stored.created_at.microsecond == 0
        assert stored.updated_at.microsecond == 0
        assert stored.created_at.tzinfo is None and stored.updated_at.tzinfo is None
        assert stored.version == 1


def test_updated_at_has_no_implicit_onupdate_hook(app):
    """``updated_at`` moves only when the write path assigns it. An
    implicit ``onupdate`` would both bypass the whole-second truncation
    and fire on writes M03 does not want timestamped."""
    with app.app_context():
        submission, teacher = _chain(app)
        row = _feedback(submission, teacher)
        assert SubmissionFeedback.__table__.c.updated_at.onupdate is None

        # A field change that does NOT touch updated_at leaves it alone.
        row.feedback_text = "Revised."
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(SubmissionFeedback, row.id).updated_at == REVIEWED


# ---------------------------------------------------------------------------
# what is deliberately absent
# ---------------------------------------------------------------------------


def test_no_grade_status_or_history_column_exists(app):
    with app.app_context():
        columns = {c["name"] for c in inspect(db.engine).get_columns("submission_feedback")}
        for forbidden in ("score", "grade", "max_points", "points", "passed", "status",
                          "state", "published", "published_at", "is_published", "attempt",
                          "review_status", "deleted_at", "previous_text"):
            assert forbidden not in columns, forbidden


def test_no_denormalized_owner_column_exists(app):
    """Assignment, Student, Group, Course, Level and AcademicTerm are all
    derived through the Submission -- none is duplicated here."""
    with app.app_context():
        columns = {c["name"] for c in inspect(db.engine).get_columns("submission_feedback")}
        for forbidden in ("assignment_id", "student_id", "group_id", "course_id",
                          "level_id", "academic_term_id"):
            assert forbidden not in columns, forbidden


def test_only_the_two_justified_index_objects_exist(app):
    """``uq_submission_feedback_submission`` covers the ``submission_id``
    FK and every M03 read; ``ix_submission_feedback_reviewer_id`` exists
    for the ``reviewer_id`` FK. No speculative reporting index."""
    with app.app_context():
        indexes = inspect(db.engine).get_indexes("submission_feedback")
        secondary = [i for i in indexes if not i["unique"]]
        assert [i["name"] for i in secondary] == ["ix_submission_feedback_reviewer_id"]
        assert list(secondary[0]["column_names"]) == ["reviewer_id"]
        # No separate single-column index duplicates the unique one.
        assert not any(
            not i["unique"] and list(i["column_names"]) == ["submission_id"] for i in indexes
        )


@pytest.mark.parametrize(
    "table", ["submissions", "assignments", "users", "enrollments", "groups"]
)
def test_no_existing_table_gained_a_feedback_column(app, table):
    with app.app_context():
        columns = {c["name"] for c in inspect(db.engine).get_columns(table)}
        assert not any("feedback" in name for name in columns), columns
        assert not any("review" in name for name in columns), columns


# ---------------------------------------------------------------------------
# migration b26b20c3d20d
# ---------------------------------------------------------------------------


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m03_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def test_revision_identifiers():
    module, _ = _load_migration()
    assert module.revision == _REVISION
    assert module.down_revision == _DOWN_REVISION
    assert module.branch_labels is None
    assert module.depends_on is None


def test_the_repository_has_one_linear_alembic_head_ending_here():
    """Every revision file's parentage forms one chain, and this revision
    is its only head."""
    parents, revisions = {}, set()
    for path in _MIGRATIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        rev = re.search(r"^revision = '([^']+)'", source, re.M).group(1)
        down = re.search(r"^down_revision = (?:'([^']+)'|None)", source, re.M).group(1)
        revisions.add(rev)
        parents[rev] = down

    heads = revisions - {d for d in parents.values() if d is not None}
    assert heads == {_REVISION}
    # Linear: every parent is claimed exactly once, and exactly one root.
    claimed = [d for d in parents.values() if d is not None]
    assert len(claimed) == len(set(claimed))
    assert len([r for r, d in parents.items() if d is None]) == 1


def test_migration_creates_only_submission_feedback_and_alters_nothing():
    _, source = _load_migration()
    created = re.findall(r"op\.create_table\('([^']+)'", source)
    assert created == ["submission_feedback"]
    batch_targets = set(re.findall(r"batch_alter_table\('([^']+)'", source))
    assert batch_targets == {"submission_feedback"}
    # No existing table is touched, and there is no data backfill.
    for forbidden in ("add_column", "drop_column", "alter_column", "execute(",
                      "create_foreign_key", "op.bulk_insert"):
        assert forbidden not in source, forbidden
    # `submissions` and `users` appear ONLY as foreign-key targets.
    assert source.count("['submissions.id']") == 1
    assert source.count("['users.id']") == 1
    assert "op.create_table('submissions'" not in source
    assert "op.create_table('users'" not in source


def test_migration_downgrade_is_symmetric():
    _, source = _load_migration()
    upgrade_src, downgrade_src = source.split("def downgrade():")
    created = re.findall(r"create_index\((?:batch_op\.f\()?'([^']+)'", upgrade_src)
    dropped = re.findall(r"drop_index\((?:batch_op\.f\()?'([^']+)'", downgrade_src)
    assert created == ["ix_submission_feedback_reviewer_id"]
    assert dropped == list(reversed(created))
    assert "op.drop_table('submission_feedback')" in downgrade_src
    assert "drop_table" not in upgrade_src


def test_migration_declares_the_named_constraints():
    _, source = _load_migration()
    assert "name='uq_submission_feedback_submission'" in source
    assert "name='ck_submission_feedback_version_positive'" in source
    assert "'version > 0'" in source


def test_migration_applies_and_reverses_on_an_isolated_temporary_database():
    """Runs the real ``upgrade()`` / ``downgrade()`` against a throwaway
    SQLite file -- never the development MySQL database, which this Part
    is not authorized to touch. This proves the operations are internally
    consistent, that the downgrade is genuinely reversible, and that the
    prerequisite tables and their rows survive untouched; it proves
    nothing about MySQL/InnoDB DDL.
    """
    module, _ = _load_migration()
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + os.path.join(tmp, "probe.db").replace(os.sep, "/")
        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn:
                # The two FK targets must exist; nothing else is needed.
                conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
                conn.execute(sa.text("CREATE TABLE submissions (id INTEGER PRIMARY KEY)"))
                conn.execute(sa.text("INSERT INTO users (id) VALUES (7)"))
                conn.execute(sa.text("INSERT INTO submissions (id) VALUES (3)"))
                conn.commit()

                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()
                conn.commit()

                insp = sa.inspect(conn)
                assert sorted(insp.get_table_names()) == [
                    "submission_feedback", "submissions", "users",
                ]
                assert [i["name"] for i in insp.get_indexes("submission_feedback")] == [
                    "ix_submission_feedback_reviewer_id",
                ]
                assert [c["name"] for c in insp.get_columns("submission_feedback")] == [
                    "id", "public_id", "submission_id", "reviewer_id",
                    "feedback_text", "version", "created_at", "updated_at",
                ]
                assert {
                    c["name"] for c in insp.get_unique_constraints("submission_feedback")
                } >= {"uq_submission_feedback_submission"}
                assert {
                    c["name"] for c in insp.get_check_constraints("submission_feedback")
                } == {"ck_submission_feedback_version_positive"}

                # The prerequisite tables are structurally untouched and
                # still hold their rows.
                assert [c["name"] for c in insp.get_columns("users")] == ["id"]
                assert [c["name"] for c in insp.get_columns("submissions")] == ["id"]
                assert conn.execute(sa.text("SELECT id FROM users")).scalars().all() == [7]
                assert conn.execute(
                    sa.text("SELECT id FROM submissions")
                ).scalars().all() == [3]

                with Operations.context(MigrationContext.configure(conn)):
                    module.downgrade()
                conn.commit()

                after = sa.inspect(conn)
                assert sorted(after.get_table_names()) == ["submissions", "users"]
                assert conn.execute(sa.text("SELECT id FROM users")).scalars().all() == [7]
                assert conn.execute(
                    sa.text("SELECT id FROM submissions")
                ).scalars().all() == [3]
        finally:
            engine.dispose()


def test_models_and_migration_agree_on_columns_and_nullability(app):
    """The migration's column list/nullability matches what the models
    actually build (the test backend runs ``create_all()``)."""
    _, source = _load_migration()
    declared = dict(
        re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)", source)
    )
    with app.app_context():
        actual = {
            c["name"]: str(c["nullable"])
            for c in inspect(db.engine).get_columns("submission_feedback")
        }
    # The primary key is NOT NULL in both, but SQLite reports it differently;
    # compare every other column exactly.
    declared.pop("id", None)
    actual.pop("id", None)
    assert declared == actual


def test_the_mysql_ddl_compiles_without_a_connection(app):
    """Offline dialect rendering only -- generated text, **not** MySQL
    execution. It confirms the declared types survive the MySQL dialect
    (``BIGINT`` key, ``VARCHAR(36)`` public id, ``TEXT`` body, ``DATETIME``
    with no fractional precision)."""
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateTable

    with app.app_context():
        ddl = str(CreateTable(SubmissionFeedback.__table__).compile(dialect=mysql.dialect()))
    assert "BIGINT" in ddl
    assert "VARCHAR(36)" in ddl
    assert "TEXT" in ddl
    assert "DATETIME" in ddl and "DATETIME(" not in ddl
    assert "uq_submission_feedback_submission" in ddl
    assert "ck_submission_feedback_version_positive" in ddl
