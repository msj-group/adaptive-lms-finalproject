"""Phase 5 / M07 migration checks (``e9c4b2d7a1f3``).

The revision creates ``payment_provider_events`` and extends
``payment_intents``, ``payment_transactions``, ``receipts`` and
``payment_audit_events``. Every replaced CHECK's "before" text is compared
with the revision that declared it and its "after" text with the model.

The execution probes reuse the M05 and M06 suites' isolated temporary SQLite
database: representative M04 invoices and events, M05 payments, a voided
receipt and events, and M06 intents. SQLite can rebuild a table other tables
reference only with foreign keys off, so the revision requires
``PRAGMA foreign_keys=OFF`` there and refuses otherwise; the probes run it that
way, prove ``PRAGMA foreign_key_check`` empty after every run, and turn
enforcement back on to prove the new constraints.

The MySQL checks compile dialect DDL and render the revision's offline
(``--sql``) MySQL script; neither connects to a database. The real upgrade of
the authorized development MySQL database is a separate, manually executed
check.
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

import tests.payment_intent_fixtures as ix
import tests.test_payment_intents_migration as m06
import tests.test_payments_migration as m05
from app.extensions import db
from app.models import (
    PaymentAuditEvent,
    PaymentIntent,
    PaymentProviderEvent,
    PaymentTransaction,
    Receipt,
)

_REVISION = "e9c4b2d7a1f3"
_DOWN_REVISION = "d4f7a2c9e1b6"
_M05 = "c5e8f2a7d914"
_INBOX = "payment_provider_events"
_ALTERED = ("payment_intents", "payment_transactions", "receipts", "payment_audit_events")
_MODELS = {
    "payment_intents": PaymentIntent,
    "payment_transactions": PaymentTransaction,
    "receipts": Receipt,
    "payment_audit_events": PaymentAuditEvent,
    _INBOX: PaymentProviderEvent,
}
_HISTORY_TABLES = ("invoice_number_sequences", "invoices", "invoice_items",
                   "receipt_number_sequences") + _ALTERED


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
    return m05._load(_REVISION, "p5m07")


def _norm(text):
    return " ".join(str(text).split())


def _model_checks(model):
    return {c.name: _norm(c.sqltext) for c in model.__table__.constraints
            if isinstance(c, sa.CheckConstraint)}


def _table_checks(table):
    return {c.name: _norm(c.sqltext) for c in table.constraints
            if isinstance(c, sa.CheckConstraint)}


#: Phase 5 / M10 (``b3d8f1a6c472``) follows this revision: it adds the
#: visible-deletion tombstone -- three columns, a CHECK, two indexes and a
#: foreign key -- to ``payment_transactions`` and ``receipts`` and widens four
#: audit-event CHECKs. The current models carry that; this suite compares the
#: M07 schema, so M10's additions are taken back out and its replacements
#: mapped back to the M07 text M10 records.
#: ``tests/test_financial_deletion_migration.py`` compares them with M10.
_M10 = "b3d8f1a6c472"
_TOMBSTONE = {"deleted_at", "deleted_by_id", "deletion_reason"}


def _m10_old_checks():
    module, _ = m05._load(_M10, "p5m10for07")
    return {name: _norm(old) for name, old, _new in module._AUDIT_CHECKS}


def _at_m07(checks):
    """`checks` -- ``{name: text}`` of the current schema -- as M07 left them."""
    old = _m10_old_checks()
    return {name: old.get(name, text) for name, text in checks.items()
            if not name.endswith("_deletion_state")}


def _m07_checks(model):
    return _at_m07(_model_checks(model))


def _m07_shape(shape):
    """A current ``m05._shape`` without M10's additions."""
    return {
        "columns": shape["columns"] - _TOMBSTONE,
        "nullable": shape["nullable"] - _TOMBSTONE,
        "checks": {name for name in shape["checks"] if not name.endswith("_deletion_state")},
        "indexes": {name: columns for name, columns in shape["indexes"].items()
                    if "_deleted_" not in name},
        "fks": shape["fks"] - {("deleted_by_id", "users")},
        "uniques": shape["uniques"],
    }


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
    # Phase 5 / M10 follows this revision, so the single head is now M10's.
    assert heads == {"b3d8f1a6c472"}
    assert [r for r, p in parents.items() if p == _REVISION] == ["b3d8f1a6c472"]
    assert [r for r, p in parents.items() if p == _DOWN_REVISION] == [_REVISION]
    assert len([r for r, p in parents.items() if p is None]) == 1


