"""M06 migration checks for ``speaking_activities``,
``speaking_submissions`` and ``speaking_feedback``.

The execution probe uses an isolated temporary SQLite database seeded with
the prerequisite tables **and representative existing rows** -- an
ordinary Assignment with a text Submission and its feedback, plus a
Listening activity's Quiz, an UploadedFile, a Material and a file access
log -- so the "nothing existing is touched, and every existing Assignment
stays ordinary" claim is executed rather than asserted. MySQL checks
compile dialect DDL only and never connect to the real application
database.
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
from app.models import SpeakingActivity, SpeakingFeedback, SpeakingSubmission

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "4e2c9b7f1a83"
_DOWN_REVISION = "3f81b0c7d942"

_NEW_TABLES = ["speaking_activities", "speaking_submissions", "speaking_feedback"]

_EXPECTED_COLUMNS = {
    "speaking_activities": {
        "id", "public_id", "assignment_id", "creation_nonce", "created_at", "updated_at",
    },
    "speaking_submissions": {
        "id", "public_id", "speaking_activity_id", "student_id", "audio_file_id",
        "creation_nonce", "submitted_at",
    },
    "speaking_feedback": {
        "id", "public_id", "speaking_submission_id", "reviewer_id", "feedback_text",
        "version", "created_at", "updated_at",
    },
}


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m06_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def test_revision_identifiers_and_one_linear_chain():
    """M06's revision is no longer the repository head -- Phase 4 / M07
    adds one after it -- so this checks its **place in the chain** rather
    than claiming it is last, exactly as M04C's, M04D's and M05's own
    tests were relaxed when a later revision landed after each of them.
    The chain must still be linear: one root, one head, and no revision
    claimed as the parent of two others.

    The current head is asserted by the newest revision's own test
    (``tests/test_attendance_migration.py``), which is the one place that
    claim belongs."""
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

    claimed = [p for p in parents.values() if p is not None]
    # No revision is claimed as the parent of two others (no branch).
    assert len(claimed) == len(set(claimed)), "a revision is claimed twice (branch)"
    # Exactly one root and exactly one head.
    assert len([r for r, p in parents.items() if p is None]) == 1, "one root"
    assert len(revisions - set(claimed)) == 1, "one head"
    # This revision is in the chain and has exactly one child.
    assert _REVISION in revisions
    assert claimed.count(_REVISION) == 1


def test_migration_creates_exactly_three_tables_and_alters_nothing_else():
    _, source = _load_migration()
    assert re.findall(r"op\.create_table\('([^']+)'", source) == _NEW_TABLES
    # No pre-existing table is altered at all: there is no batch_alter_table,
    # no add_column, no drop, no data rewrite and no seeded row.
    for forbidden in (
        "batch_alter_table", "add_column", "drop_column", "alter_column",
        "op.execute(", "op.bulk_insert", "ondelete", "onupdate",
        "DELETE FROM", "UPDATE ", "INSERT INTO",
    ):
        assert forbidden not in source, forbidden
    upgrade = source.split("def downgrade():")[0]
    assert "drop_table" not in upgrade
    # The only foreign-key targets are the three existing parents plus the
    # two tables this revision itself creates.
    assert sorted(set(re.findall(r"\['([a-z_]+\.id)'\]", source))) == [
        "assignments.id",
        "speaking_activities.id",
        "speaking_submissions.id",
        "uploaded_files.id",
        "users.id",
    ]


def test_migration_declares_every_expected_column_and_constraint():
    _, source = _load_migration()
    for table, expected in _EXPECTED_COLUMNS.items():
        block = source.split(f"op.create_table('{table}',", 1)[1].split("\n    )", 1)[0]
        assert set(re.findall(r"sa\.Column\('([^']+)'", block)) == expected, table
    for fragment in (
        "sa.UniqueConstraint('assignment_id')",
        "sa.UniqueConstraint('creation_nonce')",
        "sa.UniqueConstraint('public_id')",
        "sa.UniqueConstraint('audio_file_id')",
        "name='uq_speaking_submissions_activity_student'",
        "name='uq_speaking_feedback_submission'",
        "name='ck_speaking_feedback_version_positive'",
        "'ix_speaking_submissions_activity_submitted_id'",
        "op.f('ix_speaking_submissions_student_id')",
        "op.f('ix_speaking_feedback_reviewer_id')",
    ):
        assert fragment in source, fragment


def test_downgrade_drops_the_three_tables_it_created_and_nothing_else():
    _, source = _load_migration()
    downgrade = source.split("def downgrade():")[1]
    assert re.findall(r"op\.drop_table\('([^']+)'", downgrade) == list(
        reversed(_NEW_TABLES)
    )
    assert "drop_column" not in downgrade
    assert "drop_constraint" not in downgrade


_PREREQ = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, role VARCHAR(32))",
    "CREATE TABLE groups (id INTEGER PRIMARY KEY)",
    "CREATE TABLE lessons (id INTEGER PRIMARY KEY)",
    """CREATE TABLE uploaded_files (
        id INTEGER PRIMARY KEY,
        public_id VARCHAR(36) NOT NULL UNIQUE,
        storage_key VARCHAR(120) NOT NULL UNIQUE,
        category VARCHAR(32) NOT NULL,
        sha256 VARCHAR(64) NOT NULL,
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
    """CREATE TABLE file_access_logs (
        id INTEGER PRIMARY KEY,
        uploaded_file_id INTEGER NOT NULL,
        actor_id INTEGER NOT NULL,
        action VARCHAR(16) NOT NULL,
        FOREIGN KEY(uploaded_file_id) REFERENCES uploaded_files (id),
        FOREIGN KEY(actor_id) REFERENCES users (id)
    )""",
    """CREATE TABLE assignments (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        group_id INTEGER NOT NULL,
        title VARCHAR(150) NOT NULL,
        instructions TEXT NOT NULL,
        opens_at DATETIME NOT NULL,
        due_at DATETIME NOT NULL,
        status VARCHAR(32) NOT NULL,
        published_at DATETIME,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        CONSTRAINT pk_assignments PRIMARY KEY (id),
        CONSTRAINT uq_assignments_group_title UNIQUE (group_id, title),
        UNIQUE (public_id),
        FOREIGN KEY(group_id) REFERENCES groups (id)
    )""",
    """CREATE TABLE submissions (
        id INTEGER PRIMARY KEY,
        public_id VARCHAR(36) NOT NULL UNIQUE,
        assignment_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        answer_text TEXT NOT NULL,
        submitted_at DATETIME NOT NULL,
        FOREIGN KEY(assignment_id) REFERENCES assignments (id),
        FOREIGN KEY(student_id) REFERENCES users (id)
    )""",
    """CREATE TABLE submission_feedback (
        id INTEGER PRIMARY KEY,
        public_id VARCHAR(36) NOT NULL UNIQUE,
        submission_id INTEGER NOT NULL UNIQUE,
        reviewer_id INTEGER NOT NULL,
        feedback_text TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(submission_id) REFERENCES submissions (id),
        FOREIGN KEY(reviewer_id) REFERENCES users (id)
    )""",
    "CREATE TABLE quizzes (id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL,"
    " FOREIGN KEY(group_id) REFERENCES groups (id))",
    "CREATE TABLE listening_activities (id INTEGER PRIMARY KEY, quiz_id INTEGER NOT NULL"
    " UNIQUE, audio_file_id INTEGER NOT NULL UNIQUE,"
    " FOREIGN KEY(quiz_id) REFERENCES quizzes (id),"
    " FOREIGN KEY(audio_file_id) REFERENCES uploaded_files (id))",
    "INSERT INTO users (id, role) VALUES (11, 'student')",
    "INSERT INTO users (id, role) VALUES (12, 'teacher')",
    "INSERT INTO groups (id) VALUES (7)",
    "INSERT INTO lessons (id) VALUES (3)",
    "INSERT INTO uploaded_files (id, public_id, storage_key, category, sha256,"
    " uploaded_by_id) VALUES (21, 'file-public-1', 'abc123', 'audio', 'f'||'0'*63, 12)",
    "INSERT INTO materials (id, lesson_id, uploaded_file_id) VALUES (31, 3, NULL)",
    "INSERT INTO file_access_logs (id, uploaded_file_id, actor_id, action)"
    " VALUES (41, 21, 12, 'upload')",
    "INSERT INTO assignments (id, public_id, group_id, title, instructions, opens_at,"
    " due_at, status, published_at, created_at, updated_at) VALUES (1,"
    " 'assignment-public-1', 7, 'Existing assignment', 'Write.',"
    " '2026-05-01 08:00:00', '2026-06-01 08:00:00', 'published',"
    " '2026-04-30 08:00:00', '2026-04-29 08:00:00', '2026-04-30 08:00:00')",
    "INSERT INTO submissions (id, public_id, assignment_id, student_id, answer_text,"
    " submitted_at) VALUES (5, 'submission-public-1', 1, 11, 'My answer.',"
    " '2026-05-05 10:00:00')",
    "INSERT INTO submission_feedback (id, public_id, submission_id, reviewer_id,"
    " feedback_text, version, created_at, updated_at) VALUES (9, 'feedback-public-1',"
    " 5, 12, 'Well written.', 3, '2026-05-06 10:00:00', '2026-05-07 10:00:00')",
    "INSERT INTO quizzes (id, group_id) VALUES (2, 7)",
    "INSERT INTO listening_activities (id, quiz_id, audio_file_id) VALUES (13, 2, 21)",
]

