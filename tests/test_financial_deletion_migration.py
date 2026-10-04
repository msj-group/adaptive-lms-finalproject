"""Phase 5 / M10 migration checks (``b3d8f1a6c472``).

The revision adds the visible-deletion tombstone -- ``deleted_at``,
``deleted_by_id`` and ``deletion_reason``, a CHECK, a named foreign key and two
indexes -- to ``invoices``, ``payment_transactions`` and ``receipts``, and
widens four ``payment_audit_events`` CHECKs to the four deletion kinds. Every
replaced CHECK's "before" text is compared with the M07 revision and its
"after" text with the model.

The execution probes reuse the M05 to M07 suites' isolated temporary SQLite
database, carried to M07 with representative M04 to M07 history. SQLite can
rebuild a table other tables reference only with foreign keys off, so the
revision requires ``PRAGMA foreign_keys=OFF`` there and refuses otherwise; the
probes run it that way, prove ``PRAGMA foreign_key_check`` empty after every
run, and turn enforcement back on to prove the new constraints.

The MySQL checks render the revision's offline (``--sql``) MySQL script; they
do not connect to a database. The real upgrade of the authorized development
MySQL database is a separate, manually executed check.
"""

import io
import re

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

import tests.payment_intent_fixtures as ix
import tests.test_payments_migration as m05
import tests.test_verified_webhooks_migration as m07
from app.extensions import db
from app.models import Invoice, PaymentAuditEvent, PaymentTransaction, Receipt

_REVISION = "b3d8f1a6c472"
_DOWN_REVISION = "e9c4b2d7a1f3"
_TOMBSTONED = ("invoices", "payment_transactions", "receipts")
_EVENTS = "payment_audit_events"
_ALTERED = _TOMBSTONED + (_EVENTS,)
_MODELS = {
    "invoices": Invoice,
    "payment_transactions": PaymentTransaction,
    "receipts": Receipt,
    _EVENTS: PaymentAuditEvent,
}
_TOMBSTONE = ("deleted_at", "deleted_by_id", "deletion_reason")
_DELETION_KINDS = ("invoice_deleted", "payment_deleted", "payment_replaced", "receipt_deleted")
_HISTORY_TABLES = ("invoice_number_sequences", "invoice_items", "receipt_number_sequences",
                   "payment_intents", "payment_provider_events") + _ALTERED


@pytest.fixture
def app():
    application = ix.make_app()
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def _load_migration():
    return m05._load(_REVISION, "p5m10")


def _norm(text):
    return " ".join(str(text).split())


def _model_checks(model):
    return {c.name: _norm(c.sqltext) for c in model.__table__.constraints
            if isinstance(c, sa.CheckConstraint)}


def _table_checks(table):
    return {c.name: _norm(c.sqltext) for c in table.constraints
            if isinstance(c, sa.CheckConstraint)}


# ===========================================================================
# Revision identity and what the revision does
# ===========================================================================


def test_revision_identifiers_and_one_linear_head():
    module, _ = _load_migration()
    assert (module.revision, module.down_revision) == (_REVISION, _DOWN_REVISION)
    assert module.branch_labels is None and module.depends_on is None
    parents = {}
    for path in m05._MIGRATIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r"^revision = '([^']+)'", source, re.M).group(1)
        parents[revision] = re.search(r"^down_revision = (?:'([^']+)'|None)", source, re.M).group(1)
    heads = set(parents) - {p for p in parents.values() if p is not None}
    # Phase 6 / M01, M02A and the Phase 6 replacement follow, so the single
    # head is now the Phase 6 replacement's; M01 still follows this one.
    assert heads == {"d574ab56594f"}
    assert [r for r, p in parents.items() if p == _REVISION] == ["f2a6d1c84b37"]
    assert [r for r, p in parents.items() if p == _DOWN_REVISION] == [_REVISION]
    assert len([r for r, p in parents.items() if p is None]) == 1


