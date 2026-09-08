"""Phase 4 / M02 Submission model, database invariants, and the additive
``6b1f0ad74c92`` migration.

The test backend is SQLite in memory built with ``db.create_all()``, so
the schema checks here prove the **models** match what the migration is
written to produce -- never that the migration runs on MySQL. SQLite does
enforce UNIQUE, NOT NULL and (with the project's ``PRAGMA
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
from datetime import date, datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    ANSWER_MAX_LENGTH,
    AcademicStatus,
    AcademicTerm,
    Assignment,
    AssignmentStatus,
    Course,
    Group,
    Level,
    Submission,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "6b1f0ad74c92"
_DOWN_REVISION = "4f7c1d9b2e30"

OPENS = datetime(2026, 5, 1, 8, 0)
DUE = datetime(2026, 5, 8, 23, 59)
SUBMITTED = datetime(2026, 5, 3, 10, 30)

PW = "Sup3rSecret!123"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _student(email="student@example.com", role=UserRole.STUDENT.value):
    row = User(email=email, password_hash=hash_password(PW), full_name="A Student",
               role=role, status=UserStatus.ACTIVE.value)
    db.session.add(row)
    db.session.commit()
    return row


def _group(name="Group A"):
    term = AcademicTerm(name=f"Term {name}", start_date=date(2026, 1, 1),
                        end_date=date(2026, 12, 31), status=AcademicStatus.ACTIVE.value)
    level = Level(name=f"Level {name}", display_order=0, status=AcademicStatus.ACTIVE.value)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title=f"English {name}", level_id=level.id, display_order=0,
                    status=AcademicStatus.ACTIVE.value)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=name,
                  capacity=20, status=AcademicStatus.ACTIVE.value)
    db.session.add(group)
    db.session.commit()
    return group


def _assignment(group, title="Task"):
    row = Assignment(group_id=group.id, title=title, instructions="Do the work.",
                     opens_at=OPENS, due_at=DUE,
                     status=AssignmentStatus.PUBLISHED.value,
                     published_at=datetime(2026, 4, 1, 9, 0))
    db.session.add(row)
    db.session.commit()
    return row


def _submit(assignment, student, answer="My answer.", submitted_at=SUBMITTED):
    row = Submission(assignment_id=assignment.id, student_id=student.id,
                     answer_text=answer, submitted_at=submitted_at)
    db.session.add(row)
    db.session.commit()
    return row


# ---------------------------------------------------------------------------
# required fields and defaults
# ---------------------------------------------------------------------------


def test_a_submission_stores_exactly_what_was_given(app):
    with app.app_context():
        student = _student()
        assignment = _assignment(_group())
        row = _submit(assignment, student, answer="Line one\nLine two")

        stored = db.session.get(Submission, row.id)
        assert stored.assignment_id == assignment.id
        assert stored.student_id == student.id
        assert stored.answer_text == "Line one\nLine two"
        assert stored.submitted_at == SUBMITTED
        assert uuid.UUID(stored.public_id).version == 4


@pytest.mark.parametrize("missing", ["assignment_id", "student_id", "answer_text"])
def test_required_columns_are_not_nullable(app, missing):
    with app.app_context():
        student = _student()
        assignment = _assignment(_group())
        values = {
            "assignment_id": assignment.id,
            "student_id": student.id,
            "answer_text": "x",
            "submitted_at": SUBMITTED,
        }
        values[missing] = None
        db.session.add(Submission(**values))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_submitted_at_has_a_server_side_default(app):
    """Defense in depth: the route always supplies its post-lock
    authoritative moment, but the column can never end up NULL and can
    never take a browser value, because no code path reads one."""
    with app.app_context():
        student = _student()
        assignment = _assignment(_group())
        row = Submission(assignment_id=assignment.id, student_id=student.id,
                         answer_text="x")
        db.session.add(row)
        db.session.commit()
        assert row.submitted_at is not None
        assert row.submitted_at.tzinfo is None  # naive UTC, like every other column


def test_the_column_default_uses_the_canonical_whole_second_precision(app):
    """The default must match the route's authoritative moment: the
    column is MySQL ``DATETIME`` with fractional precision 0, and MySQL
    ROUNDS an excess fraction rather than truncating it, so a fractional
    value could be written one second later than the value the code
    compared."""
    with app.app_context():
        student = _student()
        assignment = _assignment(_group())
        row = Submission(assignment_id=assignment.id, student_id=student.id,
                         answer_text="x")
        db.session.add(row)
        db.session.commit()
        assert row.submitted_at.microsecond == 0

    # The default really is a live clock rather than a frozen constant,
    # so the assertion above is not passing vacuously.
    default = Submission.__table__.c.submitted_at.default.arg
    first, second = default(None), default(None)
    assert first.microsecond == 0 and second.microsecond == 0
    assert abs((second - first).total_seconds()) < 5


def test_the_timestamp_column_compiles_to_second_precision_on_mysql(app):
    """The reason the canonical precision exists at all -- stated as a
    check rather than only as a comment. This is dialect compilation, not
    a MySQL connection."""
    from sqlalchemy.dialects import mysql

    rendered = Submission.__table__.c.submitted_at.type.compile(dialect=mysql.dialect())
    assert rendered == "DATETIME"  # no (fsp) -> fractional precision 0


def test_public_id_is_unique(app):
    with app.app_context():
        student_a = _student("a@example.com")
        student_b = _student("b@example.com")
        assignment = _assignment(_group())
        first = _submit(assignment, student_a)

        db.session.add(Submission(assignment_id=assignment.id, student_id=student_b.id,
                                  answer_text="x", submitted_at=SUBMITTED,
                                  public_id=first.public_id))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_two_submissions_get_different_public_ids(app):
    with app.app_context():
        assignment = _assignment(_group())
        first = _submit(assignment, _student("a@example.com"))
        second = _submit(assignment, _student("b@example.com"))
        assert first.public_id != second.public_id


# ---------------------------------------------------------------------------
# the uniqueness invariant
# ---------------------------------------------------------------------------


def test_one_submission_per_assignment_and_student(app):
    with app.app_context():
        student = _student()
        assignment = _assignment(_group())
        _submit(assignment, student, answer="First")

        db.session.add(Submission(assignment_id=assignment.id, student_id=student.id,
                                  answer_text="Second", submitted_at=SUBMITTED))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        # The original row is untouched by the rejected attempt.
        assert Submission.query.count() == 1
        assert Submission.query.first().answer_text == "First"


def test_different_students_can_submit_to_the_same_assignment(app):
    with app.app_context():
        assignment = _assignment(_group())
        _submit(assignment, _student("a@example.com"), answer="A")
        _submit(assignment, _student("b@example.com"), answer="B")
        assert {row.answer_text for row in Submission.query} == {"A", "B"}


def test_one_student_can_submit_to_different_assignments(app):
    with app.app_context():
        student = _student()
        group = _group()
        _submit(_assignment(group, title="One"), student, answer="A1")
        _submit(_assignment(group, title="Two"), student, answer="A2")
        assert {row.answer_text for row in Submission.query} == {"A1", "A2"}


# ---------------------------------------------------------------------------
# foreign keys: exact targets, no cascade, no ORM relationship
# ---------------------------------------------------------------------------


def test_foreign_keys_point_at_assignments_and_users_with_no_cascade(app):
    with app.app_context():
        fks = {
            tuple(fk["constrained_columns"]): fk
            for fk in inspect(db.engine).get_foreign_keys("submissions")
        }
        assert set(fks) == {("assignment_id",), ("student_id",)}
        assert fks[("assignment_id",)]["referred_table"] == "assignments"
        assert fks[("assignment_id",)]["referred_columns"] == ["id"]
        assert fks[("student_id",)]["referred_table"] == "users"
        assert fks[("student_id",)]["referred_columns"] == ["id"]
        for fk in fks.values():
            options = fk.get("options") or {}
            assert options.get("ondelete") in (None, "")
            assert options.get("onupdate") in (None, "")


def test_a_submission_cannot_reference_a_missing_assignment_or_user(app):
    with app.app_context():
        student = _student()
        assignment = _assignment(_group())
        for values in (
            {"assignment_id": assignment.id + 9999, "student_id": student.id},
            {"assignment_id": assignment.id, "student_id": student.id + 9999},
        ):
            db.session.add(Submission(answer_text="x", submitted_at=SUBMITTED, **values))
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()


def test_the_model_declares_no_relationship_in_either_direction(app):
    """No relationship means no lazy load on a rendered page and, more
    importantly, no ``cascade`` / ``delete-orphan`` configuration that
    could ever remove submission history."""
    with app.app_context():
        assert list(Submission.__mapper__.relationships.keys()) == []
        assert "submissions" not in Assignment.__mapper__.relationships.keys()
        assert "submissions" not in User.__mapper__.relationships.keys()
        # Positive control: the relationships that DO exist are still there,
        # so this is not passing because the mappers were not configured.
        assert "group" in Assignment.__mapper__.relationships.keys()


def test_no_duplicated_ownership_or_workflow_columns(app):
    """Group / Course / Level / AcademicTerm / Teacher / Enrollment are
    all reachable through Assignment and Student, and none of the
    deferred grading or attempt machinery exists yet."""
    with app.app_context():
        columns = {c["name"] for c in inspect(db.engine).get_columns("submissions")}
        assert columns == {
            "id", "public_id", "assignment_id", "student_id",
            "answer_text", "submitted_at",
        }


# ---------------------------------------------------------------------------
# answer text
# ---------------------------------------------------------------------------


def test_answer_limit_constant_is_ten_thousand(app):
    from app.blueprints.student.forms import SubmissionForm

    assert ANSWER_MAX_LENGTH == 10000
    assert SubmissionForm.ANSWER_MAX == ANSWER_MAX_LENGTH


def test_the_column_itself_stores_a_maximum_length_answer(app):
    """The finite boundary is the form's ``Length``; the column is
    deliberately unbounded ``Text`` so the limit can be tuned without a
    migration."""
    with app.app_context():
        student = _student()
        assignment = _assignment(_group())
        answer = "x" * ANSWER_MAX_LENGTH
        row = _submit(assignment, student, answer=answer)
        assert len(db.session.get(Submission, row.id).answer_text) == ANSWER_MAX_LENGTH


def test_unicode_and_line_breaks_round_trip_unchanged(app):
    with app.app_context():
        student = _student()
        assignment = _assignment(_group())
        answer = "أهلاً بالعالم\n\nSecond paragraph\tindented — ✓"
        row = _submit(assignment, student, answer=answer)
        db.session.expire_all()
        assert db.session.get(Submission, row.id).answer_text == answer


# ---------------------------------------------------------------------------
# indexes -- one per justification, and no redundant FK index
# ---------------------------------------------------------------------------


def test_exactly_the_justified_indexes_exist(app):
    with app.app_context():
        indexes = {
            i["name"]: list(i["column_names"])
            for i in inspect(db.engine).get_indexes("submissions")
        }
        assert indexes == {
            "ix_submissions_assignment_submitted_id": [
                "assignment_id", "submitted_at", "id"
            ],
            "ix_submissions_student_id": ["student_id"],
        }


def test_the_unique_pair_constraint_is_named_and_ordered(app):
    with app.app_context():
        uniques = {
            u["name"]: list(u["column_names"])
            for u in inspect(db.engine).get_unique_constraints("submissions")
        }
        assert uniques["uq_submissions_assignment_student"] == [
            "assignment_id", "student_id"
        ]


def test_no_redundant_single_column_assignment_id_index(app):
    """``uq_submissions_assignment_student`` and
    ``ix_submissions_assignment_submitted_id`` both start with
    ``assignment_id``, so that foreign key already has a usable leftmost
    prefix. ``student_id`` leads nothing, which is exactly why it carries
    its own index."""
    with app.app_context():
        for index in inspect(db.engine).get_indexes("submissions"):
            assert list(index["column_names"]) != ["assignment_id"], index["name"]


# ---------------------------------------------------------------------------
# migration 6b1f0ad74c92
# ---------------------------------------------------------------------------


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m02_{_REVISION}", path)
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
    assert [r for r, d in parents.items() if d is None] != []
    assert len([r for r, d in parents.items() if d is None]) == 1


def test_migration_creates_only_submissions_and_alters_nothing(app):
    _, source = _load_migration()
    created = re.findall(r"op\.create_table\('([^']+)'", source)
    assert created == ["submissions"]
    batch_targets = set(re.findall(r"batch_alter_table\('([^']+)'", source))
    assert batch_targets == {"submissions"}
    # No existing table is touched, and there is no data backfill.
    for forbidden in ("add_column", "drop_column", "alter_column", "execute(",
                      "create_foreign_key", "op.bulk_insert", "'users'", '"users"'):
        assert forbidden not in source, forbidden
    # `assignments` and `users` appear ONLY as foreign-key targets.
    assert source.count("['assignments.id']") == 1
    assert source.count("['users.id']") == 1
    assert "op.create_table('assignments'" not in source


def test_migration_downgrade_is_symmetric():
    _, source = _load_migration()
    upgrade_src, downgrade_src = source.split("def downgrade():")
    created = re.findall(r"create_index\((?:batch_op\.f\()?'([^']+)'", upgrade_src)
    dropped = re.findall(r"drop_index\((?:batch_op\.f\()?'([^']+)'", downgrade_src)
    assert created == [
        "ix_submissions_assignment_submitted_id",
        "ix_submissions_student_id",
    ]
    assert dropped == list(reversed(created))
    assert "op.drop_table('submissions')" in downgrade_src
    assert "drop_table" not in upgrade_src


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
                conn.execute(sa.text("CREATE TABLE assignments (id INTEGER PRIMARY KEY)"))
                conn.execute(sa.text("INSERT INTO users (id) VALUES (7)"))
                conn.execute(sa.text("INSERT INTO assignments (id) VALUES (3)"))
                conn.commit()

                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()
                conn.commit()

                insp = sa.inspect(conn)
                assert sorted(insp.get_table_names()) == [
                    "assignments", "submissions", "users",
                ]
                assert sorted(i["name"] for i in insp.get_indexes("submissions")) == [
                    "ix_submissions_assignment_submitted_id",
                    "ix_submissions_student_id",
                ]
                assert [c["name"] for c in insp.get_columns("submissions")] == [
                    "id", "public_id", "assignment_id", "student_id",
                    "answer_text", "submitted_at",
                ]
                # The prerequisite tables are structurally untouched and
                # still hold their rows.
                assert [c["name"] for c in insp.get_columns("users")] == ["id"]
                assert [c["name"] for c in insp.get_columns("assignments")] == ["id"]
                assert conn.execute(sa.text("SELECT id FROM users")).scalars().all() == [7]
                assert conn.execute(
                    sa.text("SELECT id FROM assignments")
                ).scalars().all() == [3]

                with Operations.context(MigrationContext.configure(conn)):
                    module.downgrade()
                conn.commit()

                after = sa.inspect(conn)
                assert sorted(after.get_table_names()) == ["assignments", "users"]
                assert conn.execute(sa.text("SELECT id FROM users")).scalars().all() == [7]
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
            for c in inspect(db.engine).get_columns("submissions")
        }
    # The primary key is NOT NULL in both, but SQLite reports it differently;
    # compare every other column exactly.
    declared.pop("id", None)
    actual.pop("id", None)
    assert declared == actual


@pytest.mark.parametrize("table", ["assignments", "users", "enrollments", "groups"])
def test_no_existing_table_gained_a_submission_column(app, table):
    with app.app_context():
        columns = {c["name"] for c in inspect(db.engine).get_columns(table)}
        assert not any("submission" in name for name in columns), columns


def test_assignment_time_window_is_unaffected_by_a_submission(app):
    """A Submission row carries no window of its own -- the deadline it
    was judged against lives on the Assignment, and stays there."""
    with app.app_context():
        student = _student()
        assignment = _assignment(_group())
        _submit(assignment, student, submitted_at=DUE - timedelta(seconds=1))
        db.session.expire_all()
        stored = db.session.get(Assignment, assignment.id)
        assert stored.opens_at == OPENS and stored.due_at == DUE
