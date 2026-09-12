"""M07 migration checks for ``attendance_sessions`` and
``attendance_records``.

The execution probe uses an isolated temporary SQLite database seeded with
the prerequisite tables **and representative existing rows** -- a Group
with a Schedule and an Enrollment, a teacher assignment, an ordinary
Assignment with a text Submission and its feedback, a Quiz, a Speaking
activity with a recording, an UploadedFile and a Material -- so the
"nothing existing is touched" claim is executed rather than asserted.
MySQL checks compile dialect DDL only and never connect to the real
application database.
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
from app.models import AttendanceRecord, AttendanceSession

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "9c4d7e2b6f15"
_DOWN_REVISION = "4e2c9b7f1a83"

_NEW_TABLES = ["attendance_sessions", "attendance_records"]

_EXPECTED_COLUMNS = {
    "attendance_sessions": {
        "id", "public_id", "group_id", "schedule_id", "session_date", "start_time",
        "end_time", "location", "version", "finalized_at", "created_at", "updated_at",
    },
    "attendance_records": {
        "id", "public_id", "attendance_session_id", "student_id", "status", "note",
        "version", "created_at", "updated_at",
    },
}


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m07_{_REVISION}", path)
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
    # Exactly one head.  M08 follows this historical M07 revision.
    assert revisions - {p for p in parents.values() if p is not None} == {
        "2f6d1c83ab47"
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


def test_migration_creates_exactly_two_tables_and_alters_nothing_else():
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
    # The only foreign-key targets are the three existing parents plus the
    # table this revision itself creates first.
    assert sorted(set(re.findall(r"\['([a-z_]+\.id)'\]", code))) == [
        "attendance_sessions.id",
        "groups.id",
        "schedules.id",
        "users.id",
    ]


def test_migration_declares_every_expected_column_and_constraint():
    _, source = _load_migration()
    for table, expected in _EXPECTED_COLUMNS.items():
        block = source.split(f"op.create_table('{table}',", 1)[1].split("\n    )", 1)[0]
        assert set(re.findall(r"sa\.Column\('([^']+)'", block)) == expected, table
    for fragment in (
        "name='uq_attendance_sessions_schedule_date'",
        "name='ck_attendance_sessions_version_positive'",
        "name='ck_attendance_sessions_time_order'",
        "name='uq_attendance_records_session_student'",
        "name='ck_attendance_records_status'",
        "name='ck_attendance_records_version_positive'",
        "sa.UniqueConstraint('public_id')",
        "'ix_attendance_sessions_group_date_id'",
        "'ix_attendance_sessions_date_id'",
        "op.f('ix_attendance_records_student_id')",
        "status IN ('present', 'absent', 'late', 'excused')",
    ):
        assert fragment in source, fragment


def test_downgrade_drops_the_two_tables_it_created_and_nothing_else():
    _, source = _load_migration()
    downgrade = source.split("def downgrade():")[1]
    assert re.findall(r"op\.drop_table\('([^']+)'", downgrade) == list(
        reversed(_NEW_TABLES)
    )
    assert "drop_column" not in downgrade
    assert "drop_constraint" not in downgrade


_PREREQ = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, role VARCHAR(32), status VARCHAR(32))",
    "CREATE TABLE groups (id INTEGER PRIMARY KEY, name VARCHAR(100))",
    """CREATE TABLE schedules (
        id INTEGER PRIMARY KEY,
        public_id VARCHAR(36) NOT NULL UNIQUE,
        group_id INTEGER NOT NULL,
        day_of_week INTEGER NOT NULL,
        start_time TIME NOT NULL,
        end_time TIME NOT NULL,
        effective_start_date DATE NOT NULL,
        effective_end_date DATE NOT NULL,
        location VARCHAR(255),
        status VARCHAR(32) NOT NULL,
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
    "CREATE TABLE quizzes (id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL,"
    " FOREIGN KEY(group_id) REFERENCES groups (id))",
    "CREATE TABLE speaking_activities (id INTEGER PRIMARY KEY, assignment_id INTEGER"
    " NOT NULL UNIQUE, FOREIGN KEY(assignment_id) REFERENCES assignments (id))",
    "CREATE TABLE speaking_submissions (id INTEGER PRIMARY KEY, speaking_activity_id"
    " INTEGER NOT NULL, student_id INTEGER NOT NULL, audio_file_id INTEGER NOT NULL"
    " UNIQUE, FOREIGN KEY(speaking_activity_id) REFERENCES speaking_activities (id),"
    " FOREIGN KEY(student_id) REFERENCES users (id),"
    " FOREIGN KEY(audio_file_id) REFERENCES uploaded_files (id))",
    "INSERT INTO users (id, role, status) VALUES (11, 'student', 'active')",
    "INSERT INTO users (id, role, status) VALUES (12, 'teacher', 'active')",
    "INSERT INTO users (id, role, status) VALUES (13, 'student', 'active')",
    "INSERT INTO users (id, role, status) VALUES (14, 'student', 'suspended')",
    "INSERT INTO groups (id, name) VALUES (7, 'Group A')",
    "INSERT INTO schedules (id, public_id, group_id, day_of_week, start_time, end_time,"
    " effective_start_date, effective_end_date, location, status) VALUES (3,"
    " 'schedule-public-1', 7, 1, '18:00:00', '20:00:00', '2026-02-01', '2026-11-30',"
    " 'Room 3', 'active')",
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
    "INSERT INTO quizzes (id, group_id) VALUES (2, 7)",
    "INSERT INTO speaking_activities (id, assignment_id) VALUES (13, 1)",
    "INSERT INTO speaking_submissions (id, speaking_activity_id, student_id,"
    " audio_file_id) VALUES (17, 13, 11, 21)",
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
    ("speaking_activities", 1),
    ("speaking_submissions", 1),
    ("uploaded_files", 1),
    ("materials", 1),
)

