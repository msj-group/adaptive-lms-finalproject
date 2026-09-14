"""add student fee assignments

Revision ID: f9b2d6e4a318
Revises: e4a1c6b9d273
Create Date: 2026-09-14

Phase 5 / M03. Exactly one thing happens here: the ``student_fee_assignments``
table is created, with its CHECK constraints, plain foreign keys and
query-driven indexes.

Nothing else is touched: no other table, column, index or constraint is
altered, no existing row is read or rewritten, and nothing is seeded -- an
assignment exists only because an Administrator assigned a fee plan to one
Enrollment, so a migration that invented one would be charging a Student
nobody decided to charge.

The new table
-------------
``student_fee_assignments`` -- ``public_id``; ``enrollment_id`` (the exact
registration the fees apply to); ``fee_plan_id``; ``status`` (``assigned`` or
``cancelled``); ``assigned_at`` / ``assigned_by_id``; ``cancelled_at`` /
``cancelled_by_id`` (set once, at cancellation); a positive ``version``;
whole-second UTC ``created_at`` / ``updated_at``.

Deliberately absent: ``student_id``, ``group_id``, ``course_id``, any term id,
the plan's name, currency, items, amounts or total, and any invoice, payment,
receipt or provider column. The Enrollment supplies the academic and Student
identity, and the plan -- frozen from its first activation -- supplies the
financial definition. No column stores, or is shaped to store, card or bank
data.

What each rule is for
---------------------
- ``ck_student_fee_assignments_status_valid`` -- the closed set, as a literal
  ``IN`` list rather than a MySQL ``ENUM``.
- ``ck_student_fee_assignments_version_positive``.
- ``ck_student_fee_assignments_assignment_pair`` /
  ``ck_student_fee_assignments_cancellation_pair`` -- an attribution moment and
  its actor are set together.
- ``ck_student_fee_assignments_lifecycle_state`` -- an assigned row carries no
  cancellation; a cancelled row always does.
- ``ck_student_fee_assignments_timestamps_ordered``.

Conditions a CHECK cannot express are stated here rather than hidden: that an
Enrollment has at most one ``assigned`` row (MySQL has no portable partial
unique index), that the Enrollment, its Student and its academic chain were
active and the plan active and frozen when the row was written, and that an
attributed account is an active Administrator. The application proves each
against locked rows.

Indexes, one per real lookup path
---------------------------------
- ``ix_student_fee_assignments_enrollment_status_id`` (``enrollment_id``,
  ``status``, ``id``) -- the assigned-plan check, the rows to lock, one
  Enrollment's history, and the ``enrollment_id`` foreign key.
- ``ix_student_fee_assignments_fee_plan_id``,
  ``ix_student_fee_assignments_assigned_by_id``,
  ``ix_student_fee_assignments_cancelled_by_id`` -- the other three foreign
  keys.

**No MySQL execution plan has been measured for this table.**

Every foreign key is plain, with no ``ON DELETE`` and no ``ON UPDATE`` action:
nothing here is ever hard-deleted. No ``mysql_engine`` / ``mysql_charset``
argument is declared, so the table inherits the server's defaults like every
earlier table.

The downgrade drops the table in one statement. Indexes are not dropped first:
several lead with a foreign-key column, which MySQL refuses to drop while the
constraint exists (errno 1553), and ``DROP TABLE`` removes them anyway.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f9b2d6e4a318'
down_revision = 'e4a1c6b9d273'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('student_fee_assignments',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('enrollment_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('fee_plan_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('assigned_at', sa.DateTime(), nullable=False),
    sa.Column('assigned_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('cancelled_at', sa.DateTime(), nullable=True),
    sa.Column('cancelled_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "status IN ('assigned', 'cancelled')",
        name='ck_student_fee_assignments_status_valid',
    ),
    sa.CheckConstraint('version > 0', name='ck_student_fee_assignments_version_positive'),
    sa.CheckConstraint(
        'assigned_at IS NOT NULL AND assigned_by_id IS NOT NULL',
        name='ck_student_fee_assignments_assignment_pair',
    ),
    sa.CheckConstraint(
        "(cancelled_at IS NULL AND cancelled_by_id IS NULL)"
        " OR (cancelled_at IS NOT NULL AND cancelled_by_id IS NOT NULL)",
        name='ck_student_fee_assignments_cancellation_pair',
    ),
    sa.CheckConstraint(
        "(status = 'assigned' AND cancelled_at IS NULL)"
        " OR (status = 'cancelled' AND cancelled_at IS NOT NULL)",
        name='ck_student_fee_assignments_lifecycle_state',
    ),
    sa.CheckConstraint(
        "assigned_at >= created_at"
        " AND updated_at >= assigned_at"
        " AND (cancelled_at IS NULL"
        " OR (cancelled_at >= assigned_at AND updated_at >= cancelled_at))",
        name='ck_student_fee_assignments_timestamps_ordered',
    ),
    sa.ForeignKeyConstraint(['enrollment_id'], ['enrollments.id'], ),
    sa.ForeignKeyConstraint(['fee_plan_id'], ['fee_plans.id'], ),
    sa.ForeignKeyConstraint(['assigned_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['cancelled_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_student_fee_assignments_enrollment_status_id',
        'student_fee_assignments',
        ['enrollment_id', 'status', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_student_fee_assignments_fee_plan_id',
        'student_fee_assignments',
        ['fee_plan_id'],
        unique=False,
    )
    op.create_index(
        'ix_student_fee_assignments_assigned_by_id',
        'student_fee_assignments',
        ['assigned_by_id'],
        unique=False,
    )
    op.create_index(
        'ix_student_fee_assignments_cancelled_by_id',
        'student_fee_assignments',
        ['cancelled_by_id'],
        unique=False,
    )


def downgrade():
    # One statement. Dropping the indexes first would fail on MySQL with
    # errno 1553 for those leading with a foreign-key column, and DROP TABLE
    # removes them anyway.
    op.drop_table('student_fee_assignments')
