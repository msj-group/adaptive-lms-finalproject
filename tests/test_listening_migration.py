"""M05 migration checks for ``listening_activities``.

The execution probe uses an isolated temporary SQLite database seeded with
the prerequisite tables **and representative existing rows** -- an
ordinary Quiz with a question, an option, an attempt, an answer and a
selection, plus an UploadedFile, a Material and a file access log -- so
the "nothing existing is touched, and every existing Quiz stays ordinary"
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
from app.models import ListeningActivity

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "3f81b0c7d942"
_DOWN_REVISION = "7a4f19c6b8de"

_EXPECTED_COLUMNS = {
    "id", "public_id", "quiz_id", "audio_file_id", "transcript",
    "transcript_visibility", "vocabulary_notes", "creation_nonce",
    "created_at", "updated_at",
}


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m05_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def test_revision_identifiers_and_one_linear_chain():
    """M05's revision is no longer the repository head -- Phase 4 / M06
    adds one after it -- so this checks its **place in the chain** rather
    than claiming it is last, exactly as M04C's and M04D's own tests were
    relaxed when a later revision landed after each of them. The chain
    must still be linear: one root, one head, and no revision claimed as
    the parent of two others.

    The current head is asserted by the newest revision's own test
    (``tests/test_speaking_migration.py``), which is the one place that
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


def test_migration_creates_exactly_one_table_and_alters_nothing_else():
    _, source = _load_migration()
    assert re.findall(r"op\.create_table\('([^']+)'", source) == ["listening_activities"]
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
    # The only foreign-key targets are the two existing parents.
    assert sorted(set(re.findall(r"\['([a-z_]+\.id)'\]", source))) == [
        "quizzes.id", "uploaded_files.id",
    ]


def test_migration_declares_every_expected_column_and_constraint():
    _, source = _load_migration()
    declared = set(re.findall(r"sa\.Column\('([^']+)'", source))
    assert declared == _EXPECTED_COLUMNS
    for name in (
        "ck_listening_activities_transcript_visibility_valid",
        "sa.UniqueConstraint('quiz_id')",
        "sa.UniqueConstraint('audio_file_id')",
        "sa.UniqueConstraint('public_id')",
        "sa.UniqueConstraint('creation_nonce')",
    ):
        assert name in source, name
    # No speculative index: both foreign keys already have a usable unique
    # index of their own, and M05 declares no other read shape.
    assert "create_index" not in source


def test_downgrade_drops_the_one_table_it_created_and_nothing_else():
    _, source = _load_migration()
    downgrade = source.split("def downgrade():")[1]
    assert re.findall(r"op\.drop_table\('([^']+)'", downgrade) == [
        "listening_activities"
    ]
    assert "drop_column" not in downgrade
    assert "drop_constraint" not in downgrade


