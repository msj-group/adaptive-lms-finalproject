"""add submission_feedback table for teacher feedback on submissions

Revision ID: b26b20c3d20d
Revises: 6b1f0ad74c92
Create Date: 2026-09-08 17:11:06.572119

Additive only (Phase 4 / M03): creates ``submission_feedback`` and
nothing else. No existing table, column, index, constraint or row is
touched -- in particular ``submissions`` and ``users`` are **not**
altered, because the two foreign keys created here live entirely on the
child side and the parents need no schema change. There is no data
backfill and no seeded row: feedback is a Teacher's own act, so
pre-existing Submissions deliberately produce no historical rows.

Each row is the **one** shared Teacher feedback record on one immutable
Submission: a UUID ``public_id`` (the only identifier of this row that
could ever reach a page, and in M03 it appears only inside the signed
stale-form token), a ``submission_id`` FK, a ``reviewer_id`` FK naming
the Teacher who **last changed** the text -- Assignment, Student, Group,
Course, Level and AcademicTerm are all reachable through the Submission
and are deliberately **not** duplicated -- the required ``feedback_text``,
the ``version`` stale-form counter, and the server-generated naive-UTC
``created_at`` / ``updated_at``.

There is deliberately no ``score`` / ``grade`` / ``max_points`` /
``passed`` column, no review ``status`` enum, no publication state, no
``attempt``, and no historical-version table: M03 delivers a plain-text
comment that is immediately visible when the Student's receipt is
authorized, not a grade and not a review workflow. Both foreign keys are
plain references with **no** ``ON DELETE`` behaviour, so no lifecycle
change anywhere in the hierarchy can cascade into feedback -- and there
is no delete endpoint in this milestone at all.

Two named constraints are the final integrity defense:

- ``uq_submission_feedback_submission`` -- exactly one feedback row per
  Submission. This is what makes a replayed form, a double click, or two
  co-teachers racing to write the *first* feedback a safe rejection
  rather than a second row, even if the application check were somehow
  bypassed.
- ``ck_submission_feedback_version_positive`` -- ``version > 0``. A
  plain comparison CHECK, enforced by MySQL 8 and by the SQLite test
  backend alike.

One index is created beyond those constraints:

- ``ix_submission_feedback_reviewer_id`` -- created for the
  ``reviewer_id`` **foreign key**, not for a query shape. Nothing else
  here leads with ``reviewer_id``, and InnoDB requires an index on a
  referencing column; declaring it explicitly keeps the model, this
  revision and the real schema in agreement instead of letting MySQL
  create an auto-named one behind their backs.

No separate index is created for ``submission_id``:
``uq_submission_feedback_submission`` is a single-column unique index on
exactly that column, so the foreign key already has the index InnoDB
requires -- the same reasoning M02 applied to
``submissions.assignment_id`` and M14 to ``notifications.recipient_id``.
That unique index is also the exact shape of every M03 read, all of which
resolve feedback for one already-authorized Submission. No speculative
reporting index is declared: there is no feedback-by-Teacher page, no
review queue and no aggregate counter in this milestone.

**No MySQL execution plan has been measured for this table.** As with
M01's and M02's indexes, this is a reasoned design pending an authorized
real ``EXPLAIN``.

The downgrade is symmetric: the index is dropped, then the table -- and
nothing else. Engine, charset and collation follow the existing project
defaults (InnoDB, ``utf8mb4_0900_ai_ci``).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b26b20c3d20d'
down_revision = '6b1f0ad74c92'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('submission_feedback',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('submission_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('reviewer_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('feedback_text', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('version > 0', name='ck_submission_feedback_version_positive'),
    sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('submission_id', name='uq_submission_feedback_submission')
    )
    with op.batch_alter_table('submission_feedback', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_submission_feedback_reviewer_id'), ['reviewer_id'], unique=False)


def downgrade():
    # Dropped in the reverse of the creation order above.
    # MySQL DROP TABLE releases its own FK-supporting indexes together.
    if op.get_context().dialect.name != 'mysql':
        with op.batch_alter_table('submission_feedback', schema=None) as batch_op:
            batch_op.drop_index(batch_op.f('ix_submission_feedback_reviewer_id'))

    op.drop_table('submission_feedback')
