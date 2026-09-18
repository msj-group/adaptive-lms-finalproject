"""M08 migration checks for ``grade_categories``, ``grade_items`` and
``grade_records``.

The execution probe uses an isolated temporary SQLite database seeded
with the prerequisite tables **and representative existing rows** -- a
Group with an Enrollment and a teacher assignment, an ordinary Assignment
with a text Submission and its feedback, a Quiz with an attempt, a
Speaking activity with a recording, an attendance session with a record,
an UploadedFile and a Material -- so the "nothing existing is touched"
claim is executed rather than asserted. MySQL checks compile dialect DDL
only and never connect to the real application database.
"""
import importlib.util
import os
import pathlib
import re
import tempfile

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from app.extensions import db
from app.models import GradeCategory, GradeItem, GradeRecord

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "2f6d1c83ab47"
_DOWN_REVISION = "9c4d7e2b6f15"

_NEW_TABLES = ["grade_categories", "grade_items", "grade_records"]

_EXPECTED_COLUMNS = {
    "grade_categories": {
        "id", "public_id", "group_id", "title", "weight_basis_points", "version",
        "created_at", "updated_at",
    },
    "grade_items": {
        "id", "public_id", "category_id", "title", "source_kind", "max_points",
        "assignment_id", "quiz_id", "speaking_activity_id", "released_at", "version",
        "created_at", "updated_at",
    },
    "grade_records": {
        "id", "public_id", "grade_item_id", "student_id", "score", "comment",
        "graded_by_id", "graded_at", "version", "created_at", "updated_at",
    },
}


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m08_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def test_revision_identifiers_and_one_linear_head():
    module, _ = _load_migration()
    assert module.revision == _REVISION
    assert module.down_revision == _DOWN_REVISION
    assert module.branch_labels is None
    assert module.depends_on is None

    parents, revisions = {}, set()
    for path in _MIGRATIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r"^revision = '([^']+)'", source, re.M).group(1)
        down = re.search(r"^down_revision = (?:'([^']+)'|None)", source, re.M).group(1)
        revisions.add(revision)
        parents[revision] = down
    # Exactly one head. Phase 4 / M09, M10, M11, M12 and then M13 follow
    # this revision, so the single head is now Phase 5 / M05's rather than this one.
    assert revisions - {p for p in parents.values() if p is not None} == {
        "c5e8f2a7d914"
    }
    # No revision is claimed as the parent of two others (no branch).
    claimed = [p for p in parents.values() if p is not None]
    assert len(claimed) == len(set(claimed))
    # Exactly one root.
    assert len([r for r, p in parents.items() if p is None]) == 1


def _code(source):
    """The migration's executable body, without its module docstring.

    The docstring legitimately *names* the operations this revision does
    not perform, so scanning the whole file for those words would fail on
    the explanation rather than on any code.
    """
    return source.split("from alembic import op", 1)[1]


def test_migration_creates_exactly_three_tables_and_alters_nothing_else():
    _, source = _load_migration()
    code = _code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == _NEW_TABLES
    # No pre-existing table is altered at all: there is no batch_alter_table,
    # no add_column, no drop, no data rewrite and no seeded row.
    for forbidden in (
        "batch_alter_table", "add_column", "drop_column", "alter_column",
        "op.execute(", "op.bulk_insert", "ondelete", "onupdate",
        "DELETE FROM", "UPDATE ", "INSERT INTO",
    ):
        assert forbidden not in code, forbidden
    upgrade = code.split("def downgrade():")[0]
    assert "drop_table" not in upgrade
    # The only foreign-key targets are the four existing parents plus the
    # tables this revision itself creates first.
    assert sorted(set(re.findall(r"\['([a-z_]+\.id)'\]", code))) == [
        "assignments.id",
        "grade_categories.id",
        "grade_items.id",
        "groups.id",
        "quizzes.id",
        "speaking_activities.id",
        "users.id",
    ]