def test_the_revision_creates_the_inbox_and_alters_only_four_tables():
    _, source = _load_migration()
    code = m05._code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == [_INBOX]
    assert set(re.findall(r"op\.batch_alter_table\(\s*(_\w+)", code)) == {
        "_INTENTS", "_PAYMENTS", "_RECEIPTS", "_EVENTS"}
    assert re.findall(r"op\.drop_table\((\w+)\)", code) == ["_INBOX"]
    for forbidden in ("op.execute", "op.bulk_insert", "server_default", "ondelete", "onupdate",
                      "mysql_engine", "mysql_charset", "Enum(", "Float", "trigger", "TRIGGER",
                      "INSERT", "UPDATE ", "DELETE FROM", "card", "cvv", "secret", "password",
                      "signature", "raw_body", "refund"):
        assert forbidden not in code, forbidden
    # The one read is the downgrade's refusal count.
    assert re.findall(r"\"(SELECT[^\"]*)", code) == ["SELECT (SELECT COUNT(*) FROM "
                                                     "payment_provider_events)"]
    upgrade = m05._function(code, "upgrade")
    for forbidden in ("drop_table", "drop_column", "_M07_ROWS_SQL"):
        assert forbidden not in upgrade, forbidden


def test_every_replaced_check_starts_where_its_revision_left_it_and_ends_at_the_model():
    module, _ = _load_migration()
    m05_module, m05_source = m05._load(_M05, "p5m05for07")
    _, m06_source = m05._load(_DOWN_REVISION, "p5m06for07")
    m05_flat, m06_flat = m05._flat(m05_source), m05._flat(m06_source)
    m05_replaced = {name: after for name, _before, after in m05_module._REPLACED_CHECKS}
    for name, old, new in module._INTENT_CHECKS:
        assert old in m06_flat, name
        assert _m07_checks(PaymentIntent)[name] == new, name
    for name, old, new in module._PAYMENT_CHECKS:
        assert old in m05_flat, name
        assert _m07_checks(PaymentTransaction)[name] == new, name
    for name, old, new in module._AUDIT_CHECKS:
        # M05 replaced three of these M04 CHECKs and created the fourth.
        if name in m05_replaced:
            assert m05_replaced[name] == old, name
        else:
            assert old in m05_flat, name
        assert _m07_checks(PaymentAuditEvent)[name] == new, name
    assert _m07_checks(PaymentTransaction)["ck_payment_transactions_online_origin"] == (
        module._ONLINE_ORIGIN_SQL)
    assert _m07_checks(PaymentAuditEvent)["ck_payment_audit_events_actor_origin"] == (
        module._ACTOR_ORIGIN_SQL)
    # Each old expression only gains branches or values.
    for name, old, new in module._INTENT_CHECKS + module._PAYMENT_CHECKS + module._AUDIT_CHECKS:
        assert old != new, name


