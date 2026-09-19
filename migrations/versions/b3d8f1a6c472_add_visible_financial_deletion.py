"""add visible financial deletion

Revision ID: b3d8f1a6c472
Revises: e9c4b2d7a1f3
Create Date: 2026-09-19

Phase 5 / M10. An Administrator may *delete* an invoice, a payment
transaction or a receipt visibly: the row is kept as a tombstone, leaves every
live list, balance and report, and appears in Deleted Records. No table is
created or dropped, no existing row is rewritten or deleted, and nothing is
seeded.

The extensions
--------------
- ``invoices``, ``payment_transactions`` and ``receipts`` each gain three
  nullable columns: ``deleted_at``, ``deleted_by_id`` (a named foreign key to
  ``users``: ``fk_<table>_deleted_by_id``) and ``deletion_reason``
  (``VARCHAR(500)``, an audit reason's width); the CHECK
  ``ck_<table>_deletion_state`` -- no deletion, or all three together with a
  non-empty reason, no earlier than the row's own anchor moment and no later
  than ``updated_at`` (and, for an invoice, only a draft or issued one); and
  two indexes, ``ix_<table>_deleted_at_id`` (``deleted_at``, ``id``) for
  Deleted Records and ``ix_<table>_deleted_by_id`` for the foreign key.
- ``payment_audit_events``: ``_kind_valid``, ``_version_transition``,
  ``_reason_required`` and ``_subject_links`` are replaced to know the four
  deletion kinds -- ``invoice_deleted`` (an invoice event that moves the
  version), ``payment_deleted`` and ``payment_replaced`` (payment events) and
  ``receipt_deleted`` (a receipt event) -- each with a required reason.

**Every existing row satisfies every new expression exactly as it satisfied
the old one**: no existing row is deleted (all three columns are NULL), and
each replaced expression only adds values.

How, per backend
----------------
- **MySQL 8**: the columns are added, each foreign key's index is created
  before the foreign key, so MySQL uses it, and each replaced CHECK is dropped
  and added again under the same name. MySQL validates every existing row as
  each CHECK is added. MySQL DDL is not transactional.
- **SQLite** (the isolated migration tests only): each altered table is
  rebuilt by batch mode from an explicitly declared ``copy_from`` definition
  -- never a reflected one. All four tables are referenced by other tables,
  and SQLite refuses to drop a referenced table while foreign keys are
  enforced, so the revision requires ``PRAGMA foreign_keys=OFF`` on SQLite (it
  refuses otherwise) and proves ``PRAGMA foreign_key_check`` empty after the
  rebuilds.

**No MySQL execution plan has been measured.** Every foreign key is plain; no
``mysql_engine`` / ``mysql_charset`` is declared.

Downgrade
---------
The earlier schema cannot represent a deleted invoice, payment or receipt, or
a deletion event, and this revision never deletes history. The downgrade
therefore first counts every such row and **refuses** before changing anything
when any exists. With none, it restores the four tables to exactly their
``e9c4b2d7a1f3`` shape. In offline (``--sql``) mode that read cannot run, and
it is skipped. Development MySQL is never downgraded.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3d8f1a6c472'
down_revision = 'e9c4b2d7a1f3'
branch_labels = None
depends_on = None


_ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')

_INVOICES = 'invoices'
_PAYMENTS = 'payment_transactions'
_RECEIPTS = 'receipts'
_EVENTS = 'payment_audit_events'

#: The tables that gain the tombstone, in the order they are altered.
_TOMBSTONED = (_INVOICES, _PAYMENTS, _RECEIPTS)

_DELETION_CHECKS = {
    _INVOICES: (
        'ck_invoices_deletion_state',
        "(deleted_at IS NULL AND deleted_by_id IS NULL AND deletion_reason IS NULL)"
        " OR (deleted_at IS NOT NULL AND deleted_by_id IS NOT NULL"
        " AND deletion_reason IS NOT NULL AND LENGTH(deletion_reason) > 0"
        " AND status IN ('draft', 'issued') AND deleted_at >= created_at"
        " AND updated_at >= deleted_at)",
    ),
    _PAYMENTS: (
        'ck_payment_transactions_deletion_state',
        "(deleted_at IS NULL AND deleted_by_id IS NULL AND deletion_reason IS NULL)"
        " OR (deleted_at IS NOT NULL AND deleted_by_id IS NOT NULL"
        " AND deletion_reason IS NOT NULL AND LENGTH(deletion_reason) > 0"
        " AND deleted_at >= recorded_at AND updated_at >= deleted_at)",
    ),
    _RECEIPTS: (
        'ck_receipts_deletion_state',
        "(deleted_at IS NULL AND deleted_by_id IS NULL AND deletion_reason IS NULL)"
        " OR (deleted_at IS NOT NULL AND deleted_by_id IS NOT NULL"
        " AND deletion_reason IS NOT NULL AND LENGTH(deletion_reason) > 0"
        " AND deleted_at >= issued_at AND updated_at >= deleted_at)",
    ),
}


def _tombstone_columns():
    return (
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_by_id', _ID, nullable=True),
        sa.Column('deletion_reason', sa.String(length=500), nullable=True),
    )


def _fk_name(table):
    return f'fk_{table}_deleted_by_id'


def _at_index(table):
    return f'ix_{table}_deleted_at_id'


def _by_index(table):
    return f'ix_{table}_deleted_by_id'


# ---------------------------------------------------------------------------
# invoices (as Phase 5 / M04 created it)
# ---------------------------------------------------------------------------

_INVOICE_CHECKS = (
    ('ck_invoices_status_valid', "status IN ('draft', 'issued', 'cancelled')"),
    ('ck_invoices_currency_code', "currency_code = 'LYD'"),
    ('ck_invoices_version_positive', 'version > 0'),
    (
        'ck_invoices_issue_pair',
        "(issued_at IS NULL AND issued_by_id IS NULL)"
        " OR (issued_at IS NOT NULL AND issued_by_id IS NOT NULL)",
    ),
    (
        'ck_invoices_cancellation_pair',
        "(cancelled_at IS NULL AND cancelled_by_id IS NULL)"
        " OR (cancelled_at IS NOT NULL AND cancelled_by_id IS NOT NULL)",
    ),
    (
        'ck_invoices_number_matches_issue',
        "(invoice_number IS NULL AND issued_at IS NULL)"
        " OR (invoice_number IS NOT NULL AND issued_at IS NOT NULL)",
    ),
    (
        'ck_invoices_number_format',
        "invoice_number IS NULL"
        " OR (invoice_number LIKE 'INV-____-______' AND LENGTH(invoice_number) = 15)",
    ),
    (
        'ck_invoices_lifecycle_state',
        "(status = 'draft' AND issued_at IS NULL AND cancelled_at IS NULL)"
        " OR (status = 'issued' AND issued_at IS NOT NULL AND cancelled_at IS NULL)"
        " OR (status = 'cancelled' AND cancelled_at IS NOT NULL)",
    ),
    (
        'ck_invoices_timestamps_ordered',
        "updated_at >= created_at"
        " AND (issued_at IS NULL OR (issued_at >= created_at AND updated_at >= issued_at))"
        " AND (cancelled_at IS NULL"
        " OR (cancelled_at >= created_at AND updated_at >= cancelled_at"
        " AND (issued_at IS NULL OR cancelled_at >= issued_at)))",
    ),
)


def _invoices_table(with_plain_tombstone=False):
    """The ``invoices`` definition -- used only as a SQLite rebuild's
    ``copy_from``. `with_plain_tombstone` describes the table a downgrade
    starts from: the three M10 columns without their keys, which the batch
    drops."""
    return sa.Table(
        _INVOICES, sa.MetaData(),
        sa.Column('id', _ID, nullable=False),
        sa.Column('public_id', sa.String(length=36), nullable=False),
        sa.Column('student_fee_assignment_id', _ID, nullable=False),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('invoice_number', sa.String(length=15), nullable=True),
        sa.Column('issued_at', sa.DateTime(), nullable=True),
        sa.Column('issued_by_id', _ID, nullable=True),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.Column('cancelled_by_id', _ID, nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        *(_tombstone_columns() if with_plain_tombstone else ()),
        *(sa.CheckConstraint(sql, name=name) for name, sql in _INVOICE_CHECKS),
        sa.ForeignKeyConstraint(['student_fee_assignment_id'], ['student_fee_assignments.id'], ),
        sa.ForeignKeyConstraint(['issued_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['cancelled_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invoice_number', name='uq_invoices_invoice_number'),
        sa.UniqueConstraint('public_id'),
        sa.Index(
            'ix_invoices_assignment_status_id', 'student_fee_assignment_id', 'status', 'id'
        ),
        sa.Index('ix_invoices_issued_by_id', 'issued_by_id'),
        sa.Index('ix_invoices_cancelled_by_id', 'cancelled_by_id'),
    )


# ---------------------------------------------------------------------------
# payment_transactions (as Phase 5 / M07 left it)
# ---------------------------------------------------------------------------

_PAYMENT_CHECKS = (
    ('ck_payment_transactions_kind_valid', "kind IN ('collection', 'reversal')"),
    ('ck_payment_transactions_method_valid', "method IN ('cash', 'bank_transfer', 'online')"),
    (
        'ck_payment_transactions_status_valid',
        "status IN ('pending', 'confirmed', 'rejected')",
    ),
    ('ck_payment_transactions_currency_code', "currency_code = 'LYD'"),
    ('ck_payment_transactions_amount_range', 'amount >= 0.001 AND amount <= 99999.999'),
    ('ck_payment_transactions_version_positive', 'version > 0'),
    (
        'ck_payment_transactions_bank_transfer_details',
        "(kind = 'collection' AND method = 'bank_transfer'"
        " AND bank_transfer_reference IS NOT NULL AND LENGTH(bank_transfer_reference) > 0"
        " AND bank_transfer_date IS NOT NULL)"
        " OR ((kind = 'reversal' OR method IN ('cash', 'online'))"
        " AND bank_transfer_reference IS NULL AND bank_transfer_date IS NULL)",
    ),
    (
        'ck_payment_transactions_confirmation_pair',
        "(confirmed_at IS NULL AND confirmed_by_id IS NULL)"
        " OR (confirmed_at IS NOT NULL AND confirmed_by_id IS NOT NULL)"
        " OR (kind = 'collection' AND method = 'online'"
        " AND confirmed_at IS NOT NULL AND confirmed_by_id IS NULL)",
    ),
    (
        'ck_payment_transactions_rejection_state',
        "(rejected_at IS NULL AND rejected_by_id IS NULL AND rejection_reason IS NULL)"
        " OR (rejected_at IS NOT NULL AND rejected_by_id IS NOT NULL"
        " AND rejection_reason IS NOT NULL AND LENGTH(rejection_reason) > 0)",
    ),
    (
        'ck_payment_transactions_lifecycle_state',
        "(status = 'pending' AND kind = 'collection' AND method = 'bank_transfer'"
        " AND confirmed_at IS NULL AND rejected_at IS NULL)"
        " OR (status = 'confirmed' AND confirmed_at IS NOT NULL AND rejected_at IS NULL)"
        " OR (status = 'rejected' AND kind = 'collection' AND method = 'bank_transfer'"
        " AND rejected_at IS NOT NULL AND confirmed_at IS NULL)",
    ),
    (
        'ck_payment_transactions_immediate_confirmation',
        "(kind = 'collection' AND method = 'bank_transfer')"
        " OR (kind = 'collection' AND method = 'online' AND confirmed_at = recorded_at"
        " AND recorded_by_id IS NULL AND confirmed_by_id IS NULL)"
        " OR (confirmed_at = recorded_at AND confirmed_by_id = recorded_by_id)",
    ),
    (
        'ck_payment_transactions_reversal_link',
        "(kind = 'collection' AND reversal_of_payment_transaction_id IS NULL)"
        " OR (kind = 'reversal' AND reversal_of_payment_transaction_id IS NOT NULL)",
    ),
    (
        'ck_payment_transactions_timestamps_ordered',
        "recorded_at >= created_at AND updated_at >= recorded_at"
        " AND (confirmed_at IS NULL OR (confirmed_at >= recorded_at AND updated_at >= confirmed_at))"
        " AND (rejected_at IS NULL OR (rejected_at >= recorded_at AND updated_at >= rejected_at))",
    ),
    (
        'ck_payment_transactions_online_origin',
        "(kind = 'collection' AND method = 'online' AND payment_intent_id IS NOT NULL"
        " AND recorded_by_id IS NULL AND confirmed_by_id IS NULL)"
        " OR ((kind <> 'collection' OR method <> 'online') AND payment_intent_id IS NULL"
        " AND recorded_by_id IS NOT NULL)",
    ),
)


def _payments_table(with_plain_tombstone=False):
    """The ``payment_transactions`` definition -- used only as a SQLite
    rebuild's ``copy_from``; see :func:`_invoices_table`."""
    return sa.Table(
        _PAYMENTS, sa.MetaData(),
        sa.Column('id', _ID, nullable=False),
        sa.Column('public_id', sa.String(length=36), nullable=False),
        sa.Column('invoice_id', _ID, nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('method', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('amount', sa.DECIMAL(precision=19, scale=4), nullable=False),
        sa.Column('bank_transfer_reference', sa.String(length=64), nullable=True),
        sa.Column('bank_transfer_date', sa.Date(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=False),
        sa.Column('recorded_by_id', _ID, nullable=True),
        sa.Column('confirmed_at', sa.DateTime(), nullable=True),
        sa.Column('confirmed_by_id', _ID, nullable=True),
        sa.Column('rejected_at', sa.DateTime(), nullable=True),
        sa.Column('rejected_by_id', _ID, nullable=True),
        sa.Column('rejection_reason', sa.String(length=500), nullable=True),
        sa.Column('reversal_of_payment_transaction_id', _ID, nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('payment_intent_id', _ID, nullable=True),
        *(_tombstone_columns() if with_plain_tombstone else ()),
        *(sa.CheckConstraint(sql, name=name) for name, sql in _PAYMENT_CHECKS),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
        sa.ForeignKeyConstraint(['recorded_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['confirmed_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['rejected_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(
            ['reversal_of_payment_transaction_id'], ['payment_transactions.id'],
        ),
        sa.ForeignKeyConstraint(
            ['payment_intent_id'], ['payment_intents.id'],
            name='fk_payment_transactions_payment_intent_id',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'reversal_of_payment_transaction_id', name='uq_payment_transactions_reversal_of'
        ),
        sa.UniqueConstraint(
            'payment_intent_id', name='uq_payment_transactions_payment_intent_id'
        ),
        sa.UniqueConstraint('public_id'),
        sa.Index('ix_payment_transactions_invoice_id_id', 'invoice_id', 'id'),
        sa.Index('ix_payment_transactions_status_id', 'status', 'id'),
        sa.Index('ix_payment_transactions_method_id', 'method', 'id'),
        sa.Index('ix_payment_transactions_recorded_by_id', 'recorded_by_id'),
        sa.Index('ix_payment_transactions_confirmed_by_id', 'confirmed_by_id'),
        sa.Index('ix_payment_transactions_rejected_by_id', 'rejected_by_id'),
    )


# ---------------------------------------------------------------------------
# receipts (as Phase 5 / M07 left it)
# ---------------------------------------------------------------------------

_RECEIPT_CHECKS = (
    ('ck_receipts_status_valid', "status IN ('issued', 'voided')"),
    ('ck_receipts_version_positive', 'version > 0'),
    (
        'ck_receipts_number_format',
        "receipt_number LIKE 'RCT-____-______' AND LENGTH(receipt_number) = 15",
    ),
    (
        'ck_receipts_void_state',
        "(status = 'issued' AND voided_at IS NULL AND voided_by_id IS NULL AND void_reason IS NULL)"
        " OR (status = 'voided' AND voided_at IS NOT NULL AND voided_by_id IS NOT NULL"
        " AND void_reason IS NOT NULL AND LENGTH(void_reason) > 0)",
    ),
    (
        'ck_receipts_timestamps_ordered',
        "issued_at >= created_at AND updated_at >= issued_at"
        " AND (voided_at IS NULL OR (voided_at >= issued_at AND updated_at >= voided_at))",
    ),
)


def _receipts_table(with_plain_tombstone=False):
    """The ``receipts`` definition -- used only as a SQLite rebuild's
    ``copy_from``; see :func:`_invoices_table`."""
    return sa.Table(
        _RECEIPTS, sa.MetaData(),
        sa.Column('id', _ID, nullable=False),
        sa.Column('public_id', sa.String(length=36), nullable=False),
        sa.Column('payment_transaction_id', _ID, nullable=False),
        sa.Column('receipt_number', sa.String(length=15), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('issued_at', sa.DateTime(), nullable=False),
        sa.Column('issued_by_id', _ID, nullable=True),
        sa.Column('voided_at', sa.DateTime(), nullable=True),
        sa.Column('voided_by_id', _ID, nullable=True),
        sa.Column('void_reason', sa.String(length=500), nullable=True),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        *(_tombstone_columns() if with_plain_tombstone else ()),
        *(sa.CheckConstraint(sql, name=name) for name, sql in _RECEIPT_CHECKS),
        sa.ForeignKeyConstraint(['payment_transaction_id'], ['payment_transactions.id'], ),
        sa.ForeignKeyConstraint(['issued_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['voided_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('payment_transaction_id', name='uq_receipts_payment_transaction_id'),
        sa.UniqueConstraint('receipt_number', name='uq_receipts_receipt_number'),
        sa.UniqueConstraint('public_id'),
        sa.Index('ix_receipts_issued_by_id', 'issued_by_id'),
        sa.Index('ix_receipts_voided_by_id', 'voided_by_id'),
    )


_TABLES = {_INVOICES: _invoices_table, _PAYMENTS: _payments_table, _RECEIPTS: _receipts_table}


# ---------------------------------------------------------------------------
# payment_audit_events
# ---------------------------------------------------------------------------

_AUDIT_CHECKS = (
    (
        'ck_payment_audit_events_kind_valid',
        "kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued',"
        " 'invoice_issued_edited', 'invoice_cancelled', 'payment_cash_recorded',"
        " 'payment_bank_transfer_recorded', 'payment_bank_transfer_confirmed',"
        " 'payment_bank_transfer_rejected', 'payment_reversed', 'receipt_issued',"
        " 'receipt_voided', 'payment_online_confirmed', 'receipt_online_issued')",
        "kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued',"
        " 'invoice_issued_edited', 'invoice_cancelled', 'payment_cash_recorded',"
        " 'payment_bank_transfer_recorded', 'payment_bank_transfer_confirmed',"
        " 'payment_bank_transfer_rejected', 'payment_reversed', 'receipt_issued',"
        " 'receipt_voided', 'payment_online_confirmed', 'receipt_online_issued',"
        " 'invoice_deleted', 'payment_deleted', 'payment_replaced', 'receipt_deleted')",
    ),
    (
        'ck_payment_audit_events_version_transition',
        "(kind = 'invoice_draft_created' AND invoice_version_before IS NULL"
        " AND invoice_version_after = 1)"
        " OR (kind IN ('invoice_cancelled', 'invoice_draft_edited', 'invoice_issued',"
        " 'invoice_issued_edited') AND invoice_version_before IS NOT NULL"
        " AND invoice_version_after = invoice_version_before + 1)"
        " OR (kind IN ('payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
        " 'payment_bank_transfer_rejected', 'payment_cash_recorded', 'payment_online_confirmed',"
        " 'payment_reversed', 'receipt_issued', 'receipt_online_issued', 'receipt_voided')"
        " AND invoice_version_before IS NOT NULL"
        " AND invoice_version_after = invoice_version_before)",
        "(kind = 'invoice_draft_created' AND invoice_version_before IS NULL"
        " AND invoice_version_after = 1)"
        " OR (kind IN ('invoice_cancelled', 'invoice_deleted', 'invoice_draft_edited',"
        " 'invoice_issued', 'invoice_issued_edited') AND invoice_version_before IS NOT NULL"
        " AND invoice_version_after = invoice_version_before + 1)"
        " OR (kind IN ('payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
        " 'payment_bank_transfer_rejected', 'payment_cash_recorded', 'payment_deleted',"
        " 'payment_online_confirmed', 'payment_replaced', 'payment_reversed', 'receipt_deleted',"
        " 'receipt_issued', 'receipt_online_issued', 'receipt_voided')"
        " AND invoice_version_before IS NOT NULL"
        " AND invoice_version_after = invoice_version_before)",
    ),
    (
        'ck_payment_audit_events_reason_required',
        "(kind IN ('invoice_cancelled', 'invoice_issued_edited', 'payment_bank_transfer_rejected',"
        " 'payment_reversed', 'receipt_voided') AND reason IS NOT NULL AND LENGTH(reason) > 0)"
        " OR (kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued',"
        " 'payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
        " 'payment_cash_recorded', 'payment_online_confirmed', 'receipt_issued',"
        " 'receipt_online_issued') AND reason IS NULL)",
        "(kind IN ('invoice_cancelled', 'invoice_deleted', 'invoice_issued_edited',"
        " 'payment_bank_transfer_rejected', 'payment_deleted', 'payment_replaced',"
        " 'payment_reversed', 'receipt_deleted', 'receipt_voided')"
        " AND reason IS NOT NULL AND LENGTH(reason) > 0)"
        " OR (kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued',"
        " 'payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
        " 'payment_cash_recorded', 'payment_online_confirmed', 'receipt_issued',"
        " 'receipt_online_issued') AND reason IS NULL)",
    ),
    (
        'ck_payment_audit_events_subject_links',
        "(kind IN ('invoice_cancelled', 'invoice_draft_created', 'invoice_draft_edited',"
        " 'invoice_issued', 'invoice_issued_edited')"
        " AND payment_transaction_id IS NULL AND receipt_id IS NULL)"
        " OR (kind IN ('payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
        " 'payment_bank_transfer_rejected', 'payment_cash_recorded', 'payment_online_confirmed',"
        " 'payment_reversed') AND payment_transaction_id IS NOT NULL AND receipt_id IS NULL)"
        " OR (kind IN ('receipt_issued', 'receipt_online_issued', 'receipt_voided')"
        " AND payment_transaction_id IS NOT NULL AND receipt_id IS NOT NULL)",
        "(kind IN ('invoice_cancelled', 'invoice_deleted', 'invoice_draft_created',"
        " 'invoice_draft_edited', 'invoice_issued', 'invoice_issued_edited')"
        " AND payment_transaction_id IS NULL AND receipt_id IS NULL)"
        " OR (kind IN ('payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
        " 'payment_bank_transfer_rejected', 'payment_cash_recorded', 'payment_deleted',"
        " 'payment_online_confirmed', 'payment_replaced', 'payment_reversed')"
        " AND payment_transaction_id IS NOT NULL AND receipt_id IS NULL)"
        " OR (kind IN ('receipt_deleted', 'receipt_issued', 'receipt_online_issued',"
        " 'receipt_voided') AND payment_transaction_id IS NOT NULL AND receipt_id IS NOT NULL)",
    ),
)
_AUDIT_KEPT_CHECKS = (
    (
        'ck_payment_audit_events_versions_positive',
        "invoice_version_after > 0"
        " AND (invoice_version_before IS NULL OR invoice_version_before > 0)",
    ),
    (
        'ck_payment_audit_events_snapshots_present',
        "(kind = 'invoice_draft_created' AND before_snapshot IS NULL"
        " AND after_snapshot IS NOT NULL)"
        " OR (kind <> 'invoice_draft_created' AND before_snapshot IS NOT NULL"
        " AND after_snapshot IS NOT NULL)",
    ),
    (
        'ck_payment_audit_events_actor_origin',
        "(kind IN ('payment_online_confirmed', 'receipt_online_issued') AND actor_id IS NULL)"
        " OR (kind NOT IN ('payment_online_confirmed', 'receipt_online_issued')"
        " AND actor_id IS NOT NULL)",
    ),
)


def _audit_events_table(after):
    """The ``payment_audit_events`` definition with the M07 (``after=False``)
    or M10 CHECKs -- used only as a SQLite rebuild's ``copy_from``."""
    checks = [(name, new if after else old) for name, old, new in _AUDIT_CHECKS]
    return sa.Table(
        _EVENTS, sa.MetaData(),
        sa.Column('id', _ID, nullable=False),
        sa.Column('invoice_id', _ID, nullable=False),
        sa.Column('actor_id', _ID, nullable=True),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('occurred_at', sa.DateTime(), nullable=False),
        sa.Column('invoice_version_before', sa.Integer(), nullable=True),
        sa.Column('invoice_version_after', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=True),
        sa.Column('before_snapshot', sa.JSON(), nullable=True),
        sa.Column('after_snapshot', sa.JSON(), nullable=False),
        sa.Column('payment_transaction_id', _ID, nullable=True),
        sa.Column('receipt_id', _ID, nullable=True),
        *(sa.CheckConstraint(sql, name=name) for name, sql in checks + list(_AUDIT_KEPT_CHECKS)),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(
            ['payment_transaction_id'], ['payment_transactions.id'],
            name='fk_payment_audit_events_payment_transaction_id',
        ),
        sa.ForeignKeyConstraint(
            ['receipt_id'], ['receipts.id'], name='fk_payment_audit_events_receipt_id'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('ix_payment_audit_events_invoice_id_id', 'invoice_id', 'id'),
        sa.Index('ix_payment_audit_events_actor_id', 'actor_id'),
        sa.Index('ix_payment_audit_events_payment_transaction_id', 'payment_transaction_id'),
        sa.Index('ix_payment_audit_events_receipt_id', 'receipt_id'),
    )


# ---------------------------------------------------------------------------
# The downgrade's refusal
# ---------------------------------------------------------------------------

_M10_ROWS_SQL = (
    "SELECT (SELECT COUNT(*) FROM invoices WHERE deleted_at IS NOT NULL"
    " OR deleted_by_id IS NOT NULL OR deletion_reason IS NOT NULL)"
    " + (SELECT COUNT(*) FROM payment_transactions WHERE deleted_at IS NOT NULL"
    " OR deleted_by_id IS NOT NULL OR deletion_reason IS NOT NULL)"
    " + (SELECT COUNT(*) FROM receipts WHERE deleted_at IS NOT NULL"
    " OR deleted_by_id IS NOT NULL OR deletion_reason IS NOT NULL)"
    " + (SELECT COUNT(*) FROM payment_audit_events WHERE kind IN"
    " ('invoice_deleted', 'payment_deleted', 'payment_replaced', 'receipt_deleted'))"
)


def _sqlite(bind):
    return bind.dialect.name == 'sqlite'


def _require_sqlite_foreign_keys_off(bind):
    if bind.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise RuntimeError(
            "On SQLite this revision rebuilds tables other tables reference, which SQLite "
            "allows only with PRAGMA foreign_keys=OFF; run it with foreign keys disabled."
        )


def _prove_sqlite_references(bind):
    if bind.exec_driver_sql('PRAGMA foreign_key_check').fetchall():
        raise RuntimeError("A rebuild left a dangling reference.")


def _add_tombstone(target, table):
    """Add one table's tombstone through `target` -- ``op`` itself on MySQL,
    or a batch operation on SQLite, whose calls omit the table name."""
    batch = target is not op
    lead = () if batch else (table,)
    for column in _tombstone_columns():
        target.add_column(*lead, column)
    target.create_index(_by_index(table), *lead, ['deleted_by_id'], unique=False)
    if batch:
        target.create_foreign_key(_fk_name(table), 'users', ['deleted_by_id'], ['id'])
    else:
        target.create_foreign_key(_fk_name(table), table, 'users', ['deleted_by_id'], ['id'])
    target.create_index(_at_index(table), *lead, ['deleted_at', 'id'], unique=False)
    name, sql = _DELETION_CHECKS[table]
    target.create_check_constraint(name, *lead, sql)


def upgrade():
    bind = op.get_bind()
    if _sqlite(bind):
        _require_sqlite_foreign_keys_off(bind)
        for table in _TOMBSTONED:
            with op.batch_alter_table(
                table, copy_from=_TABLES[table](), recreate='always'
            ) as batch_op:
                _add_tombstone(batch_op, table)
        with op.batch_alter_table(_EVENTS, copy_from=_audit_events_table(True), recreate='always'):
            pass
        _prove_sqlite_references(bind)
        return

    for table in _TOMBSTONED:
        _add_tombstone(op, table)
    for name, _old, new in _AUDIT_CHECKS:
        op.drop_constraint(name, _EVENTS, type_='check')
        op.create_check_constraint(name, _EVENTS, new)


def downgrade():
    bind = op.get_bind()
    if not op.get_context().as_sql:
        existing = bind.execute(sa.text(_M10_ROWS_SQL)).scalar()
        if existing:
            raise RuntimeError(
                f"Refusing to downgrade: {existing} deleted invoice, payment or receipt row(s) "
                "or deletion event(s) exist. The previous schema cannot represent that history, "
                "and this revision never deletes it."
            )
    if _sqlite(bind):
        _require_sqlite_foreign_keys_off(bind)
        with op.batch_alter_table(
            _EVENTS, copy_from=_audit_events_table(False), recreate='always'
        ):
            pass
        for table in reversed(_TOMBSTONED):
            with op.batch_alter_table(
                table, copy_from=_TABLES[table](with_plain_tombstone=True), recreate='always'
            ) as batch_op:
                for column in reversed(_tombstone_columns()):
                    batch_op.drop_column(column.name)
        _prove_sqlite_references(bind)
        return

    for name, old, _new in _AUDIT_CHECKS:
        op.drop_constraint(name, _EVENTS, type_='check')
        op.create_check_constraint(name, _EVENTS, old)
    for table in reversed(_TOMBSTONED):
        name, _sql = _DELETION_CHECKS[table]
        op.drop_constraint(name, table, type_='check')
        op.drop_constraint(_fk_name(table), table, type_='foreignkey')
        op.drop_index(_at_index(table), table_name=table)
        op.drop_index(_by_index(table), table_name=table)
        for column in reversed(_tombstone_columns()):
            op.drop_column(table, column.name)