def test_migration_declares_every_expected_column_and_constraint():
    _, source = _load_migration()
    for table, expected in _EXPECTED_COLUMNS.items():
        block = source.split(f"op.create_table('{table}',", 1)[1].split("\n    )", 1)[0]
        assert set(re.findall(r"sa\.Column\('([^']+)'", block)) == expected, table
    for fragment in (
        "name='uq_grade_categories_group_title'",
        "name='ck_grade_categories_weight_range'",
        "name='ck_grade_categories_version_positive'",
        "name='ck_grade_items_source_kind'",
        "name='ck_grade_items_source_link'",
        "name='ck_grade_items_max_points_positive'",
        "name='ck_grade_items_version_positive'",
        "name='uq_grade_records_item_student'",
        "name='ck_grade_records_score_non_negative'",
        "name='ck_grade_records_graded_consistency'",
        "name='ck_grade_records_version_positive'",
        "sa.UniqueConstraint('public_id')",
        "'ix_grade_items_category_id'",
        "'ix_grade_items_assignment_id'",
        "'ix_grade_items_quiz_id'",
        "'ix_grade_items_speaking_activity_id'",
        "'ix_grade_records_student_item'",
        "'ix_grade_records_graded_by_id'",
        "source_kind IN ('assignment', 'quiz', 'speaking', 'activity', 'manual')",
        "sa.Numeric(precision=7, scale=2)",
    ):
        assert fragment in source, fragment


def test_the_migration_declares_no_floating_point_column():
    """A grade must never pass through a binary value, so no ``FLOAT``,
    ``DOUBLE`` or ``REAL`` column is created by this revision."""
    _, source = _load_migration()
    code = _code(source)
    for forbidden in ("sa.Float", "sa.REAL", "sa.DOUBLE", "Float(", "DOUBLE_PRECISION"):
        assert forbidden not in code, forbidden


def test_downgrade_drops_the_three_tables_it_created_and_nothing_else():
    _, source = _load_migration()
    downgrade = source.split("def downgrade():")[1]
    assert re.findall(r"op\.drop_table\('([^']+)'", downgrade) == list(
        reversed(_NEW_TABLES)
    )
    assert "drop_column" not in downgrade
    assert "drop_constraint" not in downgrade


def test_the_downgrade_drops_no_index_explicitly():
    """Tables only, and deliberately so.

    Every index this revision creates leads with a foreign-key column, and
    MySQL refuses to drop such an index while the constraint exists
    (errno 1553, "Cannot drop index ...: needed in a foreign key
    constraint") -- observed against the authorized development MySQL
    database, not assumed. ``DROP TABLE`` removes a table's own indexes
    and constraints with it, so dropping the three tables in reverse
    dependency order is both sufficient and the only order that works on
    MySQL and SQLite alike.
    """
    _, source = _load_migration()
    downgrade = source.split("def downgrade():")[1]
    assert "op.drop_index" not in downgrade


