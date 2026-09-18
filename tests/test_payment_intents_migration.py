"""Phase 5 / M06 migration checks for ``payment_intents``.

The execution probes use an isolated temporary SQLite database seeded with
minimal existing tables and representative rows -- accounts, Groups,
Enrollments, fee plans and assignments -- upgraded by the M04 revision and
given representative M04 invoices, lines, sequences and audit events, then
upgraded by the M05 revision and given representative M05 payments, a voided
receipt, a receipt sequence and M05 audit events. They are reused from
``tests/test_payments_migration.py`` (its private constants and helpers only),
so "every existing financial row stays unchanged" is executed rather than
asserted. Both directions run with foreign keys **enforced** and
``PRAGMA foreign_key_check`` asserted after each.

The MySQL checks compile dialect DDL and render the revision's offline
(``--sql``) MySQL script; neither connects to a database. The real upgrade
against the authorized development MySQL database is a separate, manually
executed check.
"""

import io
import re

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

import tests.test_payments_migration as m05
from app.extensions import db
from app.models import PaymentIntent, PaymentIntentStatus

_MIGRATIONS = m05._MIGRATIONS
_REVISION = "d4f7a2c9e1b6"
_DOWN_REVISION = "c5e8f2a7d914"
_TABLE = "payment_intents"
_FINANCIAL_TABLES = ("invoice_number_sequences", "invoices", "invoice_items",
                     "payment_audit_events", "payment_transactions", "receipt_number_sequences",
                     "receipts")

_EXPECTED = {
    "columns": {"id", "public_id", "invoice_id", "provider", "provider_reference",
                "idempotency_key", "status", "currency_code", "amount", "created_by_id",
                "provider_result_at", "provider_result_by_id", "terminal_at", "cancelled_by_id",
                "version", "created_at", "updated_at"},
    "nullable": {"provider_result_at", "provider_result_by_id", "terminal_at", "cancelled_by_id"},
    "checks": {"ck_payment_intents_status_valid", "ck_payment_intents_provider_valid",
               "ck_payment_intents_currency_code", "ck_payment_intents_amount_range",
               "ck_payment_intents_version_positive",
               "ck_payment_intents_provider_reference_present",
               "ck_payment_intents_idempotency_key_length",
               "ck_payment_intents_provider_result_pair", "ck_payment_intents_terminal_state",
               "ck_payment_intents_lifecycle_state", "ck_payment_intents_timestamps_ordered"},
    "indexes": {"ix_payment_intents_invoice_id_id": ["invoice_id", "id"],
                "ix_payment_intents_status_id": ["status", "id"],
                "ix_payment_intents_created_by_id": ["created_by_id"],
                "ix_payment_intents_provider_result_by_id": ["provider_result_by_id"],
                "ix_payment_intents_cancelled_by_id": ["cancelled_by_id"]},
    "fks": {("invoice_id", "invoices"), ("created_by_id", "users"),
            ("provider_result_by_id", "users"), ("cancelled_by_id", "users")},
    "uniques": sorted([("", ("public_id",)),
                       ("uq_payment_intents_idempotency_key", ("idempotency_key",)),
                       ("uq_payment_intents_provider_reference", ("provider_reference",))]),
}

#: Column-name parts that would mean card, bank or credential data.
_PROHIBITED_PARTS = (
    "card", "pan", "cvv", "cvc", "pin", "iban", "swift", "account", "token", "secret",
    "password", "credential", "customer", "webhook", "proof", "upload", "refund", "signature",
    "total", "paid", "outstanding", "balance",
)


def _load_migration():
    return m05._load(_REVISION, "p5m06")


