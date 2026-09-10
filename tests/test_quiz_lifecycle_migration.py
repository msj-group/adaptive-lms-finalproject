"""M04D migration checks for the Quiz publication lifecycle and the
attempt aggregate.

The execution probe uses an isolated temporary SQLite database seeded with
the prerequisite tables **and a representative existing Quiz row**, so the
"existing quizzes are preserved as drafts" claim is executed rather than
asserted. MySQL checks compile dialect DDL only and never connect to the
real application database.
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
from app.models import Quiz, QuizAnswer, QuizAnswerSelection, QuizAttempt

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "7a4f19c6b8de"
_DOWN_REVISION = "5d2c8a4e91f7"

_NEW_QUIZ_COLUMNS = [
    "status", "opens_at", "closes_at", "time_limit_minutes",
    "attempt_limit", "published_at",
]


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m04d_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def test_revision_identifiers_and_one_linear_chain():
    """M04D's revision is no longer the repository head -- Phase 4 / M05
    adds one after it -- so this checks its **place in the chain** rather
    than claiming it is last, exactly as M04C's own test was relaxed when
    M04D landed after it. The chain must still be linear: one root, one
    head, and no revision claimed as the parent of two others.

    The current head is asserted by the newest revision's own test
    (``tests/test_listening_migration.py``), which is the one place that
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


def test_migration_is_additive_and_touches_only_the_quiz_aggregate():
    _, source = _load_migration()
    assert re.findall(r"op\.create_table\('([^']+)'", source) == [
        "quiz_attempts", "quiz_answers", "quiz_answer_selections",
    ]
    assert set(re.findall(r"batch_alter_table\('([^']+)'", source)) == {
        "quizzes", "quiz_attempts", "quiz_answers", "quiz_answer_selections",
    }
    upgrade, downgrade = source.split("def downgrade():")
    # Additive: the upgrade adds columns and never drops one, and never
    # rewrites data.
    assert "drop_column" not in upgrade
    assert "drop_table" not in upgrade
    for forbidden in ("op.execute(", "op.bulk_insert", "ondelete", "DELETE FROM", "UPDATE "):
        assert forbidden not in source, forbidden
    # Every added column belongs to `quizzes`; no unrelated table is
    # altered at all.
    added = re.findall(r"batch_op\.add_column\(\s*\n?\s*sa\.Column\('([^']+)'", upgrade)
    assert sorted(added) == sorted(_NEW_QUIZ_COLUMNS)
    # The only foreign-key targets are the four expected ones.
    assert sorted(set(re.findall(r"\['([a-z_]+\.id)'\]", source))) == [
        "question_options.id", "quiz_answers.id", "quiz_attempts.id",
        "quiz_questions.id", "quizzes.id", "users.id",
    ]


def test_temporary_server_defaults_exist_only_for_the_not_null_columns():
    """MySQL cannot add a NOT NULL column to a populated table without a
    default, so the two NOT NULL columns get one -- and it is dropped
    immediately, because the final models declare none."""
    _, source = _load_migration()
    upgrade = source.split("def downgrade():")[0]
    assert upgrade.count("server_default='draft'") == 1
    assert upgrade.count("server_default='1'") == 1
    assert upgrade.count("server_default=None") == 2
    assert "batch_op.alter_column('status'" in upgrade
    assert "batch_op.alter_column('attempt_limit'" in upgrade
    # The models must not declare a server default, or the migration would
    # be leaving the schema and the model disagreeing.
    for column in ("status", "attempt_limit"):
        assert Quiz.__table__.c[column].server_default is None


def test_downgrade_is_reverse_dependency_order_and_symmetric():
    _, source = _load_migration()
    upgrade, downgrade = source.split("def downgrade():")
    assert re.findall(r"op\.drop_table\('([^']+)'", downgrade) == [
        "quiz_answer_selections", "quiz_answers", "quiz_attempts",
    ]
    dropped = re.findall(r"drop_column\('([^']+)'", downgrade)
    assert sorted(dropped) == sorted(_NEW_QUIZ_COLUMNS)
    created_checks = re.findall(r"create_check_constraint\(\s*\n?\s*'([^']+)'", upgrade)
    dropped_checks = re.findall(r"drop_constraint\('([^']+)'", downgrade)
    assert sorted(created_checks) == sorted(dropped_checks)