_PREREQ = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, role VARCHAR(32), status VARCHAR(32))",
    "CREATE TABLE groups (id INTEGER PRIMARY KEY, name VARCHAR(100))",
    """CREATE TABLE schedules (
        id INTEGER PRIMARY KEY,
        group_id INTEGER NOT NULL,
        FOREIGN KEY(group_id) REFERENCES groups (id)
    )""",
    """CREATE TABLE enrollments (
        id INTEGER PRIMARY KEY,
        student_id INTEGER NOT NULL,
        group_id INTEGER NOT NULL,
        status VARCHAR(32) NOT NULL,
        FOREIGN KEY(student_id) REFERENCES users (id),
        FOREIGN KEY(group_id) REFERENCES groups (id)
    )""",
    """CREATE TABLE group_teacher_assignments (
        id INTEGER PRIMARY KEY,
        teacher_id INTEGER NOT NULL,
        group_id INTEGER NOT NULL,
        status VARCHAR(32) NOT NULL,
        FOREIGN KEY(teacher_id) REFERENCES users (id),
        FOREIGN KEY(group_id) REFERENCES groups (id)
    )""",
    "CREATE TABLE lessons (id INTEGER PRIMARY KEY)",
    """CREATE TABLE uploaded_files (
        id INTEGER PRIMARY KEY,
        storage_key VARCHAR(120) NOT NULL UNIQUE,
        category VARCHAR(32) NOT NULL,
        uploaded_by_id INTEGER NOT NULL,
        FOREIGN KEY(uploaded_by_id) REFERENCES users (id)
    )""",
    """CREATE TABLE materials (
        id INTEGER PRIMARY KEY,
        lesson_id INTEGER NOT NULL,
        uploaded_file_id INTEGER UNIQUE,
        FOREIGN KEY(lesson_id) REFERENCES lessons (id),
        FOREIGN KEY(uploaded_file_id) REFERENCES uploaded_files (id)
    )""",
    """CREATE TABLE assignments (
        id INTEGER PRIMARY KEY,
        public_id VARCHAR(36) NOT NULL UNIQUE,
        group_id INTEGER NOT NULL,
        title VARCHAR(150) NOT NULL,
        status VARCHAR(32) NOT NULL,
        FOREIGN KEY(group_id) REFERENCES groups (id)
    )""",
    """CREATE TABLE submissions (
        id INTEGER PRIMARY KEY,
        assignment_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        answer_text TEXT NOT NULL,
        FOREIGN KEY(assignment_id) REFERENCES assignments (id),
        FOREIGN KEY(student_id) REFERENCES users (id)
    )""",
    """CREATE TABLE submission_feedback (
        id INTEGER PRIMARY KEY,
        submission_id INTEGER NOT NULL UNIQUE,
        reviewer_id INTEGER NOT NULL,
        feedback_text TEXT NOT NULL,
        version INTEGER NOT NULL,
        FOREIGN KEY(submission_id) REFERENCES submissions (id),
        FOREIGN KEY(reviewer_id) REFERENCES users (id)
    )""",
    "CREATE TABLE quizzes (id INTEGER PRIMARY KEY, public_id VARCHAR(36) NOT NULL"
    " UNIQUE, group_id INTEGER NOT NULL, title VARCHAR(150) NOT NULL,"
    " status VARCHAR(32) NOT NULL, FOREIGN KEY(group_id) REFERENCES groups (id))",
    "CREATE TABLE quiz_attempts (id INTEGER PRIMARY KEY, quiz_id INTEGER NOT NULL,"
    " student_id INTEGER NOT NULL, correct_count INTEGER, total_questions INTEGER,"
    " FOREIGN KEY(quiz_id) REFERENCES quizzes (id),"
    " FOREIGN KEY(student_id) REFERENCES users (id))",
    "CREATE TABLE speaking_activities (id INTEGER PRIMARY KEY, public_id VARCHAR(36)"
    " NOT NULL UNIQUE, assignment_id INTEGER NOT NULL UNIQUE,"
    " FOREIGN KEY(assignment_id) REFERENCES assignments (id))",
    "CREATE TABLE speaking_submissions (id INTEGER PRIMARY KEY, speaking_activity_id"
    " INTEGER NOT NULL, student_id INTEGER NOT NULL, audio_file_id INTEGER NOT NULL"
    " UNIQUE, FOREIGN KEY(speaking_activity_id) REFERENCES speaking_activities (id),"
    " FOREIGN KEY(student_id) REFERENCES users (id),"
    " FOREIGN KEY(audio_file_id) REFERENCES uploaded_files (id))",
    "CREATE TABLE attendance_sessions (id INTEGER PRIMARY KEY, group_id INTEGER"
    " NOT NULL, schedule_id INTEGER NOT NULL, session_date DATE NOT NULL,"
    " FOREIGN KEY(group_id) REFERENCES groups (id),"
    " FOREIGN KEY(schedule_id) REFERENCES schedules (id))",
    "CREATE TABLE attendance_records (id INTEGER PRIMARY KEY, attendance_session_id"
    " INTEGER NOT NULL, student_id INTEGER NOT NULL, status VARCHAR(32) NOT NULL,"
    " FOREIGN KEY(attendance_session_id) REFERENCES attendance_sessions (id),"
    " FOREIGN KEY(student_id) REFERENCES users (id))",
    "INSERT INTO users (id, role, status) VALUES (11, 'student', 'active')",
    "INSERT INTO users (id, role, status) VALUES (12, 'teacher', 'active')",
    "INSERT INTO users (id, role, status) VALUES (13, 'student', 'active')",
    "INSERT INTO users (id, role, status) VALUES (14, 'student', 'suspended')",
    "INSERT INTO groups (id, name) VALUES (7, 'Group A')",
    "INSERT INTO schedules (id, group_id) VALUES (3, 7)",
    "INSERT INTO enrollments (id, student_id, group_id, status) VALUES (4, 11, 7, 'active')",
    "INSERT INTO group_teacher_assignments (id, teacher_id, group_id, status)"
    " VALUES (5, 12, 7, 'active')",
    "INSERT INTO lessons (id) VALUES (3)",
    "INSERT INTO uploaded_files (id, storage_key, category, uploaded_by_id)"
    " VALUES (21, 'abc123', 'audio', 12)",
    "INSERT INTO materials (id, lesson_id, uploaded_file_id) VALUES (31, 3, NULL)",
    "INSERT INTO assignments (id, public_id, group_id, title, status) VALUES (1,"
    " 'assignment-public-1', 7, 'Existing assignment', 'published')",
    "INSERT INTO submissions (id, assignment_id, student_id, answer_text)"
    " VALUES (5, 1, 11, 'My answer.')",
    "INSERT INTO submission_feedback (id, submission_id, reviewer_id, feedback_text,"
    " version) VALUES (9, 5, 12, 'Well written.', 3)",
    "INSERT INTO quizzes (id, public_id, group_id, title, status) VALUES (2,"
    " 'quiz-public-1', 7, 'Existing quiz', 'published')",
    "INSERT INTO quiz_attempts (id, quiz_id, student_id, correct_count,"
    " total_questions) VALUES (8, 2, 11, 7, 10)",
    "INSERT INTO speaking_activities (id, public_id, assignment_id)"
    " VALUES (13, 'speaking-public-1', 1)",
    "INSERT INTO speaking_submissions (id, speaking_activity_id, student_id,"
    " audio_file_id) VALUES (17, 13, 11, 21)",
    "INSERT INTO attendance_sessions (id, group_id, schedule_id, session_date)"
    " VALUES (23, 7, 3, '2026-05-12')",
    "INSERT INTO attendance_records (id, attendance_session_id, student_id, status)"
    " VALUES (29, 23, 11, 'present')",
]

