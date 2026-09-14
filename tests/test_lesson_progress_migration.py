"""Phase 4 / M13 migration checks for lesson progress.

The execution probes use an isolated temporary SQLite database seeded with
minimal ``users``, ``groups``, ``units`` and ``lessons`` tables and
representative rows in each -- so "nothing existing is lost" is executed
rather than asserted. Both directions run with foreign keys **enforced** and
``PRAGMA foreign_key_check`` asserted after each.

The MySQL checks here compile dialect DDL and render the revision's offline
(``--sql``) MySQL script; neither connects to a database. The real upgrade
against the authorized development MySQL database is a separate, manually
executed check.
"""

import importlib.util
import io
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
from app.models import LessonProgress

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "d2b7e6a4c519"
_DOWN_REVISION = "f3c8a1d5e927"

_TABLE = "lesson_progress"
_EXPECTED_COLUMNS = {"id", "student_id", "group_id", "lesson_id", "created_at", "completed_at",
                     "last_opened_at", "version"}
_EXPECTED_CHECKS = {"ck_lesson_progress_version_positive"}
_EXPECTED_INDEXES = {
    "ix_lesson_progress_student_opened_id": ["student_id", "last_opened_at", "id"],
    "ix_lesson_progress_group_student": ["group_id", "student_id"],
    "ix_lesson_progress_lesson_group": ["lesson_id", "group_id"],
}
_EXPECTED_FKS = {("student_id", "users"), ("group_id", "groups"), ("lesson_id", "lessons")}
_UNIQUE_NAME = "uq_lesson_progress_student_group_lesson"
_UNIQUE_COLUMNS = ["student_id", "group_id", "lesson_id"]

_MOMENT = "'2026-05-13 09:00:00'"


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m13_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def _flat(source):
    return " ".join(source.split()).replace('" "', "")


def _code(source):
    return source.split("from alembic import op", 1)[1]


def _function(code, name):
    return code.split(f"def {name}():", 1)[1].split("\ndef ", 1)[0]


# ===========================================================================
# Revision identity and what the revision does
# ===========================================================================


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
    # Phase 5 / M02, M02R and M03 follow this revision, so the single head is now M03's.
    assert revisions - {p for p in parents.values() if p is not None} == {"f9b2d6e4a318"}
    claimed = [p for p in parents.values() if p is not None]
    assert len(claimed) == len(set(claimed))
    assert len([r for r, p in parents.items() if p is None]) == 1


def test_the_revision_creates_one_table_and_touches_nothing_else():
    _, source = _load_migration()
    code = _code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == [_TABLE]
    for forbidden in ("add_column", "drop_column", "alter_column", "batch_alter_table",
                      "drop_constraint", "create_check_constraint", "op.execute",
                      "op.bulk_insert", "ondelete", "onupdate", "mysql_engine", "mysql_charset",
                      "server_default"):
        assert forbidden not in code, forbidden
    assert sorted(set(re.findall(r"\['([a-z_]+\.id)'\]", code))) == [
        "groups.id", "lessons.id", "users.id"
    ]
    upgrade = _function(code, "upgrade")
    for forbidden in ("drop_table", "drop_index"):
        assert forbidden not in upgrade, forbidden
    assert re.findall(r"op\.create_index\(\s*'([^']+)',\s*'([^']+)'", upgrade) == [
        (name, _TABLE) for name in _EXPECTED_INDEXES
    ]


def test_the_downgrade_drops_only_the_new_table():
    _, source = _load_migration()
    downgrade = _function(_code(source), "downgrade")
    assert re.findall(r"op\.([a-z_]+)\(", downgrade) == ["drop_table"]
    assert "op.drop_table('lesson_progress')" in downgrade