def test_migration_declares_every_named_constraint():
    _, source = _load_migration()
    for name in (
        "ck_quizzes_status_valid",
        "ck_quizzes_availability_window",
        "ck_quizzes_time_limit_range",
        "ck_quizzes_attempt_limit_range",
        "ck_quizzes_status_published_at_consistency",
        "ix_quizzes_group_status_opens_id",
        "uq_quiz_attempts_quiz_student_number",
        "ck_quiz_attempts_status_valid",
        "ck_quiz_attempts_number_positive",
        "ck_quiz_attempts_quiz_version_positive",
        "ck_quiz_attempts_status_finalization_consistency",
        "ck_quiz_attempts_counts_range",
        "ix_quiz_attempts_quiz_started_id",
        "uq_quiz_answers_attempt_question",
        "uq_quiz_answer_selections_answer_option",
    ):
        assert name in source, name


_PREREQ = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY)",
    "CREATE TABLE groups (id INTEGER PRIMARY KEY)",
    """CREATE TABLE quizzes (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        group_id INTEGER NOT NULL,
        title VARCHAR(150) NOT NULL,
        instructions TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        CONSTRAINT pk_quizzes PRIMARY KEY (id),
        CONSTRAINT ck_quizzes_version_positive CHECK (version > 0),
        CONSTRAINT uq_quizzes_group_title UNIQUE (group_id, title),
        UNIQUE (public_id),
        FOREIGN KEY(group_id) REFERENCES groups (id)
    )""",
    "CREATE INDEX ix_quizzes_group_created_id ON quizzes (group_id, created_at, id)",
    "CREATE TABLE quiz_questions (id INTEGER PRIMARY KEY, quiz_id INTEGER NOT NULL,"
    " FOREIGN KEY(quiz_id) REFERENCES quizzes (id))",
    "CREATE TABLE question_options (id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL,"
    " FOREIGN KEY(question_id) REFERENCES quiz_questions (id))",
    "INSERT INTO users (id) VALUES (11)",
    "INSERT INTO groups (id) VALUES (7)",
    "INSERT INTO quizzes (id, public_id, group_id, title, instructions, version,"
    " created_at, updated_at) VALUES (1, 'quiz-public-1', 7, 'Existing draft',"
    " 'Answer.', 3, '2026-05-01 08:00:00', '2026-05-02 09:00:00')",
    "INSERT INTO quiz_questions (id, quiz_id) VALUES (5, 1)",
    "INSERT INTO question_options (id, question_id) VALUES (9, 5)",
]


