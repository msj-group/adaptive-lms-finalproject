"""Phase 5 / M02 migration checks for ``fee_plans`` and ``fee_plan_items``.

The execution probes use an isolated temporary SQLite database seeded with
minimal existing tables and representative rows -- accounts, a Group and a
center calendar event -- so "nothing existing is touched and nothing is
seeded" is executed rather than asserted. Both directions run with foreign
keys **enforced** and ``PRAGMA foreign_key_check`` asserted after each.

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

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from app.extensions import db
from app.models import FeePlan, FeePlanItem, FeePlanItemKind, FeePlanItemStatus, FeePlanStatus

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "b7c3e9a15d42"
_DOWN_REVISION = "d2b7e6a4c519"
#: Phase 5 / M02R's correction revision, which follows this one.
_CORRECTION = "e4a1c6b9d273"
#: Phase 5 / M03's revision follows the correction.
_ASSIGNMENTS = "f9b2d6e4a318"
#: Phase 5 / M04's revision follows M03's and M05's follows M04's, so M05's is the head.
_HEAD = "e9c4b2d7a1f3"
_LIFECYCLE = "ck_fee_plans_lifecycle_state"

_PLANS = "fee_plans"
_ITEMS = "fee_plan_items"
_NEW_TABLES = [_PLANS, _ITEMS]

_EXPECTED_COLUMNS = {
    _PLANS: {
        "id", "public_id", "name", "description", "currency_code", "status",
        "created_by_id", "first_activated_at", "first_activated_by_id",
        "status_changed_at", "status_changed_by_id", "version", "created_at", "updated_at",
    },
    _ITEMS: {
        "id", "public_id", "fee_plan_id", "kind", "label", "amount", "status",
        "removed_at", "removed_by_id", "version", "created_at", "updated_at",
    },
}
_EXPECTED_NULLABLE = {
    _PLANS: {
        "description", "first_activated_at", "first_activated_by_id",
        "status_changed_at", "status_changed_by_id",
    },
    _ITEMS: {"removed_at", "removed_by_id"},
}
_EXPECTED_CHECKS = {
    _PLANS: {
        "ck_fee_plans_status_valid",
        "ck_fee_plans_currency_code",
        "ck_fee_plans_version_positive",
        "ck_fee_plans_first_activation_pair",
        "ck_fee_plans_status_change_pair",
        "ck_fee_plans_lifecycle_state",
        "ck_fee_plans_timestamps_ordered",
    },
    _ITEMS: {
        "ck_fee_plan_items_kind_valid",
        "ck_fee_plan_items_status_valid",
        "ck_fee_plan_items_amount_range",
        "ck_fee_plan_items_version_positive",
        "ck_fee_plan_items_removal_state",
        "ck_fee_plan_items_timestamps_ordered",
    },
}
_EXPECTED_INDEXES = {
    _PLANS: {
        "ix_fee_plans_status_id": ["status", "id"],
        "ix_fee_plans_created_by_id": ["created_by_id"],
        "ix_fee_plans_first_activated_by_id": ["first_activated_by_id"],
        "ix_fee_plans_status_changed_by_id": ["status_changed_by_id"],
    },
    _ITEMS: {
        "ix_fee_plan_items_plan_status_id": ["fee_plan_id", "status", "id"],
        "ix_fee_plan_items_removed_by_id": ["removed_by_id"],
    },
}
_EXPECTED_FKS = {
    _PLANS: {
        ("created_by_id", "users"),
        ("first_activated_by_id", "users"),
        ("status_changed_by_id", "users"),
    },
    _ITEMS: {("fee_plan_id", "fee_plans"), ("removed_by_id", "users")},
}
_EXPECTED_UNIQUES = {
    _PLANS: [("", ("public_id",)), ("uq_fee_plans_name", ("name",))],
    _ITEMS: [("", ("public_id",))],
}

#: Column-name fragments that would mean card or bank data is being stored.
_CARD_DATA_FRAGMENTS = (
    "card", "pan", "cvv", "cvc", "cvn", "expiry", "expiration", "exp_month",
    "exp_year", "security_code", "cardholder", "holder", "iban", "account_number",
    "track", "pin", "token", "bank",
)


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"p5m02_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def _flat(source):
    return " ".join(source.split()).replace('" "', "")


def _code(source):
    return source.split("from alembic import op", 1)[1]


def _function(code, name):
    return code.split(f"def {name}():", 1)[1].split("\ndef ", 1)[0]


def _table_block(source, table):
    return source.split(f"op.create_table('{table}',", 1)[1].split("\n    )\n", 1)[0]


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
    # Phase 5 / M02R, M03, M04, M05, M06 and then M07 follow this revision, so the single head is now M07's.
    assert revisions - {p for p in parents.values() if p is not None} == {_HEAD}
    claimed = [p for p in parents.values() if p is not None]
    assert len(claimed) == len(set(claimed))
    assert len([r for r, p in parents.items() if p is None]) == 1


def test_the_revision_creates_two_tables_and_touches_nothing_else():
    _, source = _load_migration()
    code = _code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == _NEW_TABLES
    for forbidden in ("add_column", "drop_column", "alter_column", "batch_alter_table",
                      "drop_constraint", "create_check_constraint", "op.execute",
                      "op.bulk_insert", "ondelete", "onupdate", "mysql_engine",
                      "mysql_charset", "server_default", "Float", "Numeric(", "Enum("):
        assert forbidden not in code, forbidden
    assert sorted(set(re.findall(r"\['([a-z_]+\.id)'\]", code))) == ["fee_plans.id", "users.id"]
    upgrade = _function(code, "upgrade")
    for forbidden in ("drop_table", "drop_index"):
        assert forbidden not in upgrade, forbidden
    assert re.findall(r"op\.create_index\(\s*'([^']+)',\s*'([^']+)'", upgrade) == [
        (name, table) for table in _NEW_TABLES for name in _EXPECTED_INDEXES[table]
    ]


def test_the_downgrade_drops_the_child_then_the_parent_and_nothing_else():
    _, source = _load_migration()
    downgrade = _function(_code(source), "downgrade")
    assert re.findall(r"op\.([a-z_]+)\(", downgrade) == ["drop_table", "drop_table"]
    assert re.findall(r"op\.drop_table\('([^']+)'\)", downgrade) == [_ITEMS, _PLANS]


def test_the_revision_declares_every_expected_column_constraint_and_index():
    _, source = _load_migration()
    for table in _NEW_TABLES:
        block = _table_block(source, table)
        assert set(re.findall(r"sa\.Column\('([^']+)'", block)) == _EXPECTED_COLUMNS[table]
        for name in _EXPECTED_CHECKS[table]:
            assert f"name='{name}'" in block, name
    for fragment in (
        "sa.Column('amount', sa.DECIMAL(precision=19, scale=4), nullable=False)",
        "sa.Column('currency_code', sa.String(length=3), nullable=False)",
        "sa.Column('name', sa.String(length=150), nullable=False)",
        "sa.Column('description', sa.String(length=1000), nullable=True)",
        "sa.Column('label', sa.String(length=150), nullable=False)",
        "sa.UniqueConstraint('name', name='uq_fee_plans_name')",
        "\"currency_code = 'LYD'\"",
        "'amount >= 0.001 AND amount <= 99999.999'",
    ):
        assert fragment in source, fragment


def test_the_migrations_closed_sets_match_the_application_enums():
    _, source = _load_migration()
    for column, enum in (("status", FeePlanStatus), ("kind", FeePlanItemKind),
                         ("status", FeePlanItemStatus)):
        expected = f"{column} IN (" + ", ".join(f"'{member.value}'" for member in enum) + ")"
        assert expected in source, expected


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
    """CREATE TABLE calendar_events (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        created_by_id INTEGER NOT NULL,
        title VARCHAR(150) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (public_id),
        FOREIGN KEY(created_by_id) REFERENCES users (id)
    )""",
    "INSERT INTO users (id, public_id, role, status) VALUES"
    " (11, 'u-11', 'administrator', 'active'), (12, 'u-12', 'administrator', 'suspended'),"
    " (13, 'u-13', 'student', 'active'), (14, 'u-14', 'teacher', 'active')",
    "INSERT INTO groups (id, public_id, name) VALUES (21, 'g-21', 'Group A')",
    "INSERT INTO calendar_events (id, public_id, created_by_id, title) VALUES"
    " (31, 'e-31', 11, 'Open day')",
]

_EXISTING_TABLES = ("users", "groups", "calendar_events")

_T0 = "'2026-05-01 09:00:00'"
_T1 = "'2026-05-02 09:00:00'"

_PLAN_INSERT = (
    "INSERT INTO fee_plans (id, public_id, name, description, currency_code, status,"
    " created_by_id, first_activated_at, first_activated_by_id, status_changed_at,"
    " status_changed_by_id, version, created_at, updated_at) VALUES "
)
_ITEM_INSERT = (
    "INSERT INTO fee_plan_items (id, public_id, fee_plan_id, kind, label, amount, status,"
    " removed_at, removed_by_id, version, created_at, updated_at) VALUES "
)


def _existing_rows(conn):
    return {
        table: conn.execute(sa.text(f"SELECT * FROM {table} ORDER BY id")).fetchall()
        for table in _EXISTING_TABLES
    }


def _shape(conn, table):
    schema = inspect(conn)
    return (
        {c["name"] for c in schema.get_columns(table)},
        {c["name"] for c in schema.get_check_constraints(table)},
        {i["name"]: i["column_names"] for i in schema.get_indexes(table)},
        {(fk["constrained_columns"][0], fk["referred_table"])
         for fk in schema.get_foreign_keys(table)},
        sorted((u["name"] or "", tuple(u["column_names"]))
               for u in schema.get_unique_constraints(table)),
        {c["name"] for c in schema.get_columns(table) if c["nullable"]},
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
        assert set(inspect(conn).get_table_names()) - tables_before == set(_NEW_TABLES)
        for table in _NEW_TABLES:
            columns, checks, indexes, fks, uniques, nullable = _shape(conn, table)
            assert columns == _EXPECTED_COLUMNS[table]
            assert checks == _EXPECTED_CHECKS[table]
            assert indexes == _EXPECTED_INDEXES[table]
            assert fks == _EXPECTED_FKS[table]
            assert uniques == _EXPECTED_UNIQUES[table]
            assert nullable == _EXPECTED_NULLABLE[table]
            # Nothing is seeded.
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        assert _existing_rows(conn) == before

        # One legitimate row per lifecycle shape, and items in both states.
        conn.execute(sa.text(
            _PLAN_INSERT
            + f"(1, 'p-1', 'Draft plan', NULL, 'LYD', 'draft', 11, NULL, NULL, NULL, NULL,"
            f" 1, {_T0}, {_T0}),"
            f" (2, 'p-2', 'Active plan', 'Text', 'LYD', 'active', 11, {_T1}, 11, {_T1}, 11,"
            f" 3, {_T0}, {_T1}),"
            f" (3, 'p-3', 'Archived draft', NULL, 'LYD', 'archived', 11, NULL, NULL, {_T1},"
            f" 11, 2, {_T0}, {_T1})"
        ))
        conn.execute(sa.text(
            _ITEM_INSERT
            + f"(1, 'i-1', 1, 'registration', 'Registration', 0.001, 'active', NULL, NULL,"
            f" 1, {_T0}, {_T0}),"
            f" (2, 'i-2', 1, 'course', 'Course', 99999.999, 'removed', {_T1}, 11, 2,"
            f" {_T0}, {_T1})"
        ))
        conn.commit()
        amounts = conn.execute(sa.text(
            "SELECT CAST(amount AS TEXT) FROM fee_plan_items ORDER BY id"
        )).scalars().all()
        assert [float(a) for a in amounts] == [0.001, 99999.999]

        for statement, rule in (
            (_PLAN_INSERT + f"(9, 'p-9', 'Draft plan', NULL, 'LYD', 'draft', 11, NULL, NULL,"
             f" NULL, NULL, 1, {_T0}, {_T0})", "uq_fee_plans_name"),
            (_PLAN_INSERT + f"(9, 'p-9', 'X', NULL, 'EUR', 'draft', 11, NULL, NULL, NULL,"
             f" NULL, 1, {_T0}, {_T0})", "currency"),
            (_PLAN_INSERT + f"(9, 'p-9', 'X', NULL, 'LYD', 'active', 11, NULL, NULL, {_T1},"
             f" 11, 1, {_T0}, {_T1})", "lifecycle state"),
            (_PLAN_INSERT + f"(9, 'p-9', 'X', NULL, 'LYD', 'draft', 99, NULL, NULL, NULL,"
             f" NULL, 1, {_T0}, {_T0})", "created_by foreign key"),
            (_ITEM_INSERT + f"(9, 'i-9', 1, 'discount', 'X', 5, 'active', NULL, NULL, 1,"
             f" {_T0}, {_T0})", "kind"),
            (_ITEM_INSERT + f"(9, 'i-9', 1, 'course', 'X', 0, 'active', NULL, NULL, 1,"
             f" {_T0}, {_T0})", "amount range"),
            (_ITEM_INSERT + f"(9, 'i-9', 1, 'course', 'X', 100000, 'active', NULL, NULL, 1,"
             f" {_T0}, {_T0})", "amount range"),
            (_ITEM_INSERT + f"(9, 'i-9', 1, 'course', 'X', 5, 'removed', NULL, NULL, 1,"
             f" {_T0}, {_T0})", "removal state"),
            (_ITEM_INSERT + f"(9, 'i-9', 99, 'course', 'X', 5, 'active', NULL, NULL, 1,"
             f" {_T0}, {_T0})", "fee_plan foreign key"),
            ("DELETE FROM fee_plans WHERE id = 1", "no cascade from a plan to its items"),
            ("DELETE FROM users WHERE id = 11", "no cascade from an account"),
        ):
            _refused(conn, statement, rule)

        # Leave no probe row behind, then reverse.
        conn.execute(sa.text("DELETE FROM fee_plan_items"))
        conn.execute(sa.text("DELETE FROM fee_plans"))
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
            shapes.append([_shape(conn, table) for table in _NEW_TABLES])
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


def test_the_models_and_migration_agree_on_both_tables(app):
    _, source = _load_migration()
    with app.app_context():
        inspector = inspect(db.engine)
        for table in _NEW_TABLES:
            declared = dict(re.findall(
                r"sa\.Column\('([^']+)',.*?nullable=(True|False)", _table_block(source, table)
            ))
            actual = {c["name"]: str(c["nullable"]) for c in inspector.get_columns(table)}
            declared.pop("id", None)
            actual.pop("id", None)
            assert declared == actual, table
            assert {
                i["name"]: i["column_names"] for i in inspector.get_indexes(table)
            } == _EXPECTED_INDEXES[table]
            assert sorted(
                (u["name"] or "", tuple(u["column_names"]))
                for u in inspector.get_unique_constraints(table)
            ) == _EXPECTED_UNIQUES[table]


def test_the_models_check_expressions_match_the_migrations():
    """Every model CHECK is the one ``b7c3e9a15d42`` wrote -- except the
    lifecycle CHECK, which Phase 5 / M02R replaced in ``e4a1c6b9d273``."""
    _, source = _load_migration()
    flat = _flat(source)
    _, correction_source = _load_correction()
    correction_flat = _flat(correction_source)
    for model, table in ((FeePlan, _PLANS), (FeePlanItem, _ITEMS)):
        checks = _model_checks(model)
        assert set(checks) == _EXPECTED_CHECKS[table]
        for name, expression in checks.items():
            if name == _LIFECYCLE:
                assert expression in correction_flat, expression
                assert expression not in flat, expression
            else:
                assert expression in flat, expression


def test_mysql_ddl_compiles_without_a_connection():
    plans = str(CreateTable(FeePlan.__table__).compile(dialect=mysql.dialect()))
    items = str(CreateTable(FeePlanItem.__table__).compile(dialect=mysql.dialect()))
    assert "id BIGINT NOT NULL AUTO_INCREMENT" in plans
    assert "currency_code VARCHAR(3) NOT NULL" in plans
    assert "FOREIGN KEY(created_by_id) REFERENCES users (id)" in plans
    assert "FOREIGN KEY(first_activated_by_id) REFERENCES users (id)" in plans
    assert "FOREIGN KEY(status_changed_by_id) REFERENCES users (id)" in plans
    assert "CONSTRAINT uq_fee_plans_name UNIQUE (name)" in plans
    assert "CONSTRAINT ck_fee_plans_currency_code CHECK (currency_code = 'LYD')" in plans
    # The corrected (M02R) lifecycle CHECK, and not M02's.
    assert f"CONSTRAINT {_LIFECYCLE} CHECK ({_model_lifecycle_sql()})" in " ".join(plans.split())
    assert "first_activated_at IS NULL AND status_changed_at IS NULL" not in plans
    assert "amount DECIMAL(19, 4) NOT NULL" in items
    assert "FOREIGN KEY(fee_plan_id) REFERENCES fee_plans (id)" in items
    assert "FOREIGN KEY(removed_by_id) REFERENCES users (id)" in items
    assert ("CONSTRAINT ck_fee_plan_items_amount_range CHECK"
            " (amount >= 0.001 AND amount <= 99999.999)") in items
    for ddl, table in ((plans, _PLANS), (items, _ITEMS)):
        for name in _EXPECTED_CHECKS[table]:
            assert f"CONSTRAINT {name} CHECK" in ddl, name
        for forbidden in ("ON DELETE", "ON UPDATE", "DATETIME(", "ENUM(", "FLOAT", "DOUBLE",
                          "REAL", "NUMERIC"):
            assert forbidden not in ddl.upper(), (table, forbidden)


def test_no_column_stores_card_or_bank_data():
    for model in (FeePlan, FeePlanItem):
        for column in model.__table__.columns:
            for fragment in _CARD_DATA_FRAGMENTS:
                assert fragment not in column.name.split("_") and not column.name.startswith(
                    fragment
                ), (model.__tablename__, column.name, fragment)


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


def test_the_offline_mysql_upgrade_creates_only_the_two_tables_and_their_indexes():
    sql = _offline_mysql("upgrade")
    steps = [sql.index("CREATE TABLE fee_plans (")]
    steps += [sql.index(f"CREATE INDEX {name} ON fee_plans") for name in _EXPECTED_INDEXES[_PLANS]]
    steps.append(sql.index("CREATE TABLE fee_plan_items ("))
    steps += [
        sql.index(f"CREATE INDEX {name} ON fee_plan_items") for name in _EXPECTED_INDEXES[_ITEMS]
    ]
    assert steps == sorted(steps)
    assert sql.count("CREATE TABLE") == 2
    assert sql.count("CREATE INDEX") == 6
    assert "amount DECIMAL(19, 4) NOT NULL" in sql
    for forbidden in ("ALTER TABLE", "ON DELETE", "ON UPDATE", "ENGINE=", "CHARSET", "DROP",
                      "DELETE FROM", "INSERT INTO", "UPDATE "):
        assert forbidden not in sql, forbidden


def test_the_offline_mysql_downgrade_drops_only_the_two_tables_child_first():
    sql = _offline_mysql("downgrade")
    assert sql.count("DROP TABLE") == 2
    assert sql.index("DROP TABLE fee_plan_items") < sql.index("DROP TABLE fee_plans")
    for forbidden in ("DROP INDEX", "ALTER TABLE", "DELETE FROM", "CREATE"):
        assert forbidden not in sql, forbidden


def test_the_new_tables_use_the_deployment_engine_and_charset():
    assert FeePlan.__table__.kwargs == {}
    assert FeePlanItem.__table__.kwargs == {}


# ===========================================================================
# Phase 5 / M02R -- the lifecycle CHECK correction (e4a1c6b9d273)
# ===========================================================================
#
# The correction rebuilds ``fee_plans`` on SQLite. ``fee_plan_items``
# references that table, so the rebuild runs with foreign-key enforcement
# switched off outside any transaction and back on afterwards -- SQLite's
# documented table-rebuild procedure -- and every test proves enforcement is
# on again and every reference still resolves.

_T2 = "'2026-05-03 09:00:00'"

_M02_PLANS = (
    f"(1, 'p-1', 'Draft plan', NULL, 'LYD', 'draft', 11, NULL, NULL, NULL, NULL, 2,"
    f" {_T0}, {_T0}),"
    f" (2, 'p-2', 'Active plan', 'Text', 'LYD', 'active', 11, {_T1}, 11, {_T1}, 11, 3,"
    f" {_T0}, {_T1}),"
    f" (3, 'p-3', 'Archived draft', NULL, 'LYD', 'archived', 11, NULL, NULL, {_T1}, 12, 2,"
    f" {_T0}, {_T1}),"
    f" (4, 'p-4', 'Archived active', NULL, 'LYD', 'archived', 11, {_T1}, 11, {_T2}, 12, 4,"
    f" {_T0}, {_T2})"
)
_M02_ITEMS = (
    f"(1, 'i-1', 1, 'registration', 'Registration', 0.001, 'active', NULL, NULL, 1,"
    f" {_T0}, {_T0}),"
    f" (2, 'i-2', 1, 'course', 'Old course', 99999.999, 'removed', {_T1}, 11, 2, {_T0}, {_T1}),"
    f" (3, 'i-3', 2, 'course', 'Course', 1250.5, 'active', NULL, NULL, 1, {_T0}, {_T0}),"
    f" (4, 'i-4', 3, 'course', 'Course', 10, 'active', NULL, NULL, 1, {_T0}, {_T0}),"
    f" (5, 'i-5', 4, 'registration', 'Registration', 75.25, 'active', NULL, NULL, 1,"
    f" {_T0}, {_T0})"
)
#: A draft restored from its archive: never activated, one historical
#: transition. Refused by M02's CHECK, accepted by the correction's.
_RESTORED_DRAFT = _PLAN_INSERT + (
    f"(10, 'p-10', 'Restored draft', NULL, 'LYD', 'draft', 11, NULL, NULL, {_T2}, 12, 3,"
    f" {_T0}, {_T2})"
)
#: Rows the corrected schema must still refuse, with the rule each proves.
_STILL_REFUSED = (
    (f"(20, 'p-20', 'X20', NULL, 'LYD', 'draft', 11, {_T1}, 11, {_T1}, 11, 2, {_T0}, {_T1})",
     "a draft with a first activation"),
    (f"(21, 'p-21', 'X21', NULL, 'LYD', 'draft', 11, {_T1}, 11, NULL, NULL, 2, {_T0}, {_T1})",
     "a draft with a first activation and no transition"),
    (f"(22, 'p-22', 'X22', NULL, 'LYD', 'active', 11, NULL, NULL, {_T1}, 11, 2, {_T0}, {_T1})",
     "an active plan never activated"),
    (f"(23, 'p-23', 'X23', NULL, 'LYD', 'active', 11, {_T1}, 11, NULL, NULL, 2, {_T0}, {_T1})",
     "an active plan without a transition"),
    (f"(24, 'p-24', 'X24', NULL, 'LYD', 'active', 11, {_T2}, 11, {_T1}, 11, 2, {_T0}, {_T2})",
     "an active transition before the first activation"),
    (f"(25, 'p-25', 'X25', NULL, 'LYD', 'archived', 11, NULL, NULL, NULL, NULL, 2,"
     f" {_T0}, {_T0})", "an archived plan without a transition"),
    (f"(26, 'p-26', 'X26', NULL, 'LYD', 'archived', 11, {_T2}, 11, {_T1}, 11, 2,"
     f" {_T0}, {_T2})", "an archive before the first activation"),
    (f"(27, 'p-27', 'X27', NULL, 'LYD', 'draft', 11, NULL, NULL, {_T2}, NULL, 2, {_T0}, {_T2})",
     "a restoration without its actor"),
    (f"(28, 'p-28', 'X28', NULL, 'LYD', 'draft', 11, NULL, NULL, {_T2}, 11, 2, {_T0}, {_T1})",
     "a restored draft updated before its restoration"),
    (f"(29, 'p-29', 'X29', NULL, 'EUR', 'draft', 11, NULL, NULL, NULL, NULL, 1, {_T0}, {_T0})",
     "the currency CHECK"),
    (f"(30, 'p-30', 'X30', NULL, 'LYD', 'draft', 11, NULL, NULL, NULL, NULL, 0, {_T0}, {_T0})",
     "the version CHECK"),
    (f"(31, 'p-31', 'Draft plan', NULL, 'LYD', 'draft', 11, NULL, NULL, NULL, NULL, 1,"
     f" {_T0}, {_T0})", "uq_fee_plans_name"),
    (f"(32, 'p-32', 'X32', NULL, 'LYD', 'draft', 99, NULL, NULL, NULL, NULL, 1, {_T0}, {_T0})",
     "the created_by foreign key"),
)


def _load_correction():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _CORRECTION in p.name)
    spec = importlib.util.spec_from_file_location(f"p5m02r_{_CORRECTION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def _model_checks(model):
    return {
        constraint.name: " ".join(str(constraint.sqltext).split())
        for constraint in model.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def _model_lifecycle_sql():
    return _model_checks(FeePlan)[_LIFECYCLE]


def _rows(conn, table):
    return conn.execute(sa.text(f"SELECT * FROM {table} ORDER BY id")).fetchall()


def _table_sql(conn, table):
    sql = conn.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :t"), {"t": table}
    ).scalar_one()
    return " ".join(sql.split())


def _run_correction(conn, direction):
    """Run the correction the way SQLite's table-rebuild procedure requires:
    enforcement off outside any transaction, on again afterwards -- proved
    on, with every reference still resolving."""
    module, _ = _load_correction()
    conn.commit()
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(module, direction)()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(sa.text("PRAGMA foreign_keys=ON"))
    assert conn.execute(sa.text("PRAGMA foreign_keys")).scalar() == 1
    assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []


def _seeded_probe(name):
    tmp, engine, conn = _probe(name)
    _run(conn, "upgrade")
    conn.execute(sa.text(_PLAN_INSERT + _M02_PLANS))
    conn.execute(sa.text(_ITEM_INSERT + _M02_ITEMS))
    conn.commit()
    return tmp, engine, conn


def test_the_correction_revision_follows_m02_and_precedes_the_single_head():
    module, _ = _load_correction()
    assert (module.revision, module.down_revision) == (_CORRECTION, _REVISION)
    assert module.branch_labels is None and module.depends_on is None
    parents = {}
    for path in _MIGRATIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r"^revision = '([^']+)'", source, re.M).group(1)
        parents[revision] = re.search(
            r"^down_revision = (?:'([^']+)'|None)", source, re.M
        ).group(1)
    # Phase 5 / M03 follows the correction, M04 follows M03, M05 follows M04, M06 follows M05 and M07 follows M06, so the head is M07's.
    assert set(parents) - {p for p in parents.values() if p is not None} == {_HEAD}
    assert [r for r, p in parents.items() if p == _CORRECTION] == [_ASSIGNMENTS]
    assert [r for r, p in parents.items() if p == _REVISION] == [_CORRECTION]


def test_the_correction_replaces_only_the_lifecycle_check():
    module, source = _load_correction()
    code = _code(source)
    assert set(re.findall(r"\b(op\.\w+|batch_op\.\w+)\(", code)) == {
        "op.get_bind",
        "op.get_context",
        "op.batch_alter_table",
        "batch_op.alter_column",
        "op.drop_constraint",
        "op.create_check_constraint",
    }
    assert re.findall(r"op\.drop_constraint\(([^)]*)\)", code) == [
        "_LIFECYCLE_CHECK_NAME, 'fee_plans', type_='check'"
    ]
    assert re.findall(r"op\.create_check_constraint\(([^)]*)\)", code) == [
        "_LIFECYCLE_CHECK_NAME, 'fee_plans', lifecycle_sql"
    ]
    assert re.findall(r"batch_alter_table\(\s*'([^']+)'", code) == ["fee_plans"]
    assert module._LIFECYCLE_CHECK_NAME == _LIFECYCLE
    for forbidden in ("INSERT", "UPDATE ", "DELETE", "server_default", "ondelete", "onupdate",
                      "mysql_engine", "mysql_charset", "DECIMAL", "Numeric", "sa.Enum"):
        assert forbidden not in code, forbidden
    # The one read the revision issues is the downgrade's refusal count.
    assert re.findall(r"SELECT[^\"]*", code) == ["SELECT COUNT(*) FROM fee_plans"]


def test_the_correction_widens_only_the_draft_branch():
    module, _ = _load_correction()
    _, m02_source = _load_migration()
    assert module._LIFECYCLE_AFTER == _model_lifecycle_sql()
    assert module._LIFECYCLE_BEFORE in _flat(m02_source)
    before = module._LIFECYCLE_BEFORE.split(" OR (status = ")
    after = module._LIFECYCLE_AFTER.split(" OR (status = ")
    assert before[1:] == after[1:]
    assert len(after) == 3
    assert before[0] == (
        "(status = 'draft' AND first_activated_at IS NULL AND status_changed_at IS NULL)"
    )
    assert after[0] == "(status = 'draft' AND first_activated_at IS NULL)"


def test_the_rebuild_definition_is_exactly_the_model_table():
    module, _ = _load_correction()

    def ddl_lines(table):
        if "users" not in table.metadata.tables:
            sa.Table("users", table.metadata, sa.Column("id", sa.BigInteger(), primary_key=True))
        ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))
        return {line.strip().rstrip(",").strip() for line in ddl.splitlines()} - {"", ")"}

    def index_shape(table):
        return {(index.name, tuple(c.name for c in index.columns)) for index in table.indexes}

    after = module._fee_plans_table(module._LIFECYCLE_AFTER)
    before = module._fee_plans_table(module._LIFECYCLE_BEFORE)
    assert ddl_lines(after) == ddl_lines(FeePlan.__table__)
    assert index_shape(after) == index_shape(before) == index_shape(FeePlan.__table__)
    assert ddl_lines(before) ^ ddl_lines(after) == {
        f"CONSTRAINT {_LIFECYCLE} CHECK ({module._LIFECYCLE_BEFORE})",
        f"CONSTRAINT {_LIFECYCLE} CHECK ({module._LIFECYCLE_AFTER})",
    }


def test_the_correction_applies_and_reverses_preserving_every_m02_row():
    module, _ = _load_correction()
    tmp, engine, conn = _seeded_probe("correction.db")
    try:
        existing = _existing_rows(conn)
        plans, items = _rows(conn, "fee_plans"), _rows(conn, "fee_plan_items")
        assert [row[5] for row in plans] == ["draft", "active", "archived", "archived"]
        shapes = [_shape(conn, table) for table in _NEW_TABLES]
        items_sql = _table_sql(conn, "fee_plan_items")
        assert module._LIFECYCLE_BEFORE in _table_sql(conn, "fee_plans")
        _refused(conn, _RESTORED_DRAFT, "M02's lifecycle CHECK")

        _run_correction(conn, "upgrade")
        assert [_shape(conn, table) for table in _NEW_TABLES] == shapes
        assert _rows(conn, "fee_plans") == plans
        assert _rows(conn, "fee_plan_items") == items
        assert _existing_rows(conn) == existing
        assert _table_sql(conn, "fee_plan_items") == items_sql
        corrected = _table_sql(conn, "fee_plans")
        assert module._LIFECYCLE_AFTER in corrected
        assert module._LIFECYCLE_BEFORE not in corrected
        for expression in _model_checks(FeePlan).values():
            assert expression in corrected, expression
        assert "_alembic_tmp_fee_plans" not in inspect(conn).get_table_names()

        conn.execute(sa.text(_RESTORED_DRAFT))
        conn.commit()
        for values, rule in _STILL_REFUSED:
            _refused(conn, _PLAN_INSERT + values, rule)
        _refused(conn, _ITEM_INSERT + f"(9, 'i-9', 1, 'course', 'X', 0, 'active', NULL, NULL,"
                 f" 1, {_T0}, {_T0})", "the item amount CHECK")
        _refused(conn, _ITEM_INSERT + f"(9, 'i-9', 99, 'course', 'X', 5, 'active', NULL, NULL,"
                 f" 1, {_T0}, {_T0})", "the fee_plan foreign key after the rebuild")
        _refused(conn, "DELETE FROM fee_plans WHERE id = 1", "no cascade to a rebuilt plan's items")
        _refused(conn, "DELETE FROM users WHERE id = 11", "no cascade from an account")
        with_restored = _rows(conn, "fee_plans")

        # The downgrade refuses while a restored draft exists, before
        # touching anything.
        with pytest.raises(RuntimeError, match="Refusing to downgrade"):
            _run_correction(conn, "downgrade")
        assert _rows(conn, "fee_plans") == with_restored
        assert module._LIFECYCLE_AFTER in _table_sql(conn, "fee_plans")

        # With only rows valid under M02, the downgrade restores M02 exactly.
        conn.execute(sa.text("DELETE FROM fee_plans WHERE id = 10"))
        conn.commit()
        _run_correction(conn, "downgrade")
        assert [_shape(conn, table) for table in _NEW_TABLES] == shapes
        assert _rows(conn, "fee_plans") == plans
        assert _rows(conn, "fee_plan_items") == items
        assert _existing_rows(conn) == existing
        assert module._LIFECYCLE_BEFORE in _table_sql(conn, "fee_plans")
        _refused(conn, _RESTORED_DRAFT, "the restored M02 lifecycle CHECK")

        # And the round trip really is one. The CREATE TABLE text is compared
        # semantically, not byte for byte: a second batch rebuild may list
        # the same constraints in a different order.
        _run_correction(conn, "upgrade")
        assert [_shape(conn, table) for table in _NEW_TABLES] == shapes
        assert _rows(conn, "fee_plans") == plans
        assert _rows(conn, "fee_plan_items") == items
        again = _table_sql(conn, "fee_plans")
        assert module._LIFECYCLE_AFTER in again and module._LIFECYCLE_BEFORE not in again
        for expression in _model_checks(FeePlan).values():
            assert expression in again, expression
        assert len(again) == len(corrected)
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_the_sqlite_rebuild_refuses_to_run_under_enforced_foreign_keys():
    module, _ = _load_correction()
    tmp, engine, conn = _seeded_probe("enforced.db")
    try:
        plans = _rows(conn, "fee_plans")
        assert conn.execute(sa.text("PRAGMA foreign_keys")).scalar() == 1
        with pytest.raises(RuntimeError, match="PRAGMA foreign_keys=OFF"):
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        conn.rollback()
        assert module._LIFECYCLE_BEFORE in _table_sql(conn, "fee_plans")
        assert _rows(conn, "fee_plans") == plans
        assert "_alembic_tmp_fee_plans" not in inspect(conn).get_table_names()
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def _offline_mysql_correction(direction):
    module, _ = _load_correction()
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        getattr(module, direction)()
    return " ".join(buffer.getvalue().split())


@pytest.mark.parametrize("direction, attribute", [("upgrade", "_LIFECYCLE_AFTER"),
                                                  ("downgrade", "_LIFECYCLE_BEFORE")])
def test_the_offline_mysql_correction_only_drops_and_re_adds_the_lifecycle_check(
    direction, attribute
):
    module, _ = _load_correction()
    sql = _offline_mysql_correction(direction)
    drop = f"ALTER TABLE fee_plans DROP CHECK {_LIFECYCLE}"
    add = (f"ALTER TABLE fee_plans ADD CONSTRAINT {_LIFECYCLE}"
           f" CHECK ({getattr(module, attribute)})")
    assert sql.index(drop) < sql.index(add)
    assert sql.count("ALTER TABLE") == 2
    for forbidden in ("CREATE TABLE", "DROP TABLE", "INDEX", "fee_plan_items", "INSERT",
                      "UPDATE ", "DELETE", "FOREIGN KEY", "UNIQUE", "SELECT", "ENGINE=",
                      "CHARSET", "RENAME"):
        assert forbidden not in sql, forbidden