_PREREQ = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY)",
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
    """CREATE TABLE quizzes (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        group_id INTEGER NOT NULL,
        title VARCHAR(150) NOT NULL,
        instructions TEXT NOT NULL,
        status VARCHAR(32) NOT NULL,
        version INTEGER NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        CONSTRAINT pk_quizzes PRIMARY KEY (id),
        CONSTRAINT uq_quizzes_group_title UNIQUE (group_id, title),
        UNIQUE (public_id),
        FOREIGN KEY(group_id) REFERENCES groups (id)
    )""",
    "CREATE TABLE quiz_questions (id INTEGER PRIMARY KEY, quiz_id INTEGER NOT NULL,"
    " FOREIGN KEY(quiz_id) REFERENCES quizzes (id))",
    "CREATE TABLE question_options (id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL,"
    " FOREIGN KEY(question_id) REFERENCES quiz_questions (id))",
    "CREATE TABLE quiz_attempts (id INTEGER PRIMARY KEY, quiz_id INTEGER NOT NULL,"
    " student_id INTEGER NOT NULL, correct_count INTEGER,"
    " FOREIGN KEY(quiz_id) REFERENCES quizzes (id),"
    " FOREIGN KEY(student_id) REFERENCES users (id))",
    "CREATE TABLE quiz_answers (id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL,"
    " question_id INTEGER NOT NULL, FOREIGN KEY(attempt_id) REFERENCES quiz_attempts (id),"
    " FOREIGN KEY(question_id) REFERENCES quiz_questions (id))",
    "CREATE TABLE quiz_answer_selections (id INTEGER PRIMARY KEY, answer_id INTEGER NOT NULL,"
    " option_id INTEGER NOT NULL, FOREIGN KEY(answer_id) REFERENCES quiz_answers (id),"
    " FOREIGN KEY(option_id) REFERENCES question_options (id))",
    "INSERT INTO users (id) VALUES (11)",
    "INSERT INTO groups (id) VALUES (7)",
    "INSERT INTO lessons (id) VALUES (3)",
    "INSERT INTO uploaded_files (id, public_id, storage_key, category, sha256,"
    " uploaded_by_id) VALUES (21, 'file-public-1', 'abc123', 'audio', 'f'||'0'*63, 11)",
    "INSERT INTO materials (id, lesson_id, uploaded_file_id) VALUES (31, 3, NULL)",
    "INSERT INTO file_access_logs (id, uploaded_file_id, actor_id, action)"
    " VALUES (41, 21, 11, 'upload')",
    "INSERT INTO quizzes (id, public_id, group_id, title, instructions, status, version,"
    " created_at, updated_at) VALUES (1, 'quiz-public-1', 7, 'Existing quiz',"
    " 'Answer.', 'published', 4, '2026-05-01 08:00:00', '2026-05-02 09:00:00')",
    "INSERT INTO quiz_questions (id, quiz_id) VALUES (5, 1)",
    "INSERT INTO question_options (id, question_id) VALUES (9, 5)",
    "INSERT INTO quiz_attempts (id, quiz_id, student_id, correct_count) VALUES (13, 1, 11, 1)",
    "INSERT INTO quiz_answers (id, attempt_id, question_id) VALUES (17, 13, 5)",
    "INSERT INTO quiz_answer_selections (id, answer_id, option_id) VALUES (19, 17, 9)",
]

_PRESERVED = (
    ("quizzes", 1),
    ("quiz_questions", 1),
    ("question_options", 1),
    ("quiz_attempts", 1),
    ("quiz_answers", 1),
    ("quiz_answer_selections", 1),
    ("uploaded_files", 1),
    ("materials", 1),
    ("file_access_logs", 1),
)