def test_every_rebuild_definition_carries_exactly_the_models_checks():
    module, _ = _load_migration()
    assert _table_checks(module._intents_table(True)) == _m07_checks(PaymentIntent)
    assert _table_checks(module._receipts_table()) == _m07_checks(Receipt)
    payments = dict(_table_checks(module._payments_table(True)),
                    ck_payment_transactions_online_origin=module._ONLINE_ORIGIN_SQL)
    assert payments == _m07_checks(PaymentTransaction)
    events = dict(_table_checks(module._audit_events_table(True)),
                  ck_payment_audit_events_actor_origin=module._ACTOR_ORIGIN_SQL)
    assert events == _m07_checks(PaymentAuditEvent)
    # The "before" definitions are exactly the previous revisions' CHECKs.
    before = {name: old for group in (module._INTENT_CHECKS, module._PAYMENT_CHECKS,
                                      module._AUDIT_CHECKS) for name, old, _new in group}
    for table, model, added in (
        (module._intents_table(False), PaymentIntent, set()),
        (module._payments_table(False), PaymentTransaction,
         {"ck_payment_transactions_online_origin"}),
        (module._audit_events_table(False), PaymentAuditEvent,
         {"ck_payment_audit_events_actor_origin"}),
    ):
        expected = {name: before.get(name, text) for name, text in _m07_checks(model).items()
                    if name not in added}
        assert _table_checks(table) == expected, table.name
    # The rebuild definitions match the models' columns and nullability,
    # before the batch's own changes.
    for table, model, changed in (
        (module._intents_table(True), PaymentIntent, set()),
        (module._payments_table(True), PaymentTransaction,
         {"recorded_by_id", "payment_intent_id"}),
        (module._receipts_table(), Receipt, {"issued_by_id"}),
        (module._audit_events_table(True), PaymentAuditEvent, {"actor_id"}),
    ):
        declared = {c.name: c.nullable for c in table.columns}
        modelled = {c.name: c.nullable for c in model.__table__.columns
                    if c.name not in _TOMBSTONE}
        for column in changed:
            modelled.pop(column)
            declared.pop(column, None)
        assert declared == modelled, table.name


def test_the_inbox_block_matches_the_model():
    _, source = _load_migration()
    block = source.split("op.create_table('payment_provider_events',", 1)[1].split(
        "\n    )\n", 1)[0]
    declared = dict(re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)", block))
    assert declared == {c.name: str(c.nullable) for c in PaymentProviderEvent.__table__.columns}
    flat = m05._flat(block)
    for name, expression in _model_checks(PaymentProviderEvent).items():
        assert f"name='{name}'" in flat, name
        assert expression in flat, (name, expression)
    for column in declared:
        for part in ("raw", "body", "signature", "secret", "header", "reference", "card", "bank",
                     "token", "credential", "password"):
            assert part not in column.split("_"), (column, part)


# ===========================================================================
# Execution against an isolated SQLite database
# ===========================================================================

_T3, _T4 = m06._T3, m06._T4
_T5 = "'2026-06-04 11:00:00'"
_KEY_4, _KEY_5, _KEY_6 = "4" * 64, "5" * 64, "6" * 64
_DIGEST = "'" + "a" * 64 + "'"
_EVENT_ID = "'evt_mock_" + "1" * 32 + "'"

_INTENT = m06._INTENT
_PAYMENT = m05._PAYMENT.replace(" updated_at)", " updated_at, payment_intent_id)")
_RECEIPT = m05._RECEIPT
_AUDIT = m05._EVENT
_INBOX_ROW = ("INSERT INTO payment_provider_events (id, public_id, provider, provider_event_id,"
              " payment_intent_id, event_type, currency_code, amount, provider_occurred_at,"
              " received_at, processed_at, payload_digest, outcome, payment_transaction_id,"
              " created_at) VALUES ")


def _intent(n, key, status, result_at="NULL", result_by="NULL", terminal=_T4, reference=None):
    reference = reference or f"mock_pi_{n}"
    version = 1 if status == "pending" else 2
    return _INTENT + (
        f"({n}, 'pi-{n}', 2, 'mock', '{reference}', '{key}', '{status}', 'LYD', 1150.5, 11,"
        f" {result_at}, {result_by}, {terminal}, NULL, {version}, {_T3}, {_T4})")


def _online(n, intent, recorded_by="NULL", confirmed_by="NULL"):
    return _PAYMENT + (
        f"({n}, 'pt-{n}', 2, 'collection', 'online', 'confirmed', 'LYD', 1150.5, NULL, NULL,"
        f" {_T4}, {recorded_by}, {_T4}, {confirmed_by}, NULL, NULL, NULL, NULL, 1, {_T4}, {_T4},"
        f" {intent})")