def _model_checks():
    return {
        constraint.name: " ".join(str(constraint.sqltext).split())
        for constraint in PaymentIntent.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


# ===========================================================================
# Revision identity and what the revision does
# ===========================================================================


def test_revision_identifiers_and_one_linear_head():
    module, _ = _load_migration()
    assert (module.revision, module.down_revision) == (_REVISION, _DOWN_REVISION)
    assert module.branch_labels is None and module.depends_on is None
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


def test_the_revision_creates_one_table_and_alters_nothing():
    _, source = _load_migration()
    code = m05._code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == [_TABLE]
    assert set(re.findall(r"\b(op\.\w+|batch_op\.\w+)\(", code)) == {
        "op.create_table", "op.create_index", "op.get_bind", "op.get_context", "op.drop_table"}
    for forbidden in ("op.execute", "op.bulk_insert", "alter_column", "add_column", "drop_column",
                      "batch_alter_table", "drop_constraint", "ondelete", "onupdate",
                      "mysql_engine", "mysql_charset", "server_default", "Float", "Numeric(",
                      "Enum(", "trigger", "TRIGGER", "INSERT", "UPDATE ", "DELETE FROM", "card",
                      "cvv", "cvc", "account", "secret", "password", "customer", "webhook",
                      "refund"):
        assert forbidden not in code, forbidden
    assert re.findall(r"SELECT[^\"]*", code) == ["SELECT COUNT(*) FROM payment_intents"]
    upgrade = m05._function(code, "upgrade")
    for forbidden in ("drop_table", "drop_index", "get_bind"):
        assert forbidden not in upgrade, forbidden


# ===========================================================================
# Execution against an isolated SQLite database
# ===========================================================================

_T3 = "'2026-06-04 09:00:00'"
_T4 = "'2026-06-04 10:00:00'"
_KEY_1, _KEY_2, _KEY_3 = "1" * 64, "2" * 64, "3" * 64
_INTENT = ("INSERT INTO payment_intents (id, public_id, invoice_id, provider, provider_reference,"
           " idempotency_key, status, currency_code, amount, created_by_id, provider_result_at,"
           " provider_result_by_id, terminal_at, cancelled_by_id, version, created_at, updated_at)"
           " VALUES ")
_M06_ROWS = [
    _INTENT + f"(1, 'pi-1', 2, 'mock', 'mock_pi_1', '{_KEY_1}', 'cancelled', 'LYD', 1150.5, 11,"
    f" NULL, NULL, {_T4}, 12, 2, {_T3}, {_T4}),"
    f" (2, 'pi-2', 2, 'mock', 'mock_pi_2', '{_KEY_2}', 'provider_succeeded', 'LYD', 1150.5, 11,"
    f" {_T4}, 11, NULL, NULL, 2, {_T3}, {_T4})",
]


def _financial_rows(conn):
    rows = {table: m05._rows(conn, table) for table in _FINANCIAL_TABLES if table !=
            "payment_audit_events"}
    rows["payment_audit_events"] = m05._rows(
        conn, "payment_audit_events",
        ", ".join(sorted(m05._EXPECTED["payment_audit_events"]["columns"])))
    return rows


def _probe(name):
    """An isolated database at the M05 revision holding representative M04 and
    M05 financial history, foreign keys enforced."""
    tmp, engine, conn = m05._probe(name)
    m05_module, _ = m05._load(_DOWN_REVISION, "p5m05probe")
    m05._run(conn, m05_module, "upgrade")
    for statement in m05._M05_ROWS:
        conn.execute(sa.text(statement))
    conn.commit()
    return tmp, engine, conn


def test_migration_applies_and_reverses_preserving_every_existing_row():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("probe.db")
    try:
        existing, financial = m05._existing_rows(conn), _financial_rows(conn)
        tables_before = set(inspect(conn).get_table_names())
        shapes_before = {table: m05._shape(conn, table) for table in _FINANCIAL_TABLES}
        assert financial["payment_transactions"] and financial["receipts"]

        m05._run(conn, module, "upgrade")
        assert set(inspect(conn).get_table_names()) - tables_before == {_TABLE}
        assert m05._shape(conn, _TABLE) == _EXPECTED
        assert conn.execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar_one() == 0
        assert {table: m05._shape(conn, table) for table in _FINANCIAL_TABLES} == shapes_before
        assert m05._existing_rows(conn) == existing
        assert _financial_rows(conn) == financial

        for statement in _M06_ROWS:
            conn.execute(sa.text(statement))
        conn.commit()
        pending = (f"(9, 'pi-9', 2, 'mock', 'mock_pi_9', '{_KEY_3}', 'pending', 'LYD', 10, 11,"
                   f" NULL, NULL, NULL, NULL, 1, {_T3}, {_T3})")
        for statement, rule in (
            (_INTENT + pending.replace("'pending'", "'paid'"), "the status CHECK"),
            (_INTENT + pending.replace("'mock', 'mock_pi_9'", "'stripe', 'mock_pi_9'"),
             "the provider CHECK"),
            (_INTENT + pending.replace("'LYD'", "'USD'"), "the currency CHECK"),
            (_INTENT + pending.replace(", 10, 11,", ", 100000, 11,"), "the amount CHECK"),
            (_INTENT + pending.replace(f"'{_KEY_3}'", "'short'"), "the key length CHECK"),
            (_INTENT + pending.replace("'mock_pi_9'", "''"), "the reference CHECK"),
            (_INTENT + pending.replace("'mock_pi_9'", "'mock_pi_1'"), "the unique reference"),
            (_INTENT + pending.replace(f"'{_KEY_3}'", f"'{_KEY_1}'"), "the unique key"),
            (_INTENT + pending.replace("'pi-9', 2,", "'pi-9', 99,"), "the invoice foreign key"),
            (_INTENT + pending.replace("NULL, NULL, 1,", f"{_T3}, NULL, 1,"),
             "the terminal-state CHECK"),
            (_INTENT + pending.replace("NULL, NULL, NULL, NULL, 1", f"{_T4}, 11, NULL, NULL, 2"),
             "the lifecycle CHECK"),
            (_INTENT + pending.replace(f"1, {_T3}, {_T3})", f"1, {_T4}, {_T3})"),
             "the timestamps CHECK"),
            ("DELETE FROM invoices WHERE id = 2", "no cascade from an invoice"),
            ("DELETE FROM users WHERE id = 12", "no cascade from an account"),
        ):
            m05._refused(conn, statement, rule)
        with_intents = m05._rows(conn, _TABLE)
        assert len(with_intents) == 2

        # The downgrade refuses while any intent exists, before touching anything.
        with pytest.raises(RuntimeError, match="Refusing to downgrade"):
            with Operations.context(MigrationContext.configure(conn)):
                module.downgrade()
        conn.rollback()
        assert m05._rows(conn, _TABLE) == with_intents
        assert m05._shape(conn, _TABLE) == _EXPECTED

        # With no intent, the downgrade drops the table and nothing else.
        conn.execute(sa.text(f"DELETE FROM {_TABLE}"))
        conn.commit()
        m05._run(conn, module, "downgrade")
        assert set(inspect(conn).get_table_names()) == tables_before
        assert {table: m05._shape(conn, table) for table in _FINANCIAL_TABLES} == shapes_before
        assert m05._existing_rows(conn) == existing
        assert _financial_rows(conn) == financial
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_a_second_upgrade_after_a_downgrade_rebuilds_the_same_shape():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("probe2.db")
    try:
        existing, financial = m05._existing_rows(conn), _financial_rows(conn)
        shapes = []
        for _ in range(2):
            m05._run(conn, module, "upgrade")
            shapes.append(m05._shape(conn, _TABLE))
            m05._run(conn, module, "downgrade")
            assert m05._existing_rows(conn) == existing and _financial_rows(conn) == financial
        assert shapes[0] == shapes[1] == _EXPECTED
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


# ===========================================================================
# Model / migration agreement, and MySQL DDL
# ===========================================================================


def test_the_revision_declares_every_expected_column_and_check():
    _, source = _load_migration()
    block = m05._table_block(source, _TABLE)
    assert set(re.findall(r"sa\.Column\('([^']+)'", block)) == _EXPECTED["columns"]
    flat = m05._flat(source)
    checks = _model_checks()
    assert set(checks) == _EXPECTED["checks"]
    for name, expression in checks.items():
        assert f"name='{name}'" in block, name
        assert expression in flat, (name, expression)


def test_the_migrations_closed_sets_match_the_application_enums():
    _, source = _load_migration()
    flat = m05._flat(source)
    expected = "status IN (" + ", ".join(f"'{m.value}'" for m in PaymentIntentStatus) + ")"
    assert expected in flat
    assert "provider IN ('mock')" in flat
    for fragment in ("sa.Column('amount', sa.DECIMAL(precision=19, scale=4), nullable=False)",
                     "sa.Column('provider_reference', sa.String(length=64), nullable=False)",
                     "sa.Column('idempotency_key', sa.String(length=64), nullable=False)",
                     "sa.Column('terminal_at', sa.DateTime(), nullable=True)"):
        assert fragment in source, fragment


def test_the_model_and_migration_agree(app):
    _, source = _load_migration()
    with app.app_context():
        inspector = inspect(db.engine)
        actual = {c["name"]: str(c["nullable"]) for c in inspector.get_columns(_TABLE)}
        actual.pop("id", None)
        declared = dict(re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)",
                                   m05._table_block(source, _TABLE)))
        declared.pop("id", None)
        assert declared == actual
        shape = {
            "columns": {c["name"] for c in inspector.get_columns(_TABLE)},
            "nullable": {c["name"] for c in inspector.get_columns(_TABLE) if c["nullable"]},
            "indexes": {i["name"]: i["column_names"] for i in inspector.get_indexes(_TABLE)},
            "uniques": sorted((u["name"] or "", tuple(u["column_names"]))
                              for u in inspector.get_unique_constraints(_TABLE)),
            "checks": {c["name"] for c in inspector.get_check_constraints(_TABLE)},
            "fks": {(fk["constrained_columns"][0], fk["referred_table"])
                    for fk in inspector.get_foreign_keys(_TABLE)},
        }
        assert shape == _EXPECTED