_PRESERVED = (
    ("assignments", 1),
    ("submissions", 1),
    ("submission_feedback", 1),
    ("quizzes", 1),
    ("listening_activities", 1),
    ("uploaded_files", 1),
    ("materials", 1),
    ("file_access_logs", 1),
    ("users", 2),
)


def test_migration_applies_and_reverses_on_isolated_sqlite():
    """Execute both directions against a temporary database seeded with the
    prerequisite tables and representative existing rows.

    Foreign keys stay **enforced** throughout: this revision only creates
    tables, so no Alembic batch rebuild of an existing table applies here
    at all. ``PRAGMA foreign_key_check`` is asserted after each direction
    anyway.
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
                names = set(schema.get_table_names())
                assert set(_NEW_TABLES).issubset(names)
                for table, expected in _EXPECTED_COLUMNS.items():
                    assert {c["name"] for c in schema.get_columns(table)} == expected
                assert {
                    c["name"] for c in schema.get_check_constraints("speaking_feedback")
                } == {"ck_speaking_feedback_version_positive"}
                assert {
                    fk["constrained_columns"][0]: fk["referred_table"]
                    for fk in schema.get_foreign_keys("speaking_activities")
                } == {"assignment_id": "assignments"}
                assert {
                    fk["constrained_columns"][0]: fk["referred_table"]
                    for fk in schema.get_foreign_keys("speaking_submissions")
                } == {
                    "speaking_activity_id": "speaking_activities",
                    "student_id": "users",
                    "audio_file_id": "uploaded_files",
                }
                assert {
                    fk["constrained_columns"][0]: fk["referred_table"]
                    for fk in schema.get_foreign_keys("speaking_feedback")
                } == {
                    "speaking_submission_id": "speaking_submissions",
                    "reviewer_id": "users",
                }
                assert {
                    index["name"] for index in schema.get_indexes("speaking_submissions")
                } >= {
                    "ix_speaking_submissions_activity_submitted_id",
                    "ix_speaking_submissions_student_id",
                }
                assert {
                    index["name"] for index in schema.get_indexes("speaking_feedback")
                } >= {"ix_speaking_feedback_reviewer_id"}

                # THE data-preservation claim, executed: every existing row of
                # every neighbouring table survives untouched...
                after = {
                    table: conn.execute(
                        sa.text(f"SELECT count(*) FROM {table}")
                    ).scalar_one()
                    for table, _ in _PRESERVED
                }
                assert after == before == dict(_PRESERVED)
                row = conn.execute(sa.text(
                    "SELECT public_id, title, status, published_at FROM assignments"
                    " WHERE id = 1"
                )).one()
                assert row.public_id == "assignment-public-1"
                assert row.title == "Existing assignment"
                assert row.status == "published"
                assert str(row.published_at).startswith("2026-04-30 08:00:00")
                feedback = conn.execute(sa.text(
                    "SELECT feedback_text, version FROM submission_feedback WHERE id = 9"
                )).one()
                assert feedback == ("Well written.", 3)

                # ...and the existing Assignment is still an ORDINARY one,
                # because no extension row was seeded for it.
                assert conn.execute(
                    sa.text("SELECT count(*) FROM speaking_activities")
                ).scalar_one() == 0
                assert conn.execute(sa.text(
                    "SELECT count(*) FROM assignments a WHERE EXISTS ("
                    "SELECT 1 FROM speaking_activities sa WHERE sa.assignment_id = a.id)"
                )).scalar_one() == 0

                # The unique rules really are enforced by the new tables.
                conn.execute(sa.text(
                    "INSERT INTO speaking_activities (id, public_id, assignment_id,"
                    " creation_nonce, created_at, updated_at) VALUES (1, 'sa-1', 1,"
                    " 'nonce-1', '2026-05-03 10:00:00', '2026-05-03 10:00:00')"
                ))
                conn.commit()
                for values, column in (
                    ("(2, 'sa-2', 1, 'nonce-2',", "assignment_id"),
                    ("(3, 'sa-1', 1, 'nonce-3',", "public_id"),
                    ("(4, 'sa-4', 1, 'nonce-1',", "creation_nonce"),
                ):
                    try:
                        conn.execute(sa.text(
                            "INSERT INTO speaking_activities (id, public_id,"
                            " assignment_id, creation_nonce, created_at, updated_at)"
                            f" VALUES {values} '2026-05-03 10:00:00',"
                            " '2026-05-03 10:00:00')"
                        ))
                        raise AssertionError(f"{column} uniqueness was not enforced")
                    except sa.exc.IntegrityError:
                        conn.rollback()

                conn.execute(sa.text(
                    "INSERT INTO speaking_submissions (id, public_id,"
                    " speaking_activity_id, student_id, audio_file_id, creation_nonce,"
                    " submitted_at) VALUES (1, 'ss-1', 1, 11, 21, 'sub-nonce-1',"
                    " '2026-05-04 10:00:00')"
                ))
                conn.commit()
                for values, column in (
                    ("(2, 'ss-2', 1, 11, NULL, 'sub-nonce-2',", "activity+student"),
                    ("(3, 'ss-1', 1, 12, NULL, 'sub-nonce-3',", "public_id"),
                    ("(4, 'ss-4', 1, 12, 21, 'sub-nonce-4',", "audio_file_id"),
                    ("(5, 'ss-5', 1, 12, NULL, 'sub-nonce-1',", "creation_nonce"),
                ):
                    try:
                        conn.execute(sa.text(
                            "INSERT INTO speaking_submissions (id, public_id,"
                            " speaking_activity_id, student_id, audio_file_id,"
                            " creation_nonce, submitted_at)"
                            f" VALUES {values} '2026-05-04 10:00:00')"
                        ))
                        raise AssertionError(f"{column} uniqueness was not enforced")
                    except sa.exc.IntegrityError:
                        conn.rollback()

                conn.execute(sa.text(
                    "INSERT INTO speaking_feedback (id, public_id,"
                    " speaking_submission_id, reviewer_id, feedback_text, version,"
                    " created_at, updated_at) VALUES (1, 'sf-1', 1, 12, 'Clear.', 1,"
                    " '2026-05-05 10:00:00', '2026-05-05 10:00:00')"
                ))
                conn.commit()
                for values, column in (
                    ("(2, 'sf-2', 1, 12, 'Second.', 1,", "speaking_submission_id"),
                    ("(3, 'sf-1', 1, 12, 'Third.', 1,", "public_id"),
                ):
                    try:
                        conn.execute(sa.text(
                            "INSERT INTO speaking_feedback (id, public_id,"
                            " speaking_submission_id, reviewer_id, feedback_text,"
                            " version, created_at, updated_at)"
                            f" VALUES {values} '2026-05-05 10:00:00',"
                            " '2026-05-05 10:00:00')"
                        ))
                        raise AssertionError(f"{column} uniqueness was not enforced")
                    except sa.exc.IntegrityError:
                        conn.rollback()
                # The CHECK refuses a non-positive version.
                try:
                    conn.execute(sa.text(
                        "INSERT INTO speaking_feedback (id, public_id,"
                        " speaking_submission_id, reviewer_id, feedback_text, version,"
                        " created_at, updated_at) VALUES (4, 'sf-4', 1, 12, 'Bad.', 0,"
                        " '2026-05-05 10:00:00', '2026-05-05 10:00:00')"
                    ))
                    raise AssertionError("the version CHECK was not enforced")
                except sa.exc.IntegrityError:
                    conn.rollback()

                conn.execute(sa.text("DELETE FROM speaking_feedback"))
                conn.execute(sa.text("DELETE FROM speaking_submissions"))
                conn.execute(sa.text("DELETE FROM speaking_activities"))
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
                    "SELECT title, status FROM assignments WHERE id = 1"
                )).one() == ("Existing assignment", "published")
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
        for model in (SpeakingActivity, SpeakingSubmission, SpeakingFeedback)
    }

    activities = ddl["speaking_activities"]
    assert "FOREIGN KEY(assignment_id) REFERENCES assignments (id)" in activities
    assert "UNIQUE (assignment_id)" in activities
    assert "UNIQUE (creation_nonce)" in activities
    assert "UNIQUE (public_id)" in activities

    submissions = ddl["speaking_submissions"]
    assert (
        "FOREIGN KEY(speaking_activity_id) REFERENCES speaking_activities (id)"
        in submissions
    )
    assert "FOREIGN KEY(student_id) REFERENCES users (id)" in submissions
    assert "FOREIGN KEY(audio_file_id) REFERENCES uploaded_files (id)" in submissions
    assert (
        "CONSTRAINT uq_speaking_submissions_activity_student UNIQUE"
        " (speaking_activity_id, student_id)" in submissions
    )
    assert "UNIQUE (audio_file_id)" in submissions
    assert "UNIQUE (creation_nonce)" in submissions

    feedback = ddl["speaking_feedback"]
    assert (
        "FOREIGN KEY(speaking_submission_id) REFERENCES speaking_submissions (id)"
        in feedback
    )
    assert "FOREIGN KEY(reviewer_id) REFERENCES users (id)" in feedback
    assert "ck_speaking_feedback_version_positive" in feedback
    assert "uq_speaking_feedback_submission" in feedback
    assert "TEXT" in feedback

    for name, statement in ddl.items():
        # No cascade anywhere, on any of the three.
        assert "ON DELETE" not in statement, name
        assert "ON UPDATE" not in statement, name
        assert "BIGINT" in statement, name
        # Whole-second DATETIME on MySQL: no fractional precision anywhere.
        assert "DATETIME(" not in statement, name


def test_the_new_tables_use_innodb_and_utf8mb4_when_those_are_configured(app):
    """The engine and charset are a **deployment** property, not something
    this migration sets: no ``mysql_engine`` / ``mysql_charset`` argument
    is declared on any Speaking table, so each inherits the MySQL server's
    (or the database's) default exactly as every earlier table in this
    project does. Asserted here so a future silent divergence is caught,
    and verified for real against development MySQL separately -- SQLite
    can demonstrate neither."""
    for model in (SpeakingActivity, SpeakingSubmission, SpeakingFeedback):
        assert "mysql_engine" not in model.__table__.kwargs
        assert "mysql_charset" not in model.__table__.kwargs