def _audit(n, kind, actor, payment, receipt="NULL"):
    return _AUDIT + (
        f"({n}, 2, {actor}, '{kind}', {_T4}, 3, 3, NULL, '{{}}', '{{}}', {payment}, {receipt})")


def _inbox(n, intent, event_type, outcome, payment="NULL", event_id=None):
    return _INBOX_ROW + (
        f"({n}, 'pe-{n}', 'mock', {event_id or _EVENT_ID.replace('1', str(n))}, {intent},"
        f" '{event_type}', 'LYD', 1150.5, {_T4}, {_T4}, {_T4}, {_DIGEST}, '{outcome}',"
        f" {payment}, {_T4})")


#: One of every row only M07 can represent, in insertion order.
_M07_ROWS = [
    _intent(3, _KEY_4, "confirmed"),
    _intent(4, _KEY_5, "provider_failed"),
    _online(4, 3),
    _PAYMENT + (f"(5, 'pt-5', 2, 'reversal', 'online', 'confirmed', 'LYD', 1150.5, NULL, NULL,"
                f" {_T5}, 11, {_T5}, 11, NULL, NULL, NULL, 4, 1, {_T5}, {_T5}, NULL)"),
    _RECEIPT + (f"(2, 'rc-2', 4, 'RCT-2026-000002', 'issued', {_T4}, NULL, NULL, NULL, NULL,"
                f" '{{}}', 1, {_T4}, {_T4})"),
    _audit(12, "payment_online_confirmed", "NULL", 4),
    _audit(13, "receipt_online_issued", "NULL", 4, 2),
    _inbox(1, 3, "payment.succeeded", "confirmed", payment=4),
    _inbox(2, 4, "payment.failed", "failed"),
    _inbox(3, 3, "payment.succeeded", "duplicate"),
]
#: Removing them, children first.
_M07_CLEANUP = [
    "DELETE FROM payment_provider_events",
    "DELETE FROM payment_audit_events WHERE id IN (12, 13)",
    "DELETE FROM receipts WHERE id = 2",
    "DELETE FROM payment_transactions WHERE id IN (5, 4)",
    "DELETE FROM payment_intents WHERE id IN (3, 4)",
]

#: Each alone is history the previous schema cannot represent.
_SINGLE_M07_ROWS = {
    "provider event": [_inbox(7, 2, "payment.succeeded", "duplicate")],
    "online payment": [_online(7, 2)],
    "issuer-less receipt": [
        _RECEIPT + (f"(7, 'rc-7', 2, 'RCT-2026-000007', 'issued', {_T4}, NULL, NULL, NULL,"
                    f" NULL, '{{}}', 1, {_T4}, {_T4})")],
    "actor-less event": [_audit(17, "payment_online_confirmed", "NULL", 1)],
    "confirmed intent": [_intent(7, _KEY_6, "confirmed")],
    "webhook failure": [_intent(7, _KEY_6, "provider_failed")],
    "webhook failure after the browser": [
        _intent(7, _KEY_6, "provider_failed", result_at=_T3, result_by=11)],
}
_SINGLE_CLEANUP = [
    "DELETE FROM payment_provider_events", "DELETE FROM payment_audit_events WHERE id = 17",
    "DELETE FROM receipts WHERE id = 7", "DELETE FROM payment_transactions WHERE id = 7",
    "DELETE FROM payment_intents WHERE id = 7",
]


def _foreign_keys(conn, on):
    conn.execute(sa.text(f"PRAGMA foreign_keys={'ON' if on else 'OFF'}"))
    assert conn.execute(sa.text("PRAGMA foreign_keys")).scalar() == (1 if on else 0)


def _run(conn, module, direction):
    with Operations.context(MigrationContext.configure(conn)):
        getattr(module, direction)()
    conn.commit()
    assert conn.execute(sa.text("PRAGMA foreign_keys")).scalar() == 0
    assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []


