"""add fee plans and fee plan items

Revision ID: b7c3e9a15d42
Revises: d2b7e6a4c519
Create Date: 2026-09-14

Phase 5 / M02. Exactly two things happen here: the ``fee_plans`` table is
created, then the ``fee_plan_items`` table, each with its CHECK constraints,
unique constraints, plain foreign keys and query-driven indexes.

Nothing else is touched: no other table, column, index or constraint is
altered, no existing row is read or rewritten, and nothing is seeded -- a
fee plan exists only because an Administrator created one, so a migration
that invented fees would be stating prices nobody set.

The new tables
--------------
``fee_plans`` -- ``public_id``; a unique ``name``; an optional plain-text
``description``; ``currency_code`` (always ``LYD``); ``status`` (``draft``,
``active`` or ``archived``); ``created_by_id``; ``first_activated_at`` /
``first_activated_by_id`` (set once, at the freeze); ``status_changed_at`` /
``status_changed_by_id`` (the latest lifecycle transition); a positive
``version``; whole-second UTC ``created_at`` / ``updated_at``.

``fee_plan_items`` -- ``public_id``; ``fee_plan_id``; ``kind``
(``registration`` or ``course``); ``label``; an exact ``DECIMAL(19, 4)``
``amount``; ``status`` (``active`` or ``removed``); ``removed_at`` /
``removed_by_id``; a positive ``version``; whole-second UTC ``created_at`` /
``updated_at``.

No column stores, or is shaped to store, card or bank data: no card number,
expiry, security code, holder name, token or account number.

What each rule is for
---------------------
- ``ck_fee_plans_status_valid`` / ``ck_fee_plan_items_kind_valid`` /
  ``ck_fee_plan_items_status_valid`` -- the closed sets, as literal ``IN``
  lists rather than MySQL ``ENUM`` columns.
- ``ck_fee_plans_currency_code`` -- ``LYD`` only.
- ``ck_fee_plans_version_positive`` / ``ck_fee_plan_items_version_positive``.
- ``ck_fee_plans_first_activation_pair`` / ``ck_fee_plans_status_change_pair``
  -- an attribution moment and its actor are set together.
- ``ck_fee_plans_lifecycle_state`` -- a draft was never activated and never
  changed state; an active plan was activated; an archived plan changed
  state, no earlier than any activation.
- ``ck_fee_plans_timestamps_ordered`` / ``ck_fee_plan_items_timestamps_ordered``.
- ``ck_fee_plan_items_amount_range`` -- 0.001 through 99999.999 inclusive.
- ``ck_fee_plan_items_removal_state`` -- removal attribution exactly while
  ``removed``.
- ``uq_fee_plans_name`` -- one name across every status.

Conditions a CHECK cannot express are stated here rather than hidden: that an
attributed account is an active Administrator, that a frozen plan's
definition never changes, and that an active plan has one to twenty active
items with distinct labels. The application proves each against locked rows.

Indexes, one per real query path
--------------------------------
- ``ix_fee_plans_status_id`` (``status``, ``id``) -- the filtered list.
- ``ix_fee_plans_created_by_id``, ``ix_fee_plans_first_activated_by_id``,
  ``ix_fee_plans_status_changed_by_id`` -- the three ``users`` foreign keys.
- ``ix_fee_plan_items_plan_status_id`` (``fee_plan_id``, ``status``, ``id``)
  -- a plan's active items, the list totals and the removed history; also
  the ``fee_plan_id`` foreign key.
- ``ix_fee_plan_items_removed_by_id`` -- the ``removed_by_id`` foreign key.

**No MySQL execution plan has been measured for these tables.**

Every foreign key is plain, with no ``ON DELETE`` and no ``ON UPDATE``
action: nothing in the catalogue is ever hard-deleted, and no account change
may remove a plan or an item. No ``mysql_engine`` / ``mysql_charset``
argument is declared, so both tables inherit the server's defaults like
every earlier table.

The downgrade drops ``fee_plan_items`` and then ``fee_plans`` -- child before
parent, one statement each. Indexes are not dropped first: several lead with
a foreign-key column, which MySQL refuses to drop while the constraint exists
(errno 1553), and ``DROP TABLE`` removes them anyway.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7c3e9a15d42'
down_revision = 'd2b7e6a4c519'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('fee_plans',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('description', sa.String(length=1000), nullable=True),
    sa.Column('currency_code', sa.String(length=3), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('created_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('first_activated_at', sa.DateTime(), nullable=True),
    sa.Column('first_activated_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('status_changed_at', sa.DateTime(), nullable=True),
    sa.Column('status_changed_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "status IN ('draft', 'active', 'archived')",
        name='ck_fee_plans_status_valid',
    ),
    sa.CheckConstraint(
        "currency_code = 'LYD'",
        name='ck_fee_plans_currency_code',
    ),
    sa.CheckConstraint('version > 0', name='ck_fee_plans_version_positive'),
    sa.CheckConstraint(
        "(first_activated_at IS NULL AND first_activated_by_id IS NULL)"
        " OR (first_activated_at IS NOT NULL AND first_activated_by_id IS NOT NULL)",
        name='ck_fee_plans_first_activation_pair',
    ),
    sa.CheckConstraint(
        "(status_changed_at IS NULL AND status_changed_by_id IS NULL)"
        " OR (status_changed_at IS NOT NULL AND status_changed_by_id IS NOT NULL)",
        name='ck_fee_plans_status_change_pair',
    ),
    sa.CheckConstraint(
        "(status = 'draft' AND first_activated_at IS NULL AND status_changed_at IS NULL)"
        " OR (status = 'active' AND first_activated_at IS NOT NULL"
        " AND status_changed_at IS NOT NULL AND status_changed_at >= first_activated_at)"
        " OR (status = 'archived' AND status_changed_at IS NOT NULL"
        " AND (first_activated_at IS NULL OR status_changed_at >= first_activated_at))",
        name='ck_fee_plans_lifecycle_state',
    ),
    sa.CheckConstraint(
        "updated_at >= created_at"
        " AND (first_activated_at IS NULL OR first_activated_at >= created_at)"
        " AND (status_changed_at IS NULL"
        " OR (status_changed_at >= created_at AND updated_at >= status_changed_at))",
        name='ck_fee_plans_timestamps_ordered',
    ),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['first_activated_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['status_changed_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', name='uq_fee_plans_name'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_fee_plans_status_id',
        'fee_plans',
        ['status', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_fee_plans_created_by_id',
        'fee_plans',
        ['created_by_id'],
        unique=False,
    )
    op.create_index(
        'ix_fee_plans_first_activated_by_id',
        'fee_plans',
        ['first_activated_by_id'],
        unique=False,
    )
    op.create_index(
        'ix_fee_plans_status_changed_by_id',
        'fee_plans',
        ['status_changed_by_id'],
        unique=False,
    )

    op.create_table('fee_plan_items',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('fee_plan_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('label', sa.String(length=150), nullable=False),
    sa.Column('amount', sa.DECIMAL(precision=19, scale=4), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('removed_at', sa.DateTime(), nullable=True),
    sa.Column('removed_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "kind IN ('registration', 'course')",
        name='ck_fee_plan_items_kind_valid',
    ),
    sa.CheckConstraint(
        "status IN ('active', 'removed')",
        name='ck_fee_plan_items_status_valid',
    ),
    sa.CheckConstraint(
        'amount >= 0.001 AND amount <= 99999.999',
        name='ck_fee_plan_items_amount_range',
    ),
    sa.CheckConstraint('version > 0', name='ck_fee_plan_items_version_positive'),
    sa.CheckConstraint(
        "(status = 'active' AND removed_at IS NULL AND removed_by_id IS NULL)"
        " OR (status = 'removed' AND removed_at IS NOT NULL AND removed_by_id IS NOT NULL)",
        name='ck_fee_plan_items_removal_state',
    ),
    sa.CheckConstraint(
        "updated_at >= created_at"
        " AND (removed_at IS NULL OR (removed_at >= created_at AND updated_at >= removed_at))",
        name='ck_fee_plan_items_timestamps_ordered',
    ),
    sa.ForeignKeyConstraint(['fee_plan_id'], ['fee_plans.id'], ),
    sa.ForeignKeyConstraint(['removed_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_fee_plan_items_plan_status_id',
        'fee_plan_items',
        ['fee_plan_id', 'status', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_fee_plan_items_removed_by_id',
        'fee_plan_items',
        ['removed_by_id'],
        unique=False,
    )


def downgrade():
    # Child before parent, one statement each. Dropping the indexes first
    # would fail on MySQL with errno 1553 for those leading with a
    # foreign-key column, and DROP TABLE removes them anyway.
    op.drop_table('fee_plan_items')
    op.drop_table('fee_plans')