def test_the_revision_alters_four_tables_and_creates_or_drops_none():
    _, source = _load_migration()
    code = m05._code(source)
    for forbidden in ("op.create_table", "op.drop_table", "op.execute", "op.bulk_insert",
                      "server_default", "ondelete", "onupdate", "mysql_engine", "mysql_charset",
                      "Enum(", "Float", "trigger", "TRIGGER", "INSERT", "UPDATE ", "DELETE FROM",
                      "restore", "card", "secret", "password", "reference_number"):
        assert forbidden not in code, forbidden
    assert set(re.findall(r"op\.batch_alter_table\(\s*(_\w+|table)", code)) == {"table", "_EVENTS"}
    assert re.findall(r"\"(SELECT[^\"]*)", code) == [
        "SELECT (SELECT COUNT(*) FROM invoices WHERE deleted_at IS NOT NULL"]
    upgrade = m05._function(code, "upgrade")
    for forbidden in ("drop_column", "drop_index", "drop_table", "_M10_ROWS_SQL"):
        assert forbidden not in upgrade, forbidden


def test_every_check_starts_where_m07_left_it_and_ends_at_the_model():
    module, _ = _load_migration()
    m07_module, _ = m07._load_migration()
    m07_after = {name: new for name, _old, new in m07_module._AUDIT_CHECKS}
    for name, old, new in module._AUDIT_CHECKS:
        assert m07_after[name] == old, name
        assert _model_checks(PaymentAuditEvent)[name] == new, name
        assert old != new, name
        # Each replaced expression only gains the four deletion kinds.
        for kind in _DELETION_KINDS:
            assert kind not in old and kind in new, (name, kind)
    for table, (name, sql) in module._DELETION_CHECKS.items():
        assert _model_checks(_MODELS[table])[name] == sql, name
    kept = {name: sql for name, sql in module._AUDIT_KEPT_CHECKS}
    assert kept["ck_payment_audit_events_actor_origin"] == m07_module._ACTOR_ORIGIN_SQL
    for name, sql in kept.items():
        assert _model_checks(PaymentAuditEvent)[name] == sql, name


def test_every_rebuild_definition_carries_exactly_the_previous_schema():
    module, _ = _load_migration()
    for table, build in module._TABLES.items():
        model = _MODELS[table]
        name, _sql = module._DELETION_CHECKS[table]
        expected = {key: value for key, value in _model_checks(model).items() if key != name}
        assert _table_checks(build()) == expected, table
        declared = {c.name: c.nullable for c in build().columns}
        modelled = {c.name: c.nullable for c in model.__table__.columns
                    if c.name not in _TOMBSTONE}
        assert declared == modelled, table
        plain = build(with_plain_tombstone=True)
        assert {c.name for c in plain.columns} == set(declared) | set(_TOMBSTONE)
        assert all(plain.c[column].nullable for column in _TOMBSTONE)
        assert not [fk for fk in plain.foreign_keys if fk.parent.name in _TOMBSTONE]
        assert not [i for i in plain.indexes if set(i.columns.keys()) & set(_TOMBSTONE)]
        # The previous revisions' indexes, exactly.
        assert {i.name for i in build().indexes} == {
            i.name for i in model.__table__.indexes
            if i.name not in (f"ix_{table}_deleted_at_id", f"ix_{table}_deleted_by_id")}
    events = dict(_table_checks(module._audit_events_table(True)))
    assert events == _model_checks(PaymentAuditEvent)
    old = {name: before for name, before, _after in module._AUDIT_CHECKS}
    assert _table_checks(module._audit_events_table(False)) == {
        name: old.get(name, sql) for name, sql in _model_checks(PaymentAuditEvent).items()}


# ===========================================================================
# Execution against an isolated SQLite database
# ===========================================================================

_T6 = "'2026-06-06 09:00:00'"


def _probe(name):
    """An isolated database at the M07 revision holding representative M04
    to M07 history, with foreign keys off."""
    tmp, engine, conn = m07._probe(name)
    m07_module, _ = m07._load_migration()
    m07._run(conn, m07_module, "upgrade")
    for statement in m07._M07_ROWS:
        conn.execute(sa.text(statement))
    conn.commit()
    return tmp, engine, conn


