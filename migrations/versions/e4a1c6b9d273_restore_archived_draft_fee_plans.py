"""restore archived draft fee plans

Revision ID: e4a1c6b9d273
Revises: b7c3e9a15d42
Create Date: 2026-09-14

Phase 5 / M02R. Exactly one thing changes: the ``ck_fee_plans_lifecycle_state``
CHECK on ``fee_plans`` is replaced.

Why
---
M02 required a ``draft`` plan's ``status_changed_at`` to be NULL. The approved
lifecycle restores an archived plan that was never activated to ``draft``
while keeping the restoration in ``status_changed_at`` / ``status_changed_by_id``
-- a row that CHECK refuses. The corrected expression permits a draft whose
``first_activated_at`` is NULL whether or not it carries a historical
transition, and is otherwise identical: an active plan still needs a first
activation and a transition no earlier than it; an archived plan still needs a
transition, no earlier than any first activation.

What is not touched
-------------------
No table, column, index, foreign key, unique constraint or other CHECK --
the status, currency, version, pair and timestamp CHECKs on ``fee_plans`` and
every ``fee_plan_items`` constraint, the money range included, stay exactly
as ``b7c3e9a15d42`` created them. No row is written, rewritten or deleted, and
nothing is seeded. ``fee_plan_items`` is never named.

How, per backend
----------------
A CHECK expression cannot be altered in place on either backend.

- **MySQL 8**: the constraint is dropped and added again under the same name.
  MySQL validates every existing row when it is added; the corrected
  expression accepts every row the old one did, so the upgrade cannot fail on
  data.
- **SQLite** (the isolated migration tests only; SQLite is not a deployment
  target): the table is rebuilt by batch mode from an explicitly declared
  ``copy_from`` definition -- never a reflected one, which would carry the old
  CHECK back into the rebuild -- the arrangement M09, M11 and M12 used for
  ``notifications``. Unlike ``notifications``, ``fee_plans`` is *referenced*:
  ``fee_plan_items.fee_plan_id`` points at it, and the rebuild's
  ``DROP TABLE fee_plans`` step is refused while SQLite enforces foreign keys
  and any item exists. Alembic does not manage that pragma, and SQLite ignores
  ``PRAGMA foreign_keys`` inside an open transaction, so the revision cannot
  switch it safely itself. Following SQLite's documented table-rebuild
  procedure, the revision therefore **requires** enforcement to be off --
  refusing rather than silently disabling it -- and checks
  ``PRAGMA foreign_key_check`` after the rebuild; the caller restores
  enforcement afterwards, as the migration test does and proves.

Downgrade
---------
The downgrade restores the exact ``b7c3e9a15d42`` expression. A restored draft
-- ``status = 'draft'`` with a non-NULL ``status_changed_at`` -- is valid only
under the corrected CHECK. Rather than rewrite or delete that history, the
downgrade first counts such rows and **refuses** before dropping anything:
MySQL DDL is not transactional, and an ``ADD CONSTRAINT`` refused after the
drop would leave ``fee_plans`` with no lifecycle CHECK at all. In offline
(``--sql``) mode that read cannot run, and it is skipped.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e4a1c6b9d273'
down_revision = 'b7c3e9a15d42'
branch_labels = None
depends_on = None


_LIFECYCLE_CHECK_NAME = 'ck_fee_plans_lifecycle_state'

#: The ``b7c3e9a15d42`` expression, restored by the downgrade.
_LIFECYCLE_BEFORE = (
    "(status = 'draft' AND first_activated_at IS NULL AND status_changed_at IS NULL)"
    " OR (status = 'active' AND first_activated_at IS NOT NULL"
    " AND status_changed_at IS NOT NULL AND status_changed_at >= first_activated_at)"
    " OR (status = 'archived' AND status_changed_at IS NOT NULL"
    " AND (first_activated_at IS NULL OR status_changed_at >= first_activated_at))"
)

#: The corrected expression: only the draft branch is wider.
_LIFECYCLE_AFTER = (
    "(status = 'draft' AND first_activated_at IS NULL)"
    " OR (status = 'active' AND first_activated_at IS NOT NULL"
    " AND status_changed_at IS NOT NULL AND status_changed_at >= first_activated_at)"
    " OR (status = 'archived' AND status_changed_at IS NOT NULL"
    " AND (first_activated_at IS NULL OR status_changed_at >= first_activated_at))"
)

_RESTORED_DRAFTS_SQL = (
    "SELECT COUNT(*) FROM fee_plans"
    " WHERE status = 'draft' AND status_changed_at IS NOT NULL"
)


def _fee_plans_table(lifecycle_sql):
    """The complete ``fee_plans`` definition from ``b7c3e9a15d42`` with the
    given lifecycle CHECK -- used only as the SQLite rebuild's ``copy_from``.
    Every other column, constraint and index is exactly as that revision
    created it."""
    return sa.Table(
        'fee_plans',
        sa.MetaData(),
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
        sa.CheckConstraint(lifecycle_sql, name=_LIFECYCLE_CHECK_NAME),
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
        sa.UniqueConstraint('public_id'),
        sa.Index('ix_fee_plans_status_id', 'status', 'id'),
        sa.Index('ix_fee_plans_created_by_id', 'created_by_id'),
        sa.Index('ix_fee_plans_first_activated_by_id', 'first_activated_by_id'),
        sa.Index('ix_fee_plans_status_changed_by_id', 'status_changed_by_id'),
    )


def _replace_lifecycle_check(lifecycle_sql):
    """Replace ``ck_fee_plans_lifecycle_state`` with `lifecycle_sql`,
    preserving every row. See the module docstring for the SQLite rule."""
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        if bind.exec_driver_sql('PRAGMA foreign_keys').scalar():
            raise RuntimeError(
                "Rebuilding fee_plans on SQLite requires PRAGMA foreign_keys=OFF: "
                "fee_plan_items references fee_plans, and the rebuild's DROP TABLE "
                "step is refused under enforced foreign keys. Disable enforcement "
                "outside a transaction, run the migration, then re-enable it."
            )
        with op.batch_alter_table(
            'fee_plans',
            copy_from=_fee_plans_table(lifecycle_sql),
            recreate='always',
        ) as batch_op:
            # A deliberate no-change alter: batch mode needs one operation
            # to flush, and the rebuilt shape comes entirely from copy_from.
            batch_op.alter_column(
                'status',
                existing_type=sa.String(length=32),
                existing_nullable=False,
            )
        if bind.exec_driver_sql('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError("The fee_plans rebuild left a dangling foreign-key reference.")
    else:
        op.drop_constraint(_LIFECYCLE_CHECK_NAME, 'fee_plans', type_='check')
        op.create_check_constraint(_LIFECYCLE_CHECK_NAME, 'fee_plans', lifecycle_sql)


def upgrade():
    _replace_lifecycle_check(_LIFECYCLE_AFTER)


def downgrade():
    if not op.get_context().as_sql:
        restored = op.get_bind().execute(sa.text(_RESTORED_DRAFTS_SQL)).scalar()
        if restored:
            raise RuntimeError(
                f"Refusing to downgrade: {restored} fee plan(s) are drafts restored from "
                "the archive. The previous lifecycle CHECK cannot represent that history, "
                "and this revision never rewrites or deletes it."
            )
    _replace_lifecycle_check(_LIFECYCLE_BEFORE)
