"""add mock payment intents

Revision ID: d4f7a2c9e1b6
Revises: c5e8f2a7d914
Create Date: 2026-09-18

Phase 5 / M06. One table is created; nothing existing is altered, rewritten
or deleted, and nothing is seeded.

The new table
-------------
``payment_intents`` -- ``public_id``; ``invoice_id``; ``provider`` (``mock``
only); the unique, opaque ``provider_reference``; the unique, server-generated
``idempotency_key`` (64 hex digits); ``status`` (``pending``,
``provider_succeeded``, ``provider_failed`` or ``cancelled``);
``currency_code`` (``LYD``); ``amount DECIMAL(19, 4)``, the invoice's
outstanding balance when the intent was created; ``created_by_id``; nullable
``provider_result_at`` / ``provider_result_by_id``; nullable ``terminal_at``;
nullable ``cancelled_by_id``; a positive ``version``; ``created_at`` /
``updated_at``.

A payment intent is **not** a payment: no payment transaction, receipt, audit
event or balance is derived from it in this Part. No column stores, or is
shaped to store, a card number, CVV/CVC, PIN, account number, bank credential,
proof, customer or provider secret. No trigger is created.

Constraints and indexes
-----------------------
Closed sets are literal ``IN`` CHECKs, never MySQL ``ENUM``: status,
provider, currency, M02's amount range, a positive version, a non-empty
reference, a 64-character key, the provider-result pair, the terminal-state
timestamp (present exactly for ``provider_failed`` and ``cancelled``), the
lifecycle truth table and ordered timestamps.

- ``ix_payment_intents_invoice_id_id`` (``invoice_id``, ``id``) -- one
  invoice's intents, the rows to lock and the foreign key;
- ``ix_payment_intents_status_id`` -- the filtered overview;
- ``ix_payment_intents_created_by_id``, ``_provider_result_by_id`` and
  ``_cancelled_by_id`` -- the ``users`` foreign keys.

"At most one active intent per invoice" has no database constraint (no
portable partial unique index); the application proves it under the invoice
lock. **No MySQL execution plan has been measured for this table.** Every
foreign key is plain, with no ``ON DELETE`` and no ``ON UPDATE`` action. No
``mysql_engine`` / ``mysql_charset`` argument is declared, so the table
inherits the server's defaults like every earlier table.

Downgrade
---------
Dropping the table would destroy payment-intent history. The downgrade
therefore first counts the table's rows and **refuses** before changing
anything when any exists -- MySQL DDL is not transactional. With none, it drops
the table. In offline (``--sql``) mode that read cannot run, and it is
skipped. Development MySQL is never downgraded.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4f7a2c9e1b6'
down_revision = 'c5e8f2a7d914'
branch_labels = None
depends_on = None


_ROWS_SQL = "SELECT COUNT(*) FROM payment_intents"


def upgrade():
    op.create_table('payment_intents',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('invoice_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('provider', sa.String(length=16), nullable=False),
    sa.Column('provider_reference', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('currency_code', sa.String(length=3), nullable=False),
    sa.Column('amount', sa.DECIMAL(precision=19, scale=4), nullable=False),
    sa.Column('created_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('provider_result_at', sa.DateTime(), nullable=True),
    sa.Column('provider_result_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('terminal_at', sa.DateTime(), nullable=True),
    sa.Column('cancelled_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "status IN ('pending', 'provider_succeeded', 'provider_failed', 'cancelled')",
        name='ck_payment_intents_status_valid',
    ),
    sa.CheckConstraint("provider IN ('mock')", name='ck_payment_intents_provider_valid'),
    sa.CheckConstraint("currency_code = 'LYD'", name='ck_payment_intents_currency_code'),
    sa.CheckConstraint(
        'amount >= 0.001 AND amount <= 99999.999', name='ck_payment_intents_amount_range'
    ),
    sa.CheckConstraint('version > 0', name='ck_payment_intents_version_positive'),
    sa.CheckConstraint(
        'LENGTH(provider_reference) > 0', name='ck_payment_intents_provider_reference_present'
    ),
    sa.CheckConstraint(
        'LENGTH(idempotency_key) = 64', name='ck_payment_intents_idempotency_key_length'
    ),
    sa.CheckConstraint(
        "(provider_result_at IS NULL AND provider_result_by_id IS NULL)"
        " OR (provider_result_at IS NOT NULL AND provider_result_by_id IS NOT NULL)",
        name='ck_payment_intents_provider_result_pair',
    ),
    sa.CheckConstraint(
        "(status IN ('pending', 'provider_succeeded') AND terminal_at IS NULL)"
        " OR (status IN ('provider_failed', 'cancelled') AND terminal_at IS NOT NULL)",
        name='ck_payment_intents_terminal_state',
    ),
    sa.CheckConstraint(
        "(status = 'pending' AND provider_result_at IS NULL AND cancelled_by_id IS NULL)"
        " OR (status = 'provider_succeeded' AND provider_result_at IS NOT NULL"
        " AND cancelled_by_id IS NULL)"
        " OR (status = 'provider_failed' AND provider_result_at IS NOT NULL"
        " AND terminal_at = provider_result_at AND cancelled_by_id IS NULL)"
        " OR (status = 'cancelled' AND provider_result_at IS NULL AND cancelled_by_id IS NOT NULL)",
        name='ck_payment_intents_lifecycle_state',
    ),
    sa.CheckConstraint(
        "updated_at >= created_at"
        " AND (provider_result_at IS NULL"
        " OR (provider_result_at >= created_at AND updated_at >= provider_result_at))"
        " AND (terminal_at IS NULL OR (terminal_at >= created_at AND updated_at >= terminal_at))",
        name='ck_payment_intents_timestamps_ordered',
    ),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['provider_result_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['cancelled_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider_reference', name='uq_payment_intents_provider_reference'),
    sa.UniqueConstraint('idempotency_key', name='uq_payment_intents_idempotency_key'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_payment_intents_invoice_id_id', 'payment_intents', ['invoice_id', 'id'], unique=False
    )
    op.create_index(
        'ix_payment_intents_status_id', 'payment_intents', ['status', 'id'], unique=False
    )
    op.create_index(
        'ix_payment_intents_created_by_id', 'payment_intents', ['created_by_id'], unique=False
    )
    op.create_index(
        'ix_payment_intents_provider_result_by_id',
        'payment_intents',
        ['provider_result_by_id'],
        unique=False,
    )
    op.create_index(
        'ix_payment_intents_cancelled_by_id', 'payment_intents', ['cancelled_by_id'], unique=False
    )


def downgrade():
    if not op.get_context().as_sql:
        existing = op.get_bind().execute(sa.text(_ROWS_SQL)).scalar()
        if existing:
            raise RuntimeError(
                f"Refusing to downgrade: {existing} payment intent row(s) exist. This revision "
                "never deletes payment-intent history."
            )
    # One statement. Dropping the indexes first would fail on MySQL with errno
    # 1553 for those leading with a foreign-key column, and DROP TABLE removes
    # them anyway.
    op.drop_table('payment_intents')
