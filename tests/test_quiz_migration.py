"""M04C migration checks for the accepted M04A/M04B quiz aggregate.

The execution probe uses an isolated temporary SQLite database. MySQL checks
compile dialect DDL only and never connect to the real application database.
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
from app.models import QuestionOption, Quiz, QuizQuestion


_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "5d2c8a4e91f7"
_DOWN_REVISION = "b26b20c3d20d"


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m04c_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def test_revision_identifiers_and_linear_head():
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
    assert revisions - {p for p in parents.values() if p is not None} == {_REVISION}
    claimed = [p for p in parents.values() if p is not None]
    assert len(claimed) == len(set(claimed))
    assert len([r for r, p in parents.items() if p is None]) == 1


def test_migration_is_additive_and_creates_only_the_quiz_aggregate():
    _, source = _load_migration()
    assert re.findall(r"op\.create_table\('([^']+)'", source) == [
        "quizzes", "quiz_questions", "question_options",
    ]
    assert set(re.findall(r"batch_alter_table\('([^']+)'", source)) == {
        "quizzes", "quiz_questions", "question_options",
    }
    for forbidden in (
        "add_column", "drop_column", "alter_column", "execute(",
        "create_foreign_key", "op.bulk_insert", "ondelete",
    ):
        assert forbidden not in source, forbidden
    assert source.count("['groups.id']") == 1
    assert source.count("['quizzes.id']") == 1
    assert source.count("['quiz_questions.id']") == 1


def test_downgrade_is_reverse_dependency_order_and_symmetric():
    _, source = _load_migration()
    upgrade, downgrade = source.split("def downgrade():")
    created_indexes = re.findall(r"create_index\('([^']+)'", upgrade)
    dropped_indexes = re.findall(r"drop_index\('([^']+)'", downgrade)
    assert created_indexes == [
        "ix_quizzes_group_created_id",
        "ix_quiz_questions_quiz_order_id",
        "ix_question_options_question_active_order_id",
    ]
    assert dropped_indexes == list(reversed(created_indexes))
    assert re.findall(r"op\.drop_table\('([^']+)'", downgrade) == [
        "question_options", "quiz_questions", "quizzes",
    ]
    assert "drop_table" not in upgrade


def test_migration_declares_every_named_constraint():
    _, source = _load_migration()
    for name in (
        "uq_quizzes_group_title",
        "ck_quizzes_version_positive",
        "ck_quiz_questions_answer_mode_valid",
        "ck_quiz_questions_display_order_non_negative",
        "ck_quiz_questions_version_positive",
        "ck_question_options_display_order_non_negative",
        "ck_question_options_active_retired_consistency",
    ):
        assert f"name='{name}'" in source


def test_migration_applies_and_reverses_on_isolated_sqlite():
    module, _ = _load_migration()
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + os.path.join(tmp, "probe.db").replace(os.sep, "/")
        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("CREATE TABLE groups (id INTEGER PRIMARY KEY)"))
                conn.execute(sa.text("INSERT INTO groups (id) VALUES (7)"))
                conn.commit()

                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()
                conn.commit()

                schema = inspect(conn)
                assert sorted(schema.get_table_names()) == [
                    "groups", "question_options", "quiz_questions", "quizzes",
                ]
                assert [i["name"] for i in schema.get_indexes("quizzes")] == [
                    "ix_quizzes_group_created_id",
                ]
                assert [i["name"] for i in schema.get_indexes("quiz_questions")] == [
                    "ix_quiz_questions_quiz_order_id",
                ]
                assert [i["name"] for i in schema.get_indexes("question_options")] == [
                    "ix_question_options_question_active_order_id",
                ]
                assert {c["name"] for c in schema.get_check_constraints("quizzes")} == {
                    "ck_quizzes_version_positive",
                }
                assert {c["name"] for c in schema.get_check_constraints("quiz_questions")} == {
                    "ck_quiz_questions_answer_mode_valid",
                    "ck_quiz_questions_display_order_non_negative",
                    "ck_quiz_questions_version_positive",
                }
                assert {c["name"] for c in schema.get_check_constraints("question_options")} == {
                    "ck_question_options_active_retired_consistency",
                    "ck_question_options_display_order_non_negative",
                }

                with Operations.context(MigrationContext.configure(conn)):
                    module.downgrade()
                conn.commit()
                after = inspect(conn)
                assert after.get_table_names() == ["groups"]
                assert conn.execute(sa.text("SELECT id FROM groups")).scalar_one() == 7
        finally:
            engine.dispose()


def test_models_and_migration_agree_on_columns_and_nullability(app):
    _, source = _load_migration()
    for table in (Quiz.__table__, QuizQuestion.__table__, QuestionOption.__table__):
        block = source.split(f"op.create_table('{table.name}',", 1)[1].split("\n    )", 1)[0]
        declared = dict(re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)", block))
        with app.app_context():
            actual = {
                column["name"]: str(column["nullable"])
                for column in inspect(db.engine).get_columns(table.name)
            }
        declared.pop("id", None)
        actual.pop("id", None)
        assert declared == actual


def test_mysql_ddl_compiles_without_a_connection():
    dialect = mysql.dialect()
    ddl = {
        table.name: str(CreateTable(table).compile(dialect=dialect))
        for table in (Quiz.__table__, QuizQuestion.__table__, QuestionOption.__table__)
    }
    assert "FOREIGN KEY(group_id) REFERENCES `groups` (id)" in ddl["quizzes"]
    assert "FOREIGN KEY(quiz_id) REFERENCES quizzes (id)" in ddl["quiz_questions"]
    assert "FOREIGN KEY(question_id) REFERENCES quiz_questions (id)" in ddl["question_options"]
    assert "ON DELETE" not in "\n".join(ddl.values())
    assert "BIGINT" in ddl["quizzes"]
    assert "VARCHAR(36)" in ddl["quiz_questions"]
    assert "BOOL" in ddl["question_options"]
    assert all("DATETIME(" not in statement for statement in ddl.values())