def test_the_revision_declares_every_expected_constraint_and_index():
    _, source = _load_migration()
    for fragment in (
        "name='ck_lesson_progress_version_positive'",
        "'version > 0'",
        f"name='{_UNIQUE_NAME}'",
        "'student_id', 'group_id', 'lesson_id',",
        "'ix_lesson_progress_student_opened_id'",
        "['student_id', 'last_opened_at', 'id']",
        "'ix_lesson_progress_group_student'",
        "['group_id', 'student_id']",
        "'ix_lesson_progress_lesson_group'",
        "['lesson_id', 'group_id']",
        "sa.Column('completed_at', sa.DateTime(), nullable=True)",
        "sa.Column('last_opened_at', sa.DateTime(), nullable=True)",
        "sa.Column('created_at', sa.DateTime(), nullable=False)",
        "sa.Column('version', sa.Integer(), nullable=False)",
    ):
        assert fragment in source, fragment
    assert "public_id" not in _code(source)


# ===========================================================================
# Execution against an isolated SQLite database
# ===========================================================================


_PREREQ = [
    """CREATE TABLE users (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        role VARCHAR(32) NOT NULL,
        status VARCHAR(32) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (public_id)
    )""",
    """CREATE TABLE groups (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        name VARCHAR(100) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (public_id)
    )""",
    """CREATE TABLE units (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        group_id INTEGER NOT NULL,
        title VARCHAR(150) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (public_id),
        FOREIGN KEY(group_id) REFERENCES groups (id)
    )""",
    """CREATE TABLE lessons (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        unit_id INTEGER NOT NULL,
        title VARCHAR(150) NOT NULL,
        status VARCHAR(32) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (public_id),
        FOREIGN KEY(unit_id) REFERENCES units (id)
    )""",
    "INSERT INTO users (id, public_id, role, status) VALUES (11, 'u-11', 'student', 'active')",
    "INSERT INTO users (id, public_id, role, status) VALUES (12, 'u-12', 'student', 'suspended')",
    "INSERT INTO users (id, public_id, role, status) VALUES (13, 'u-13', 'teacher', 'active')",
    "INSERT INTO groups (id, public_id, name) VALUES (21, 'g-21', 'Group A')",
    "INSERT INTO groups (id, public_id, name) VALUES (22, 'g-22', 'Group B')",
    "INSERT INTO units (id, public_id, group_id, title) VALUES (31, 'un-31', 21, 'Unit A')",
    "INSERT INTO units (id, public_id, group_id, title) VALUES (32, 'un-32', 22, 'Unit B')",
    "INSERT INTO lessons (id, public_id, unit_id, title, status) VALUES"
    " (41, 'l-41', 31, 'Greetings', 'published'), (42, 'l-42', 32, 'Numbers', 'draft')",
]

_EXISTING_TABLES = ("users", "groups", "units", "lessons")

_INSERT = ("INSERT INTO lesson_progress (student_id, group_id, lesson_id, created_at,"
           " completed_at, last_opened_at, version) VALUES ")


def _values(student_id=11, group_id=21, lesson_id=41, created_at=_MOMENT, completed_at="NULL",
            last_opened_at="NULL", version="1"):
    return (f"({student_id}, {group_id}, {lesson_id}, {created_at}, {completed_at},"
            f" {last_opened_at}, {version})")


def _existing_rows(conn):
    return {
        table: conn.execute(sa.text(f"SELECT * FROM {table} ORDER BY id")).fetchall()
        for table in _EXISTING_TABLES
    }


def _shape(conn):
    schema = inspect(conn)
    return (
        {c["name"] for c in schema.get_columns(_TABLE)},
        {c["name"] for c in schema.get_check_constraints(_TABLE)},
        {i["name"]: i["column_names"] for i in schema.get_indexes(_TABLE)},
        {(fk["constrained_columns"][0], fk["referred_table"])
         for fk in schema.get_foreign_keys(_TABLE)},
        sorted((u["name"], tuple(u["column_names"])) for u in schema.get_unique_constraints(_TABLE)),
        {c["name"]: c["nullable"] for c in schema.get_columns(_TABLE)},
    )


def _run(conn, direction):
    module, _ = _load_migration()
    with Operations.context(MigrationContext.configure(conn)):
        getattr(module, direction)()
    conn.commit()
    assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []


def _refused(conn, statement, rule):
    try:
        conn.execute(sa.text(statement))
    except sa.exc.IntegrityError:
        conn.rollback()
        return
    raise AssertionError(f"{rule} was not enforced")