def _columns(conn, table):
    return [c["name"] for c in inspect(conn).get_columns(table)]


def _checks(conn, table):
    return {c["name"]: _norm(c["sqltext"]) for c in inspect(conn).get_check_constraints(table)}


def _history(conn, columns):
    rows = m05._existing_rows(conn)
    for table, names in columns.items():
        rows[table] = conn.execute(
            sa.text(f"SELECT {', '.join(names)} FROM {table} ORDER BY id")).fetchall()
    return rows


def _model_shapes(app):
    with app.app_context(), db.engine.connect() as model:
        return ({table: m05._shape(model, table) for table in _MODELS},
                {table: _checks(model, table) for table in _MODELS})


def _refuses_downgrade(conn, module):
    with pytest.raises(RuntimeError, match="Refusing to downgrade"):
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
    conn.rollback()


def _delete(table, row_id, moment=_T6, actor="11", reason="'Entered twice'"):
    return (f"UPDATE {table} SET deleted_at = {moment}, deleted_by_id = {actor},"
            f" deletion_reason = {reason}, updated_at = {_T6} WHERE id = {row_id}")


def _undelete(table):
    return (f"UPDATE {table} SET deleted_at = NULL, deleted_by_id = NULL,"
            " deletion_reason = NULL")


def _event(n, kind, payment="NULL", receipt="NULL", reason="'Entered twice'", before=3, after=3):
    return m05._EVENT + (
        f"({n}, 2, 11, '{kind}', {_T6}, {before}, {after}, {reason}, '{{}}', '{{}}',"
        f" {payment}, {receipt})")


#: One of every row only M10 can represent, in insertion order.
_M10_ROWS = [
    _delete("receipts", 1),
    _delete("payment_transactions", 2),
    _delete("invoices", 2),
    _event(20, "receipt_deleted", payment=1, receipt=1),
    _event(21, "payment_deleted", payment=2),
    _event(22, "payment_replaced", payment=1),
    _event(23, "invoice_deleted", before=3, after=4),
]
_M10_CLEANUP = [
    "DELETE FROM payment_audit_events WHERE id >= 20",
    _undelete("invoices"),
    _undelete("payment_transactions"),
    _undelete("receipts"),
]
_SINGLE_M10_ROWS = {
    "deleted invoice": _delete("invoices", 2),
    "deleted payment": _delete("payment_transactions", 3),
    "deleted receipt": _delete("receipts", 2),
    "deletion event": _event(24, "payment_deleted", payment=2),
}


