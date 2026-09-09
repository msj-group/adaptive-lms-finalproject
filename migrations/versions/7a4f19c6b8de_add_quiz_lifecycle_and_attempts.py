"""add quiz publication lifecycle, attempts, answers, and selections

Revision ID: 7a4f19c6b8de
Revises: 5d2c8a4e91f7
Create Date: 2026-09-09

Additive only (Phase 4 / M04D).

**Existing quizzes are preserved as drafts.** The two new NOT NULL columns
on ``quizzes`` -- ``status`` and ``attempt_limit`` -- are added with
temporary explicit server defaults because MySQL cannot add a NOT NULL
column to a table that already holds rows without one. Those defaults are
dropped immediately afterwards, because the final models carry no server
default: the application always supplies both values explicitly, and
leaving a default behind would let a future insert silently omit them.
Every existing row therefore ends up ``status='draft'`` with
``published_at`` NULL and ``attempt_limit=1``, which is exactly the state
M04A/M04B rows were already in conceptually.

The four availability/lifecycle CHECK constraints are added after the
columns exist, so they are evaluated against rows that already satisfy
them.

Three new tables follow the dependency order
``quizzes -> quiz_attempts -> quiz_answers -> quiz_answer_selections``,
with ``users``, ``quiz_questions`` and ``question_options`` as existing
referents. Foreign keys are plain references with **no** ``ON DELETE``
action, matching the aggregate's history-preserving lifecycle.

No unrelated table is altered and no data is seeded. The downgrade
reverses everything in exact reverse dependency order and returns
``quizzes`` to its M04C shape.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7a4f19c6b8de'
down_revision = '5d2c8a4e91f7'
branch_labels = None
depends_on = None


def upgrade():
    # ---- quizzes: the publication lifecycle -------------------------------
    with op.batch_alter_table('quizzes', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('status', sa.String(length=32), nullable=False,
                      server_default='draft')
        )
        batch_op.add_column(sa.Column('opens_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('closes_at', sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column('time_limit_minutes', sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column('attempt_limit', sa.Integer(), nullable=False,
                      server_default='1')
        )
        batch_op.add_column(sa.Column('published_at', sa.DateTime(), nullable=True))

    # The temporary defaults existed only so MySQL could add the two NOT
    # NULL columns to a populated table. The models declare none, so they
    # are removed rather than left as an invisible second source of truth.
    with op.batch_alter_table('quizzes', schema=None) as batch_op:
        batch_op.alter_column('status', existing_type=sa.String(length=32),
                              existing_nullable=False, server_default=None)
        batch_op.alter_column('attempt_limit', existing_type=sa.Integer(),
                              existing_nullable=False, server_default=None)

    with op.batch_alter_table('quizzes', schema=None) as batch_op:
        batch_op.create_check_constraint(
            'ck_quizzes_status_valid', "status IN ('draft', 'published')"
        )
        batch_op.create_check_constraint(
            'ck_quizzes_availability_window',
            '(opens_at IS NULL AND closes_at IS NULL) '
            'OR (opens_at IS NOT NULL AND closes_at IS NOT NULL AND opens_at < closes_at)',
        )
        batch_op.create_check_constraint(
            'ck_quizzes_time_limit_range',
            'time_limit_minutes IS NULL '
            'OR (time_limit_minutes >= 1 AND time_limit_minutes <= 300)',
        )
        batch_op.create_check_constraint(
            'ck_quizzes_attempt_limit_range',
            'attempt_limit >= 1 AND attempt_limit <= 10',
        )
        batch_op.create_check_constraint(
            'ck_quizzes_status_published_at_consistency',
            "(status = 'draft' AND published_at IS NULL) "
            "OR (status = 'published' AND published_at IS NOT NULL)",
        )
        batch_op.create_index(
            'ix_quizzes_group_status_opens_id',
            ['group_id', 'status', 'opens_at', 'id'],
            unique=False,
        )

    # ---- quiz_attempts ----------------------------------------------------
    op.create_table('quiz_attempts',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('quiz_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('student_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('attempt_number', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('quiz_version', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('deadline_at', sa.DateTime(), nullable=False),
    sa.Column('submitted_at', sa.DateTime(), nullable=True),
    sa.Column('correct_count', sa.Integer(), nullable=True),
    sa.Column('total_questions', sa.Integer(), nullable=True),
    sa.CheckConstraint('attempt_number >= 1', name='ck_quiz_attempts_number_positive'),
    sa.CheckConstraint('correct_count IS NULL OR (correct_count >= 0 AND total_questions >= 0 AND correct_count <= total_questions)', name='ck_quiz_attempts_counts_range'),
    sa.CheckConstraint('quiz_version > 0', name='ck_quiz_attempts_quiz_version_positive'),
    sa.CheckConstraint("status IN ('in_progress', 'submitted', 'expired')", name='ck_quiz_attempts_status_valid'),
    sa.CheckConstraint("(status = 'in_progress' AND submitted_at IS NULL AND correct_count IS NULL AND total_questions IS NULL) OR (status = 'submitted' AND submitted_at IS NOT NULL AND correct_count IS NOT NULL AND total_questions IS NOT NULL) OR (status = 'expired' AND submitted_at IS NULL AND correct_count IS NOT NULL AND total_questions IS NOT NULL)", name='ck_quiz_attempts_status_finalization_consistency'),
    sa.ForeignKeyConstraint(['quiz_id'], ['quizzes.id'], ),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('quiz_id', 'student_id', 'attempt_number', name='uq_quiz_attempts_quiz_student_number')
    )
    with op.batch_alter_table('quiz_attempts', schema=None) as batch_op:
        batch_op.create_index('ix_quiz_attempts_quiz_started_id', ['quiz_id', 'started_at', 'id'], unique=False)
        batch_op.create_index(batch_op.f('ix_quiz_attempts_student_id'), ['student_id'], unique=False)

    # ---- quiz_answers -----------------------------------------------------
    op.create_table('quiz_answers',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('attempt_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('question_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['attempt_id'], ['quiz_attempts.id'], ),
    sa.ForeignKeyConstraint(['question_id'], ['quiz_questions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('attempt_id', 'question_id', name='uq_quiz_answers_attempt_question'),
    sa.UniqueConstraint('public_id')
    )
    with op.batch_alter_table('quiz_answers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_quiz_answers_question_id'), ['question_id'], unique=False)

    # ---- quiz_answer_selections ------------------------------------------
    op.create_table('quiz_answer_selections',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('answer_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('option_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['answer_id'], ['quiz_answers.id'], ),
    sa.ForeignKeyConstraint(['option_id'], ['question_options.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('answer_id', 'option_id', name='uq_quiz_answer_selections_answer_option'),
    sa.UniqueConstraint('public_id')
    )
    with op.batch_alter_table('quiz_answer_selections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_quiz_answer_selections_option_id'), ['option_id'], unique=False)


def downgrade():
    with op.batch_alter_table('quiz_answer_selections', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_quiz_answer_selections_option_id'))
    op.drop_table('quiz_answer_selections')

    with op.batch_alter_table('quiz_answers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_quiz_answers_question_id'))
    op.drop_table('quiz_answers')

    with op.batch_alter_table('quiz_attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_quiz_attempts_student_id'))
        batch_op.drop_index('ix_quiz_attempts_quiz_started_id')
    op.drop_table('quiz_attempts')

    with op.batch_alter_table('quizzes', schema=None) as batch_op:
        batch_op.drop_index('ix_quizzes_group_status_opens_id')
        batch_op.drop_constraint('ck_quizzes_status_published_at_consistency', type_='check')
        batch_op.drop_constraint('ck_quizzes_attempt_limit_range', type_='check')
        batch_op.drop_constraint('ck_quizzes_time_limit_range', type_='check')
        batch_op.drop_constraint('ck_quizzes_availability_window', type_='check')
        batch_op.drop_constraint('ck_quizzes_status_valid', type_='check')

    with op.batch_alter_table('quizzes', schema=None) as batch_op:
        batch_op.drop_column('published_at')
        batch_op.drop_column('attempt_limit')
        batch_op.drop_column('time_limit_minutes')
        batch_op.drop_column('closes_at')
        batch_op.drop_column('opens_at')
        batch_op.drop_column('status')