def _refused(conn, statement, rule):
    """`statement` fails -- from the named CHECK when `rule` names one, or
    from one of the named CHECKs when `rule` is a tuple (a row that breaks
    several: SQLite's evaluation order is not fixed)."""
    try:
        conn.execute(sa.text(statement))
    except sa.exc.IntegrityError as error:
        conn.rollback()
        names = rule if isinstance(rule, tuple) else (rule,) if rule.startswith("ck_") else ()
        if names:
            assert any(f"CHECK constraint failed: {name}" in str(error.orig)
                       for name in names), (rule, error.orig)
        return
    raise AssertionError(f"{rule} was not enforced")


def _probe(name):
    """An isolated database at the M06 revision holding representative M04,
    M05 and M06 history, with foreign keys then turned off."""
    tmp, engine, conn = m06._probe(name)
    m06_module, _ = m06._load_migration()
    m05._run(conn, m06_module, "upgrade")
    for statement in m06._M06_ROWS:
        conn.execute(sa.text(statement))
    conn.commit()
    _foreign_keys(conn, False)
    return tmp, engine, conn


def _checks(conn, table):
    return {c["name"]: _norm(c["sqltext"]) for c in inspect(conn).get_check_constraints(table)}


def _columns(conn, table):
    return [c["name"] for c in inspect(conn).get_columns(table)]


def _history(conn, columns):
    """Every earlier row, read through the columns each table had before."""
    rows = m05._existing_rows(conn)
    for table, names in columns.items():
        rows[table] = conn.execute(
            sa.text(f"SELECT {', '.join(names)} FROM {table} ORDER BY id")).fetchall()
    return rows


def _model_shapes(app):
    """The current models' schema as M07 left it (see :func:`_m07_shape`)."""
    with app.app_context(), db.engine.connect() as model:
        return ({table: _m07_shape(m05._shape(model, table)) for table in _MODELS},
                {table: _at_m07(_checks(model, table)) for table in _MODELS})


def _refuses_downgrade(conn, module):
    with pytest.raises(RuntimeError, match="Refusing to downgrade"):
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
    conn.rollback()


