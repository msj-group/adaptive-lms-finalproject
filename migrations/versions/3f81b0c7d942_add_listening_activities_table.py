"""add listening_activities, the one-to-one Listening extension of a Quiz

Revision ID: 3f81b0c7d942
Revises: 7a4f19c6b8de
Create Date: 2026-09-09

Additive only (Phase 4 / M05). Exactly one new table.

**Every existing Quiz stays an ordinary Quiz, by construction.** A Quiz is
a Listening activity precisely when a ``listening_activities`` row points
at it, so a migration that creates an empty table classifies nothing:
there is no ``kind`` column to backfill, no default to choose, and no row
to seed. Nothing in ``quizzes``, ``quiz_questions``, ``question_options``,
``quiz_attempts``, ``quiz_answers``, ``quiz_answer_selections``,
``materials``, ``uploaded_files`` or ``file_access_logs`` is altered,
rewritten or read.

``quizzes`` and ``uploaded_files`` appear only as existing foreign-key
targets. Both references are plain, with **no** ``ON DELETE`` action,
matching the aggregate's history-preserving lifecycle: no Quiz, Group or
file lifecycle change may remove a Listening activity, and nothing is ever
hard-deleted.

``quiz_id`` and ``audio_file_id`` are each UNIQUE, which is the schema's
own statement of the two one-to-one rules -- one extension per Quiz, one
recording per activity. Those unique indexes also give both foreign keys a
usable index, so no separate single-column index is declared for either.
``creation_nonce`` is UNIQUE for a third reason: it is the final defense
behind the duplicate-request protection, so a concurrent replay of one
create request loses at the database instead of producing a second Quiz,
extension row, upload, access-log entry and physical file.

The ``audio`` category of the referenced upload is a cross-table condition
a CHECK cannot express; it is enforced in the application against the
locked rows, and that is stated in the model rather than hidden here.

The downgrade drops the one table it created and nothing else.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3f81b0c7d942'
down_revision = '7a4f19c6b8de'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('listening_activities',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('quiz_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('audio_file_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('transcript', sa.Text(), nullable=False),
    sa.Column('transcript_visibility', sa.String(length=32), nullable=False),
    sa.Column('vocabulary_notes', sa.Text(), nullable=False),
    sa.Column('creation_nonce', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("transcript_visibility IN ('hidden', 'after_submission', 'always')", name='ck_listening_activities_transcript_visibility_valid'),
    sa.ForeignKeyConstraint(['audio_file_id'], ['uploaded_files.id'], ),
    sa.ForeignKeyConstraint(['quiz_id'], ['quizzes.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('audio_file_id'),
    sa.UniqueConstraint('creation_nonce'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('quiz_id')
    )


def downgrade():
    op.drop_table('listening_activities')