def test_migration_applies_preserves_history_and_refuses_to_destroy_it(app):
    module, _ = _load_migration()
    model_shapes, model_checks = _model_shapes(app)
    tmp, engine, conn = _probe("probe10.db")
    try:
        columns = {table: _columns(conn, table) for table in _HISTORY_TABLES}
        history = _history(conn, columns)
        assert history["invoices"] and history["payment_transactions"] and history["receipts"]
        tables_before = set(inspect(conn).get_table_names())
        shapes_before = {table: m05._shape(conn, table) for table in _ALTERED}
        checks_before = {table: _checks(conn, table) for table in _ALTERED}

        m07._run(conn, module, "upgrade")
        assert set(inspect(conn).get_table_names()) == tables_before
        for table in _MODELS:
            assert m05._shape(conn, table) == model_shapes[table], table
            assert _checks(conn, table) == model_checks[table], table
        assert _history(conn, columns) == history
        for table in _TOMBSTONED:
            assert conn.execute(sa.text(
                f"SELECT count(*) FROM {table} WHERE deleted_at IS NOT NULL"
                " OR deleted_by_id IS NOT NULL OR deletion_reason IS NOT NULL")).scalar_one() == 0
            assert f"_alembic_tmp_{table}" not in inspect(conn).get_table_names()

        # Every row only M10 can represent is accepted, with foreign keys on.
        m07._foreign_keys(conn, True)
        for statement in _M10_ROWS:
            conn.execute(sa.text(statement))
        conn.commit()
        assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []
        for statement, rule in (
            (_delete("invoices", 1), "ck_invoices_deletion_state"),
            (_delete("payment_transactions", 3, reason="NULL"),
             "ck_payment_transactions_deletion_state"),
            (_delete("payment_transactions", 3, reason="''"),
             "ck_payment_transactions_deletion_state"),
            (_delete("payment_transactions", 3, moment="'2026-01-01 00:00:00'"),
             "ck_payment_transactions_deletion_state"),
            (_delete("receipts", 2, actor="NULL"), "ck_receipts_deletion_state"),
            (_delete("receipts", 2, actor="99"), "the deleting account's foreign key"),
            ("UPDATE payment_transactions SET deleted_by_id = 11 WHERE id = 3",
             "ck_payment_transactions_deletion_state"),
            (_event(25, "invoice_deleted", reason="NULL", before=4, after=5),
             "ck_payment_audit_events_reason_required"),
            (_event(25, "invoice_deleted", before=4, after=4),
             "ck_payment_audit_events_version_transition"),
            (_event(25, "payment_deleted", payment=1, receipt=1),
             "ck_payment_audit_events_subject_links"),
            (_event(25, "receipt_deleted", payment=1),
             "ck_payment_audit_events_subject_links"),
            # An unknown kind breaks several CHECKs; SQLite names whichever it meets first.
            (_event(25, "invoice_purged"), "an unknown kind"),
        ):
            m07._refused(conn, statement, rule)
        with_m10 = _history(conn, {table: _columns(conn, table) for table in _MODELS})

        # The downgrade refuses while any M10 row exists, before changing anything.
        m07._foreign_keys(conn, False)
        _refuses_downgrade(conn, module)
        assert _history(conn, {table: _columns(conn, table) for table in _MODELS}) == with_m10
        for table in _MODELS:
            assert m05._shape(conn, table) == model_shapes[table], table

        # ... and each kind of M10 row alone is enough.
        for statement in _M10_CLEANUP:
            conn.execute(sa.text(statement))
        conn.commit()
        for statement in _SINGLE_M10_ROWS.values():
            conn.execute(sa.text(statement))
            conn.commit()
            _refuses_downgrade(conn, module)
            for cleanup in _M10_CLEANUP:
                conn.execute(sa.text(cleanup))
            conn.commit()

        # The probe's own deletions moved ``updated_at``; everything else is as it was.
        cleaned = _history(conn, columns)

        # A downgrade with foreign keys enforced is refused before any change.
        m07._foreign_keys(conn, True)
        with pytest.raises(RuntimeError, match="foreign keys disabled"):
            with Operations.context(MigrationContext.configure(conn)):
                module.downgrade()
        conn.rollback()
        assert "deleted_at" in _columns(conn, "invoices")

        # With none, the downgrade restores the M07 schema exactly.
        m07._foreign_keys(conn, False)
        m07._run(conn, module, "downgrade")
        assert set(inspect(conn).get_table_names()) == tables_before
        assert {table: m05._shape(conn, table) for table in _ALTERED} == shapes_before
        assert {table: _checks(conn, table) for table in _ALTERED} == checks_before
        assert _history(conn, columns) == cleaned
        m07._foreign_keys(conn, True)
        m07._refused(conn, _event(26, "invoice_deleted", before=3, after=4),
                     "a deletion kind the M07 schema does not know")
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_the_upgrade_refuses_to_run_with_foreign_keys_enforced():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("probe10fk.db")
    try:
        m07._foreign_keys(conn, True)
        shapes = {table: m05._shape(conn, table) for table in _ALTERED}
        with pytest.raises(RuntimeError, match="foreign keys disabled"):
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        conn.rollback()
        assert {table: m05._shape(conn, table) for table in _ALTERED} == shapes
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_a_second_upgrade_after_a_downgrade_rebuilds_the_same_schema():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("probe10again.db")
    try:
        columns = {table: _columns(conn, table) for table in _HISTORY_TABLES}
        history = _history(conn, columns)
        results = []
        for _ in range(2):
            m07._run(conn, module, "upgrade")
            results.append(({table: m05._shape(conn, table) for table in _MODELS},
                            {table: _checks(conn, table) for table in _MODELS}))
            m07._run(conn, module, "downgrade")
            assert _history(conn, columns) == history
        assert results[0] == results[1]
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