_SESSION_COLUMNS = (
    "id, public_id, group_id, schedule_id, session_date, start_time, end_time,"
    " location, version, finalized_at, created_at, updated_at"
)
_RECORD_COLUMNS = (
    "id, public_id, attendance_session_id, student_id, status, note, version,"
    " created_at, updated_at"
)


def test_migration_applies_and_reverses_on_isolated_sqlite():
    """Execute both directions against a temporary database seeded with the
    prerequisite tables and representative existing rows.

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
                    c["name"] for c in schema.get_check_constraints("attendance_sessions")
                } == {
                    "ck_attendance_sessions_time_order",
                    "ck_attendance_sessions_version_positive",
                }
                assert {
                    c["name"] for c in schema.get_check_constraints("attendance_records")
                } == {
                    "ck_attendance_records_status",
                    "ck_attendance_records_version_positive",
                }
                assert {
                    fk["constrained_columns"][0]: fk["referred_table"]
                    for fk in schema.get_foreign_keys("attendance_sessions")
                } == {"group_id": "groups", "schedule_id": "schedules"}
                assert {
                    fk["constrained_columns"][0]: fk["referred_table"]
                    for fk in schema.get_foreign_keys("attendance_records")
                } == {
                    "attendance_session_id": "attendance_sessions",
                    "student_id": "users",
                }
                assert {
                    index["name"] for index in schema.get_indexes("attendance_sessions")
                } >= {
                    "ix_attendance_sessions_group_date_id",
                    "ix_attendance_sessions_date_id",
                }
                assert {
                    index["name"] for index in schema.get_indexes("attendance_records")
                } >= {"ix_attendance_records_student_id"}

                # THE data-preservation claim, executed: every existing row of
                # every neighbouring table survives untouched...
                after = {
                    table: conn.execute(
                        sa.text(f"SELECT count(*) FROM {table}")
                    ).scalar_one()
                    for table, _ in _PRESERVED
                }
                assert after == before == dict(_PRESERVED)
                schedule = conn.execute(sa.text(
                    "SELECT public_id, start_time, end_time, location, status"
                    " FROM schedules WHERE id = 3"
                )).one()
                assert schedule.public_id == "schedule-public-1"
                assert schedule.location == "Room 3"
                assert schedule.status == "active"
                feedback = conn.execute(sa.text(
                    "SELECT feedback_text, version FROM submission_feedback WHERE id = 9"
                )).one()
                assert feedback == ("Well written.", 3)

                # ...and the new tables start EMPTY: no attendance is seeded
                # for any existing schedule, enrollment or past class.
                for table in _NEW_TABLES:
                    assert conn.execute(
                        sa.text(f"SELECT count(*) FROM {table}")
                    ).scalar_one() == 0

                # The uniqueness rules and CHECKs really are enforced.
                conn.execute(sa.text(
                    f"INSERT INTO attendance_sessions ({_SESSION_COLUMNS}) VALUES"
                    " (1, 'as-1', 7, 3, '2026-05-12', '18:00:00', '20:00:00', 'Room 3',"
                    " 1, NULL, '2026-05-13 09:00:00', '2026-05-13 09:00:00')"
                ))
                conn.commit()
                for values, rule in (
                    ("(2, 'as-2', 7, 3, '2026-05-12', '18:00:00', '20:00:00', 'Room 3',"
                     " 1, NULL,", "schedule+date"),
                    ("(3, 'as-1', 7, 3, '2026-05-19', '18:00:00', '20:00:00', 'Room 3',"
                     " 1, NULL,", "public_id"),
                    ("(4, 'as-4', 7, 3, '2026-05-19', '18:00:00', '20:00:00', 'Room 3',"
                     " 0, NULL,", "version > 0"),
                    ("(5, 'as-5', 7, 3, '2026-05-19', '20:00:00', '18:00:00', 'Room 3',"
                     " 1, NULL,", "start < end"),
                ):
                    try:
                        conn.execute(sa.text(
                            f"INSERT INTO attendance_sessions ({_SESSION_COLUMNS})"
                            f" VALUES {values} '2026-05-13 09:00:00',"
                            " '2026-05-13 09:00:00')"
                        ))
                        raise AssertionError(f"{rule} was not enforced")
                    except sa.exc.IntegrityError:
                        conn.rollback()

                conn.execute(sa.text(
                    f"INSERT INTO attendance_records ({_RECORD_COLUMNS}) VALUES"
                    " (1, 'ar-1', 1, 11, 'absent', NULL, 1, '2026-05-13 09:00:00',"
                    " '2026-05-13 09:00:00')"
                ))
                conn.commit()
                for values, rule in (
                    ("(2, 'ar-2', 1, 11, 'present', NULL, 1,", "session+student"),
                    ("(3, 'ar-1', 1, 12, 'present', NULL, 1,", "public_id"),
                    ("(4, 'ar-4', 1, 12, 'sick', NULL, 1,", "status CHECK"),
                    ("(5, 'ar-5', 1, 12, 'present', NULL, 0,", "version > 0"),
                ):
                    try:
                        conn.execute(sa.text(
                            f"INSERT INTO attendance_records ({_RECORD_COLUMNS})"
                            f" VALUES {values} '2026-05-13 09:00:00',"
                            " '2026-05-13 09:00:00')"
                        ))
                        raise AssertionError(f"{rule} was not enforced")
                    except sa.exc.IntegrityError:
                        conn.rollback()

                # Leave no probe row behind.
                conn.execute(sa.text("DELETE FROM attendance_records"))
                conn.execute(sa.text("DELETE FROM attendance_sessions"))
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
                    "SELECT location, status FROM schedules WHERE id = 3"
                )).one() == ("Room 3", "active")
        finally:
            engine.dispose()


def test_every_approved_status_is_accepted_by_the_check_constraint():
    """The CHECK refuses what is not a member -- and accepts all four that
    are. Run on its own isolated database so nothing is left behind."""
    module, _ = _load_migration()
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + os.path.join(tmp, "status.db").replace(os.sep, "/")
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
                    f"INSERT INTO attendance_sessions ({_SESSION_COLUMNS}) VALUES"
                    " (1, 'as-1', 7, 3, '2026-05-12', '18:00:00', '20:00:00', 'Room 3',"
                    " 1, NULL, '2026-05-13 09:00:00', '2026-05-13 09:00:00')"
                ))
                for index, status in enumerate(
                    ("present", "absent", "late", "excused"), start=1
                ):
                    conn.execute(sa.text(
                        f"INSERT INTO attendance_records ({_RECORD_COLUMNS}) VALUES"
                        f" ({index}, 'ar-{index}', 1, {10 + index}, '{status}', NULL, 1,"
                        " '2026-05-13 09:00:00', '2026-05-13 09:00:00')"
                    ))
                conn.commit()
                assert conn.execute(
                    sa.text("SELECT count(*) FROM attendance_records")
                ).scalar_one() == 4
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
        for model in (AttendanceSession, AttendanceRecord)
    }

    sessions = ddl["attendance_sessions"]
    assert "FOREIGN KEY(group_id) REFERENCES `groups` (id)" in sessions
    assert "FOREIGN KEY(schedule_id) REFERENCES schedules (id)" in sessions
    assert (
        "CONSTRAINT uq_attendance_sessions_schedule_date UNIQUE"
        " (schedule_id, session_date)" in sessions
    )
    assert "ck_attendance_sessions_version_positive" in sessions
    assert "ck_attendance_sessions_time_order" in sessions
    assert "UNIQUE (public_id)" in sessions
    assert "session_date DATE NOT NULL" in sessions
    assert "start_time TIME NOT NULL" in sessions
    assert "finalized_at DATETIME" in sessions

    records = ddl["attendance_records"]
    assert (
        "FOREIGN KEY(attendance_session_id) REFERENCES attendance_sessions (id)"
        in records
    )
    assert "FOREIGN KEY(student_id) REFERENCES users (id)" in records
    assert (
        "CONSTRAINT uq_attendance_records_session_student UNIQUE"
        " (attendance_session_id, student_id)" in records
    )
    assert "ck_attendance_records_status" in records
    assert "status IN ('present', 'absent', 'late', 'excused')" in records
    assert "ck_attendance_records_version_positive" in records
    assert "note TEXT" in records
    assert "status VARCHAR(32) NOT NULL" in records

    for name, statement in ddl.items():
        # No cascade anywhere, on either table.
        assert "ON DELETE" not in statement, name
        assert "ON UPDATE" not in statement, name
        assert "BIGINT" in statement, name
        # Whole-second DATETIME on MySQL: no fractional precision anywhere.
        assert "DATETIME(" not in statement, name
        # No MySQL ENUM column: the status is a VARCHAR + CHECK, the same
        # convention every other status column in this project uses.
        assert "ENUM(" not in statement, name


def test_the_new_tables_use_the_deployment_engine_and_charset(app):
    """The engine and charset are a **deployment** property, not something
    this migration sets: no ``mysql_engine`` / ``mysql_charset`` argument
    is declared on either table, so each inherits the MySQL server's (or
    the database's) default exactly as every earlier table in this project
    does. Asserted here so a future silent divergence is caught, and
    verified for real against development MySQL separately -- SQLite can
    demonstrate neither."""
    for model in (AttendanceSession, AttendanceRecord):
        assert "mysql_engine" not in model.__table__.kwargs
        assert "mysql_charset" not in model.__table__.kwargs