def test_migration_applies_preserves_history_and_refuses_to_destroy_it(app):
    module, _ = _load_migration()
    model_shapes, model_checks = _model_shapes(app)
    tmp, engine, conn = _probe("probe7.db")
    try:
        columns = {table: _columns(conn, table) for table in _HISTORY_TABLES}
        history = _history(conn, columns)
        assert history["payment_intents"] and history["payment_transactions"]
        tables_before = set(inspect(conn).get_table_names())
        shapes_before = {table: m05._shape(conn, table) for table in _ALTERED}
        checks_before = {table: _checks(conn, table) for table in _ALTERED}

        _run(conn, module, "upgrade")
        assert set(inspect(conn).get_table_names()) - tables_before == {_INBOX}
        for table in _MODELS:
            assert m05._shape(conn, table) == model_shapes[table], table
            assert _checks(conn, table) == model_checks[table], table
        assert _history(conn, columns) == history
        assert conn.execute(sa.text(f"SELECT count(*) FROM {_INBOX}")).scalar_one() == 0
        assert conn.execute(sa.text(
            "SELECT count(*) FROM payment_transactions WHERE payment_intent_id IS NOT NULL"
        )).scalar_one() == 0

        # Every row only M07 can represent is accepted, with foreign keys on.
        _foreign_keys(conn, True)
        for statement in _M07_ROWS:
            conn.execute(sa.text(statement))
        conn.commit()
        assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []
        for statement, rule in (
            # A human recorder without the same confirmer also breaks M05's rule.
            (_online(6, 4, recorded_by=11), ("ck_payment_transactions_online_origin",
                                             "ck_payment_transactions_immediate_confirmation")),
            (_online(6, 4, recorded_by=11, confirmed_by=11),
             "ck_payment_transactions_online_origin"),
            (_online(6, "NULL"), "ck_payment_transactions_online_origin"),
            (_online(6, 3), "uq_payment_transactions_payment_intent_id"),
            (_online(6, 99), "the intent foreign key"),
            (_online(6, 4, recorded_by=11, confirmed_by=11).replace(
                "'online', 'confirmed'", "'cash', 'confirmed'"),
             "ck_payment_transactions_online_origin"),
            (_audit(14, "payment_online_confirmed", 11, 4),
             "ck_payment_audit_events_actor_origin"),
            (_audit(14, "payment_cash_recorded", "NULL", 1),
             "ck_payment_audit_events_actor_origin"),
            (_audit(14, "payment_online_confirmed", "NULL", 4, 2),
             "ck_payment_audit_events_subject_links"),
            (_inbox(4, 3, "payment.succeeded", "duplicate", event_id=_EVENT_ID),
             "uq_payment_provider_events_provider_event"),
            (_inbox(4, 3, "payment.succeeded", "confirmed"),
             "ck_payment_provider_events_outcome_link"),
            (_inbox(4, 99, "payment.succeeded", "duplicate"), "the inbox's intent foreign key"),
            (_inbox(4, 4, "payment.succeeded", "failed"),
             "ck_payment_provider_events_outcome_type"),
            (_inbox(4, 3, "payment.refunded", "duplicate"),
             "ck_payment_provider_events_event_type_valid"),
            # An unknown status breaks several CHECKs; SQLite names whichever it meets first.
            (_intent(8, "8" * 64, "paid"), "an unknown status"),
            (_intent(8, "8" * 64, "confirmed", terminal="NULL"),
             "ck_payment_intents_terminal_state"),
            (_intent(8, "8" * 64, "confirmed", result_at=_T4, result_by=11, terminal=_T3),
             "ck_payment_intents_lifecycle_state"),
            ("DELETE FROM payment_intents WHERE id = 3", "no cascade from an intent"),
        ):
            _refused(conn, statement, rule)
        with_m07 = _history(conn, {table: _columns(conn, table) for table in _MODELS})

        # The downgrade refuses while any M07 row exists, before changing anything.
        _refuses_downgrade(conn, module)
        assert _history(conn, {table: _columns(conn, table) for table in _MODELS}) == with_m07
        for table in _MODELS:
            assert m05._shape(conn, table) == model_shapes[table], table

        # ... and each kind of M07 row alone is enough.
        for statement in _M07_CLEANUP:
            conn.execute(sa.text(statement))
        conn.commit()
        for statements in _SINGLE_M07_ROWS.values():
            for statement in statements:
                conn.execute(sa.text(statement))
            conn.commit()
            _refuses_downgrade(conn, module)
            for statement in _SINGLE_CLEANUP:
                conn.execute(sa.text(statement))
            conn.commit()

        # A downgrade with foreign keys enforced is refused before any change.
        with pytest.raises(RuntimeError, match="foreign keys disabled"):
            with Operations.context(MigrationContext.configure(conn)):
                module.downgrade()
        conn.rollback()
        assert _INBOX in inspect(conn).get_table_names()

        # With none, the downgrade restores the M06 schema exactly.
        _foreign_keys(conn, False)
        _run(conn, module, "downgrade")
        assert set(inspect(conn).get_table_names()) == tables_before
        assert {table: m05._shape(conn, table) for table in _ALTERED} == shapes_before
        assert {table: _checks(conn, table) for table in _ALTERED} == checks_before
        assert _history(conn, columns) == history
        _foreign_keys(conn, True)
        _refused(conn, _PAYMENT.replace(", payment_intent_id)", ")") + (
            f"(9, 'pt-9', 2, 'collection', 'online', 'confirmed', 'LYD', 1, NULL, NULL, {_T4}, 11,"
            f" {_T4}, 11, NULL, NULL, NULL, NULL, 1, {_T4}, {_T4})"),
            ("ck_payment_transactions_method_valid",
             "ck_payment_transactions_bank_transfer_details"))
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_the_upgrade_refuses_to_run_with_foreign_keys_enforced():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("probe7fk.db")
    try:
        _foreign_keys(conn, True)
        tables = set(inspect(conn).get_table_names())
        shapes = {table: m05._shape(conn, table) for table in _ALTERED}
        with pytest.raises(RuntimeError, match="foreign keys disabled"):
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        conn.rollback()
        assert set(inspect(conn).get_table_names()) == tables
        assert {table: m05._shape(conn, table) for table in _ALTERED} == shapes
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_a_second_upgrade_after_a_downgrade_rebuilds_the_same_schema():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("probe7again.db")
    try:
        columns = {table: _columns(conn, table) for table in _HISTORY_TABLES}
        history = _history(conn, columns)
        results = []
        for _ in range(2):
            _run(conn, module, "upgrade")
            results.append(({table: m05._shape(conn, table) for table in _MODELS},
                            {table: _checks(conn, table) for table in _MODELS}))
            _run(conn, module, "downgrade")
            assert _history(conn, columns) == history
        assert results[0] == results[1]
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