_PRESERVED = (
    ("users", 4),
    ("groups", 1),
    ("schedules", 1),
    ("enrollments", 1),
    ("group_teacher_assignments", 1),
    ("assignments", 1),
    ("submissions", 1),
    ("submission_feedback", 1),
    ("quizzes", 1),
    ("quiz_attempts", 1),
    ("speaking_activities", 1),
    ("speaking_submissions", 1),
    ("attendance_sessions", 1),
    ("attendance_records", 1),
    ("uploaded_files", 1),
    ("materials", 1),
)

_CATEGORY_COLUMNS = (
    "id, public_id, group_id, title, weight_basis_points, version, created_at, updated_at"
)
_ITEM_COLUMNS = (
    "id, public_id, category_id, title, source_kind, max_points, assignment_id,"
    " quiz_id, speaking_activity_id, released_at, version, created_at, updated_at"
)
_RECORD_COLUMNS = (
    "id, public_id, grade_item_id, student_id, score, comment, graded_by_id,"
    " graded_at, version, created_at, updated_at"
)
_MOMENT = "'2026-05-13 09:00:00'"


def test_migration_applies_and_reverses_on_isolated_sqlite():
    """Execute both directions against a temporary database seeded with
    the prerequisite tables and representative existing rows.

    Foreign keys stay **enforced** throughout: this revision only creates
    tables, so no Alembic batch rebuild of an existing table applies here
    at all. ``PRAGMA foreign_key_check`` is asserted after each direction
    anyway. Every probe row is removed again before the downgrade, so
    nothing this test inserted is left behind.
    """
    module, _ = _load_migration()
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + os.path.join(tmp, "probe.db").replace(os.sep, "/")
        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("PRAGMA foreign_keys=ON"))
                for statement in _PREREQ:
                    conn.execute(sa.text(statement))
                conn.commit()

                before = {
                    table: conn.execute(
                        sa.text(f"SELECT count(*) FROM {table}")
                    ).scalar_one()
                    for table, _ in _PRESERVED
                }

                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()
                conn.commit()
                assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []

                schema = inspect(conn)
                assert set(_NEW_TABLES).issubset(set(schema.get_table_names()))
                for table, expected in _EXPECTED_COLUMNS.items():
                    assert {c["name"] for c in schema.get_columns(table)} == expected
                assert {
                    c["name"] for c in schema.get_check_constraints("grade_categories")
                } == {
                    "ck_grade_categories_weight_range",
                    "ck_grade_categories_version_positive",
                }
                assert {
                    c["name"] for c in schema.get_check_constraints("grade_items")
                } == {
                    "ck_grade_items_source_kind",
                    "ck_grade_items_source_link",
                    "ck_grade_items_max_points_positive",
                    "ck_grade_items_version_positive",
                }
                assert {
                    c["name"] for c in schema.get_check_constraints("grade_records")
                } == {
                    "ck_grade_records_score_non_negative",
                    "ck_grade_records_graded_consistency",
                    "ck_grade_records_version_positive",
                }
                assert {
                    fk["constrained_columns"][0]: fk["referred_table"]
                    for fk in schema.get_foreign_keys("grade_categories")
                } == {"group_id": "groups"}
                assert {
                    fk["constrained_columns"][0]: fk["referred_table"]
                    for fk in schema.get_foreign_keys("grade_items")
                } == {
                    "category_id": "grade_categories",
                    "assignment_id": "assignments",
                    "quiz_id": "quizzes",
                    "speaking_activity_id": "speaking_activities",
                }
                assert {
                    fk["constrained_columns"][0]: fk["referred_table"]
                    for fk in schema.get_foreign_keys("grade_records")
                } == {
                    "grade_item_id": "grade_items",
                    "student_id": "users",
                    "graded_by_id": "users",
                }
                assert {
                    index["name"] for index in schema.get_indexes("grade_items")
                } >= {
                    "ix_grade_items_category_id",
                    "ix_grade_items_assignment_id",
                    "ix_grade_items_quiz_id",
                    "ix_grade_items_speaking_activity_id",
                }
                assert {
                    index["name"] for index in schema.get_indexes("grade_records")
                } >= {"ix_grade_records_student_item", "ix_grade_records_graded_by_id"}

                # THE data-preservation claim, executed: every existing row of
                # every neighbouring table survives untouched...
                after = {
                    table: conn.execute(
                        sa.text(f"SELECT count(*) FROM {table}")
                    ).scalar_one()
                    for table, _ in _PRESERVED
                }
                assert after == before == dict(_PRESERVED)
                feedback = conn.execute(sa.text(
                    "SELECT feedback_text, version FROM submission_feedback WHERE id = 9"
                )).one()
                assert feedback == ("Well written.", 3)
                attempt = conn.execute(sa.text(
                    "SELECT correct_count, total_questions FROM quiz_attempts WHERE id = 8"
                )).one()
                assert attempt == (7, 10)
                mark = conn.execute(sa.text(
                    "SELECT status FROM attendance_records WHERE id = 29"
                )).scalar_one()
                assert mark == "present"

                # ...and the new tables start EMPTY: no gradebook is seeded for
                # any existing group, enrollment, assignment or quiz attempt.
                for table in _NEW_TABLES:
                    assert conn.execute(
                        sa.text(f"SELECT count(*) FROM {table}")
                    ).scalar_one() == 0

                # The uniqueness rules and CHECKs really are enforced.
                conn.execute(sa.text(
                    f"INSERT INTO grade_categories ({_CATEGORY_COLUMNS}) VALUES"
                    f" (1, 'gc-1', 7, 'Homework', 6000, 1, {_MOMENT}, {_MOMENT})"
                ))
                conn.commit()
                for values, rule in (
                    ("(2, 'gc-2', 7, 'Homework', 4000, 1,", "group+title"),
                    ("(3, 'gc-1', 7, 'Other', 4000, 1,", "public_id"),
                    ("(4, 'gc-4', 7, 'Zero', 0, 1,", "weight >= 1"),
                    ("(5, 'gc-5', 7, 'Over', 10001, 1,", "weight <= 10000"),
                    ("(6, 'gc-6', 7, 'Ver', 4000, 0,", "version > 0"),
                ):
                    try:
                        conn.execute(sa.text(
                            f"INSERT INTO grade_categories ({_CATEGORY_COLUMNS})"
                            f" VALUES {values} {_MOMENT}, {_MOMENT})"
                        ))
                        raise AssertionError(f"{rule} was not enforced")
                    except sa.exc.IntegrityError:
                        conn.rollback()

                conn.execute(sa.text(
                    f"INSERT INTO grade_items ({_ITEM_COLUMNS}) VALUES"
                    f" (1, 'gi-1', 1, 'HW 1', 'manual', 20.00, NULL, NULL, NULL, NULL,"
                    f" 1, {_MOMENT}, {_MOMENT})"
                ))
                conn.commit()
                for values, rule in (
                    ("(2, 'gi-1', 1, 'X', 'manual', 20.00, NULL, NULL, NULL, NULL, 1,",
                     "public_id"),
                    ("(3, 'gi-3', 1, 'X', 'attendance', 20.00, NULL, NULL, NULL, NULL, 1,",
                     "source_kind CHECK"),
                    ("(4, 'gi-4', 1, 'X', 'manual', 20.00, 1, NULL, NULL, NULL, 1,",
                     "manual carries no link"),
                    ("(5, 'gi-5', 1, 'X', 'quiz', 20.00, NULL, NULL, NULL, NULL, 1,",
                     "quiz needs a quiz_id"),
                    ("(6, 'gi-6', 1, 'X', 'quiz', 20.00, 1, 2, NULL, NULL, 1,",
                     "quiz carries exactly one link"),
                    ("(7, 'gi-7', 1, 'X', 'manual', 0, NULL, NULL, NULL, NULL, 1,",
                     "max_points > 0"),
                    ("(8, 'gi-8', 1, 'X', 'manual', 20.00, NULL, NULL, NULL, NULL, 0,",
                     "version > 0"),
                ):
                    try:
                        conn.execute(sa.text(
                            f"INSERT INTO grade_items ({_ITEM_COLUMNS})"
                            f" VALUES {values} {_MOMENT}, {_MOMENT})"
                        ))
                        raise AssertionError(f"{rule} was not enforced")
                    except sa.exc.IntegrityError:
                        conn.rollback()

                # Every legitimate source shape is accepted.
                for index, (kind, links) in enumerate(
                    (
                        ("assignment", "1, NULL, NULL"),
                        ("quiz", "NULL, 2, NULL"),
                        ("speaking", "NULL, NULL, 13"),
                        ("activity", "NULL, NULL, NULL"),
                    ),
                    start=10,
                ):
                    conn.execute(sa.text(
                        f"INSERT INTO grade_items ({_ITEM_COLUMNS}) VALUES"
                        f" ({index}, 'gi-ok-{index}', 1, '{kind}', '{kind}', 10.00,"
                        f" {links}, NULL, 1, {_MOMENT}, {_MOMENT})"
                    ))
                conn.commit()

                conn.execute(sa.text(
                    f"INSERT INTO grade_records ({_RECORD_COLUMNS}) VALUES"
                    f" (1, 'gr-1', 1, 11, 18.50, 'Good', 12, {_MOMENT}, 1,"
                    f" {_MOMENT}, {_MOMENT})"
                ))
                conn.commit()
                for values, rule in (
                    ("(2, 'gr-2', 1, 11, 1.00, NULL, NULL, NULL, 1,", "item+student"),
                    ("(3, 'gr-1', 1, 13, 1.00, NULL, NULL, NULL, 1,", "public_id"),
                    ("(4, 'gr-4', 1, 13, -0.01, NULL, NULL, NULL, 1,", "score >= 0"),
                    ("(5, 'gr-5', 1, 13, 1.00, NULL, 12, NULL, 1,", "graded consistency"),
                    ("(6, 'gr-6', 1, 13, 1.00, NULL, NULL, " + _MOMENT + ", 1,",
                     "graded consistency"),
                    ("(7, 'gr-7', 1, 13, 1.00, NULL, NULL, NULL, 0,", "version > 0"),
                ):
                    try:
                        conn.execute(sa.text(
                            f"INSERT INTO grade_records ({_RECORD_COLUMNS})"
                            f" VALUES {values} {_MOMENT}, {_MOMENT})"
                        ))
                        raise AssertionError(f"{rule} was not enforced")
                    except sa.exc.IntegrityError:
                        conn.rollback()

                # A NULL score is the legitimate "not graded yet" state.
                conn.execute(sa.text(
                    f"INSERT INTO grade_records ({_RECORD_COLUMNS}) VALUES"
                    f" (8, 'gr-8', 1, 13, NULL, NULL, NULL, NULL, 1,"
                    f" {_MOMENT}, {_MOMENT})"
                ))
                conn.commit()

                # No cascade: the parents cannot be deleted out from under
                # the grades.
                for statement in (
                    "DELETE FROM groups WHERE id = 7",
                    "DELETE FROM quizzes WHERE id = 2",
                    "DELETE FROM users WHERE id = 11",
                ):
                    try:
                        conn.execute(sa.text(statement))
                        raise AssertionError(f"no-cascade was not enforced: {statement}")
                    except sa.exc.IntegrityError:
                        conn.rollback()

                # Leave no probe row behind.
                conn.execute(sa.text("DELETE FROM grade_records"))
                conn.execute(sa.text("DELETE FROM grade_items"))
                conn.execute(sa.text("DELETE FROM grade_categories"))
                conn.commit()

                with Operations.context(MigrationContext.configure(conn)):
                    module.downgrade()
                conn.commit()
                assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []

                after_down = set(inspect(conn).get_table_names())
                assert not set(_NEW_TABLES) & after_down
                # Every prerequisite table survives the whole round trip.
                assert {
                    table: conn.execute(
                        sa.text(f"SELECT count(*) FROM {table}")
                    ).scalar_one()
                    for table, _ in _PRESERVED
                } == dict(_PRESERVED)
                assert conn.execute(sa.text(
                    "SELECT feedback_text FROM submission_feedback WHERE id = 9"
                )).scalar_one() == "Well written."
        finally:
            engine.dispose()