_DDL_FRAGMENTS = (
    "id BIGINT NOT NULL AUTO_INCREMENT", "public_id VARCHAR(36) NOT NULL",
    "invoice_id BIGINT NOT NULL", "provider VARCHAR(16) NOT NULL",
    "provider_reference VARCHAR(64) NOT NULL", "idempotency_key VARCHAR(64) NOT NULL",
    "status VARCHAR(32) NOT NULL", "currency_code VARCHAR(3) NOT NULL",
    "amount DECIMAL(19, 4) NOT NULL", "created_by_id BIGINT NOT NULL",
    "provider_result_at DATETIME,", "provider_result_by_id BIGINT,", "terminal_at DATETIME,",
    "cancelled_by_id BIGINT,", "version INTEGER NOT NULL", "created_at DATETIME NOT NULL",
    "FOREIGN KEY(invoice_id) REFERENCES invoices (id)",
    "FOREIGN KEY(created_by_id) REFERENCES users (id)",
    "FOREIGN KEY(provider_result_by_id) REFERENCES users (id)",
    "FOREIGN KEY(cancelled_by_id) REFERENCES users (id)",
    "CONSTRAINT uq_payment_intents_provider_reference UNIQUE (provider_reference)",
    "CONSTRAINT uq_payment_intents_idempotency_key UNIQUE (idempotency_key)",
    "UNIQUE (public_id)",
)