def _probe(name):
    tmp = tempfile.TemporaryDirectory()
    url = "sqlite:///" + os.path.join(tmp.name, name).replace(os.sep, "/")
    engine = sa.create_engine(url)
    conn = engine.connect()
    conn.execute(sa.text("PRAGMA foreign_keys=ON"))
    for statement in _PREREQ:
        conn.execute(sa.text(statement))
    conn.commit()
    return tmp, engine, conn


def test_migration_applies_and_reverses_on_isolated_sqlite():
    tmp, engine, conn = _probe("probe.db")
    try:
        before = _existing_rows(conn)
        tables_before = set(inspect(conn).get_table_names())

        _run(conn, "upgrade")
        assert set(inspect(conn).get_table_names()) - tables_before == {_TABLE}
        columns, checks, indexes, fks, uniques, nullable = _shape(conn)
        assert columns == _EXPECTED_COLUMNS
        assert checks == _EXPECTED_CHECKS
        for name, cols in _EXPECTED_INDEXES.items():
            assert indexes[name] == cols, name
        assert fks == _EXPECTED_FKS
        assert uniques == [(_UNIQUE_NAME, tuple(_UNIQUE_COLUMNS))]
        assert {name for name, value in nullable.items() if value} == {
            "completed_at", "last_opened_at"
        }
        # Every existing row survives, and nothing is seeded.
        assert _existing_rows(conn) == before
        assert conn.execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar_one() == 0

        conn.execute(sa.text(_INSERT + _values()))
        conn.execute(sa.text(_INSERT + _values(lesson_id=42, completed_at=_MOMENT, version="2")))
        conn.execute(sa.text(_INSERT + _values(student_id=12, last_opened_at=_MOMENT)))
        # The schema cannot prove the Group binding -- lessons has no
        # group_id to reference -- so a row naming another Group is accepted
        # here and refused by the application, which proves it under locks.
        conn.execute(sa.text(_INSERT + _values(group_id=22)))
        conn.commit()
        after_inserts = conn.execute(sa.text(f"SELECT * FROM {_TABLE} ORDER BY id")).fetchall()

        _refused(conn, _INSERT + _values(), "student/group/lesson uniqueness")
        _refused(conn, _INSERT + _values(lesson_id=42, version="0"), "positive version")
        _refused(conn, _INSERT + _values(student_id=13, version="-1"), "positive version")
        _refused(conn, _INSERT + _values(student_id=99), "student foreign key")
        _refused(conn, _INSERT + _values(student_id=13, group_id=99), "group foreign key")
        _refused(conn, _INSERT + _values(student_id=13, lesson_id=99), "lesson foreign key")
        _refused(conn, _INSERT + _values(student_id=13, created_at="NULL"), "created_at NOT NULL")
        _refused(conn, _INSERT + _values(student_id=13, version="NULL"), "version NOT NULL")
        assert conn.execute(sa.text(f"SELECT * FROM {_TABLE} ORDER BY id")).fetchall() == after_inserts

        _run(conn, "downgrade")
        assert set(inspect(conn).get_table_names()) == tables_before
        assert _existing_rows(conn) == before
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_a_second_upgrade_after_a_downgrade_rebuilds_the_same_shape():
    tmp, engine, conn = _probe("probe2.db")
    try:
        before = _existing_rows(conn)
        shapes = []
        for _ in range(2):
            _run(conn, "upgrade")
            shapes.append(_shape(conn))
            _run(conn, "downgrade")
            assert _existing_rows(conn) == before
        assert shapes[0] == shapes[1]
        _run(conn, "upgrade")
        assert _shape(conn) == shapes[0]
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


# ===========================================================================
# Model / migration agreement, and MySQL DDL
# ===========================================================================