# ===========================================================================
# MySQL DDL, offline
# ===========================================================================


def _offline_mysql(direction):
    module, _ = _load_migration()
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        getattr(module, direction)()
    return [" ".join(s.split()) for s in buffer.getvalue().split(";") if s.strip()]


def test_the_offline_mysql_upgrade_adds_in_place_index_before_foreign_key():
    module, _ = _load_migration()
    statements = _offline_mysql("upgrade")
    expected = []
    for table in _TOMBSTONED:
        name, sql = module._DELETION_CHECKS[table]
        expected += [
            f"ALTER TABLE {table} ADD COLUMN deleted_at DATETIME",
            f"ALTER TABLE {table} ADD COLUMN deleted_by_id BIGINT",
            f"ALTER TABLE {table} ADD COLUMN deletion_reason VARCHAR(500)",
            f"CREATE INDEX ix_{table}_deleted_by_id ON {table} (deleted_by_id)",
            f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_deleted_by_id FOREIGN KEY"
            "(deleted_by_id) REFERENCES users (id)",
            f"CREATE INDEX ix_{table}_deleted_at_id ON {table} (deleted_at, id)",
            f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({sql})",
        ]
    for name, _old, new in module._AUDIT_CHECKS:
        expected += [f"ALTER TABLE {_EVENTS} DROP CHECK {name}",
                     f"ALTER TABLE {_EVENTS} ADD CONSTRAINT {name} CHECK ({new})"]
    assert statements == expected
    sql = " ; ".join(statements)
    for forbidden in ("DROP TABLE", "DROP COLUMN", "DROP INDEX", "DROP FOREIGN KEY", "MODIFY",
                      "ON DELETE", "ON UPDATE", "ENGINE=", "CHARSET", "DELETE FROM",
                      "INSERT INTO", "UPDATE ", "TRIGGER", "SELECT", "DATETIME NOT NULL",
                      "BIGINT NOT NULL", "VARCHAR(500) NOT NULL"):
        assert forbidden not in sql, forbidden


def test_the_offline_mysql_downgrade_restores_the_m07_schema():
    module, _ = _load_migration()
    statements = _offline_mysql("downgrade")
    expected = []
    for name, old, _new in module._AUDIT_CHECKS:
        expected += [f"ALTER TABLE {_EVENTS} DROP CHECK {name}",
                     f"ALTER TABLE {_EVENTS} ADD CONSTRAINT {name} CHECK ({old})"]
    for table in reversed(_TOMBSTONED):
        name, _sql = module._DELETION_CHECKS[table]
        expected += [
            f"ALTER TABLE {table} DROP CHECK {name}",
            f"ALTER TABLE {table} DROP FOREIGN KEY fk_{table}_deleted_by_id",
            f"DROP INDEX ix_{table}_deleted_at_id ON {table}",
            f"DROP INDEX ix_{table}_deleted_by_id ON {table}",
            f"ALTER TABLE {table} DROP COLUMN deletion_reason",
            f"ALTER TABLE {table} DROP COLUMN deleted_by_id",
            f"ALTER TABLE {table} DROP COLUMN deleted_at",
        ]
    assert statements == expected
    sql = " ; ".join(statements)
    for forbidden in ("DROP TABLE", "CREATE", "DELETE FROM", "INSERT", "UPDATE ", "SELECT",
                      "TRIGGER"):
        assert forbidden not in sql, forbidden