# ===========================================================================
# MySQL DDL, offline
# ===========================================================================

_INBOX_DDL = (
    "id BIGINT NOT NULL AUTO_INCREMENT", "public_id VARCHAR(36) NOT NULL",
    "provider VARCHAR(16) NOT NULL", "provider_event_id VARCHAR(64) NOT NULL",
    "payment_intent_id BIGINT NOT NULL", "event_type VARCHAR(32) NOT NULL",
    "currency_code VARCHAR(3) NOT NULL", "amount DECIMAL(19, 4) NOT NULL",
    "provider_occurred_at DATETIME NOT NULL", "received_at DATETIME NOT NULL",
    "processed_at DATETIME NOT NULL", "payload_digest VARCHAR(64) NOT NULL",
    "outcome VARCHAR(32) NOT NULL", "payment_transaction_id BIGINT,",
    "created_at DATETIME NOT NULL",
    "FOREIGN KEY(payment_intent_id) REFERENCES payment_intents (id)",
    "FOREIGN KEY(payment_transaction_id) REFERENCES payment_transactions (id)",
    "CONSTRAINT uq_payment_provider_events_provider_event UNIQUE (provider, provider_event_id)",
    "CONSTRAINT uq_payment_provider_events_payment_transaction_id UNIQUE "
    "(payment_transaction_id)",
    "UNIQUE (public_id)",
)


def test_mysql_ddl_for_the_inbox_compiles_without_a_connection():
    ddl = str(CreateTable(PaymentProviderEvent.__table__).compile(dialect=mysql.dialect()))
    flat = " ".join(ddl.split())
    for fragment in _INBOX_DDL:
        assert fragment in flat, fragment
    for name, expression in _model_checks(PaymentProviderEvent).items():
        assert f"CONSTRAINT {name} CHECK ({expression})" in flat, name
    for forbidden in ("ON DELETE", "ON UPDATE", "DATETIME(", "ENUM(", "FLOAT", "DOUBLE",
                      "ENGINE=", "CHARSET", "COLLATE", "TRIGGER", "JSON", "TEXT", "BLOB"):
        assert forbidden not in ddl.upper(), forbidden
    assert PaymentProviderEvent.__table__.kwargs == {}


def _offline_mysql(direction):
    module, _ = _load_migration()
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        getattr(module, direction)()
    return [" ".join(s.split()) for s in buffer.getvalue().split(";") if s.strip()]


