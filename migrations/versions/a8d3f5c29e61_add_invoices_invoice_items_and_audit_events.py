"""add invoices, invoice items and audit events

Revision ID: a8d3f5c29e61
Revises: f9b2d6e4a318
Create Date: 2026-09-14

Phase 5 / M04. Four tables are created, with their CHECK constraints, plain
foreign keys and query-driven indexes, and nothing else happens: no other
table, column, index or constraint is altered, no existing row is read or
rewritten, and nothing is seeded -- an invoice exists only because an
Administrator created one from a Student Fee Assignment.

The new tables
--------------
``invoice_number_sequences`` -- internal and never shown: one lockable row per
center-local ``calendar_year`` holding ``last_number``, the latest allocated
``INV-YYYY-NNNNNN`` number of that year; ``created_at`` / ``updated_at``.

``invoices`` -- ``public_id``; ``student_fee_assignment_id``; ``currency_code``
(``LYD``); ``status`` (``draft``, ``issued`` or ``cancelled``); the nullable,
unique ``invoice_number``; ``issued_at`` / ``issued_by_id``; ``cancelled_at``
/ ``cancelled_by_id``; a positive ``version``; ``created_at`` /
``updated_at``. No total is stored.

``invoice_items`` -- ``public_id``; ``invoice_id``; ``kind`` (``registration``
or ``course``); ``label``; ``amount DECIMAL(19, 4)``; ``status`` (``active``
or ``removed``); ``removed_at`` / ``removed_by_id``; ``version``;
``created_at`` / ``updated_at``. Deliberately no reference to
``fee_plan_items``: a line is a snapshot the plan can never move.

``payment_audit_events`` -- the append-only financial trail: ``invoice_id``,
``actor_id``, ``kind``, ``occurred_at``, ``invoice_version_before`` /
``invoice_version_after``, ``reason`` and the ``before_snapshot`` /
``after_snapshot`` JSON documents. No public id, and no ``updated_at``: an
event never changes.

No column stores, or is shaped to store, card, bank, provider, payment,
receipt, refund, discount, tax, installment or due-date data. No trigger is
created.

What each rule is for
---------------------
- Closed sets are literal ``IN`` lists, never MySQL ``ENUM``:
  ``ck_invoices_status_valid``, ``ck_invoice_items_kind_valid``,
  ``ck_invoice_items_status_valid``, ``ck_payment_audit_events_kind_valid``.
- ``ck_invoices_currency_code`` -- ``LYD`` only.
- Positive versions: ``ck_invoices_version_positive``,
  ``ck_invoice_items_version_positive``,
  ``ck_payment_audit_events_versions_positive``; and
  ``ck_payment_audit_events_version_transition`` -- a creation moves from
  nothing to 1, every other event by exactly one.
- Attribution pairs: ``ck_invoices_issue_pair``,
  ``ck_invoices_cancellation_pair``, ``ck_invoice_items_removal_state``.
- ``ck_invoices_number_matches_issue`` -- a number exists exactly when the
  invoice was issued; ``ck_invoices_number_format`` -- its portable shape
  (the digits are the application's); ``uq_invoices_invoice_number``.
- ``ck_invoices_lifecycle_state`` -- draft, issued and cancelled rows carry
  exactly the moments their state implies.
- Ordered timestamps: ``ck_invoices_timestamps_ordered``,
  ``ck_invoice_items_timestamps_ordered``,
  ``ck_invoice_number_sequences_timestamps_ordered``.
- ``ck_invoice_items_amount_range`` -- Phase 5 / M02's money bounds.
- ``ck_payment_audit_events_snapshots_present`` -- a creation has no "before",
  every event an "after"; ``ck_payment_audit_events_reason_required`` -- a
  post-issue edit and a cancellation carry a reason, other events none.
- ``uq_invoice_number_sequences_calendar_year``,
  ``ck_invoice_number_sequences_year_range`` and
  ``ck_invoice_number_sequences_last_number_range``.

Conditions a CHECK cannot express are stated here rather than hidden: that an
assignment has at most one ``draft`` or ``issued`` invoice (MySQL has no
portable partial unique index), that an invoice keeps at least one active
line with distinct labels, that a snapshot has the exact layout and total,
that an event's actor was an active Administrator, and that events are never
updated or deleted. The application proves each against locked rows or in
its ORM guards.

Indexes, one per real lookup path
---------------------------------
- ``ix_invoices_assignment_status_id`` (``student_fee_assignment_id``,
  ``status``, ``id``) -- the open-invoice check, the rows to lock, one
  assignment's history and the foreign key; ``ix_invoices_issued_by_id`` and
  ``ix_invoices_cancelled_by_id``.
- ``ix_invoice_items_invoice_status_id`` (``invoice_id``, ``status``, ``id``)
  and ``ix_invoice_items_removed_by_id``.
- ``ix_payment_audit_events_invoice_id_id`` (``invoice_id``, ``id``) -- one
  invoice's timeline -- and ``ix_payment_audit_events_actor_id``.

**No MySQL execution plan has been measured for these tables.**

Every foreign key is plain, with no ``ON DELETE`` and no ``ON UPDATE``
action: nothing here is ever hard-deleted. No ``mysql_engine`` /
``mysql_charset`` argument is declared, so the tables inherit the server's
defaults like every earlier table.

The downgrade drops the four tables, dependants first, one statement each.
Indexes are not dropped first: several lead with a foreign-key column, which
MySQL refuses to drop while the constraint exists (errno 1553), and
``DROP TABLE`` removes them anyway.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8d3f5c29e61'
down_revision = 'f9b2d6e4a318'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('invoice_number_sequences',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('calendar_year', sa.Integer(), nullable=False),
    sa.Column('last_number', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        'calendar_year >= 1000 AND calendar_year <= 9999',
        name='ck_invoice_number_sequences_year_range',
    ),
    sa.CheckConstraint(
        'last_number >= 0 AND last_number <= 999999',
        name='ck_invoice_number_sequences_last_number_range',
    ),
    sa.CheckConstraint(
        'updated_at >= created_at', name='ck_invoice_number_sequences_timestamps_ordered'
    ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('calendar_year', name='uq_invoice_number_sequences_calendar_year')
    )

    op.create_table('invoices',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('student_fee_assignment_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('currency_code', sa.String(length=3), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('invoice_number', sa.String(length=15), nullable=True),
    sa.Column('issued_at', sa.DateTime(), nullable=True),
    sa.Column('issued_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(), nullable=True),
    sa.Column('cancelled_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "status IN ('draft', 'issued', 'cancelled')",
        name='ck_invoices_status_valid',
    ),
    sa.CheckConstraint("currency_code = 'LYD'", name='ck_invoices_currency_code'),
    sa.CheckConstraint('version > 0', name='ck_invoices_version_positive'),
    sa.CheckConstraint(
        "(issued_at IS NULL AND issued_by_id IS NULL)"
        " OR (issued_at IS NOT NULL AND issued_by_id IS NOT NULL)",
        name='ck_invoices_issue_pair',
    ),
    sa.CheckConstraint(
        "(cancelled_at IS NULL AND cancelled_by_id IS NULL)"
        " OR (cancelled_at IS NOT NULL AND cancelled_by_id IS NOT NULL)",
        name='ck_invoices_cancellation_pair',
    ),
    sa.CheckConstraint(
        "(invoice_number IS NULL AND issued_at IS NULL)"
        " OR (invoice_number IS NOT NULL AND issued_at IS NOT NULL)",
        name='ck_invoices_number_matches_issue',
    ),
    sa.CheckConstraint(
        "invoice_number IS NULL"
        " OR (invoice_number LIKE 'INV-____-______' AND LENGTH(invoice_number) = 15)",
        name='ck_invoices_number_format',
    ),
    sa.CheckConstraint(
        "(status = 'draft' AND issued_at IS NULL AND cancelled_at IS NULL)"
        " OR (status = 'issued' AND issued_at IS NOT NULL AND cancelled_at IS NULL)"
        " OR (status = 'cancelled' AND cancelled_at IS NOT NULL)",
        name='ck_invoices_lifecycle_state',
    ),
    sa.CheckConstraint(
        "updated_at >= created_at"
        " AND (issued_at IS NULL OR (issued_at >= created_at AND updated_at >= issued_at))"
        " AND (cancelled_at IS NULL"
        " OR (cancelled_at >= created_at AND updated_at >= cancelled_at"
        " AND (issued_at IS NULL OR cancelled_at >= issued_at)))",
        name='ck_invoices_timestamps_ordered',
    ),
    sa.ForeignKeyConstraint(['student_fee_assignment_id'], ['student_fee_assignments.id'], ),
    sa.ForeignKeyConstraint(['issued_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['cancelled_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('invoice_number', name='uq_invoices_invoice_number'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_invoices_assignment_status_id',
        'invoices',
        ['student_fee_assignment_id', 'status', 'id'],
        unique=False,
    )
    op.create_index('ix_invoices_issued_by_id', 'invoices', ['issued_by_id'], unique=False)
    op.create_index('ix_invoices_cancelled_by_id', 'invoices', ['cancelled_by_id'], unique=False)

    op.create_table('invoice_items',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('invoice_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('label', sa.String(length=150), nullable=False),
    sa.Column('amount', sa.DECIMAL(precision=19, scale=4), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('removed_at', sa.DateTime(), nullable=True),
    sa.Column('removed_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("kind IN ('registration', 'course')", name='ck_invoice_items_kind_valid'),
    sa.CheckConstraint("status IN ('active', 'removed')", name='ck_invoice_items_status_valid'),
    sa.CheckConstraint(
        'amount >= 0.001 AND amount <= 99999.999', name='ck_invoice_items_amount_range'
    ),
    sa.CheckConstraint('version > 0', name='ck_invoice_items_version_positive'),
    sa.CheckConstraint(
        "(status = 'active' AND removed_at IS NULL AND removed_by_id IS NULL)"
        " OR (status = 'removed' AND removed_at IS NOT NULL AND removed_by_id IS NOT NULL)",
        name='ck_invoice_items_removal_state',
    ),
    sa.CheckConstraint(
        "updated_at >= created_at"
        " AND (removed_at IS NULL OR (removed_at >= created_at AND updated_at >= removed_at))",
        name='ck_invoice_items_timestamps_ordered',
    ),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
    sa.ForeignKeyConstraint(['removed_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_invoice_items_invoice_status_id',
        'invoice_items',
        ['invoice_id', 'status', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_invoice_items_removed_by_id', 'invoice_items', ['removed_by_id'], unique=False
    )

    op.create_table('payment_audit_events',
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
    sa.CheckConstraint(
        "kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued',"
        " 'invoice_issued_edited', 'invoice_cancelled')",
        name='ck_payment_audit_events_kind_valid',
    ),
    sa.CheckConstraint(
        "invoice_version_after > 0"
        " AND (invoice_version_before IS NULL OR invoice_version_before > 0)",
        name='ck_payment_audit_events_versions_positive',
    ),
    sa.CheckConstraint(
        "(kind = 'invoice_draft_created' AND invoice_version_before IS NULL"
        " AND invoice_version_after = 1)"
        " OR (kind <> 'invoice_draft_created' AND invoice_version_before IS NOT NULL"
        " AND invoice_version_after = invoice_version_before + 1)",
        name='ck_payment_audit_events_version_transition',
    ),
    sa.CheckConstraint(
        "(kind = 'invoice_draft_created' AND before_snapshot IS NULL"
        " AND after_snapshot IS NOT NULL)"
        " OR (kind <> 'invoice_draft_created' AND before_snapshot IS NOT NULL"
        " AND after_snapshot IS NOT NULL)",
        name='ck_payment_audit_events_snapshots_present',
    ),
    sa.CheckConstraint(
        "(kind IN ('invoice_cancelled', 'invoice_issued_edited') AND reason IS NOT NULL"
        " AND LENGTH(reason) > 0)"
        " OR (kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued')"
        " AND reason IS NULL)",
        name='ck_payment_audit_events_reason_required',
    ),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'ix_payment_audit_events_invoice_id_id',
        'payment_audit_events',
        ['invoice_id', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_payment_audit_events_actor_id', 'payment_audit_events', ['actor_id'], unique=False
    )


def downgrade():
    # Dependants first, one statement per table. Dropping the indexes first
    # would fail on MySQL with errno 1553 for those leading with a
    # foreign-key column, and DROP TABLE removes them anyway.
    op.drop_table('payment_audit_events')
    op.drop_table('invoice_items')
    op.drop_table('invoices')
    op.drop_table('invoice_number_sequences')
