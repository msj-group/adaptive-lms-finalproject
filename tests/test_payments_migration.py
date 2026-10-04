"""Phase 5 / M05 migration checks for ``payment_transactions``,
``receipt_number_sequences``, ``receipts`` and the extension of
``payment_audit_events``.

The execution probes use an isolated temporary SQLite database seeded with
minimal existing tables and representative rows -- accounts, Groups,
Enrollments, fee plans and assignments -- upgraded by the M04 revision and
then given representative M04 invoices, lines, number sequences and **audit
events**, so "every existing M04 event stays valid and unchanged" is executed
rather than asserted. Both directions run with foreign keys **enforced** and
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
    PaymentAuditEvent,
    PaymentAuditEventKind,
    PaymentMethod,
    PaymentTransaction,
    PaymentTransactionKind,
    PaymentTransactionStatus,
    Receipt,
    ReceiptNumberSequence,
    ReceiptStatus,
)

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "c5e8f2a7d914"
_DOWN_REVISION = "a8d3f5c29e61"
#: Phase 5 / M06 and then M07 follow this revision, so the single head is now
#: M07's. M07 extends three of this revision's tables; its revision records the
#: M05 text of every CHECK it replaces, which this suite compares against.
#: Phase 5 / M10 follows M07; the Phase 6 replacement's destructive revision is now the single head.
_HEAD = "d574ab56594f"
_M07 = "e9c4b2d7a1f3"

_NEW_TABLES = ["payment_transactions", "receipt_number_sequences", "receipts"]
_EVENTS = "payment_audit_events"
_MODELS = {
    "payment_transactions": PaymentTransaction,
    "receipt_number_sequences": ReceiptNumberSequence,
    "receipts": Receipt,
    _EVENTS: PaymentAuditEvent,
}

_M04_EVENTS = {
    "columns": {"id", "invoice_id", "actor_id", "kind", "occurred_at", "invoice_version_before",
                "invoice_version_after", "reason", "before_snapshot", "after_snapshot"},
    "nullable": {"invoice_version_before", "reason", "before_snapshot"},
    "checks": {"ck_payment_audit_events_kind_valid", "ck_payment_audit_events_versions_positive",
               "ck_payment_audit_events_version_transition",
               "ck_payment_audit_events_snapshots_present",
               "ck_payment_audit_events_reason_required"},
    "indexes": {"ix_payment_audit_events_invoice_id_id": ["invoice_id", "id"],
                "ix_payment_audit_events_actor_id": ["actor_id"]},
    "fks": {("invoice_id", "invoices"), ("actor_id", "users")},
    "uniques": [],
}

_EXPECTED = {
    "payment_transactions": {
        "columns": {"id", "public_id", "invoice_id", "kind", "method", "status", "currency_code",
                    "amount", "bank_transfer_reference", "bank_transfer_date", "recorded_at",
                    "recorded_by_id", "confirmed_at", "confirmed_by_id", "rejected_at",
                    "rejected_by_id", "rejection_reason", "reversal_of_payment_transaction_id",
                    "version", "created_at", "updated_at"},
        "nullable": {"bank_transfer_reference", "bank_transfer_date", "confirmed_at",
                     "confirmed_by_id", "rejected_at", "rejected_by_id", "rejection_reason",
                     "reversal_of_payment_transaction_id"},
        "checks": {"ck_payment_transactions_kind_valid", "ck_payment_transactions_method_valid",
                   "ck_payment_transactions_status_valid", "ck_payment_transactions_currency_code",
                   "ck_payment_transactions_amount_range",
                   "ck_payment_transactions_version_positive",
                   "ck_payment_transactions_bank_transfer_details",
                   "ck_payment_transactions_confirmation_pair",
                   "ck_payment_transactions_rejection_state",
                   "ck_payment_transactions_lifecycle_state",
                   "ck_payment_transactions_immediate_confirmation",
                   "ck_payment_transactions_reversal_link",
                   "ck_payment_transactions_timestamps_ordered"},
        "indexes": {"ix_payment_transactions_invoice_id_id": ["invoice_id", "id"],
                    "ix_payment_transactions_status_id": ["status", "id"],
                    "ix_payment_transactions_method_id": ["method", "id"],
                    "ix_payment_transactions_recorded_by_id": ["recorded_by_id"],
                    "ix_payment_transactions_confirmed_by_id": ["confirmed_by_id"],
                    "ix_payment_transactions_rejected_by_id": ["rejected_by_id"]},
        "fks": {("invoice_id", "invoices"), ("recorded_by_id", "users"),
                ("confirmed_by_id", "users"), ("rejected_by_id", "users"),
                ("reversal_of_payment_transaction_id", "payment_transactions")},
        "uniques": [("", ("public_id",)),
                    ("uq_payment_transactions_reversal_of",
                     ("reversal_of_payment_transaction_id",))],
    },
    "receipt_number_sequences": {
        "columns": {"id", "calendar_year", "last_number", "created_at", "updated_at"},
        "nullable": set(),
        "checks": {"ck_receipt_number_sequences_year_range",
                   "ck_receipt_number_sequences_last_number_range",
                   "ck_receipt_number_sequences_timestamps_ordered"},
        "indexes": {},
        "fks": set(),
        "uniques": [("uq_receipt_number_sequences_calendar_year", ("calendar_year",))],
    },
    "receipts": {
        "columns": {"id", "public_id", "payment_transaction_id", "receipt_number", "status",
                    "issued_at", "issued_by_id", "voided_at", "voided_by_id", "void_reason",
                    "snapshot", "version", "created_at", "updated_at"},
        "nullable": {"voided_at", "voided_by_id", "void_reason"},
        "checks": {"ck_receipts_status_valid", "ck_receipts_version_positive",
                   "ck_receipts_number_format", "ck_receipts_void_state",
                   "ck_receipts_timestamps_ordered"},
        "indexes": {"ix_receipts_issued_by_id": ["issued_by_id"],
                    "ix_receipts_voided_by_id": ["voided_by_id"]},
        "fks": {("payment_transaction_id", "payment_transactions"), ("issued_by_id", "users"),
                ("voided_by_id", "users")},
        "uniques": [("", ("public_id",)),
                    ("uq_receipts_payment_transaction_id", ("payment_transaction_id",)),
                    ("uq_receipts_receipt_number", ("receipt_number",))],
    },
    _EVENTS: {
        "columns": _M04_EVENTS["columns"] | {"payment_transaction_id", "receipt_id"},
        "nullable": _M04_EVENTS["nullable"] | {"payment_transaction_id", "receipt_id"},
        "checks": _M04_EVENTS["checks"] | {"ck_payment_audit_events_subject_links"},
        "indexes": dict(_M04_EVENTS["indexes"],
                        ix_payment_audit_events_payment_transaction_id=["payment_transaction_id"],
                        ix_payment_audit_events_receipt_id=["receipt_id"]),
        "fks": _M04_EVENTS["fks"] | {("payment_transaction_id", "payment_transactions"),
                                     ("receipt_id", "receipts")},
        "uniques": [],
    },
}

#: Column-name parts that would mean card, account or credential data.
_PROHIBITED_PARTS = (
    "card", "pan", "cvv", "cvc", "pin", "iban", "swift", "account", "token", "secret",
    "password", "credential", "provider", "intent", "customer", "webhook", "proof", "upload",
    "image", "refund", "tax", "total", "paid", "outstanding", "balance",
)


def _load(revision, prefix):
    path = next(p for p in _MIGRATIONS.glob("*.py") if revision in p.name)
    spec = importlib.util.spec_from_file_location(f"{prefix}_{revision}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def _load_migration():
    return _load(_REVISION, "p5m05")


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


#: What Phase 5 / M07 (``e9c4b2d7a1f3``) added to this revision's tables, which
#: the current model carries.
_M07_ADDITIONS = {
    "payment_transactions": {
        "columns": {"payment_intent_id"},
        "nullable": {"payment_intent_id", "recorded_by_id"},
        "checks": {"ck_payment_transactions_online_origin"},
        "indexes": {},
        "fks": {("payment_intent_id", "payment_intents")},
        "uniques": [("uq_payment_transactions_payment_intent_id", ("payment_intent_id",))],
    },
    "receipt_number_sequences": {},
    "receipts": {"nullable": {"issued_by_id"}},
    "payment_audit_events": {
        "nullable": {"actor_id"},
        "checks": {"ck_payment_audit_events_actor_origin"},
    },
}


#: Phase 5 / M10 (``b3d8f1a6c472``)'s visible-deletion tombstone on two of this
#: revision's tables: three nullable columns, one CHECK, two indexes and one
#: foreign key each. It also widened audit-event CHECKs M07 had replaced.
#: ``tests/test_financial_deletion_migration.py`` compares those with the M10
#: revision.
_M10_ADDITIONS = {
    table: {
        "columns": {"deleted_at", "deleted_by_id", "deletion_reason"},
        "nullable": {"deleted_at", "deleted_by_id", "deletion_reason"},
        "checks": {f"ck_{table}_deletion_state"},
        "indexes": {f"ix_{table}_deleted_at_id": ["deleted_at", "id"],
                    f"ix_{table}_deleted_by_id": ["deleted_by_id"]},
        "fks": {("deleted_by_id", "users")},
    }
    for table in ("payment_transactions", "receipts")
}


def _m05_checks(model):
    """The model's CHECKs as this revision declared them: M07's and M10's
    replacements mapped back to the M05 text M07 records, M07's and M10's
    additions left out."""
    m07, _ = _load(_M07, "p5m07")
    replaced = {name: old for group in (m07._PAYMENT_CHECKS, m07._AUDIT_CHECKS)
                for name, old, _new in group}
    added = _M07_ADDITIONS.get(model.__tablename__, {}).get("checks", set())
    added = added | _M10_ADDITIONS.get(model.__tablename__, {}).get("checks", set())
    return {name: replaced.get(name, expression)
            for name, expression in _model_checks(model).items() if name not in added}


def _current_shape(table):
    """This revision's expected shape of `table` plus M07's additions -- what
    the current model creates."""
    expected, additions = _EXPECTED[table], _M07_ADDITIONS.get(table, {})
    later = _M10_ADDITIONS.get(table, {})
    return {
        "columns": expected["columns"] | additions.get("columns", set())
        | later.get("columns", set()),
        "nullable": expected["nullable"] | additions.get("nullable", set())
        | later.get("nullable", set()),
        "checks": expected["checks"] | additions.get("checks", set())
        | later.get("checks", set()),
        "indexes": dict(expected["indexes"], **additions.get("indexes", {}),
                        **later.get("indexes", {})),
        "fks": expected["fks"] | additions.get("fks", set()) | later.get("fks", set()),
        "uniques": sorted(expected["uniques"] + additions.get("uniques", [])),
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
    assert set(parents) - {p for p in parents.values() if p is not None} == {_HEAD}
    claimed = [p for p in parents.values() if p is not None]
    assert len(claimed) == len(set(claimed))
    assert [r for r, p in parents.items() if p == _DOWN_REVISION] == [_REVISION]
    assert len([r for r, p in parents.items() if p is None]) == 1


def test_the_revision_creates_three_tables_and_extends_only_the_audit_trail():
    _, source = _load_migration()
    code = _code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == _NEW_TABLES
    assert set(re.findall(r"\b(op\.\w+|batch_op\.\w+)\(", code)) == {
        "op.create_table", "op.create_index", "op.get_bind", "op.get_context",
        "op.batch_alter_table", "op.add_column", "op.create_foreign_key", "op.drop_constraint",
        "op.create_check_constraint", "op.drop_index", "op.drop_column", "op.drop_table",
        "batch_op.add_column", "batch_op.create_foreign_key", "batch_op.create_index",
        "batch_op.drop_column",
    }
    for forbidden in ("op.execute", "op.bulk_insert", "alter_column", "ondelete", "onupdate",
                      "mysql_engine", "mysql_charset", "server_default", "Float", "Numeric(",
                      "Enum(", "trigger", "TRIGGER", "INSERT", "UPDATE ", "DELETE FROM",
                      "card", "account_number", "provider", "webhook", "refund_"):
        assert forbidden not in code, forbidden
    # Every alteration names the audit trail and nothing else.
    for operation in ("add_column", "drop_column", "drop_constraint", "create_check_constraint",
                      "batch_alter_table"):
        for target in re.findall(rf"(?<!\w)op\.{operation}\(\s*([^,\s)]+)", code):
            assert target in ("_EVENTS", "name", "_LINKS_CHECK", "_RECEIPT_FK", "_PAYMENT_FK"), (
                operation, target)
    assert re.findall(r"SELECT[^\"]*", code)[0].startswith("SELECT (SELECT COUNT(*) FROM")
    upgrade = _function(code, "upgrade")
    for forbidden in ("drop_table", "drop_index", "drop_column"):
        assert forbidden not in upgrade, forbidden


def test_the_replaced_checks_widen_the_m04_expressions_and_match_the_model():
    module, _ = _load_migration()
    _, m04_source = _load(_DOWN_REVISION, "p5m04")
    m04_flat = _flat(m04_source)
    checks = _m05_checks(PaymentAuditEvent)
    for name, before, after in module._REPLACED_CHECKS:
        assert before in m04_flat, name
        assert checks[name] == after, name
        assert before != after
    assert checks["ck_payment_audit_events_subject_links"] == module._LINKS
    assert checks["ck_payment_audit_events_versions_positive"] == module._VERSIONS_POSITIVE
    assert checks["ck_payment_audit_events_snapshots_present"] == module._SNAPSHOTS_PRESENT
    for kind in PaymentAuditEventKind:
        if kind.value in ("payment_online_confirmed", "receipt_online_issued"):
            continue  # Phase 5 / M07's kinds.
        if kind.value in ("invoice_deleted", "payment_deleted", "payment_replaced",
                          "receipt_deleted"):
            continue  # Phase 5 / M10's kinds.
        assert f"'{kind.value}'" in module._KIND_AFTER, kind
        assert (f"'{kind.value}'" in module._KIND_BEFORE) == kind.value.startswith("invoice_")
    # The M04 branches of the version CHECK are kept exactly, per kind.
    assert module._VERSION_AFTER.startswith(module._VERSION_BEFORE.split(" OR ")[0])


def test_the_rebuild_definitions_are_the_m04_table_with_the_given_checks():
    module, _ = _load_migration()

    def shape(table):
        return (
            [(c.name, str(c.type.compile(dialect=mysql.dialect())), c.nullable) for c in table.columns],
            {c.name: " ".join(str(c.sqltext).split()) for c in table.constraints
             if isinstance(c, sa.CheckConstraint)},
            {(i.name, tuple(c.name for c in i.columns)) for i in table.indexes},
        )

    model = PaymentAuditEvent.__table__
    upgraded_columns, upgraded_checks, upgraded_indexes = shape(module._audit_events_table(
        module._KIND_AFTER, module._VERSION_AFTER, module._REASON_AFTER, module._LINKS))
    # Phase 5 / M07 made ``actor_id`` nullable; this revision declared it not.
    model_columns = {name: (kind, nullable and name != "actor_id")
                     for name, kind, nullable in shape(model)[0]}
    for name, kind, nullable in upgraded_columns:
        assert model_columns[name] == (kind, nullable), name
    assert set(model_columns) - {name for name, _, _ in upgraded_columns} == {
        "payment_transaction_id", "receipt_id"}
    assert upgraded_checks == _m05_checks(PaymentAuditEvent)
    assert upgraded_indexes == {("ix_payment_audit_events_invoice_id_id", ("invoice_id", "id")),
                                ("ix_payment_audit_events_actor_id", ("actor_id",))}
    _, restored_checks, _ = shape(module._audit_events_table(
        module._KIND_BEFORE, module._VERSION_BEFORE, module._REASON_BEFORE, link_columns=True))
    assert set(restored_checks) == _M04_EVENTS["checks"]


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
    " (13, 'u-13', 'student', 'active')",
    "INSERT INTO groups (id, public_id, name) VALUES (21, 'g-21', 'Group A')",
    "INSERT INTO enrollments (id, public_id, student_id, group_id, status) VALUES"
    " (31, 'e-31', 13, 21, 'active')",
    "INSERT INTO fee_plans (id, public_id, name, currency_code, status, created_by_id, version)"
    " VALUES (41, 'p-41', 'Standard', 'LYD', 'active', 11, 2)",
    "INSERT INTO student_fee_assignments (id, public_id, enrollment_id, fee_plan_id, status,"
    " assigned_by_id, version) VALUES (61, 'a-61', 31, 41, 'assigned', 11, 1)",
]

_EXISTING_TABLES = ("users", "groups", "enrollments", "fee_plans", "student_fee_assignments")
_M04_TABLES = ("invoice_number_sequences", "invoices", "invoice_items", _EVENTS)

_T0 = "'2026-06-01 09:00:00'"
_T1 = "'2026-06-02 09:00:00'"
_T2 = "'2026-06-03 09:00:00'"
_INVOICE_JSON = "'{\"schema\": \"phase5-m04.invoice.v1\", \"total\": \"1250.5000\"}'"
_JSON = "'{}'"

#: Representative M04 financial history, including every kind of M04 event.
_M04_ROWS = [
    "INSERT INTO invoice_number_sequences (id, calendar_year, last_number, created_at, updated_at)"
    f" VALUES (1, 2026, 2, {_T0}, {_T1})",
    "INSERT INTO invoices (id, public_id, student_fee_assignment_id, currency_code, status,"
    " invoice_number, issued_at, issued_by_id, cancelled_at, cancelled_by_id, version, created_at,"
    f" updated_at) VALUES (1, 'inv-1', 61, 'LYD', 'cancelled', 'INV-2026-000001', {_T1}, 11,"
    f" {_T2}, 11, 3, {_T0}, {_T2}),"
    f" (2, 'inv-2', 61, 'LYD', 'issued', 'INV-2026-000002', {_T1}, 11, NULL, NULL, 3, {_T0},"
    f" {_T1})",
    "INSERT INTO invoice_items (id, public_id, invoice_id, kind, label, amount, status, removed_at,"
    " removed_by_id, version, created_at, updated_at) VALUES"
    f" (1, 'it-1', 2, 'registration', 'Registration', 50.0, 'active', NULL, NULL, 1, {_T0}, {_T0}),"
    f" (2, 'it-2', 2, 'course', 'Course', 1200.5, 'active', NULL, NULL, 2, {_T0}, {_T1})",
    "INSERT INTO payment_audit_events (id, invoice_id, actor_id, kind, occurred_at,"
    " invoice_version_before, invoice_version_after, reason, before_snapshot, after_snapshot)"
    f" VALUES (1, 1, 11, 'invoice_draft_created', {_T0}, NULL, 1, NULL, NULL, {_INVOICE_JSON}),"
    f" (2, 1, 11, 'invoice_issued', {_T1}, 1, 2, NULL, {_INVOICE_JSON}, {_INVOICE_JSON}),"
    f" (3, 1, 12, 'invoice_cancelled', {_T2}, 2, 3, 'Duplicate', {_INVOICE_JSON}, {_INVOICE_JSON}),"
    f" (4, 2, 11, 'invoice_draft_created', {_T0}, NULL, 1, NULL, NULL, {_INVOICE_JSON}),"
    f" (5, 2, 11, 'invoice_draft_edited', {_T0}, 1, 2, NULL, {_INVOICE_JSON}, {_INVOICE_JSON}),"
    f" (6, 2, 11, 'invoice_issued_edited', {_T1}, 2, 3, 'Corrected', {_INVOICE_JSON},"
    f" {_INVOICE_JSON})",
]

_PAYMENT = ("INSERT INTO payment_transactions (id, public_id, invoice_id, kind, method, status,"
            " currency_code, amount, bank_transfer_reference, bank_transfer_date, recorded_at,"
            " recorded_by_id, confirmed_at, confirmed_by_id, rejected_at, rejected_by_id,"
            " rejection_reason, reversal_of_payment_transaction_id, version, created_at, updated_at)"
            " VALUES ")
_RECEIPT = ("INSERT INTO receipts (id, public_id, payment_transaction_id, receipt_number, status,"
            " issued_at, issued_by_id, voided_at, voided_by_id, void_reason, snapshot, version,"
            " created_at, updated_at) VALUES ")
_EVENT = ("INSERT INTO payment_audit_events (id, invoice_id, actor_id, kind, occurred_at,"
          " invoice_version_before, invoice_version_after, reason, before_snapshot,"
          " after_snapshot, payment_transaction_id, receipt_id) VALUES ")
_M04_EVENT = ("INSERT INTO payment_audit_events (id, invoice_id, actor_id, kind, occurred_at,"
              " invoice_version_before, invoice_version_after, reason, before_snapshot,"
              " after_snapshot) VALUES ")

_M05_ROWS = [
    _PAYMENT + f"(1, 'pt-1', 2, 'collection', 'cash', 'confirmed', 'LYD', 100, NULL, NULL, {_T1},"
    f" 11, {_T1}, 11, NULL, NULL, NULL, NULL, 1, {_T1}, {_T1}),"
    f" (2, 'pt-2', 2, 'collection', 'bank_transfer', 'pending', 'LYD', 50.5, 'TRX-1',"
    f" '2026-06-01', {_T1}, 11, NULL, NULL, NULL, NULL, NULL, NULL, 1, {_T1}, {_T1}),"
    f" (3, 'pt-3', 2, 'reversal', 'cash', 'confirmed', 'LYD', 100, NULL, NULL, {_T2}, 11, {_T2},"
    f" 11, NULL, NULL, NULL, 1, 1, {_T2}, {_T2})",
    "INSERT INTO receipt_number_sequences (id, calendar_year, last_number, created_at, updated_at)"
    f" VALUES (1, 2026, 1, {_T1}, {_T1})",
    _RECEIPT + f"(1, 'rc-1', 1, 'RCT-2026-000001', 'voided', {_T1}, 11, {_T2}, 11, 'Wrong',"
    f" {_JSON}, 2, {_T1}, {_T2})",
    _EVENT + f"(7, 2, 11, 'payment_cash_recorded', {_T1}, 3, 3, NULL, {_JSON}, {_JSON}, 1, NULL),"
    f" (8, 2, 11, 'receipt_issued', {_T1}, 3, 3, NULL, {_JSON}, {_JSON}, 1, 1),"
    f" (9, 2, 11, 'payment_bank_transfer_recorded', {_T1}, 3, 3, NULL, {_JSON}, {_JSON}, 2, NULL),"
    f" (10, 2, 11, 'payment_reversed', {_T2}, 3, 3, 'Wrong', {_JSON}, {_JSON}, 3, NULL),"
    f" (11, 2, 11, 'receipt_voided', {_T2}, 3, 3, 'Wrong', {_JSON}, {_JSON}, 1, 1)",
]

_M05_KINDS = ("payment_cash_recorded', 'payment_bank_transfer_recorded', "
              "'payment_bank_transfer_confirmed', 'payment_bank_transfer_rejected', "
              "'payment_reversed', 'receipt_issued', 'receipt_voided")


def _rows(conn, table, columns="*"):
    return conn.execute(sa.text(f"SELECT {columns} FROM {table} ORDER BY id")).fetchall()


def _existing_rows(conn):
    return {table: _rows(conn, table) for table in _EXISTING_TABLES}


def _m04_rows(conn):
    rows = {table: _rows(conn, table) for table in _M04_TABLES[:-1]}
    rows[_EVENTS] = _rows(conn, _EVENTS, ", ".join(sorted(_M04_EVENTS["columns"])))
    return rows


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


def _run(conn, module, direction):
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
    """An isolated database at the M04 revision holding representative M04
    financial history, foreign keys enforced."""
    tmp = tempfile.TemporaryDirectory()
    url = "sqlite:///" + os.path.join(tmp.name, name).replace(os.sep, "/")
    engine = sa.create_engine(url)
    conn = engine.connect()
    conn.execute(sa.text("PRAGMA foreign_keys=ON"))
    for statement in _PREREQ:
        conn.execute(sa.text(statement))
    conn.commit()
    m04, _ = _load(_DOWN_REVISION, "p5m04probe")
    _run(conn, m04, "upgrade")
    for statement in _M04_ROWS:
        conn.execute(sa.text(statement))
    conn.commit()
    return tmp, engine, conn


def test_migration_applies_and_reverses_preserving_every_m04_row():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("probe.db")
    try:
        existing, m04 = _existing_rows(conn), _m04_rows(conn)
        tables_before = set(inspect(conn).get_table_names())
        assert _shape(conn, _EVENTS) == _M04_EVENTS
        m04_shapes = {table: _shape(conn, table) for table in _M04_TABLES[:-1]}

        _run(conn, module, "upgrade")
        assert set(inspect(conn).get_table_names()) - tables_before == set(_NEW_TABLES)
        for table in _NEW_TABLES:
            assert _shape(conn, table) == _EXPECTED[table], table
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        assert _shape(conn, _EVENTS) == _EXPECTED[_EVENTS]
        assert {table: _shape(conn, table) for table in _M04_TABLES[:-1]} == m04_shapes
        # Every M04 row -- every M04 event included -- is unchanged, links NULL.
        assert _existing_rows(conn) == existing
        assert _m04_rows(conn) == m04
        assert _rows(conn, _EVENTS, "payment_transaction_id, receipt_id") == [(None, None)] * 6
        assert "_alembic_tmp_payment_audit_events" not in inspect(conn).get_table_names()
        # ... and still satisfies the extended CHECKs: an M04 event can be written.
        conn.execute(sa.text(_M04_EVENT + f"(20, 2, 11, 'invoice_issued_edited', {_T2}, 3, 4,"
                             f" 'Again', {_INVOICE_JSON}, {_INVOICE_JSON})"))
        conn.execute(sa.text("DELETE FROM payment_audit_events WHERE id = 20"))
        conn.commit()

        for statement in _M05_ROWS:
            conn.execute(sa.text(statement))
        conn.commit()
        for statement, rule in (
            (_PAYMENT + f"(9, 'pt-9', 2, 'refund', 'cash', 'confirmed', 'LYD', 1, NULL, NULL, {_T1},"
             f" 11, {_T1}, 11, NULL, NULL, NULL, NULL, 1, {_T1}, {_T1})", "the payment kind CHECK"),
            (_PAYMENT + f"(9, 'pt-9', 2, 'reversal', 'cash', 'confirmed', 'LYD', 100, NULL, NULL,"
             f" {_T2}, 11, {_T2}, 11, NULL, NULL, NULL, 1, 1, {_T2}, {_T2})",
             "uq_payment_transactions_reversal_of"),
            (_PAYMENT + f"(9, 'pt-9', 2, 'collection', 'cash', 'pending', 'LYD', 1, NULL, NULL,"
             f" {_T1}, 11, NULL, NULL, NULL, NULL, NULL, NULL, 1, {_T1}, {_T1})",
             "the payment lifecycle CHECK"),
            (_PAYMENT + f"(9, 'pt-9', 99, 'collection', 'cash', 'confirmed', 'LYD', 1, NULL, NULL,"
             f" {_T1}, 11, {_T1}, 11, NULL, NULL, NULL, NULL, 1, {_T1}, {_T1})",
             "the invoice foreign key"),
            (_RECEIPT + f"(9, 'rc-9', 1, 'RCT-2026-000009', 'issued', {_T1}, 11, NULL, NULL, NULL,"
             f" {_JSON}, 1, {_T1}, {_T1})", "uq_receipts_payment_transaction_id"),
            (_RECEIPT + f"(9, 'rc-9', 2, 'RCT-2026-000001', 'issued', {_T1}, 11, NULL, NULL, NULL,"
             f" {_JSON}, 1, {_T1}, {_T1})", "uq_receipts_receipt_number"),
            (_EVENT + f"(30, 2, 11, 'payment_cash_recorded', {_T1}, 3, 3, NULL, {_JSON}, {_JSON},"
             " NULL, NULL)", "a payment event without its transaction"),
            (_EVENT + f"(30, 2, 11, 'invoice_draft_edited', {_T1}, 3, 4, NULL, {_JSON}, {_JSON},"
             " 1, NULL)", "an invoice event naming a transaction"),
            (_EVENT + f"(30, 2, 11, 'payment_cash_recorded', {_T1}, 3, 4, NULL, {_JSON}, {_JSON},"
             " 1, NULL)", "a payment event moving the invoice version"),
            (_EVENT + f"(30, 2, 11, 'payment_bank_transfer_rejected', {_T1}, 3, 3, NULL, {_JSON},"
             f" {_JSON}, 2, NULL)", "a rejection without its reason"),
            (_EVENT + f"(30, 2, 11, 'payment_cash_recorded', {_T1}, 3, 3, NULL, {_JSON}, {_JSON},"
             " 99, NULL)", "the payment foreign key of the rebuilt table"),
            (_EVENT + f"(30, 99, 11, 'invoice_draft_created', {_T1}, NULL, 1, NULL, NULL, {_JSON},"
             " NULL, NULL)", "the invoice foreign key of the rebuilt table"),
            (_M04_EVENT + f"(30, 2, 11, 'payment_recorded', {_T1}, 3, 4, NULL, {_JSON}, {_JSON})",
             "the extended kind CHECK"),
            ("DELETE FROM payment_transactions WHERE id = 1", "no cascade from a payment"),
            ("DELETE FROM receipts WHERE id = 1", "no cascade from a receipt"),
            ("DELETE FROM invoices WHERE id = 2", "no cascade from an invoice"),
        ):
            _refused(conn, statement, rule)
        with_payments = {table: _rows(conn, table) for table in (*_NEW_TABLES, _EVENTS)}

        # The downgrade refuses while payment history exists, before touching anything.
        with pytest.raises(RuntimeError, match="Refusing to downgrade"):
            with Operations.context(MigrationContext.configure(conn)):
                module.downgrade()
        conn.rollback()
        assert {table: _rows(conn, table) for table in (*_NEW_TABLES, _EVENTS)} == with_payments
        assert _shape(conn, _EVENTS) == _EXPECTED[_EVENTS]

        # With only M04 history, the downgrade restores M04 exactly.
        conn.execute(sa.text(f"DELETE FROM payment_audit_events WHERE kind IN ('{_M05_KINDS}')"))
        for table in ("receipts", "payment_transactions", "receipt_number_sequences"):
            conn.execute(sa.text(f"DELETE FROM {table}"))
        conn.commit()
        _run(conn, module, "downgrade")
        assert set(inspect(conn).get_table_names()) == tables_before
        assert _shape(conn, _EVENTS) == _M04_EVENTS
        assert {table: _shape(conn, table) for table in _M04_TABLES[:-1]} == m04_shapes
        assert _existing_rows(conn) == existing
        assert _m04_rows(conn) == m04
        _refused(conn, _M04_EVENT + f"(30, 2, 11, 'payment_cash_recorded', {_T1}, 3, 3, NULL,"
                 f" {_JSON}, {_JSON})", "the restored M04 kind CHECK")
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_a_second_upgrade_after_a_downgrade_rebuilds_the_same_shape():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("probe2.db")
    try:
        existing, m04 = _existing_rows(conn), _m04_rows(conn)
        shapes = []
        for _ in range(2):
            _run(conn, module, "upgrade")
            shapes.append({table: _shape(conn, table) for table in (*_NEW_TABLES, _EVENTS)})
            _run(conn, module, "downgrade")
            assert _existing_rows(conn) == existing and _m04_rows(conn) == m04
            assert _shape(conn, _EVENTS) == _M04_EVENTS
        assert shapes[0] == shapes[1]
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


# ===========================================================================
# Model / migration agreement, and MySQL DDL
# ===========================================================================


@pytest.mark.parametrize("table", _NEW_TABLES)
def test_the_revision_declares_every_expected_column_and_check(table):
    _, source = _load_migration()
    block = _table_block(source, table)
    assert set(re.findall(r"sa\.Column\('([^']+)'", block)) == _EXPECTED[table]["columns"]
    flat = _flat(source)
    checks = _m05_checks(_MODELS[table])
    assert set(checks) == _EXPECTED[table]["checks"]
    for name, expression in checks.items():
        assert f"name='{name}'" in block, name
        assert expression in flat, (name, expression)


def test_the_migrations_closed_sets_match_the_application_enums():
    _, source = _load_migration()
    flat = _flat(source)
    for column, enum in (("kind", PaymentTransactionKind), ("method", PaymentMethod),
                         ("status", PaymentTransactionStatus), ("status", ReceiptStatus)):
        # Phase 5 / M07 added the ``online`` method; this revision declared the rest.
        expected = f"{column} IN (" + ", ".join(
            f"'{member.value}'" for member in enum if member.value != "online") + ")"
        assert expected in flat, expected
    for fragment in ("sa.Column('amount', sa.DECIMAL(precision=19, scale=4), nullable=False)",
                     "sa.Column('bank_transfer_date', sa.Date(), nullable=True)",
                     "sa.Column('snapshot', sa.JSON(), nullable=False)",
                     "sa.Column('receipt_number', sa.String(length=15), nullable=False)"):
        assert fragment in source, fragment


@pytest.mark.parametrize("table", [*_NEW_TABLES, _EVENTS])
def test_the_model_and_migration_agree(app, table):
    _, source = _load_migration()
    with app.app_context():
        inspector = inspect(db.engine)
        actual = {c["name"]: str(c["nullable"]) for c in inspector.get_columns(table)}
        actual.pop("id", None)
        if table in _NEW_TABLES:
            declared = dict(re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)",
                                       _table_block(source, table)))
            declared.pop("id", None)
            # Columns Phase 5 / M07 or M10 added or made nullable are compared
            # by their own suites.
            changed = _M07_ADDITIONS[table].get("nullable", set()) | _M10_ADDITIONS.get(
                table, {}).get("nullable", set())
            assert {k: v for k, v in declared.items() if k not in changed} == {
                k: v for k, v in actual.items() if k not in changed}
        shape = {
            "columns": {c["name"] for c in inspector.get_columns(table)},
            "nullable": {c["name"] for c in inspector.get_columns(table) if c["nullable"]},
            "indexes": {i["name"]: i["column_names"] for i in inspector.get_indexes(table)},
            "uniques": sorted((u["name"] or "", tuple(u["column_names"]))
                              for u in inspector.get_unique_constraints(table)),
            "checks": {c["name"] for c in inspector.get_check_constraints(table)},
            "fks": {(fk["constrained_columns"][0], fk["referred_table"])
                    for fk in inspector.get_foreign_keys(table)},
        }
        assert shape == _current_shape(table)


_DDL_FRAGMENTS = {
    "payment_transactions": (
        "id BIGINT NOT NULL AUTO_INCREMENT", "public_id VARCHAR(36) NOT NULL",
        "invoice_id BIGINT NOT NULL", "kind VARCHAR(32) NOT NULL", "method VARCHAR(32) NOT NULL",
        "status VARCHAR(32) NOT NULL", "currency_code VARCHAR(3) NOT NULL",
        "amount DECIMAL(19, 4) NOT NULL", "bank_transfer_reference VARCHAR(64),",
        "bank_transfer_date DATE,", "recorded_at DATETIME NOT NULL",
        # Phase 5 / M07: an online collection has no recorder.
        "recorded_by_id BIGINT,", "rejection_reason VARCHAR(500),",
        "reversal_of_payment_transaction_id BIGINT,",
        "FOREIGN KEY(invoice_id) REFERENCES invoices (id)",
        "FOREIGN KEY(recorded_by_id) REFERENCES users (id)",
        "FOREIGN KEY(confirmed_by_id) REFERENCES users (id)",
        "FOREIGN KEY(rejected_by_id) REFERENCES users (id)",
        "FOREIGN KEY(reversal_of_payment_transaction_id) REFERENCES payment_transactions (id)",
        "CONSTRAINT uq_payment_transactions_reversal_of UNIQUE (reversal_of_payment_transaction_id)",
        "UNIQUE (public_id)",
    ),
    "receipt_number_sequences": (
        "id BIGINT NOT NULL AUTO_INCREMENT", "calendar_year INTEGER NOT NULL",
        "CONSTRAINT uq_receipt_number_sequences_calendar_year UNIQUE (calendar_year)",
    ),
    "receipts": (
        "id BIGINT NOT NULL AUTO_INCREMENT", "payment_transaction_id BIGINT NOT NULL",
        # Phase 5 / M07: an online collection's receipt has no issuing Administrator.
        "receipt_number VARCHAR(15) NOT NULL", "issued_by_id BIGINT,",
        "void_reason VARCHAR(500),", "snapshot JSON NOT NULL",
        "FOREIGN KEY(payment_transaction_id) REFERENCES payment_transactions (id)",
        "FOREIGN KEY(issued_by_id) REFERENCES users (id)",
        "FOREIGN KEY(voided_by_id) REFERENCES users (id)",
        "CONSTRAINT uq_receipts_payment_transaction_id UNIQUE (payment_transaction_id)",
        "CONSTRAINT uq_receipts_receipt_number UNIQUE (receipt_number)", "UNIQUE (public_id)",
    ),
    _EVENTS: (
        "invoice_id BIGINT NOT NULL", "kind VARCHAR(40) NOT NULL",
        "payment_transaction_id BIGINT,", "receipt_id BIGINT,",
        "FOREIGN KEY(invoice_id) REFERENCES invoices (id)",
        "CONSTRAINT fk_payment_audit_events_payment_transaction_id FOREIGN KEY"
        "(payment_transaction_id) REFERENCES payment_transactions (id)",
        "CONSTRAINT fk_payment_audit_events_receipt_id FOREIGN KEY(receipt_id) REFERENCES"
        " receipts (id)",
    ),
}


@pytest.mark.parametrize("table", [*_NEW_TABLES, _EVENTS])
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


@pytest.mark.parametrize("table", _NEW_TABLES)
def test_no_column_is_shaped_for_card_account_or_credential_data(table):
    for column in _MODELS[table].__table__.columns:
        if column.name == "payment_intent_id":
            continue  # Phase 5 / M07's approved link from an online collection to its intent.
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


def test_the_offline_mysql_upgrade_creates_three_tables_then_alters_only_the_audit_trail():
    module, _ = _load_migration()
    sql = _offline_mysql("upgrade")
    steps = []
    for table in _NEW_TABLES:
        steps.append(sql.index(f"CREATE TABLE {table} ("))
        steps += [sql.index(f"CREATE INDEX {name} ON {table}") for name in _EXPECTED[table]["indexes"]]
    alter = "ALTER TABLE payment_audit_events"
    steps += [
        sql.index(f"{alter} ADD COLUMN payment_transaction_id BIGINT"),
        sql.index(f"{alter} ADD COLUMN receipt_id BIGINT"),
        sql.index("CREATE INDEX ix_payment_audit_events_payment_transaction_id ON payment_audit_events"),
        sql.index("CREATE INDEX ix_payment_audit_events_receipt_id ON payment_audit_events"),
        sql.index(f"{alter} ADD CONSTRAINT fk_payment_audit_events_payment_transaction_id FOREIGN KEY"),
        sql.index(f"{alter} ADD CONSTRAINT fk_payment_audit_events_receipt_id FOREIGN KEY"),
    ]
    for name, _before, after in module._REPLACED_CHECKS:
        steps += [sql.index(f"{alter} DROP CHECK {name}"),
                  sql.index(f"{alter} ADD CONSTRAINT {name} CHECK ({after})")]
    steps.append(sql.index(f"{alter} ADD CONSTRAINT ck_payment_audit_events_subject_links"
                           f" CHECK ({module._LINKS})"))
    assert steps == sorted(steps)
    assert sql.count("CREATE TABLE") == 3
    assert sql.count("CREATE INDEX") == 10
    assert sql.count("ALTER TABLE") == sql.count(alter) == 11
    for forbidden in ("DROP TABLE", "DROP INDEX", "DROP COLUMN", "DROP FOREIGN KEY", "ON DELETE",
                      "ON UPDATE", "ENGINE=", "CHARSET", "DELETE FROM", "INSERT INTO", "UPDATE ",
                      "TRIGGER", "SELECT"):
        assert forbidden not in sql, forbidden


def test_the_offline_mysql_downgrade_restores_the_audit_trail_then_drops_the_tables():
    module, _ = _load_migration()
    sql = _offline_mysql("downgrade")
    alter = "ALTER TABLE payment_audit_events"
    steps = [sql.index(f"{alter} DROP CHECK ck_payment_audit_events_subject_links")]
    for name, before, _after in module._REPLACED_CHECKS:
        steps += [sql.index(f"{alter} DROP CHECK {name}"),
                  sql.index(f"{alter} ADD CONSTRAINT {name} CHECK ({before})")]
    steps += [
        sql.index(f"{alter} DROP FOREIGN KEY fk_payment_audit_events_receipt_id"),
        sql.index(f"{alter} DROP FOREIGN KEY fk_payment_audit_events_payment_transaction_id"),
        sql.index("DROP INDEX ix_payment_audit_events_receipt_id ON payment_audit_events"),
        sql.index("DROP INDEX ix_payment_audit_events_payment_transaction_id ON payment_audit_events"),
        sql.index(f"{alter} DROP COLUMN receipt_id"),
        sql.index(f"{alter} DROP COLUMN payment_transaction_id"),
        sql.index("DROP TABLE receipts"),
        sql.index("DROP TABLE receipt_number_sequences"),
        sql.index("DROP TABLE payment_transactions"),
    ]
    assert steps == sorted(steps)
    assert sql.count("DROP TABLE") == 3
    for forbidden in ("CREATE TABLE", "CREATE INDEX", "DELETE FROM", "INSERT", "UPDATE ", "SELECT",
                      "invoices ", "invoice_items"):
        assert forbidden not in sql, forbidden