def test_the_offline_mysql_upgrade_alters_in_place_then_creates_the_inbox():
    module, _ = _load_migration()
    statements = _offline_mysql("upgrade")
    sql = " ; ".join(statements)
    expected_order = [
        *[f"ALTER TABLE payment_intents ADD CONSTRAINT {n} CHECK ({new})"
          for n, _old, new in module._INTENT_CHECKS],
        *[f"ALTER TABLE payment_transactions DROP CHECK {n}" for n, _o, _n in
          module._PAYMENT_CHECKS],
        "ALTER TABLE payment_transactions MODIFY recorded_by_id BIGINT NULL",
        "ALTER TABLE payment_transactions ADD COLUMN payment_intent_id BIGINT",
        "ALTER TABLE payment_transactions ADD CONSTRAINT "
        "uq_payment_transactions_payment_intent_id UNIQUE (payment_intent_id)",
        "ALTER TABLE payment_transactions ADD CONSTRAINT fk_payment_transactions_payment_intent_id"
        " FOREIGN KEY(payment_intent_id) REFERENCES payment_intents (id)",
        *[f"ALTER TABLE payment_transactions ADD CONSTRAINT {n} CHECK ({new})"
          for n, _old, new in module._PAYMENT_CHECKS],
        "ALTER TABLE payment_transactions ADD CONSTRAINT ck_payment_transactions_online_origin"
        f" CHECK ({module._ONLINE_ORIGIN_SQL})",
        "ALTER TABLE receipts MODIFY issued_by_id BIGINT NULL",
        *[f"ALTER TABLE payment_audit_events DROP CHECK {n}" for n, _o, _n in
          module._AUDIT_CHECKS],
        "ALTER TABLE payment_audit_events MODIFY actor_id BIGINT NULL",
        *[f"ALTER TABLE payment_audit_events ADD CONSTRAINT {n} CHECK ({new})"
          for n, _old, new in module._AUDIT_CHECKS],
        "ALTER TABLE payment_audit_events ADD CONSTRAINT ck_payment_audit_events_actor_origin"
        f" CHECK ({module._ACTOR_ORIGIN_SQL})",
        f"CREATE TABLE {_INBOX} (",
        "CREATE INDEX ix_payment_provider_events_intent_id_id ON payment_provider_events"
        " (payment_intent_id, id)",
        "CREATE INDEX ix_payment_provider_events_outcome_id ON payment_provider_events"
        " (outcome, id)",
    ]
    positions = [sql.index(fragment) for fragment in expected_order]
    assert positions == sorted(positions)
    assert sql.count("CREATE TABLE") == 1 and sql.count("CREATE INDEX") == 2
    assert len(statements) == 33
    for forbidden in ("DROP TABLE", "DROP COLUMN", "DROP INDEX", "DROP FOREIGN KEY",
                      "ON DELETE", "ON UPDATE", "ENGINE=", "CHARSET", "DELETE FROM",
                      "INSERT INTO", "UPDATE ", "TRIGGER", "SELECT"):
        assert forbidden not in sql, forbidden


def test_the_offline_mysql_downgrade_drops_the_inbox_and_restores_the_old_checks():
    module, _ = _load_migration()
    statements = _offline_mysql("downgrade")
    sql = " ; ".join(statements)
    assert statements[0] == f"DROP TABLE {_INBOX}"
    for fragment in (
        "ALTER TABLE payment_audit_events DROP CHECK ck_payment_audit_events_actor_origin",
        "ALTER TABLE payment_audit_events MODIFY actor_id BIGINT NOT NULL",
        "ALTER TABLE receipts MODIFY issued_by_id BIGINT NOT NULL",
        "ALTER TABLE payment_transactions DROP CHECK ck_payment_transactions_online_origin",
        "ALTER TABLE payment_transactions DROP FOREIGN KEY "
        "fk_payment_transactions_payment_intent_id",
        "ALTER TABLE payment_transactions DROP INDEX uq_payment_transactions_payment_intent_id",
        "ALTER TABLE payment_transactions DROP COLUMN payment_intent_id",
        "ALTER TABLE payment_transactions MODIFY recorded_by_id BIGINT NOT NULL",
        *[f"ADD CONSTRAINT {n} CHECK ({old})" for group in (
            module._AUDIT_CHECKS, module._PAYMENT_CHECKS, module._INTENT_CHECKS)
          for n, old, _new in group],
    ):
        assert fragment in sql, fragment
    assert sql.index("DROP FOREIGN KEY") < sql.index("DROP INDEX") < sql.index("DROP COLUMN")
    assert sql.count("DROP TABLE") == 1
    for forbidden in ("CREATE", "DELETE FROM", "INSERT", "UPDATE ", "SELECT", "TRIGGER"):
        assert forbidden not in sql, forbidden