def test_mysql_ddl_compiles_without_a_connection():
    ddl = str(CreateTable(PaymentIntent.__table__).compile(dialect=mysql.dialect()))
    flat = " ".join(ddl.split())
    for fragment in _DDL_FRAGMENTS:
        assert fragment in flat, fragment
    for name, expression in _model_checks().items():
        assert f"CONSTRAINT {name} CHECK ({expression})" in flat, name
    for forbidden in ("ON DELETE", "ON UPDATE", "DATETIME(", "ENUM(", "FLOAT", "DOUBLE",
                      "ENGINE=", "CHARSET", "COLLATE", "TRIGGER"):
        assert forbidden not in ddl.upper(), forbidden
    # No engine, charset or collation is declared: the table inherits the
    # server's defaults exactly like every earlier table.
    assert PaymentIntent.__table__.kwargs == {}


def test_no_column_is_shaped_for_card_bank_or_credential_data():
    for column in PaymentIntent.__table__.columns:
        for part in _PROHIBITED_PARTS:
            assert part not in column.name.split("_"), (column.name, part)


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


def test_the_offline_mysql_upgrade_creates_one_table_then_its_indexes():
    sql = _offline_mysql("upgrade")
    steps = [sql.index(f"CREATE TABLE {_TABLE} (")]
    steps += [sql.index(f"CREATE INDEX {name} ON {_TABLE}") for name in _EXPECTED["indexes"]]
    assert steps == sorted(steps)
    assert sql.count("CREATE TABLE") == 1 and sql.count("CREATE INDEX") == 5
    for forbidden in ("ALTER TABLE", "DROP ", "ON DELETE", "ON UPDATE", "ENGINE=", "CHARSET",
                      "DELETE FROM", "INSERT INTO", "UPDATE ", "TRIGGER", "SELECT"):
        assert forbidden not in sql, forbidden


def test_the_offline_mysql_downgrade_drops_only_the_table():
    sql = _offline_mysql("downgrade")
    assert f"DROP TABLE {_TABLE}" in sql
    assert sql.count("DROP TABLE") == 1
    for forbidden in ("CREATE", "ALTER TABLE", "DROP INDEX", "DELETE FROM", "INSERT", "UPDATE ",
                      "SELECT", "invoices", "payment_transactions", "receipts"):
        assert forbidden not in sql, forbidden