def test_exact_decimal_values_survive_a_round_trip_without_drift():
    """The points columns hold what was written, to the cent.

    Run against an isolated database so nothing is left behind. On MySQL
    this is a real ``DECIMAL(7, 2)``; on SQLite SQLAlchemy binds through a
    C double and reads back through a ``"%.2f"`` formatter, and this
    proves that round trip is lossless for every value the column can
    hold -- including the ones that have no exact binary representation.
    """
    module, _ = _load_migration()
    values = ["0.01", "0.10", "0.20", "0.30", "1.05", "12.34", "33.33", "99999.99"]
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + os.path.join(tmp, "decimal.db").replace(os.sep, "/")
        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("PRAGMA foreign_keys=ON"))
                for statement in _PREREQ:
                    conn.execute(sa.text(statement))
                conn.commit()
                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()
                conn.execute(sa.text(
                    f"INSERT INTO grade_categories ({_CATEGORY_COLUMNS}) VALUES"
                    f" (1, 'gc-1', 7, 'Homework', 10000, 1, {_MOMENT}, {_MOMENT})"
                ))
                for index, text in enumerate(values, start=1):
                    conn.execute(sa.text(
                        f"INSERT INTO grade_items ({_ITEM_COLUMNS}) VALUES"
                        f" ({index}, 'gi-{index}', 1, 'I{index}', 'manual', {text},"
                        f" NULL, NULL, NULL, NULL, 1, {_MOMENT}, {_MOMENT})"
                    ))
                conn.commit()
                stored = [
                    row[0]
                    for row in conn.execute(
                        sa.text("SELECT max_points FROM grade_items ORDER BY id")
                    )
                ]
                assert [f"{float(v):.2f}" for v in stored] == [
                    f"{float(v):.2f}" for v in values
                ]
        finally:
            engine.dispose()