def test_the_model_and_migration_agree_on_the_new_table(app):
    _, source = _load_migration()
    with app.app_context():
        inspector = inspect(db.engine)
        block = source.split(f"op.create_table('{_TABLE}',", 1)[1].split("\n    )\n", 1)[0]
        declared = dict(re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)", block))
        actual = {
            column["name"]: str(column["nullable"]) for column in inspector.get_columns(_TABLE)
        }
        declared.pop("id", None)
        actual.pop("id", None)
        assert declared == actual
        assert {
            index["name"]: index["column_names"] for index in inspector.get_indexes(_TABLE)
        } == _EXPECTED_INDEXES
        assert [
            (u["name"], u["column_names"]) for u in inspector.get_unique_constraints(_TABLE)
        ] == [(_UNIQUE_NAME, _UNIQUE_COLUMNS)]


def test_the_models_check_expression_matches_the_migrations():
    _, source = _load_migration()
    flat = _flat(source)
    checks = {
        constraint.name: " ".join(str(constraint.sqltext).split())
        for constraint in LessonProgress.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert set(checks) == _EXPECTED_CHECKS
    for expression in checks.values():
        assert expression in flat, expression


def test_mysql_ddl_compiles_without_a_connection():
    ddl = str(CreateTable(LessonProgress.__table__).compile(dialect=mysql.dialect()))
    assert "FOREIGN KEY(student_id) REFERENCES users (id)" in ddl
    assert "FOREIGN KEY(group_id) REFERENCES `groups` (id)" in ddl
    assert "FOREIGN KEY(lesson_id) REFERENCES lessons (id)" in ddl
    assert "student_id BIGINT NOT NULL" in ddl
    assert "created_at DATETIME NOT NULL" in ddl
    assert "completed_at DATETIME," in ddl
    assert "last_opened_at DATETIME," in ddl
    assert "version INTEGER NOT NULL" in ddl
    assert (f"CONSTRAINT {_UNIQUE_NAME} UNIQUE (student_id, group_id, lesson_id)") in ddl
    assert "CONSTRAINT ck_lesson_progress_version_positive CHECK (version > 0)" in ddl
    for forbidden in ("ON DELETE", "ON UPDATE", "DATETIME(", "ENUM(", "public_id"):
        assert forbidden not in ddl, forbidden


def _offline_mysql(direction):
    """The revision's offline ``--sql`` script for MySQL, whitespace-flattened.
    No connection is opened."""
    module, _ = _load_migration()
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        getattr(module, direction)()
    return " ".join(buffer.getvalue().split())


def test_the_offline_mysql_upgrade_creates_only_the_table_and_its_indexes():
    sql = _offline_mysql("upgrade")
    steps = [
        sql.index("CREATE TABLE lesson_progress ("),
        sql.index("CREATE INDEX ix_lesson_progress_student_opened_id ON lesson_progress"
                  " (student_id, last_opened_at, id)"),
        sql.index("CREATE INDEX ix_lesson_progress_group_student ON lesson_progress"
                  " (group_id, student_id)"),
        sql.index("CREATE INDEX ix_lesson_progress_lesson_group ON lesson_progress"
                  " (lesson_id, group_id)"),
    ]
    assert steps == sorted(steps)
    assert sql.count("CREATE TABLE") == 1
    assert sql.count("CREATE INDEX") == 3
    assert "FOREIGN KEY(group_id) REFERENCES `groups` (id)" in sql
    assert "FOREIGN KEY(lesson_id) REFERENCES lessons (id)" in sql
    assert "FOREIGN KEY(student_id) REFERENCES users (id)" in sql
    assert f"CONSTRAINT {_UNIQUE_NAME} UNIQUE (student_id, group_id, lesson_id)" in sql
    for forbidden in ("ALTER TABLE", "ON DELETE", "ON UPDATE", "ENGINE=", "CHARSET", "DROP",
                      "DELETE FROM", "INSERT INTO", "UPDATE "):
        assert forbidden not in sql, forbidden


def test_the_offline_mysql_downgrade_drops_only_the_table():
    sql = _offline_mysql("downgrade")
    assert "DROP TABLE lesson_progress" in sql
    assert sql.count("DROP TABLE") == 1
    for forbidden in ("DROP INDEX", "ALTER TABLE", "DELETE FROM", "CREATE"):
        assert forbidden not in sql, forbidden


def test_the_new_table_uses_the_deployment_engine_and_charset():
    assert LessonProgress.__table__.kwargs == {}
