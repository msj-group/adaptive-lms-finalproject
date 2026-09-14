"""Phase 5 / M03 migration checks for ``student_fee_assignments``.

The execution probes use an isolated temporary SQLite database seeded with
minimal existing tables and representative rows -- accounts, a Group, its
Enrollments, and an M02 fee plan with items -- so "nothing existing is touched
and nothing is seeded" is executed rather than asserted. Both directions run
with foreign keys **enforced** and ``PRAGMA foreign_key_check`` asserted after
each.

The MySQL checks compile dialect DDL and render the revision's offline
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
from app.models import StudentFeeAssignment, StudentFeeAssignmentStatus

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "f9b2d6e4a318"
_DOWN_REVISION = "e4a1c6b9d273"
_TABLE = "student_fee_assignments"

_EXPECTED_COLUMNS = {
    "id", "public_id", "enrollment_id", "fee_plan_id", "status", "assigned_at",
    "assigned_by_id", "cancelled_at", "cancelled_by_id", "version", "created_at", "updated_at",
}
_EXPECTED_NULLABLE = {"cancelled_at", "cancelled_by_id"}
_EXPECTED_CHECKS = {
    "ck_student_fee_assignments_status_valid",
    "ck_student_fee_assignments_version_positive",
    "ck_student_fee_assignments_assignment_pair",
    "ck_student_fee_assignments_cancellation_pair",
    "ck_student_fee_assignments_lifecycle_state",
    "ck_student_fee_assignments_timestamps_ordered",
}
_EXPECTED_INDEXES = {
    "ix_student_fee_assignments_enrollment_status_id": ["enrollment_id", "status", "id"],
    "ix_student_fee_assignments_fee_plan_id": ["fee_plan_id"],
    "ix_student_fee_assignments_assigned_by_id": ["assigned_by_id"],
    "ix_student_fee_assignments_cancelled_by_id": ["cancelled_by_id"],
}
_EXPECTED_FKS = {
    ("enrollment_id", "enrollments"),
    ("fee_plan_id", "fee_plans"),
    ("assigned_by_id", "users"),
    ("cancelled_by_id", "users"),
}
_EXPECTED_UNIQUES = [("", ("public_id",))]

#: Column-name parts that would mean identity, money or payment data is being
#: duplicated into the row.
_PROHIBITED_PARTS = (
    "amount", "total", "currency", "price", "invoice", "payment", "paid", "receipt", "refund",
    "provider", "intent", "customer", "webhook", "card", "bank", "iban", "token", "discount",
    "installment", "tax", "quantity", "due", "term", "label", "item", "items", "name",
    "student", "group", "course", "level",
)


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"p5m03_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def _flat(source):
    return " ".join(source.split()).replace('" "', "")


def _code(source):
    return source.split("from alembic import op", 1)[1]


def _function(code, name):
    return code.split(f"def {name}():", 1)[1].split("\ndef ", 1)[0]


def _table_block(source):
    return source.split(f"op.create_table('{_TABLE}',", 1)[1].split("\n    )\n", 1)[0]


def _model_checks():
    return {
        constraint.name: " ".join(str(constraint.sqltext).split())
        for constraint in StudentFeeAssignment.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


# ===========================================================================
# Revision identity and what the revision does
# ===========================================================================


def test_revision_identifiers_and_one_linear_head():
    module, _ = _load_migration()
    assert module.revision == _REVISION
    assert module.down_revision == _DOWN_REVISION
    assert module.branch_labels is None
    assert module.depends_on is None

    parents = {}
    for path in _MIGRATIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r"^revision = '([^']+)'", source, re.M).group(1)
        parents[revision] = re.search(r"^down_revision = (?:'([^']+)'|None)", source, re.M).group(1)
    assert set(parents) - {p for p in parents.values() if p is not None} == {_REVISION}
    claimed = [p for p in parents.values() if p is not None]
    assert len(claimed) == len(set(claimed))
    assert [r for r, p in parents.items() if p == _DOWN_REVISION] == [_REVISION]
    assert len([r for r, p in parents.items() if p is None]) == 1


def test_the_revision_creates_one_table_and_touches_nothing_else():
    _, source = _load_migration()
    code = _code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == [_TABLE]
    for forbidden in ("add_column", "drop_column", "alter_column", "batch_alter_table",
                      "drop_constraint", "create_check_constraint", "op.execute",
                      "op.bulk_insert", "ondelete", "onupdate", "mysql_engine",
                      "mysql_charset", "server_default", "Float", "Numeric(", "DECIMAL",
                      "Enum(", "sa.text("):
        assert forbidden not in code, forbidden
    assert sorted(set(re.findall(r"\['([a-z_]+\.id)'\]", code))) == [
        "enrollments.id", "fee_plans.id", "users.id",
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
    assert re.findall(r"op\.drop_table\('([^']+)'\)", downgrade) == [_TABLE]


def test_the_revision_declares_every_expected_column_and_check():
    _, source = _load_migration()
    block = _table_block(source)
    assert set(re.findall(r"sa\.Column\('([^']+)'", block)) == _EXPECTED_COLUMNS
    for name in _EXPECTED_CHECKS:
        assert f"name='{name}'" in block, name
    for fragment in (
        "sa.Column('status', sa.String(length=32), nullable=False)",
        "sa.Column('assigned_at', sa.DateTime(), nullable=False)",
        "sa.Column('cancelled_at', sa.DateTime(), nullable=True)",
        "sa.Column('version', sa.Integer(), nullable=False)",
    ):
        assert fragment in source, fragment


def test_the_migrations_closed_set_matches_the_application_enum():
    _, source = _load_migration()
    expected = "status IN (" + ", ".join(
        f"'{member.value}'" for member in StudentFeeAssignmentStatus
    ) + ")"
    assert expected in source


def test_the_models_check_expressions_match_the_migration():
    _, source = _load_migration()
    flat = _flat(source)
    checks = _model_checks()
    assert set(checks) == _EXPECTED_CHECKS
    for name, expression in checks.items():
        assert expression in flat, (name, expression)


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
    """CREATE TABLE enrollments (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        student_id INTEGER NOT NULL,
        group_id INTEGER NOT NULL,
        status VARCHAR(32) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (public_id),
        FOREIGN KEY(student_id) REFERENCES users (id),
        FOREIGN KEY(group_id) REFERENCES groups (id)
    )""",
    """CREATE TABLE fee_plans (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        name VARCHAR(150) NOT NULL,
        currency_code VARCHAR(3) NOT NULL,
        status VARCHAR(32) NOT NULL,
        created_by_id INTEGER NOT NULL,
        version INTEGER NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (public_id),
        FOREIGN KEY(created_by_id) REFERENCES users (id)
    )""",
    """CREATE TABLE fee_plan_items (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        fee_plan_id INTEGER NOT NULL,
        kind VARCHAR(32) NOT NULL,
        label VARCHAR(150) NOT NULL,
        amount DECIMAL(19, 4) NOT NULL,
        status VARCHAR(32) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (public_id),
        FOREIGN KEY(fee_plan_id) REFERENCES fee_plans (id)
    )""",
    "INSERT INTO users (id, public_id, role, status) VALUES"
    " (11, 'u-11', 'administrator', 'active'), (12, 'u-12', 'administrator', 'suspended'),"
    " (13, 'u-13', 'student', 'active'), (14, 'u-14', 'student', 'suspended')",
    "INSERT INTO groups (id, public_id, name) VALUES (21, 'g-21', 'Group A'), (22, 'g-22', 'Group B')",
    "INSERT INTO enrollments (id, public_id, student_id, group_id, status) VALUES"
    " (31, 'e-31', 13, 21, 'active'), (32, 'e-32', 14, 22, 'withdrawn')",
    "INSERT INTO fee_plans (id, public_id, name, currency_code, status, created_by_id, version)"
    " VALUES (41, 'p-41', 'Standard', 'LYD', 'active', 11, 2),"
    " (42, 'p-42', 'Old', 'LYD', 'archived', 11, 3)",
    "INSERT INTO fee_plan_items (id, public_id, fee_plan_id, kind, label, amount, status) VALUES"
    " (51, 'i-51', 41, 'registration', 'Registration', 50.0, 'active'),"
    " (52, 'i-52', 41, 'course', 'Course', 1200.5, 'active')",
]

_EXISTING_TABLES = ("users", "groups", "enrollments", "fee_plans", "fee_plan_items")

_T0 = "'2026-05-10 09:00:00'"
_T1 = "'2026-05-11 09:00:00'"
_EARLIER = "'2026-05-09 09:00:00'"

_INSERT = (
    "INSERT INTO student_fee_assignments (id, public_id, enrollment_id, fee_plan_id, status,"
    " assigned_at, assigned_by_id, cancelled_at, cancelled_by_id, version, created_at,"
    " updated_at) VALUES "
)


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
        sorted((u["name"] or "", tuple(u["column_names"]))
               for u in schema.get_unique_constraints(_TABLE)),
        {c["name"] for c in schema.get_columns(_TABLE) if c["nullable"]},
    )


def _run(conn, direction):
    module, _ = _load_migration()
    with Operations.context(MigrationContext.configure(conn)):
        getattr(module, direction)()
    conn.commit()
    assert conn.execute(sa.text("PRAGMA foreign_keys")).scalar() == 1
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
        assert indexes == _EXPECTED_INDEXES
        assert fks == _EXPECTED_FKS
        assert uniques == _EXPECTED_UNIQUES
        assert nullable == _EXPECTED_NULLABLE
        # Nothing is seeded, and nothing existing moved.
        assert conn.execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar_one() == 0
        assert _existing_rows(conn) == before

        # One legitimate row per lifecycle state.
        conn.execute(sa.text(
            _INSERT
            + f"(1, 'a-1', 31, 41, 'assigned', {_T0}, 11, NULL, NULL, 1, {_T0}, {_T0}),"
            f" (2, 'a-2', 32, 42, 'cancelled', {_T0}, 11, {_T1}, 12, 2, {_T0}, {_T1})"
        ))
        conn.commit()

        def row(values):
            return _INSERT + values

        for statement, rule in (
            (row(f"(9, 'a-1', 31, 41, 'assigned', {_T0}, 11, NULL, NULL, 1, {_T0}, {_T0})"),
             "public_id uniqueness"),
            (row(f"(9, 'a-9', 31, 41, 'paid', {_T0}, 11, NULL, NULL, 1, {_T0}, {_T0})"),
             "the status CHECK"),
            (row(f"(9, 'a-9', 31, 41, 'assigned', {_T0}, 11, NULL, NULL, 0, {_T0}, {_T0})"),
             "the version CHECK"),
            (row(f"(9, 'a-9', 31, 41, 'assigned', {_T0}, 11, {_T1}, NULL, 1, {_T0}, {_T1})"),
             "the cancellation pair"),
            (row(f"(9, 'a-9', 31, 41, 'assigned', {_T0}, 11, {_T1}, 11, 1, {_T0}, {_T1})"),
             "the lifecycle state (assigned)"),
            (row(f"(9, 'a-9', 31, 41, 'cancelled', {_T0}, 11, NULL, NULL, 2, {_T0}, {_T0})"),
             "the lifecycle state (cancelled)"),
            (row(f"(9, 'a-9', 31, 41, 'assigned', {_EARLIER}, 11, NULL, NULL, 1, {_T0}, {_T0})"),
             "assigned_at before created_at"),
            (row(f"(9, 'a-9', 31, 41, 'cancelled', {_T0}, 11, {_EARLIER}, 11, 2, {_T0}, {_T1})"),
             "a cancellation before the assignment"),
            (row(f"(9, 'a-9', 31, 41, 'cancelled', {_T0}, 11, {_T1}, 11, 2, {_T0}, {_T0})"),
             "updated_at before the cancellation"),
            (row(f"(9, 'a-9', 99, 41, 'assigned', {_T0}, 11, NULL, NULL, 1, {_T0}, {_T0})"),
             "the enrollment foreign key"),
            (row(f"(9, 'a-9', 31, 99, 'assigned', {_T0}, 11, NULL, NULL, 1, {_T0}, {_T0})"),
             "the fee_plan foreign key"),
            (row(f"(9, 'a-9', 31, 41, 'assigned', {_T0}, 99, NULL, NULL, 1, {_T0}, {_T0})"),
             "the assigned_by foreign key"),
            (row(f"(9, 'a-9', 31, 41, 'cancelled', {_T0}, 11, {_T1}, 99, 2, {_T0}, {_T1})"),
             "the cancelled_by foreign key"),
            ("DELETE FROM enrollments WHERE id = 31", "no cascade from an enrollment"),
            ("DELETE FROM fee_plans WHERE id = 41", "no cascade from a fee plan"),
            ("DELETE FROM users WHERE id = 11", "no cascade from an account"),
        ):
            _refused(conn, statement, rule)

        # Leave no probe row behind, then reverse.
        conn.execute(sa.text(f"DELETE FROM {_TABLE}"))
        conn.commit()
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
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


# ===========================================================================
# Model / migration agreement, and MySQL DDL
# ===========================================================================


def test_the_model_and_migration_agree(app):
    _, source = _load_migration()
    with app.app_context():
        inspector = inspect(db.engine)
        declared = dict(re.findall(
            r"sa\.Column\('([^']+)',.*?nullable=(True|False)", _table_block(source)
        ))
        actual = {c["name"]: str(c["nullable"]) for c in inspector.get_columns(_TABLE)}
        declared.pop("id", None)
        actual.pop("id", None)
        assert declared == actual
        assert {i["name"]: i["column_names"] for i in inspector.get_indexes(_TABLE)} == (
            _EXPECTED_INDEXES
        )
        assert sorted(
            (u["name"] or "", tuple(u["column_names"]))
            for u in inspector.get_unique_constraints(_TABLE)
        ) == _EXPECTED_UNIQUES
        assert {c["name"] for c in inspector.get_check_constraints(_TABLE)} == _EXPECTED_CHECKS


def test_mysql_ddl_compiles_without_a_connection():
    ddl = str(CreateTable(StudentFeeAssignment.__table__).compile(dialect=mysql.dialect()))
    flat = " ".join(ddl.split())
    for fragment in (
        "id BIGINT NOT NULL AUTO_INCREMENT",
        "public_id VARCHAR(36) NOT NULL",
        "enrollment_id BIGINT NOT NULL",
        "fee_plan_id BIGINT NOT NULL",
        "status VARCHAR(32) NOT NULL",
        "assigned_at DATETIME NOT NULL",
        "assigned_by_id BIGINT NOT NULL",
        "cancelled_at DATETIME,",
        "cancelled_by_id BIGINT,",
        "version INTEGER NOT NULL",
        "FOREIGN KEY(enrollment_id) REFERENCES enrollments (id)",
        "FOREIGN KEY(fee_plan_id) REFERENCES fee_plans (id)",
        "FOREIGN KEY(assigned_by_id) REFERENCES users (id)",
        "FOREIGN KEY(cancelled_by_id) REFERENCES users (id)",
        "UNIQUE (public_id)",
        "CONSTRAINT ck_student_fee_assignments_status_valid CHECK"
        " (status IN ('assigned', 'cancelled'))",
    ):
        assert fragment in flat, fragment
    for name, expression in _model_checks().items():
        assert f"CONSTRAINT {name} CHECK ({expression})" in flat, name
    for forbidden in ("ON DELETE", "ON UPDATE", "DATETIME(", "ENUM(", "DECIMAL", "FLOAT",
                      "DOUBLE", "NUMERIC", "ENGINE=", "CHARSET"):
        assert forbidden not in ddl.upper(), forbidden


def test_no_column_duplicates_identity_money_or_payment_data():
    for column in StudentFeeAssignment.__table__.columns:
        parts = column.name.split("_")
        for fragment in _PROHIBITED_PARTS:
            assert fragment not in parts, (column.name, fragment)


def test_the_new_table_inherits_the_deployment_engine_and_charset():
    assert StudentFeeAssignment.__table__.kwargs == {}


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
    steps = [sql.index(f"CREATE TABLE {_TABLE} (")]
    steps += [sql.index(f"CREATE INDEX {name} ON {_TABLE}") for name in _EXPECTED_INDEXES]
    assert steps == sorted(steps)
    assert sql.count("CREATE TABLE") == 1
    assert sql.count("CREATE INDEX") == 4
    for forbidden in ("ALTER TABLE", "ON DELETE", "ON UPDATE", "ENGINE=", "CHARSET", "DROP",
                      "DELETE FROM", "INSERT INTO", "UPDATE ", "DECIMAL"):
        assert forbidden not in sql, forbidden


def test_the_offline_mysql_downgrade_drops_only_the_table():
    sql = _offline_mysql("downgrade")
    assert sql.count("DROP TABLE") == 1
    assert f"DROP TABLE {_TABLE}" in sql
    for forbidden in ("DROP INDEX", "ALTER TABLE", "DELETE FROM", "CREATE", "fee_plans ",
                      "enrollments "):
        assert forbidden not in sql, forbidden