def test_migration_applies_and_reverses_on_isolated_sqlite():
    """Execute both directions against a temporary database seeded with the
    prerequisite tables and representative existing rows.

    Foreign keys stay **enforced** throughout, because this revision only
    creates one table: unlike M04D it needs no Alembic batch rebuild of an
    existing table, so SQLite's table-rebuild procedure does not apply
    here at all. ``PRAGMA foreign_key_check`` is asserted after each
    direction anyway.
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
                assert "listening_activities" in schema.get_table_names()
                assert {
                    c["name"] for c in schema.get_columns("listening_activities")
                } == _EXPECTED_COLUMNS
                assert {
                    c["name"]
                    for c in schema.get_check_constraints("listening_activities")
                } == {"ck_listening_activities_transcript_visibility_valid"}
                foreign_keys = {
                    fk["constrained_columns"][0]: fk["referred_table"]
                    for fk in schema.get_foreign_keys("listening_activities")
                }
                assert foreign_keys == {
                    "quiz_id": "quizzes", "audio_file_id": "uploaded_files"
                }

                # THE data-preservation claim, executed: every existing row
                # of every neighbouring table survives untouched...
                after = {
                    table: conn.execute(
                        sa.text(f"SELECT count(*) FROM {table}")
                    ).scalar_one()
                    for table, _ in _PRESERVED
                }
                assert after == before == dict(_PRESERVED)
                row = conn.execute(sa.text(
                    "SELECT public_id, title, status, version, created_at"
                    " FROM quizzes WHERE id = 1"
                )).one()
                assert row.public_id == "quiz-public-1"
                assert row.title == "Existing quiz"
                assert row.status == "published"
                assert row.version == 4
                assert str(row.created_at).startswith("2026-05-01 08:00:00")

                # ...and the existing Quiz is still an ORDINARY quiz,
                # because no extension row was seeded for it.
                assert conn.execute(
                    sa.text("SELECT count(*) FROM listening_activities")
                ).scalar_one() == 0
                assert conn.execute(sa.text(
                    "SELECT count(*) FROM quizzes q WHERE EXISTS ("
                    "SELECT 1 FROM listening_activities la WHERE la.quiz_id = q.id)"
                )).scalar_one() == 0

                # The unique rules really are enforced by the new table.
                conn.execute(sa.text(
                    "INSERT INTO listening_activities (id, public_id, quiz_id,"
                    " audio_file_id, transcript, transcript_visibility,"
                    " vocabulary_notes, creation_nonce, created_at, updated_at)"
                    " VALUES (1, 'la-1', 1, 21, '', 'hidden', '', 'nonce-1',"
                    " '2026-05-03 10:00:00', '2026-05-03 10:00:00')"
                ))
                conn.commit()
                for duplicate, column in (
                    ("(2, 'la-2', 1, 21, '', 'hidden', '', 'nonce-2',", "quiz_id"),
                    ("(3, 'la-1', 1, 21, '', 'hidden', '', 'nonce-3',", "public_id"),
                    ("(4, 'la-4', 1, 21, '', 'hidden', '', 'nonce-1',", "creation_nonce"),
                ):
                    try:
                        conn.execute(sa.text(
                            "INSERT INTO listening_activities (id, public_id, quiz_id,"
                            " audio_file_id, transcript, transcript_visibility,"
                            " vocabulary_notes, creation_nonce, created_at, updated_at)"
                            f" VALUES {duplicate} '2026-05-03 10:00:00',"
                            " '2026-05-03 10:00:00')"
                        ))
                        raise AssertionError(f"{column} uniqueness was not enforced")
                    except sa.exc.IntegrityError:
                        conn.rollback()
                # The CHECK refuses an unapproved policy.
                try:
                    conn.execute(sa.text(
                        "INSERT INTO listening_activities (id, public_id, quiz_id,"
                        " audio_file_id, transcript, transcript_visibility,"
                        " vocabulary_notes, creation_nonce, created_at, updated_at)"
                        " VALUES (5, 'la-5', 1, 21, '', 'public', '', 'nonce-5',"
                        " '2026-05-03 10:00:00', '2026-05-03 10:00:00')"
                    ))
                    raise AssertionError("the visibility CHECK was not enforced")
                except sa.exc.IntegrityError:
                    conn.rollback()

                conn.execute(sa.text("DELETE FROM listening_activities"))
                conn.commit()

                with Operations.context(MigrationContext.configure(conn)):
                    module.downgrade()
                conn.commit()
                assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []

                after_down = inspect(conn)
                assert "listening_activities" not in after_down.get_table_names()
                # Every prerequisite table survives the whole round trip.
                assert {
                    table: conn.execute(
                        sa.text(f"SELECT count(*) FROM {table}")
                    ).scalar_one()
                    for table, _ in _PRESERVED
                } == dict(_PRESERVED)
                assert conn.execute(sa.text(
                    "SELECT title, version FROM quizzes WHERE id = 1"
                )).one() == ("Existing quiz", 4)
        finally:
            engine.dispose()


def test_models_and_migration_agree_on_the_new_table(app):
    _, source = _load_migration()
    block = source.split("op.create_table('listening_activities',", 1)[1].split(
        "\n    )", 1
    )[0]
    declared = dict(re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)", block))
    with app.app_context():
        actual = {
            column["name"]: str(column["nullable"])
            for column in inspect(db.engine).get_columns("listening_activities")
        }
    declared.pop("id", None)
    actual.pop("id", None)
    assert declared == actual


def test_mysql_ddl_compiles_without_a_connection():
    dialect = mysql.dialect()
    ddl = str(CreateTable(ListeningActivity.__table__).compile(dialect=dialect))
    assert "FOREIGN KEY(quiz_id) REFERENCES quizzes (id)" in ddl
    assert "FOREIGN KEY(audio_file_id) REFERENCES uploaded_files (id)" in ddl
    assert "ON DELETE" not in ddl and "ON UPDATE" not in ddl
    assert "BIGINT" in ddl
    assert "VARCHAR(36)" in ddl
    assert "TEXT" in ddl
    # Whole-second DATETIME on MySQL: no fractional precision anywhere.
    assert "DATETIME(" not in ddl
    assert "ck_listening_activities_transcript_visibility_valid" in ddl
    for column in ("quiz_id", "audio_file_id", "creation_nonce", "public_id"):
        assert f"UNIQUE ({column})" in ddl
