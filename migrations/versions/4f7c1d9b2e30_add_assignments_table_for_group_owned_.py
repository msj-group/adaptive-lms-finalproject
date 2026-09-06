"""add assignments table for group-owned time-gated assignments

Revision ID: 4f7c1d9b2e30
Revises: 023a5f5814a8
Create Date: 2026-09-02 10:41:07.514882

Additive only (Phase 4 / M01): creates ``assignments`` and nothing else.
No existing table, column, index, constraint, or row is touched -- in
particular ``groups`` is **not** altered, because the new
``Group.assignments`` relationship is the ORM's inverse of the
``assignments.group_id`` foreign key created here and needs no schema
change on the parent side. There is no data backfill: an Assignment is
authored work, so pre-existing Groups, Units, Lessons and Enrollments
deliberately produce no historical rows.

Each row is one Group-owned, time-gated Assignment: a UUID ``public_id``
(the only identifier that ever appears in a URL or in HTML), a
``group_id`` FK -- Course, Level and AcademicTerm are all reachable
through the Group and are deliberately **not** duplicated, and there is
no ``teacher_id``/``created_by`` because every active assigned Teacher is
an equal collaborator -- a ``title`` unique within the Group, required
plain-text ``instructions``, the naive-UTC ``opens_at`` / ``due_at``
window, a ``status`` constrained to ``draft`` / ``published``, a nullable
UTC ``published_at``, and UTC ``created_at`` / ``updated_at``.

Four named constraints are the final integrity defense:

- ``uq_assignments_group_title`` -- one title per Group, drafts included;
- ``ck_assignments_opens_before_due`` -- ``opens_at < due_at``, so equal
  or reversed times are rejected by the database itself;
- ``ck_assignments_status_valid`` -- the closed ``draft`` / ``published``
  set, so an unrecognised status can never be inserted by application
  code or by a manual row;
- ``ck_assignments_status_published_at_consistency`` -- a draft has no
  publication time and a published row always has one.

Two composite indexes are created:

- ``ix_assignments_group_due_id`` (``group_id``, ``due_at``, ``id``) --
  the Teacher list: a single-Group equality on ``group_id`` ordered by
  deadline, so the ordering columns follow the equality column directly.
  It lists draft **and** published rows together, which is why ``status``
  is deliberately absent: a column between the equality column and the
  sort columns is exactly the shape M14 measured resolving as
  ``Using filesort`` on ``notifications``.
- ``ix_assignments_group_status_opens_due`` (``group_id``, ``status``,
  ``opens_at``, ``due_at``) -- the Student visibility **filter**: an
  equality on ``group_id`` + ``status`` followed by the
  ``opens_at <= now`` range.

The second index is **not** claimed to supply the Student list's or
dashboard's ``due_at`` ordering: ``opens_at`` is a range predicate, so
columns after it cannot generally be assumed to provide ordering; the
Student list spans every Group the Student is enrolled in; and that
list's current/history ordering is a ``CASE`` expression, which needs a
sort of its own. Those reads are expected to sort, and the index earns
its place by narrowing what has to be sorted.

Both indexes, and ``uq_assignments_group_title``, start with
``group_id``, so the ``group_id`` foreign key already has a usable
leftmost prefix and no separate single-column index is created for it --
the same reasoning M14 applied to ``notifications.recipient_id``.

**No MySQL execution plan has been measured for this table.** Unlike
M14's indexes, these two are a reasoned design pending an authorized
real ``EXPLAIN``.

The downgrade is symmetric: both indexes are dropped in the reverse of
their creation order, then the table. Engine, charset, and collation
follow the existing project defaults (InnoDB, ``utf8mb4_0900_ai_ci``).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4f7c1d9b2e30'
down_revision = '023a5f5814a8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('assignments',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('group_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('instructions', sa.Text(), nullable=False),
    sa.Column('opens_at', sa.DateTime(), nullable=False),
    sa.Column('due_at', sa.DateTime(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('published_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('opens_at < due_at', name='ck_assignments_opens_before_due'),
    sa.CheckConstraint("status IN ('draft', 'published')", name='ck_assignments_status_valid'),
    sa.CheckConstraint("(status = 'draft' AND published_at IS NULL) OR (status = 'published' AND published_at IS NOT NULL)", name='ck_assignments_status_published_at_consistency'),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('group_id', 'title', name='uq_assignments_group_title'),
    sa.UniqueConstraint('public_id')
    )
    with op.batch_alter_table('assignments', schema=None) as batch_op:
        batch_op.create_index('ix_assignments_group_due_id', ['group_id', 'due_at', 'id'], unique=False)
        batch_op.create_index('ix_assignments_group_status_opens_due', ['group_id', 'status', 'opens_at', 'due_at'], unique=False)


def downgrade():
    # Dropped in the reverse of the creation order above.
    with op.batch_alter_table('assignments', schema=None) as batch_op:
        batch_op.drop_index('ix_assignments_group_status_opens_due')
        batch_op.drop_index('ix_assignments_group_due_id')

    op.drop_table('assignments')
