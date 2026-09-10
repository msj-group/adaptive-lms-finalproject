"""add speaking_activities, speaking_submissions and speaking_feedback

Revision ID: 4e2c9b7f1a83
Revises: 3f81b0c7d942
Create Date: 2026-09-10

Additive only (Phase 4 / M06). Exactly three new tables.

**Every existing Assignment stays an ordinary Assignment, by
construction.** An Assignment is a Speaking activity precisely when a
``speaking_activities`` row points at it, so a migration that creates an
empty table classifies nothing: there is no ``kind`` column to backfill,
no default to choose and no row to seed. Nothing in ``assignments``,
``submissions``, ``submission_feedback``, ``quizzes``,
``listening_activities``, ``materials``, ``uploaded_files`` or
``file_access_logs`` is altered, rewritten or read.

``assignments``, ``users`` and ``uploaded_files`` appear only as existing
foreign-key targets. **Every** reference is plain, with no ``ON DELETE``
and no ``ON UPDATE`` action, matching the history-preserving lifecycle of
the whole project: no Assignment, Group, account or file lifecycle change
may remove a Speaking activity, a Student's recording or a Teacher's
feedback, and nothing is ever hard-deleted.

What each uniqueness rule is for:

- ``speaking_activities.assignment_id`` UNIQUE -- one extension per
  Assignment, so "is this a Speaking activity?" has exactly one answer.
  That unique index also gives the foreign key a usable index, so no
  separate single-column index is declared for it.
- ``speaking_activities.creation_nonce`` UNIQUE -- the final defense
  behind the duplicate-request protection, so a concurrent replay of one
  create request loses at the database instead of producing a second
  Assignment and extension row.
- ``uq_speaking_submissions_activity_student`` -- exactly one final
  recording per activity and Student. It is also the Student receipt
  lookup's own shape and the leftmost prefix the
  ``speaking_activity_id`` foreign key needs.
- ``speaking_submissions.audio_file_id`` UNIQUE -- one physical recording
  backs exactly one submission, mirroring ``materials.uploaded_file_id``
  and ``listening_activities.audio_file_id``.
- ``speaking_submissions.creation_nonce`` UNIQUE -- the same
  duplicate-request defense for the submit request, so a double-pressed
  button or a replayed POST can never store a second recording.
- ``uq_speaking_feedback_submission`` -- one shared feedback record per
  recording, latest text only.

The two explicit single-column indexes
(``ix_speaking_submissions_student_id``,
``ix_speaking_feedback_reviewer_id``) exist for their **foreign keys**,
not for a query shape: nothing else leads with those columns and InnoDB
requires an index on a referencing column, so declaring them keeps the
models, this migration and the real schema in agreement instead of
letting MySQL create auto-named ones behind their backs.
``ix_speaking_submissions_activity_submitted_id`` is the Teacher
submission list's read shape (equality on the activity, then the two
ordering columns).

``ck_speaking_feedback_version_positive`` is a plain comparison CHECK,
supported by MySQL 8 and by the SQLite test backend alike.

The ``audio`` category of a referenced upload is a cross-table condition
a CHECK cannot express; it is enforced in the application against the
locked rows and re-proved on every audio request, and that is stated in
the models rather than hidden here. The same is true of
``reviewer_id`` naming a Teacher and ``student_id`` naming a Student: a
foreign key proves a row exists, never its role.

The downgrade drops the three tables it created, in reverse dependency
order, and nothing else.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4e2c9b7f1a83'
down_revision = '3f81b0c7d942'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('speaking_activities',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('assignment_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('creation_nonce', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['assignment_id'], ['assignments.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('assignment_id'),
    sa.UniqueConstraint('creation_nonce'),
    sa.UniqueConstraint('public_id')
    )
    op.create_table('speaking_submissions',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('speaking_activity_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('student_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('audio_file_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('creation_nonce', sa.String(length=64), nullable=False),
    sa.Column('submitted_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['audio_file_id'], ['uploaded_files.id'], ),
    sa.ForeignKeyConstraint(['speaking_activity_id'], ['speaking_activities.id'], ),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('audio_file_id'),
    sa.UniqueConstraint('creation_nonce'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('speaking_activity_id', 'student_id', name='uq_speaking_submissions_activity_student')
    )
    op.create_index(
        'ix_speaking_submissions_activity_submitted_id',
        'speaking_submissions',
        ['speaking_activity_id', 'submitted_at', 'id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_speaking_submissions_student_id'),
        'speaking_submissions',
        ['student_id'],
        unique=False,
    )
    op.create_table('speaking_feedback',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('speaking_submission_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('reviewer_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('feedback_text', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('version > 0', name='ck_speaking_feedback_version_positive'),
    sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['speaking_submission_id'], ['speaking_submissions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('speaking_submission_id', name='uq_speaking_feedback_submission')
    )
    op.create_index(
        op.f('ix_speaking_feedback_reviewer_id'),
        'speaking_feedback',
        ['reviewer_id'],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f('ix_speaking_feedback_reviewer_id'), table_name='speaking_feedback')
    op.drop_table('speaking_feedback')
    op.drop_index(op.f('ix_speaking_submissions_student_id'), table_name='speaking_submissions')
    op.drop_index(
        'ix_speaking_submissions_activity_submitted_id', table_name='speaking_submissions'
    )
    op.drop_table('speaking_submissions')
    op.drop_table('speaking_activities')