def test_migration_applies_and_reverses_on_isolated_sqlite():
    """Execute both directions against a temporary database seeded with the
    prerequisite tables and a representative existing Quiz row.

    ``PRAGMA foreign_keys=OFF`` around the migration is **SQLite's own
    documented table-rebuild procedure**, not a way to dodge a constraint
    failure: adding a CHECK constraint or dropping a column on SQLite
    requires Alembic's batch mode to recreate the table, and a recreate
    with foreign keys enforced would trip the child tables that reference
    ``quizzes``. Referential integrity is re-enabled and then **verified**
    with ``PRAGMA foreign_key_check`` after each direction, so the probe
    proves the rebuild left no dangling reference rather than merely
    hiding the check.

    MySQL -- the real target -- performs no rebuild at all: it adds and
    drops columns and constraints in place, so none of this applies there.
    """
    module, _ = _load_migration()
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + os.path.join(tmp, "probe.db").replace(os.sep, "/")
        engine = sa.create_engine(url)

        def integrity_is_intact(conn):
            conn.execute(sa.text("PRAGMA foreign_keys=ON"))
            return conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []

        try:
            with engine.connect() as conn:
                for statement in _PREREQ:
                    conn.execute(sa.text(statement))
                conn.commit()

                conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()
                conn.commit()
                assert integrity_is_intact(conn)

                schema = inspect(conn)
                assert sorted(schema.get_table_names()) == [
                    "groups", "question_options", "quiz_answer_selections",
                    "quiz_answers", "quiz_attempts", "quiz_questions",
                    "quizzes", "users",
                ]
                columns = {c["name"]: c for c in schema.get_columns("quizzes")}
                for name in _NEW_QUIZ_COLUMNS:
                    assert name in columns, name
                # The temporary defaults are gone.
                assert columns["status"]["default"] is None
                assert columns["attempt_limit"]["default"] is None

                assert {c["name"] for c in schema.get_check_constraints("quizzes")} == {
                    "ck_quizzes_version_positive",
                    "ck_quizzes_status_valid",
                    "ck_quizzes_availability_window",
                    "ck_quizzes_time_limit_range",
                    "ck_quizzes_attempt_limit_range",
                    "ck_quizzes_status_published_at_consistency",
                }
                assert {i["name"] for i in schema.get_indexes("quizzes")} == {
                    "ix_quizzes_group_created_id",
                    "ix_quizzes_group_status_opens_id",
                }
                assert {
                    c["name"] for c in schema.get_check_constraints("quiz_attempts")
                } == {
                    "ck_quiz_attempts_status_valid",
                    "ck_quiz_attempts_number_positive",
                    "ck_quiz_attempts_quiz_version_positive",
                    "ck_quiz_attempts_status_finalization_consistency",
                    "ck_quiz_attempts_counts_range",
                }

                # THE data-preservation claim, executed.
                row = conn.execute(sa.text(
                    "SELECT public_id, title, version, status, attempt_limit,"
                    " published_at, opens_at, closes_at, time_limit_minutes,"
                    " created_at FROM quizzes WHERE id = 1"
                )).one()
                assert row.public_id == "quiz-public-1"
                assert row.title == "Existing draft"
                assert row.version == 3
                assert row.status == "draft"
                assert row.attempt_limit == 1
                assert row.published_at is None
                assert row.opens_at is None and row.closes_at is None
                assert row.time_limit_minutes is None
                assert str(row.created_at).startswith("2026-05-01 08:00:00")

                conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
                with Operations.context(MigrationContext.configure(conn)):
                    module.downgrade()
                conn.commit()
                assert integrity_is_intact(conn)

                after = inspect(conn)
                assert sorted(after.get_table_names()) == [
                    "groups", "question_options", "quiz_questions", "quizzes", "users",
                ]
                assert {c["name"] for c in after.get_columns("quizzes")} == {
                    "id", "public_id", "group_id", "title", "instructions",
                    "version", "created_at", "updated_at",
                }
                assert {c["name"] for c in after.get_check_constraints("quizzes")} == {
                    "ck_quizzes_version_positive",
                }
                # Prerequisite rows survive the whole round trip untouched.
                assert conn.execute(sa.text(
                    "SELECT title, version FROM quizzes WHERE id = 1"
                )).one() == ("Existing draft", 3)
                assert conn.execute(
                    sa.text("SELECT id FROM quiz_questions")
                ).scalar_one() == 5
                assert conn.execute(
                    sa.text("SELECT id FROM question_options")
                ).scalar_one() == 9
                assert conn.execute(sa.text("SELECT id FROM users")).scalar_one() == 11
        finally:
            engine.dispose()


def test_models_and_migration_agree_on_the_new_tables(app):
    _, source = _load_migration()
    for table in (
        QuizAttempt.__table__, QuizAnswer.__table__, QuizAnswerSelection.__table__,
    ):
        block = source.split(f"op.create_table('{table.name}',", 1)[1].split("\n    )", 1)[0]
        declared = dict(re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)", block))
        with app.app_context():
            actual = {
                column["name"]: str(column["nullable"])
                for column in inspect(db.engine).get_columns(table.name)
            }
        declared.pop("id", None)
        actual.pop("id", None)
        assert declared == actual, table.name


def test_mysql_ddl_compiles_without_a_connection():
    dialect = mysql.dialect()
    ddl = {
        table.name: str(CreateTable(table).compile(dialect=dialect))
        for table in (
            Quiz.__table__, QuizAttempt.__table__, QuizAnswer.__table__,
            QuizAnswerSelection.__table__,
        )
    }
    assert "FOREIGN KEY(quiz_id) REFERENCES quizzes (id)" in ddl["quiz_attempts"]
    assert "FOREIGN KEY(student_id) REFERENCES users (id)" in ddl["quiz_attempts"]
    assert "FOREIGN KEY(attempt_id) REFERENCES quiz_attempts (id)" in ddl["quiz_answers"]
    assert (
        "FOREIGN KEY(option_id) REFERENCES question_options (id)"
        in ddl["quiz_answer_selections"]
    )
    assert "ON DELETE" not in "\n".join(ddl.values())
    assert "BIGINT" in ddl["quiz_attempts"]
    assert "VARCHAR(36)" in ddl["quiz_attempts"]
    # Whole-second DATETIME on MySQL: no fractional precision anywhere.
    assert all("DATETIME(" not in statement for statement in ddl.values())
    # The lifecycle CHECKs reach MySQL DDL too.
    assert "ck_quizzes_status_valid" in ddl["quizzes"]
    assert "ck_quiz_attempts_status_finalization_consistency" in ddl["quiz_attempts"]