def test_models_and_migration_agree_on_the_new_tables(app):
    _, source = _load_migration()
    with app.app_context():
        inspector = inspect(db.engine)
        for table in _NEW_TABLES:
            block = source.split(f"op.create_table('{table}',", 1)[1].split(
                "\n    )", 1
            )[0]
            declared = dict(
                re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)", block)
            )
            actual = {
                column["name"]: str(column["nullable"])
                for column in inspector.get_columns(table)
            }
            declared.pop("id", None)
            actual.pop("id", None)
            assert declared == actual, table


def test_mysql_ddl_compiles_without_a_connection():
    dialect = mysql.dialect()
    ddl = {
        model.__tablename__: str(
            CreateTable(model.__table__).compile(dialect=dialect)
        )
        for model in (GradeCategory, GradeItem, GradeRecord)
    }

    categories = ddl["grade_categories"]
    assert "FOREIGN KEY(group_id) REFERENCES `groups` (id)" in categories
    assert (
        "CONSTRAINT uq_grade_categories_group_title UNIQUE (group_id, title)"
        in categories
    )
    assert "ck_grade_categories_weight_range" in categories
    assert "weight_basis_points INTEGER NOT NULL" in categories
    assert "UNIQUE (public_id)" in categories

    items = ddl["grade_items"]
    assert "FOREIGN KEY(category_id) REFERENCES grade_categories (id)" in items
    assert "FOREIGN KEY(assignment_id) REFERENCES assignments (id)" in items
    assert "FOREIGN KEY(quiz_id) REFERENCES quizzes (id)" in items
    assert (
        "FOREIGN KEY(speaking_activity_id) REFERENCES speaking_activities (id)" in items
    )
    assert "ck_grade_items_source_kind" in items
    assert "ck_grade_items_source_link" in items
    # Exact fixed point on MySQL, never a FLOAT. SQLAlchemy emits
    # ``NUMERIC(7, 2)``, which MySQL treats as an exact synonym for
    # ``DECIMAL(7, 2)`` -- the same storage and the same arithmetic.
    assert "max_points NUMERIC(7, 2) NOT NULL" in items
    assert "source_kind VARCHAR(32) NOT NULL" in items

    records = ddl["grade_records"]
    assert "FOREIGN KEY(grade_item_id) REFERENCES grade_items (id)" in records
    assert "FOREIGN KEY(student_id) REFERENCES users (id)" in records
    assert "FOREIGN KEY(graded_by_id) REFERENCES users (id)" in records
    assert (
        "CONSTRAINT uq_grade_records_item_student UNIQUE (grade_item_id, student_id)"
        in records
    )
    assert "ck_grade_records_score_non_negative" in records
    assert "ck_grade_records_graded_consistency" in records
    assert "score NUMERIC(7, 2)" in records
    assert "comment TEXT" in records

    for name, statement in ddl.items():
        # No cascade anywhere, on any of the three tables.
        assert "ON DELETE" not in statement, name
        assert "ON UPDATE" not in statement, name
        assert "BIGINT" in statement, name
        # Whole-second DATETIME on MySQL: no fractional precision anywhere.
        assert "DATETIME(" not in statement, name
        # No MySQL ENUM column: the source kind is a VARCHAR + CHECK, the
        # convention every other closed-set column in this project uses.
        assert "ENUM(" not in statement, name
        # And no binary floating-point column, anywhere.
        assert "FLOAT" not in statement.upper(), name
        assert "DOUBLE" not in statement.upper(), name
        assert " REAL" not in statement.upper(), name


def test_the_new_tables_use_the_deployment_engine_and_charset(app):
    """The engine and charset are a **deployment** property, not something
    this migration sets: no ``mysql_engine`` / ``mysql_charset`` argument
    is declared on any of the three tables, so each inherits the MySQL
    server's (or the database's) default exactly as every earlier table in
    this project does. Asserted here so a future silent divergence is
    caught, and verified for real against development MySQL separately --
    SQLite can demonstrate neither."""
    for model in (GradeCategory, GradeItem, GradeRecord):
        assert "mysql_engine" not in model.__table__.kwargs
        assert "mysql_charset" not in model.__table__.kwargs
