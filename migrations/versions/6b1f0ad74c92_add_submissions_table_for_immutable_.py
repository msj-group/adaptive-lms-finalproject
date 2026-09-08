"""add submissions table for immutable text Assignment submissions

Revision ID: 6b1f0ad74c92
Revises: 4f7c1d9b2e30
Create Date: 2026-09-07 11:18:44.203915

Additive only (Phase 4 / M02): creates ``submissions`` and nothing else.
No existing table, column, index, constraint, or row is touched -- in
particular ``assignments`` and ``users`` are **not** altered, because the
two foreign keys created here live entirely on the child side and the
parents need no schema change. There is no data backfill and no seeded
row: a Submission is a Student's own act, so pre-existing Assignments and
Enrollments deliberately produce no historical rows.

Each row is one Student's single, final, plain-text answer to one
Assignment: a UUID ``public_id`` (the only identifier that ever reaches a
URL or the rendered HTML), an ``assignment_id`` FK and a ``student_id``
FK -- Group, Course, Level, AcademicTerm, Teacher and Enrollment are all
reachable through those two and are deliberately **not** duplicated --
the required ``answer_text``, and the server-generated naive-UTC
``submitted_at``.

There is deliberately no ``status`` / ``attempt`` / ``version`` /
``grade`` / late-policy column: row existence means "Submitted" and its
absence means "Not submitted". Both foreign keys are plain references
with **no** ``ON DELETE`` behaviour, so no lifecycle change anywhere in
the hierarchy can cascade into submission history.

One named constraint is the final integrity defense:

- ``uq_submissions_assignment_student`` -- one submission per
  (Assignment, Student) pair. This is what makes a double click, a
  replay, or a changed duplicate payload a safe no-op rather than a
  second row, even if the application check were somehow bypassed.

Two indexes are created beyond that constraint:

- ``ix_submissions_assignment_submitted_id`` (``assignment_id``,
  ``submitted_at``, ``id``) -- the Teacher submission list: a
  single-Assignment equality on ``assignment_id`` followed directly by
  the two ordering columns (``submitted_at DESC, id DESC``). The
  ordering columns follow the equality column with nothing between them,
  the shape M14 established on ``notifications`` and M01 on
  ``assignments``.
- ``ix_submissions_student_id`` -- created for the ``student_id``
  **foreign key**, not for a query shape. Neither
  ``uq_submissions_assignment_student`` nor the composite index leads
  with ``student_id``, and InnoDB requires an index on a referencing
  column; declaring it explicitly keeps the model, this revision and the
  real schema in agreement instead of letting MySQL create an auto-named
  one behind their backs.

No separate single-column index is created for ``assignment_id``: both
``uq_submissions_assignment_student`` and
``ix_submissions_assignment_submitted_id`` already start with it, so the
foreign key has a usable leftmost prefix -- the same reasoning M14
applied to ``notifications.recipient_id`` and M01 to
``assignments.group_id``.

The Student receipt lookup constrains **both** ``assignment_id`` and
``student_id``, which is exactly ``uq_submissions_assignment_student``;
it needs no index of its own.

**No MySQL execution plan has been measured for this table.** As with
M01's ``assignments`` indexes, these are a reasoned design pending an
authorized real ``EXPLAIN``.

The downgrade is symmetric: both indexes are dropped in the reverse of
their creation order, then the table -- and nothing else. Engine,
charset, and collation follow the existing project defaults (InnoDB,
``utf8mb4_0900_ai_ci``).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6b1f0ad74c92'
down_revision = '4f7c1d9b2e30'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('submissions',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('assignment_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('student_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('answer_text', sa.Text(), nullable=False),
    sa.Column('submitted_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['assignment_id'], ['assignments.id'], ),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('assignment_id', 'student_id', name='uq_submissions_assignment_student'),
    sa.UniqueConstraint('public_id')
    )
    with op.batch_alter_table('submissions', schema=None) as batch_op:
        batch_op.create_index('ix_submissions_assignment_submitted_id', ['assignment_id', 'submitted_at', 'id'], unique=False)
        batch_op.create_index(batch_op.f('ix_submissions_student_id'), ['student_id'], unique=False)


def downgrade():
    # Dropped in the reverse of the creation order above.
    with op.batch_alter_table('submissions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_submissions_student_id'))
        batch_op.drop_index('ix_submissions_assignment_submitted_id')

    op.drop_table('submissions')
