"""add manual payments and receipts

Revision ID: c5e8f2a7d914
Revises: a8d3f5c29e61
Create Date: 2026-09-15

Phase 5 / M05. Three tables are created and the existing append-only
``payment_audit_events`` table is extended so the one financial audit trail
also records manual payment and receipt movements. No existing row is
rewritten or deleted, and nothing is seeded.

The new tables
--------------
``payment_transactions`` -- ``public_id``; ``invoice_id``; ``kind``
(``collection`` or ``reversal``); ``method`` (``cash`` or ``bank_transfer``);
``status`` (``pending``, ``confirmed`` or ``rejected``); ``currency_code``
(``LYD``); ``amount DECIMAL(19, 4)``; the nullable ``bank_transfer_reference``
and ``bank_transfer_date``; ``recorded_at`` / ``recorded_by_id``; nullable
``confirmed_at`` / ``confirmed_by_id``; nullable ``rejected_at`` /
``rejected_by_id`` / ``rejection_reason``; the nullable, unique
``reversal_of_payment_transaction_id``; a positive ``version``;
``created_at`` / ``updated_at``.

``receipt_number_sequences`` -- internal and never shown: one lockable row per
center-local ``calendar_year`` holding ``last_number``, the latest allocated
``RCT-YYYY-NNNNNN`` number of that year.

``receipts`` -- ``public_id``; the unique ``payment_transaction_id``; the
unique ``receipt_number``; ``status`` (``issued`` or ``voided``);
``issued_at`` / ``issued_by_id``; nullable ``voided_at`` / ``voided_by_id`` /
``void_reason``; the server-built ``snapshot`` JSON document; a positive
``version``; ``created_at`` / ``updated_at``.

No column stores, or is shaped to store, a card number, CVV, PIN, account
number, bank credential, proof upload, provider, payment intent, webhook,
refund or tax value. No trigger is created.

The audit trail extension
-------------------------
``payment_audit_events`` gains exactly:

- two nullable, plainly referenced columns, ``payment_transaction_id`` and
  ``receipt_id``, each with its index and a **named** foreign key
  (``fk_payment_audit_events_payment_transaction_id``,
  ``fk_payment_audit_events_receipt_id``) so the downgrade can name it again;
- a wider ``ck_payment_audit_events_kind_valid`` -- the five M04 kinds plus the
  seven M05 kinds;
- a wider ``ck_payment_audit_events_version_transition`` -- the M04 branches
  unchanged for the M04 kinds, plus a payment / receipt branch in which the
  event records the invoice version it observed and moves nothing;
- a wider ``ck_payment_audit_events_reason_required`` -- the M04 kinds exactly
  as before, plus a mandatory reason for a rejection, a reversal and a receipt
  void and none for the other M05 kinds;
- the new ``ck_payment_audit_events_subject_links`` -- an invoice event links
  nothing, a payment event links its transaction only, a receipt event links
  its receipt and that receipt's collection.

``ck_payment_audit_events_versions_positive``,
``ck_payment_audit_events_snapshots_present``, every other column, both
existing indexes and both existing foreign keys are untouched. **Every M04 row
satisfies every new expression exactly as it satisfied the old one** -- an M04
kind, both links NULL -- so no existing event is invalidated, rewritten or
weakened.

How, per backend
----------------
A CHECK expression cannot be altered in place on either backend.

- **MySQL 8**: the two columns are added, their indexes are created *before*
  their foreign keys (so MySQL uses them rather than creating its own), each
  replaced CHECK is dropped and added again under the same name, and the new
  CHECK is added. MySQL validates every existing row as each CHECK is added;
  the wider expressions accept every M04 row. MySQL DDL is not transactional.
- **SQLite** (the isolated migration tests only): ``payment_audit_events`` is
  rebuilt by batch mode from an explicitly declared ``copy_from`` definition --
  never a reflected one -- that carries the final CHECKs, while the batch adds
  the columns, foreign keys and indexes. No table references
  ``payment_audit_events``, so the rebuild runs with foreign keys enforced, and
  ``PRAGMA foreign_key_check`` is proved empty afterwards.

Indexes, one per real lookup path
---------------------------------
- ``ix_payment_transactions_invoice_id_id`` (``invoice_id``, ``id``) -- one
  invoice's rows, the rows to lock and the foreign key;
  ``ix_payment_transactions_status_id`` and ``ix_payment_transactions_method_id``
  -- the filtered overview; ``ix_payment_transactions_recorded_by_id``,
  ``_confirmed_by_id`` and ``_rejected_by_id``. The reversal link is indexed by
  ``uq_payment_transactions_reversal_of``.
- ``receipts``: the two unique constraints, ``ix_receipts_issued_by_id`` and
  ``ix_receipts_voided_by_id``.
- ``ix_payment_audit_events_payment_transaction_id`` and
  ``ix_payment_audit_events_receipt_id``.

**No MySQL execution plan has been measured for these tables.** Every foreign
key is plain, with no ``ON DELETE`` and no ``ON UPDATE`` action. No
``mysql_engine`` / ``mysql_charset`` argument is declared, so the new tables
inherit the server's defaults like every earlier table.

Downgrade
---------
The M04 CHECKs cannot represent a payment or receipt event, and dropping the
new tables would destroy financial history. The downgrade therefore first
counts every payment transaction, receipt, receipt sequence and M05 event and
**refuses** before changing anything when any exists -- MySQL DDL is not
transactional. With none, it restores ``payment_audit_events`` to exactly the
M04 shape and drops ``receipts``, ``receipt_number_sequences`` and
``payment_transactions``, dependants first. In offline (``--sql``) mode that
read cannot run, and it is skipped. Development MySQL is never downgraded.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c5e8f2a7d914'
down_revision = 'a8d3f5c29e61'
branch_labels = None
depends_on = None


_EVENTS = 'payment_audit_events'
_KIND_CHECK = 'ck_payment_audit_events_kind_valid'
_VERSION_CHECK = 'ck_payment_audit_events_version_transition'
_REASON_CHECK = 'ck_payment_audit_events_reason_required'
_LINKS_CHECK = 'ck_payment_audit_events_subject_links'
_PAYMENT_FK = 'fk_payment_audit_events_payment_transaction_id'
_RECEIPT_FK = 'fk_payment_audit_events_receipt_id'
_PAYMENT_INDEX = 'ix_payment_audit_events_payment_transaction_id'
_RECEIPT_INDEX = 'ix_payment_audit_events_receipt_id'

#: The ``a8d3f5c29e61`` expressions, restored by the downgrade.
_KIND_BEFORE = (
    "kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued',"
    " 'invoice_issued_edited', 'invoice_cancelled')"
)
_VERSION_BEFORE = (
    "(kind = 'invoice_draft_created' AND invoice_version_before IS NULL"
    " AND invoice_version_after = 1)"
    " OR (kind <> 'invoice_draft_created' AND invoice_version_before IS NOT NULL"
    " AND invoice_version_after = invoice_version_before + 1)"
)
_REASON_BEFORE = (
    "(kind IN ('invoice_cancelled', 'invoice_issued_edited') AND reason IS NOT NULL"
    " AND LENGTH(reason) > 0)"
    " OR (kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued')"
    " AND reason IS NULL)"
)

#: The Phase 5 / M05 expressions.
_KIND_AFTER = (
    "kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued',"
    " 'invoice_issued_edited', 'invoice_cancelled', 'payment_cash_recorded',"
    " 'payment_bank_transfer_recorded', 'payment_bank_transfer_confirmed',"
    " 'payment_bank_transfer_rejected', 'payment_reversed', 'receipt_issued',"
    " 'receipt_voided')"
)
_VERSION_AFTER = (
    "(kind = 'invoice_draft_created' AND invoice_version_before IS NULL"
    " AND invoice_version_after = 1)"
    " OR (kind IN ('invoice_cancelled', 'invoice_draft_edited', 'invoice_issued',"
    " 'invoice_issued_edited') AND invoice_version_before IS NOT NULL"
    " AND invoice_version_after = invoice_version_before + 1)"
    " OR (kind IN ('payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
    " 'payment_bank_transfer_rejected', 'payment_cash_recorded', 'payment_reversed',"
    " 'receipt_issued', 'receipt_voided') AND invoice_version_before IS NOT NULL"
    " AND invoice_version_after = invoice_version_before)"
)
_REASON_AFTER = (
    "(kind IN ('invoice_cancelled', 'invoice_issued_edited', 'payment_bank_transfer_rejected',"
    " 'payment_reversed', 'receipt_voided') AND reason IS NOT NULL AND LENGTH(reason) > 0)"
    " OR (kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued',"
    " 'payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
    " 'payment_cash_recorded', 'receipt_issued') AND reason IS NULL)"
)
_LINKS = (
    "(kind IN ('invoice_cancelled', 'invoice_draft_created', 'invoice_draft_edited',"
    " 'invoice_issued', 'invoice_issued_edited')"
    " AND payment_transaction_id IS NULL AND receipt_id IS NULL)"
    " OR (kind IN ('payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
    " 'payment_bank_transfer_rejected', 'payment_cash_recorded', 'payment_reversed')"
    " AND payment_transaction_id IS NOT NULL AND receipt_id IS NULL)"
    " OR (kind IN ('receipt_issued', 'receipt_voided')"
    " AND payment_transaction_id IS NOT NULL AND receipt_id IS NOT NULL)"
)

_VERSIONS_POSITIVE = (
    "invoice_version_after > 0"
    " AND (invoice_version_before IS NULL OR invoice_version_before > 0)"
)
_SNAPSHOTS_PRESENT = (
    "(kind = 'invoice_draft_created' AND before_snapshot IS NULL"
    " AND after_snapshot IS NOT NULL)"
    " OR (kind <> 'invoice_draft_created' AND before_snapshot IS NOT NULL"
    " AND after_snapshot IS NOT NULL)"
)

#: ``(name, a8d3f5c29e61 expression, M05 expression)`` for each replaced CHECK.
_REPLACED_CHECKS = (
    (_KIND_CHECK, _KIND_BEFORE, _KIND_AFTER),
    (_VERSION_CHECK, _VERSION_BEFORE, _VERSION_AFTER),
    (_REASON_CHECK, _REASON_BEFORE, _REASON_AFTER),
)

_M05_KINDS_SQL = (
    "('payment_cash_recorded', 'payment_bank_transfer_recorded',"
    " 'payment_bank_transfer_confirmed', 'payment_bank_transfer_rejected',"
    " 'payment_reversed', 'receipt_issued', 'receipt_voided')"
)
_M05_ROWS_SQL = (
    "SELECT (SELECT COUNT(*) FROM payment_transactions)"
    " + (SELECT COUNT(*) FROM receipts)"
    " + (SELECT COUNT(*) FROM receipt_number_sequences)"
    f" + (SELECT COUNT(*) FROM payment_audit_events WHERE kind IN {_M05_KINDS_SQL})"
)


def _audit_events_table(kind_sql, version_sql, reason_sql, links_sql=None, link_columns=False):
    """The ``a8d3f5c29e61`` definition of ``payment_audit_events`` with the
    given CHECK expressions -- used only as a SQLite rebuild's ``copy_from``.
    `link_columns` declares the two M05 columns (without their keys or
    indexes), which a downgrade then drops."""
    elements = [
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('invoice_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('actor_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('occurred_at', sa.DateTime(), nullable=False),
        sa.Column('invoice_version_before', sa.Integer(), nullable=True),
        sa.Column('invoice_version_after', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=True),
        sa.Column('before_snapshot', sa.JSON(), nullable=True),
        sa.Column('after_snapshot', sa.JSON(), nullable=False),
    ]
    if link_columns:
        elements += [
            sa.Column('payment_transaction_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
            sa.Column('receipt_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
        ]
    elements += [
        sa.CheckConstraint(kind_sql, name=_KIND_CHECK),
        sa.CheckConstraint(_VERSIONS_POSITIVE, name='ck_payment_audit_events_versions_positive'),
        sa.CheckConstraint(version_sql, name=_VERSION_CHECK),
        sa.CheckConstraint(_SNAPSHOTS_PRESENT, name='ck_payment_audit_events_snapshots_present'),
        sa.CheckConstraint(reason_sql, name=_REASON_CHECK),
    ]
    if links_sql is not None:
        elements.append(sa.CheckConstraint(links_sql, name=_LINKS_CHECK))
    elements += [
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('ix_payment_audit_events_invoice_id_id', 'invoice_id', 'id'),
        sa.Index('ix_payment_audit_events_actor_id', 'actor_id'),
    ]
    return sa.Table(_EVENTS, sa.MetaData(), *elements)


def _link_columns():
    return (
        sa.Column('payment_transaction_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
        sa.Column('receipt_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    )


def _extend_audit_events():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table(
            _EVENTS,
            copy_from=_audit_events_table(_KIND_AFTER, _VERSION_AFTER, _REASON_AFTER, _LINKS),
            recreate='always',
        ) as batch_op:
            for column in _link_columns():
                batch_op.add_column(column)
            batch_op.create_foreign_key(
                _PAYMENT_FK, 'payment_transactions', ['payment_transaction_id'], ['id']
            )
            batch_op.create_foreign_key(_RECEIPT_FK, 'receipts', ['receipt_id'], ['id'])
            batch_op.create_index(_PAYMENT_INDEX, ['payment_transaction_id'], unique=False)
            batch_op.create_index(_RECEIPT_INDEX, ['receipt_id'], unique=False)
        if bind.exec_driver_sql('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError("The payment_audit_events rebuild left a dangling reference.")
        return
    for column in _link_columns():
        op.add_column(_EVENTS, column)
    op.create_index(_PAYMENT_INDEX, _EVENTS, ['payment_transaction_id'], unique=False)
    op.create_index(_RECEIPT_INDEX, _EVENTS, ['receipt_id'], unique=False)
    op.create_foreign_key(
        _PAYMENT_FK, _EVENTS, 'payment_transactions', ['payment_transaction_id'], ['id']
    )
    op.create_foreign_key(_RECEIPT_FK, _EVENTS, 'receipts', ['receipt_id'], ['id'])
    for name, _before, after in _REPLACED_CHECKS:
        op.drop_constraint(name, _EVENTS, type_='check')
        op.create_check_constraint(name, _EVENTS, after)
    op.create_check_constraint(_LINKS_CHECK, _EVENTS, _LINKS)


def _restore_audit_events():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table(
            _EVENTS,
            copy_from=_audit_events_table(
                _KIND_BEFORE, _VERSION_BEFORE, _REASON_BEFORE, link_columns=True
            ),
            recreate='always',
        ) as batch_op:
            batch_op.drop_column('receipt_id')
            batch_op.drop_column('payment_transaction_id')
        if bind.exec_driver_sql('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError("The payment_audit_events rebuild left a dangling reference.")
        return
    op.drop_constraint(_LINKS_CHECK, _EVENTS, type_='check')
    for name, before, _after in _REPLACED_CHECKS:
        op.drop_constraint(name, _EVENTS, type_='check')
        op.create_check_constraint(name, _EVENTS, before)
    op.drop_constraint(_RECEIPT_FK, _EVENTS, type_='foreignkey')
    op.drop_constraint(_PAYMENT_FK, _EVENTS, type_='foreignkey')
    op.drop_index(_RECEIPT_INDEX, table_name=_EVENTS)
    op.drop_index(_PAYMENT_INDEX, table_name=_EVENTS)
    op.drop_column(_EVENTS, 'receipt_id')
    op.drop_column(_EVENTS, 'payment_transaction_id')


def upgrade():
    op.create_table('payment_transactions',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('invoice_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('method', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('currency_code', sa.String(length=3), nullable=False),
    sa.Column('amount', sa.DECIMAL(precision=19, scale=4), nullable=False),
    sa.Column('bank_transfer_reference', sa.String(length=64), nullable=True),
    sa.Column('bank_transfer_date', sa.Date(), nullable=True),
    sa.Column('recorded_at', sa.DateTime(), nullable=False),
    sa.Column('recorded_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(), nullable=True),
    sa.Column('confirmed_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('rejected_at', sa.DateTime(), nullable=True),
    sa.Column('rejected_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('rejection_reason', sa.String(length=500), nullable=True),
    sa.Column('reversal_of_payment_transaction_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("kind IN ('collection', 'reversal')", name='ck_payment_transactions_kind_valid'),
    sa.CheckConstraint("method IN ('cash', 'bank_transfer')", name='ck_payment_transactions_method_valid'),
    sa.CheckConstraint(
        "status IN ('pending', 'confirmed', 'rejected')",
        name='ck_payment_transactions_status_valid',
    ),
    sa.CheckConstraint("currency_code = 'LYD'", name='ck_payment_transactions_currency_code'),
    sa.CheckConstraint(
        'amount >= 0.001 AND amount <= 99999.999', name='ck_payment_transactions_amount_range'
    ),
    sa.CheckConstraint('version > 0', name='ck_payment_transactions_version_positive'),
    sa.CheckConstraint(
        "(kind = 'collection' AND method = 'bank_transfer'"
        " AND bank_transfer_reference IS NOT NULL AND LENGTH(bank_transfer_reference) > 0"
        " AND bank_transfer_date IS NOT NULL)"
        " OR ((kind = 'reversal' OR method = 'cash')"
        " AND bank_transfer_reference IS NULL AND bank_transfer_date IS NULL)",
        name='ck_payment_transactions_bank_transfer_details',
    ),
    sa.CheckConstraint(
        "(confirmed_at IS NULL AND confirmed_by_id IS NULL)"
        " OR (confirmed_at IS NOT NULL AND confirmed_by_id IS NOT NULL)",
        name='ck_payment_transactions_confirmation_pair',
    ),
    sa.CheckConstraint(
        "(rejected_at IS NULL AND rejected_by_id IS NULL AND rejection_reason IS NULL)"
        " OR (rejected_at IS NOT NULL AND rejected_by_id IS NOT NULL"
        " AND rejection_reason IS NOT NULL AND LENGTH(rejection_reason) > 0)",
        name='ck_payment_transactions_rejection_state',
    ),
    sa.CheckConstraint(
        "(status = 'pending' AND kind = 'collection' AND method = 'bank_transfer'"
        " AND confirmed_at IS NULL AND rejected_at IS NULL)"
        " OR (status = 'confirmed' AND confirmed_at IS NOT NULL AND rejected_at IS NULL)"
        " OR (status = 'rejected' AND kind = 'collection' AND method = 'bank_transfer'"
        " AND rejected_at IS NOT NULL AND confirmed_at IS NULL)",
        name='ck_payment_transactions_lifecycle_state',
    ),
    sa.CheckConstraint(
        "(kind = 'collection' AND method = 'bank_transfer')"
        " OR (confirmed_at = recorded_at AND confirmed_by_id = recorded_by_id)",
        name='ck_payment_transactions_immediate_confirmation',
    ),
    sa.CheckConstraint(
        "(kind = 'collection' AND reversal_of_payment_transaction_id IS NULL)"
        " OR (kind = 'reversal' AND reversal_of_payment_transaction_id IS NOT NULL)",
        name='ck_payment_transactions_reversal_link',
    ),
    sa.CheckConstraint(
        "recorded_at >= created_at AND updated_at >= recorded_at"
        " AND (confirmed_at IS NULL OR (confirmed_at >= recorded_at AND updated_at >= confirmed_at))"
        " AND (rejected_at IS NULL OR (rejected_at >= recorded_at AND updated_at >= rejected_at))",
        name='ck_payment_transactions_timestamps_ordered',
    ),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
    sa.ForeignKeyConstraint(['recorded_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['confirmed_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['rejected_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['reversal_of_payment_transaction_id'], ['payment_transactions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint(
        'reversal_of_payment_transaction_id', name='uq_payment_transactions_reversal_of'
    ),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_payment_transactions_invoice_id_id',
        'payment_transactions',
        ['invoice_id', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_payment_transactions_status_id', 'payment_transactions', ['status', 'id'], unique=False
    )
    op.create_index(
        'ix_payment_transactions_method_id', 'payment_transactions', ['method', 'id'], unique=False
    )
    op.create_index(
        'ix_payment_transactions_recorded_by_id',
        'payment_transactions',
        ['recorded_by_id'],
        unique=False,
    )
    op.create_index(
        'ix_payment_transactions_confirmed_by_id',
        'payment_transactions',
        ['confirmed_by_id'],
        unique=False,
    )
    op.create_index(
        'ix_payment_transactions_rejected_by_id',
        'payment_transactions',
        ['rejected_by_id'],
        unique=False,
    )

    op.create_table('receipt_number_sequences',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('calendar_year', sa.Integer(), nullable=False),
    sa.Column('last_number', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        'calendar_year >= 1000 AND calendar_year <= 9999',
        name='ck_receipt_number_sequences_year_range',
    ),
    sa.CheckConstraint(
        'last_number >= 0 AND last_number <= 999999',
        name='ck_receipt_number_sequences_last_number_range',
    ),
    sa.CheckConstraint(
        'updated_at >= created_at', name='ck_receipt_number_sequences_timestamps_ordered'
    ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('calendar_year', name='uq_receipt_number_sequences_calendar_year')
    )

    op.create_table('receipts',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('payment_transaction_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('receipt_number', sa.String(length=15), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('issued_at', sa.DateTime(), nullable=False),
    sa.Column('issued_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('voided_at', sa.DateTime(), nullable=True),
    sa.Column('voided_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('void_reason', sa.String(length=500), nullable=True),
    sa.Column('snapshot', sa.JSON(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("status IN ('issued', 'voided')", name='ck_receipts_status_valid'),
    sa.CheckConstraint('version > 0', name='ck_receipts_version_positive'),
    sa.CheckConstraint(
        "receipt_number LIKE 'RCT-____-______' AND LENGTH(receipt_number) = 15",
        name='ck_receipts_number_format',
    ),
    sa.CheckConstraint(
        "(status = 'issued' AND voided_at IS NULL AND voided_by_id IS NULL AND void_reason IS NULL)"
        " OR (status = 'voided' AND voided_at IS NOT NULL AND voided_by_id IS NOT NULL"
        " AND void_reason IS NOT NULL AND LENGTH(void_reason) > 0)",
        name='ck_receipts_void_state',
    ),
    sa.CheckConstraint(
        "issued_at >= created_at AND updated_at >= issued_at"
        " AND (voided_at IS NULL OR (voided_at >= issued_at AND updated_at >= voided_at))",
        name='ck_receipts_timestamps_ordered',
    ),
    sa.ForeignKeyConstraint(['payment_transaction_id'], ['payment_transactions.id'], ),
    sa.ForeignKeyConstraint(['issued_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['voided_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('payment_transaction_id', name='uq_receipts_payment_transaction_id'),
    sa.UniqueConstraint('receipt_number', name='uq_receipts_receipt_number'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index('ix_receipts_issued_by_id', 'receipts', ['issued_by_id'], unique=False)
    op.create_index('ix_receipts_voided_by_id', 'receipts', ['voided_by_id'], unique=False)

    _extend_audit_events()


def downgrade():
    if not op.get_context().as_sql:
        existing = op.get_bind().execute(sa.text(_M05_ROWS_SQL)).scalar()
        if existing:
            raise RuntimeError(
                f"Refusing to downgrade: {existing} payment, receipt, receipt sequence or payment "
                "audit row(s) exist. The previous schema cannot represent that financial history, "
                "and this revision never rewrites or deletes it."
            )
    _restore_audit_events()
    # Dependants first, one statement per table. Dropping the indexes first
    # would fail on MySQL with errno 1553 for those leading with a
    # foreign-key column, and DROP TABLE removes them anyway.
    op.drop_table('receipts')
    op.drop_table('receipt_number_sequences')
    op.drop_table('payment_transactions')
