"""Phase 5 / M04 migration checks for ``invoice_number_sequences``,
``invoices``, ``invoice_items`` and ``payment_audit_events``.

The execution probes use an isolated temporary SQLite database seeded with
minimal existing tables and representative rows -- accounts, Groups and
Enrollments (non-financial), and M02 / M03 fee plans, items and assignments --
so "nothing existing is touched and nothing is seeded" is executed rather
than asserted. Both directions run with foreign keys **enforced** and
``PRAGMA foreign_key_check`` asserted after each.

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
from app.models import (
    Invoice,
    InvoiceItem,
    InvoiceItemKind,
    InvoiceItemStatus,
    InvoiceNumberSequence,
    InvoiceStatus,
    PaymentAuditEvent,
    PaymentAuditEventKind,
)

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "a8d3f5c29e61"
_DOWN_REVISION = "f9b2d6e4a318"
#: Phase 5 / M05, M06 and then M07 follow this revision; the single head is
#: M07's.
#: Phase 5 / M10 follows M07; the Phase 6 replacement's destructive revision is now the single head.
_HEAD = "d574ab56594f"
#: Phase 5 / M05, the revision that follows this one.
_M05 = "c5e8f2a7d914"

#: What Phase 5 / M05 (``c5e8f2a7d914``) changed on ``payment_audit_events``:
#: three CHECKs widened, one CHECK, two columns, two indexes and two foreign
#: keys added. ``tests/test_payments_migration.py`` compares those with the
#: M05 revision; this suite keeps comparing the rest with M04's.
_M05_REPLACED_CHECKS = {"ck_payment_audit_events_kind_valid",
                        "ck_payment_audit_events_version_transition",
                        "ck_payment_audit_events_reason_required"}
_M05_ADDED = {
    "checks": {"ck_payment_audit_events_subject_links"},
    "columns": {"payment_transaction_id", "receipt_id"},
    "indexes": {"ix_payment_audit_events_payment_transaction_id",
                "ix_payment_audit_events_receipt_id"},
    "fks": {("payment_transaction_id", "payment_transactions"), ("receipt_id", "receipts")},
}

#: What Phase 5 / M07 (``e9c4b2d7a1f3``) changed on ``payment_audit_events``:
#: ``actor_id`` became nullable and one CHECK was added (it also widened
#: CHECKs M05 had already replaced). ``tests/test_verified_webhooks_migration.py``
#: compares those with the M07 revision.
_M07_ADDED = {"checks": {"ck_payment_audit_events_actor_origin"}, "nullable": {"actor_id"}}

#: What Phase 5 / M10 (``b3d8f1a6c472``) added to ``invoices``: the three
#: nullable tombstone columns, one CHECK, two indexes and one foreign key. (It
#: also widened audit-event CHECKs M05 had already replaced.)
#: ``tests/test_financial_deletion_migration.py`` compares those with the M10
#: revision.
_M10_ADDED = {
    "invoices": {
        "checks": {"ck_invoices_deletion_state"},
        "columns": {"deleted_at", "deleted_by_id", "deletion_reason"},
        "indexes": {"ix_invoices_deleted_at_id", "ix_invoices_deleted_by_id"},
        "fks": {("deleted_by_id", "users")},
    },
}

_TABLES = ["invoice_number_sequences", "invoices", "invoice_items", "payment_audit_events"]
_MODELS = {
    "invoice_number_sequences": InvoiceNumberSequence,
    "invoices": Invoice,
    "invoice_items": InvoiceItem,
    "payment_audit_events": PaymentAuditEvent,
}

_EXPECTED = {
    "invoice_number_sequences": {
        "columns": {"id", "calendar_year", "last_number", "created_at", "updated_at"},
        "nullable": set(),
        "checks": {"ck_invoice_number_sequences_year_range",
                   "ck_invoice_number_sequences_last_number_range",
                   "ck_invoice_number_sequences_timestamps_ordered"},
        "indexes": {},
        "fks": set(),
        "uniques": [("uq_invoice_number_sequences_calendar_year", ("calendar_year",))],
    },
    "invoices": {
        "columns": {"id", "public_id", "student_fee_assignment_id", "currency_code", "status",
                    "invoice_number", "issued_at", "issued_by_id", "cancelled_at",
                    "cancelled_by_id", "version", "created_at", "updated_at"},
        "nullable": {"invoice_number", "issued_at", "issued_by_id", "cancelled_at",
                     "cancelled_by_id"},
        "checks": {"ck_invoices_status_valid", "ck_invoices_currency_code",
                   "ck_invoices_version_positive", "ck_invoices_issue_pair",
                   "ck_invoices_cancellation_pair", "ck_invoices_number_matches_issue",
                   "ck_invoices_number_format", "ck_invoices_lifecycle_state",
                   "ck_invoices_timestamps_ordered"},
        "indexes": {"ix_invoices_assignment_status_id": ["student_fee_assignment_id", "status",
                                                         "id"],
                    "ix_invoices_issued_by_id": ["issued_by_id"],
                    "ix_invoices_cancelled_by_id": ["cancelled_by_id"]},
        "fks": {("student_fee_assignment_id", "student_fee_assignments"),
                ("issued_by_id", "users"), ("cancelled_by_id", "users")},
        "uniques": [("", ("public_id",)), ("uq_invoices_invoice_number", ("invoice_number",))],
    },
    "invoice_items": {
        "columns": {"id", "public_id", "invoice_id", "kind", "label", "amount", "status",
                    "removed_at", "removed_by_id", "version", "created_at", "updated_at"},
        "nullable": {"removed_at", "removed_by_id"},
        "checks": {"ck_invoice_items_kind_valid", "ck_invoice_items_status_valid",
                   "ck_invoice_items_amount_range", "ck_invoice_items_version_positive",
                   "ck_invoice_items_removal_state", "ck_invoice_items_timestamps_ordered"},
        "indexes": {"ix_invoice_items_invoice_status_id": ["invoice_id", "status", "id"],
                    "ix_invoice_items_removed_by_id": ["removed_by_id"]},
        "fks": {("invoice_id", "invoices"), ("removed_by_id", "users")},
        "uniques": [("", ("public_id",))],
    },
    "payment_audit_events": {
        "columns": {"id", "invoice_id", "actor_id", "kind", "occurred_at",
                    "invoice_version_before", "invoice_version_after", "reason",
                    "before_snapshot", "after_snapshot"},
        "nullable": {"invoice_version_before", "reason", "before_snapshot"},
        "checks": {"ck_payment_audit_events_kind_valid",
                   "ck_payment_audit_events_versions_positive",
                   "ck_payment_audit_events_version_transition",
                   "ck_payment_audit_events_snapshots_present",
                   "ck_payment_audit_events_reason_required"},
        "indexes": {"ix_payment_audit_events_invoice_id_id": ["invoice_id", "id"],
                    "ix_payment_audit_events_actor_id": ["actor_id"]},
        "fks": {("invoice_id", "invoices"), ("actor_id", "users")},
        "uniques": [],
    },
}

#: Column-name parts that would mean card, bank, provider or payment data.
_PROHIBITED_PARTS = (
    "card", "pan", "cvv", "cvc", "expiry", "iban", "bank", "account", "token", "secret",
    "provider", "intent", "customer", "webhook", "receipt", "refund", "discount", "tax",
    "installment", "due", "quantity", "total", "paid", "payment",
)


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"p5m04_{_REVISION}", path)
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


def _model_checks(model):
    return {
        constraint.name: " ".join(str(constraint.sqltext).split())
        for constraint in model.__table__.constraints
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
    assert set(parents) - {p for p in parents.values() if p is not None} == {_HEAD}
    claimed = [p for p in parents.values() if p is not None]
    assert len(claimed) == len(set(claimed))
    assert [r for r, p in parents.items() if p == _DOWN_REVISION] == [_REVISION]
    assert [r for r, p in parents.items() if p == _REVISION] == [_M05]
    assert len([r for r, p in parents.items() if p is None]) == 1


def test_the_revision_creates_four_tables_and_touches_nothing_else():
    _, source = _load_migration()
    code = _code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == _TABLES
    for forbidden in ("add_column", "drop_column", "alter_column", "batch_alter_table",
                      "drop_constraint", "create_check_constraint", "op.execute",
                      "op.bulk_insert", "ondelete", "onupdate", "mysql_engine", "mysql_charset",
                      "server_default", "Float", "Numeric(", "Enum(", "sa.text(", "fee_plan_items",
                      "trigger", "TRIGGER"):
        assert forbidden not in code, forbidden
    assert sorted(set(re.findall(r"\['([a-z_]+\.id)'\]", code))) == [
        "invoices.id", "student_fee_assignments.id", "users.id",
    ]
    upgrade = _function(code, "upgrade")
    for forbidden in ("drop_table", "drop_index"):
        assert forbidden not in upgrade, forbidden
    expected = [(name, table) for table in _TABLES for name in _EXPECTED[table]["indexes"]]
    assert re.findall(r"op\.create_index\(\s*'([^']+)',\s*'([^']+)'", upgrade) == expected


def test_the_downgrade_drops_only_the_new_tables_dependants_first():
    _, source = _load_migration()
    downgrade = _function(_code(source), "downgrade")
    assert re.findall(r"op\.([a-z_]+)\(", downgrade) == ["drop_table"] * 4
    assert re.findall(r"op\.drop_table\('([^']+)'\)", downgrade) == list(reversed(_TABLES))


@pytest.mark.parametrize("table", _TABLES)
def test_the_revision_declares_every_expected_column_and_check(table):
    _, source = _load_migration()
    block = _table_block(source, table)
    assert set(re.findall(r"sa\.Column\('([^']+)'", block)) == _EXPECTED[table]["columns"]
    for name in _EXPECTED[table]["checks"]:
        assert f"name='{name}'" in block, name
    flat = _flat(source)
    checks = _model_checks(_MODELS[table])
    extended = table == "payment_audit_events"
    added = _M05_ADDED["checks"] | _M07_ADDED["checks"] if extended else set()
    added = added | _M10_ADDED.get(table, {}).get("checks", set())
    replaced = _M05_REPLACED_CHECKS if extended else set()
    assert set(checks) == _EXPECTED[table]["checks"] | added
    for name, expression in checks.items():
        if name in added | replaced:
            assert expression not in flat, (name, expression)
        else:
            assert expression in flat, (name, expression)


def test_the_migrations_closed_sets_match_the_application_enums():
    _, source = _load_migration()
    flat = _flat(source)
    # M04 wrote the five invoice event kinds; Phase 5 / M05 widened the set,
    # and Phase 5 / M10 added ``invoice_deleted``.
    m04_kinds = [kind for kind in PaymentAuditEventKind if kind.value.startswith("invoice_")
                 and kind is not PaymentAuditEventKind.INVOICE_DELETED]
    for column, enum in (("status", InvoiceStatus), ("kind", InvoiceItemKind),
                         ("status", InvoiceItemStatus), ("kind", m04_kinds)):
        expected = f"{column} IN (" + ", ".join(f"'{member.value}'" for member in enum) + ")"
        assert expected in flat, expected
    for fragment in ("sa.Column('amount', sa.DECIMAL(precision=19, scale=4), nullable=False)",
                     "sa.Column('before_snapshot', sa.JSON(), nullable=True)",
                     "sa.Column('after_snapshot', sa.JSON(), nullable=False)",
                     "sa.Column('invoice_number', sa.String(length=15), nullable=True)"):
        assert fragment in source, fragment


# ===========================================================================
# Execution against an isolated SQLite database
# ===========================================================================

_PREREQ = [
    """CREATE TABLE users (
        id INTEGER NOT NULL, public_id VARCHAR(36) NOT NULL, role VARCHAR(32) NOT NULL,
        status VARCHAR(32) NOT NULL, PRIMARY KEY (id), UNIQUE (public_id)
    )""",
    """CREATE TABLE groups (
        id INTEGER NOT NULL, public_id VARCHAR(36) NOT NULL, name VARCHAR(100) NOT NULL,
        PRIMARY KEY (id), UNIQUE (public_id)
    )""",
    """CREATE TABLE enrollments (
        id INTEGER NOT NULL, public_id VARCHAR(36) NOT NULL, student_id INTEGER NOT NULL,
        group_id INTEGER NOT NULL, status VARCHAR(32) NOT NULL, PRIMARY KEY (id),
        UNIQUE (public_id), FOREIGN KEY(student_id) REFERENCES users (id),
        FOREIGN KEY(group_id) REFERENCES groups (id)
    )""",
    """CREATE TABLE fee_plans (
        id INTEGER NOT NULL, public_id VARCHAR(36) NOT NULL, name VARCHAR(150) NOT NULL,
        currency_code VARCHAR(3) NOT NULL, status VARCHAR(32) NOT NULL,
        created_by_id INTEGER NOT NULL, version INTEGER NOT NULL, PRIMARY KEY (id),
        UNIQUE (public_id), FOREIGN KEY(created_by_id) REFERENCES users (id)
    )""",
    """CREATE TABLE fee_plan_items (
        id INTEGER NOT NULL, public_id VARCHAR(36) NOT NULL, fee_plan_id INTEGER NOT NULL,
        kind VARCHAR(32) NOT NULL, label VARCHAR(150) NOT NULL, amount DECIMAL(19, 4) NOT NULL,
        status VARCHAR(32) NOT NULL, PRIMARY KEY (id), UNIQUE (public_id),
        FOREIGN KEY(fee_plan_id) REFERENCES fee_plans (id)
    )""",
    """CREATE TABLE student_fee_assignments (
        id INTEGER NOT NULL, public_id VARCHAR(36) NOT NULL, enrollment_id INTEGER NOT NULL,
        fee_plan_id INTEGER NOT NULL, status VARCHAR(32) NOT NULL,
        assigned_by_id INTEGER NOT NULL, version INTEGER NOT NULL, PRIMARY KEY (id),
        UNIQUE (public_id), FOREIGN KEY(enrollment_id) REFERENCES enrollments (id),
        FOREIGN KEY(fee_plan_id) REFERENCES fee_plans (id),
        FOREIGN KEY(assigned_by_id) REFERENCES users (id)
    )""",
    "INSERT INTO users (id, public_id, role, status) VALUES"
    " (11, 'u-11', 'administrator', 'active'), (12, 'u-12', 'administrator', 'suspended'),"
    " (13, 'u-13', 'student', 'active'), (14, 'u-14', 'student', 'suspended')",
    "INSERT INTO groups (id, public_id, name) VALUES (21, 'g-21', 'Group A'),"
    " (22, 'g-22', 'Group B')",
    "INSERT INTO enrollments (id, public_id, student_id, group_id, status) VALUES"
    " (31, 'e-31', 13, 21, 'active'), (32, 'e-32', 14, 22, 'withdrawn')",
    "INSERT INTO fee_plans (id, public_id, name, currency_code, status, created_by_id, version)"
    " VALUES (41, 'p-41', 'Standard', 'LYD', 'active', 11, 2),"
    " (42, 'p-42', 'Old', 'LYD', 'archived', 11, 3)",
    "INSERT INTO fee_plan_items (id, public_id, fee_plan_id, kind, label, amount, status) VALUES"
    " (51, 'i-51', 41, 'registration', 'Registration', 50.0, 'active'),"
    " (52, 'i-52', 41, 'course', 'Course', 1200.5, 'active')",
    "INSERT INTO student_fee_assignments (id, public_id, enrollment_id, fee_plan_id, status,"
    " assigned_by_id, version) VALUES (61, 'a-61', 31, 41, 'assigned', 11, 1),"
    " (62, 'a-62', 32, 42, 'cancelled', 11, 2)",
]

_EXISTING_TABLES = ("users", "groups", "enrollments", "fee_plans", "fee_plan_items",
                    "student_fee_assignments")

_T0 = "'2026-06-01 09:00:00'"
_T1 = "'2026-06-02 09:00:00'"
_T2 = "'2026-06-03 09:00:00'"
_JSON = "'{}'"

_INVOICE = ("INSERT INTO invoices (id, public_id, student_fee_assignment_id, currency_code, status,"
            " invoice_number, issued_at, issued_by_id, cancelled_at, cancelled_by_id, version,"
            " created_at, updated_at) VALUES ")
_ITEM = ("INSERT INTO invoice_items (id, public_id, invoice_id, kind, label, amount, status,"
         " removed_at, removed_by_id, version, created_at, updated_at) VALUES ")
_EVENT = ("INSERT INTO payment_audit_events (id, invoice_id, actor_id, kind, occurred_at,"
          " invoice_version_before, invoice_version_after, reason, before_snapshot,"
          " after_snapshot) VALUES ")
_SEQUENCE = ("INSERT INTO invoice_number_sequences (id, calendar_year, last_number, created_at,"
             " updated_at) VALUES ")


def _existing_rows(conn):
    return {
        table: conn.execute(sa.text(f"SELECT * FROM {table} ORDER BY id")).fetchall()
        for table in _EXISTING_TABLES
    }


def _shape(conn, table):
    schema = inspect(conn)
    return {
        "columns": {c["name"] for c in schema.get_columns(table)},
        "nullable": {c["name"] for c in schema.get_columns(table) if c["nullable"]},
        "checks": {c["name"] for c in schema.get_check_constraints(table)},
        "indexes": {i["name"]: i["column_names"] for i in schema.get_indexes(table)},
        "fks": {(fk["constrained_columns"][0], fk["referred_table"])
                for fk in schema.get_foreign_keys(table)},
        "uniques": sorted((u["name"] or "", tuple(u["column_names"]))
                          for u in schema.get_unique_constraints(table)),
    }


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
        assert set(inspect(conn).get_table_names()) - tables_before == set(_TABLES)
        for table in _TABLES:
            assert _shape(conn, table) == _EXPECTED[table], table
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        assert _existing_rows(conn) == before

        # Legitimate rows in every lifecycle state.
        conn.execute(sa.text(_SEQUENCE + f"(1, 2026, 2, {_T0}, {_T1})"))
        conn.execute(sa.text(
            _INVOICE
            + f"(1, 'inv-1', 61, 'LYD', 'draft', NULL, NULL, NULL, NULL, NULL, 1, {_T0}, {_T0}),"
            f" (2, 'inv-2', 61, 'LYD', 'cancelled', 'INV-2026-000001', {_T1}, 11, {_T2}, 11, 3,"
            f" {_T0}, {_T2}),"
            f" (3, 'inv-3', 62, 'LYD', 'issued', 'INV-2026-000002', {_T1}, 11, NULL, NULL, 2,"
            f" {_T0}, {_T1})"))
        conn.execute(sa.text(
            _ITEM
            + f"(1, 'it-1', 1, 'registration', 'Registration', 50.0, 'active', NULL, NULL, 1,"
            f" {_T0}, {_T0}),"
            f" (2, 'it-2', 2, 'course', 'Course', 1200.5, 'removed', {_T1}, 11, 2, {_T0}, {_T1})"))
        conn.execute(sa.text(
            _EVENT
            + f"(1, 1, 11, 'invoice_draft_created', {_T0}, NULL, 1, NULL, NULL, {_JSON}),"
            f" (2, 2, 11, 'invoice_cancelled', {_T2}, 2, 3, 'Duplicate', {_JSON}, {_JSON})"))
        conn.commit()

        for statement, rule in (
            (_INVOICE + f"(9, 'inv-9', 61, 'LYD', 'paid', NULL, NULL, NULL, NULL, NULL, 1, {_T0},"
             f" {_T0})", "the invoice status CHECK"),
            (_INVOICE + f"(9, 'inv-9', 61, 'LYD', 'issued', 'INV-26-1', {_T1}, 11, NULL, NULL, 2,"
             f" {_T0}, {_T1})", "the number format CHECK"),
            (_INVOICE + f"(9, 'inv-9', 61, 'LYD', 'issued', 'INV-2026-000001', {_T1}, 11, NULL,"
             f" NULL, 2, {_T0}, {_T1})", "the unique invoice number"),
            (_INVOICE + f"(9, 'inv-9', 61, 'LYD', 'draft', NULL, NULL, NULL, {_T2}, 11, 2, {_T0},"
             f" {_T2})", "the lifecycle CHECK"),
            (_INVOICE + f"(9, 'inv-9', 61, 'LYD', 'issued', 'INV-2026-000009', {_T1}, NULL, NULL,"
             f" NULL, 2, {_T0}, {_T1})", "the issue pair"),
            (_INVOICE + f"(9, 'inv-9', 999, 'LYD', 'draft', NULL, NULL, NULL, NULL, NULL, 1,"
             f" {_T0}, {_T0})", "the assignment foreign key"),
            (_ITEM + f"(9, 'it-9', 1, 'course', 'Course', 0, 'active', NULL, NULL, 1, {_T0},"
             f" {_T0})", "the amount CHECK"),
            (_ITEM + f"(9, 'it-9', 1, 'course', 'Course', 1, 'removed', NULL, NULL, 1, {_T0},"
             f" {_T0})", "the removal CHECK"),
            (_ITEM + f"(9, 'it-9', 999, 'course', 'Course', 1, 'active', NULL, NULL, 1, {_T0},"
             f" {_T0})", "the invoice foreign key"),
            (_EVENT + f"(9, 1, 11, 'invoice_cancelled', {_T2}, 1, 2, NULL, {_JSON}, {_JSON})",
             "the reason CHECK"),
            (_EVENT + f"(9, 1, 11, 'invoice_draft_edited', {_T0}, 1, 3, NULL, {_JSON}, {_JSON})",
             "the version transition CHECK"),
            (_EVENT + f"(9, 1, 11, 'payment_recorded', {_T0}, 1, 2, NULL, {_JSON}, {_JSON})",
             "the event kind CHECK"),
            (_EVENT + f"(9, 1, 999, 'invoice_draft_created', {_T0}, NULL, 1, NULL, NULL,"
             f" {_JSON})", "the actor foreign key"),
            (_SEQUENCE + f"(9, 2026, 0, {_T0}, {_T0})", "the unique year"),
            (_SEQUENCE + f"(9, 2030, 1000000, {_T0}, {_T0})", "the sequence range"),
            ("DELETE FROM student_fee_assignments WHERE id = 61", "no cascade from an assignment"),
            ("DELETE FROM invoices WHERE id = 1", "no cascade from an invoice"),
            ("DELETE FROM users WHERE id = 11", "no cascade from an account"),
        ):
            _refused(conn, statement, rule)

        # Leave no probe row behind, then reverse.
        for table in reversed(_TABLES):
            conn.execute(sa.text(f"DELETE FROM {table}"))
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
            shapes.append({table: _shape(conn, table) for table in _TABLES})
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


@pytest.mark.parametrize("table", _TABLES)
def test_the_model_and_migration_agree(app, table):
    _, source = _load_migration()
    with app.app_context():
        declared = dict(re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)",
                                   _table_block(source, table)))
        inspector = inspect(db.engine)
        actual = {c["name"]: str(c["nullable"]) for c in inspector.get_columns(table)}
        declared.pop("id", None)
        actual.pop("id", None)
        extended = table == "payment_audit_events"
        for column in _M05_ADDED["columns"] if extended else ():
            assert actual.pop(column) == "True", column
        for column in _M07_ADDED["nullable"] if extended else ():
            assert (declared.pop(column), actual.pop(column)) == ("False", "True"), column
        m10 = _M10_ADDED.get(table, {})
        for column in m10.get("columns", ()):
            assert actual.pop(column) == "True", column
        assert declared == actual
        shape = {
            "indexes": {i["name"]: i["column_names"] for i in inspector.get_indexes(table)
                        if (not extended or i["name"] not in _M05_ADDED["indexes"])
                        and i["name"] not in m10.get("indexes", set())},
            "uniques": sorted((u["name"] or "", tuple(u["column_names"]))
                              for u in inspector.get_unique_constraints(table)),
            "checks": {c["name"] for c in inspector.get_check_constraints(table)}
            - (_M05_ADDED["checks"] | _M07_ADDED["checks"] if extended else set())
            - m10.get("checks", set()),
            "fks": {(fk["constrained_columns"][0], fk["referred_table"])
                    for fk in inspector.get_foreign_keys(table)}
            - (_M05_ADDED["fks"] if extended else set()) - m10.get("fks", set()),
        }
        assert shape == {key: _EXPECTED[table][key] for key in shape}


_DDL_FRAGMENTS = {
    "invoice_number_sequences": (
        "id BIGINT NOT NULL AUTO_INCREMENT", "calendar_year INTEGER NOT NULL",
        "last_number INTEGER NOT NULL",
        "CONSTRAINT uq_invoice_number_sequences_calendar_year UNIQUE (calendar_year)",
    ),
    "invoices": (
        "id BIGINT NOT NULL AUTO_INCREMENT", "public_id VARCHAR(36) NOT NULL",
        "student_fee_assignment_id BIGINT NOT NULL", "currency_code VARCHAR(3) NOT NULL",
        "status VARCHAR(32) NOT NULL", "invoice_number VARCHAR(15),", "issued_at DATETIME,",
        "issued_by_id BIGINT,", "cancelled_at DATETIME,", "cancelled_by_id BIGINT,",
        "version INTEGER NOT NULL",
        "FOREIGN KEY(student_fee_assignment_id) REFERENCES student_fee_assignments (id)",
        "FOREIGN KEY(issued_by_id) REFERENCES users (id)",
        "FOREIGN KEY(cancelled_by_id) REFERENCES users (id)", "UNIQUE (public_id)",
        "CONSTRAINT uq_invoices_invoice_number UNIQUE (invoice_number)",
    ),
    "invoice_items": (
        "id BIGINT NOT NULL AUTO_INCREMENT", "invoice_id BIGINT NOT NULL",
        "label VARCHAR(150) NOT NULL", "amount DECIMAL(19, 4) NOT NULL",
        "removed_at DATETIME,", "removed_by_id BIGINT,",
        "FOREIGN KEY(invoice_id) REFERENCES invoices (id)",
        "FOREIGN KEY(removed_by_id) REFERENCES users (id)", "UNIQUE (public_id)",
    ),
    "payment_audit_events": (
        "id BIGINT NOT NULL AUTO_INCREMENT", "invoice_id BIGINT NOT NULL",
        # Phase 5 / M07: a system-origin online event has no actor.
        "actor_id BIGINT,", "kind VARCHAR(40) NOT NULL", "occurred_at DATETIME NOT NULL",
        "invoice_version_before INTEGER,", "invoice_version_after INTEGER NOT NULL",
        "reason VARCHAR(500),", "before_snapshot JSON,", "after_snapshot JSON NOT NULL",
        "FOREIGN KEY(invoice_id) REFERENCES invoices (id)",
        "FOREIGN KEY(actor_id) REFERENCES users (id)",
    ),
}


@pytest.mark.parametrize("table", _TABLES)
def test_mysql_ddl_compiles_without_a_connection(table):
    model = _MODELS[table]
    ddl = str(CreateTable(model.__table__).compile(dialect=mysql.dialect()))
    flat = " ".join(ddl.split())
    for fragment in _DDL_FRAGMENTS[table]:
        assert fragment in flat, fragment
    for name, expression in _model_checks(model).items():
        assert f"CONSTRAINT {name} CHECK ({expression})" in flat, name
    for forbidden in ("ON DELETE", "ON UPDATE", "DATETIME(", "ENUM(", "FLOAT", "DOUBLE",
                      "ENGINE=", "CHARSET", "TRIGGER"):
        assert forbidden not in ddl.upper(), forbidden
    assert model.__table__.kwargs == {}


@pytest.mark.parametrize("table", _TABLES)
def test_no_column_is_shaped_for_card_or_payment_data(table):
    for column in _MODELS[table].__table__.columns:
        # Phase 5 / M05's audit links name a payment transaction and a receipt
        # by id; they hold no payment data.
        if table == "payment_audit_events" and column.name in _M05_ADDED["columns"]:
            continue
        # Phase 5 / M10's tombstone names who deleted the row, and why.
        if column.name in _M10_ADDED.get(table, {}).get("columns", set()):
            continue
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


def test_the_offline_mysql_upgrade_creates_only_the_tables_and_their_indexes():
    sql = _offline_mysql("upgrade")
    steps = []
    for table in _TABLES:
        steps.append(sql.index(f"CREATE TABLE {table} ("))
        steps += [sql.index(f"CREATE INDEX {name} ON {table}") for name in _EXPECTED[table]["indexes"]]
    assert steps == sorted(steps)
    assert sql.count("CREATE TABLE") == 4
    assert sql.count("CREATE INDEX") == 7
    assert "before_snapshot JSON" in sql and "amount DECIMAL(19, 4) NOT NULL" in sql
    for forbidden in ("ALTER TABLE", "ON DELETE", "ON UPDATE", "ENGINE=", "CHARSET", "DROP",
                      "DELETE FROM", "INSERT INTO", "UPDATE ", "TRIGGER"):
        assert forbidden not in sql, forbidden


def test_the_offline_mysql_downgrade_drops_only_the_tables():
    sql = _offline_mysql("downgrade")
    assert sql.count("DROP TABLE") == 4
    assert [sql.index(f"DROP TABLE {table}") for table in reversed(_TABLES)] == sorted(
        sql.index(f"DROP TABLE {table}") for table in _TABLES)
    for forbidden in ("DROP INDEX", "ALTER TABLE", "DELETE FROM", "CREATE", "student_fee_assignments",
                      "fee_plans"):
        assert forbidden not in sql, forbidden
