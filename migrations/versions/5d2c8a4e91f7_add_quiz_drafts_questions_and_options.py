"""add quiz drafts, questions, and answer options

Revision ID: 5d2c8a4e91f7
Revises: b26b20c3d20d
Create Date: 2026-09-09

Additive only (Phase 4 / M04C): creates the three tables implemented and
accepted in M04A/M04B. Existing tables, columns, constraints, indexes, and
rows are untouched. There is no backfill: quizzes and their authored content
are created by Teachers after this schema becomes available.

The dependency order is ``groups -> quizzes -> quiz_questions ->
question_options``. Foreign keys are plain references with no ``ON DELETE``
action, matching the models' history-preserving lifecycle. The downgrade
drops indexes and tables in the exact reverse dependency order.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5d2c8a4e91f7'
down_revision = 'b26b20c3d20d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('quizzes',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('group_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('instructions', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('version > 0', name='ck_quizzes_version_positive'),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('group_id', 'title', name='uq_quizzes_group_title'),
    sa.UniqueConstraint('public_id')
    )
    with op.batch_alter_table('quizzes', schema=None) as batch_op:
        batch_op.create_index('ix_quizzes_group_created_id', ['group_id', 'created_at', 'id'], unique=False)

    op.create_table('quiz_questions',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('quiz_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('answer_mode', sa.String(length=32), nullable=False),
    sa.Column('display_order', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("answer_mode IN ('single', 'multiple')", name='ck_quiz_questions_answer_mode_valid'),
    sa.CheckConstraint('display_order >= 0', name='ck_quiz_questions_display_order_non_negative'),
    sa.CheckConstraint('version > 0', name='ck_quiz_questions_version_positive'),
    sa.ForeignKeyConstraint(['quiz_id'], ['quizzes.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    with op.batch_alter_table('quiz_questions', schema=None) as batch_op:
        batch_op.create_index('ix_quiz_questions_quiz_order_id', ['quiz_id', 'display_order', 'id'], unique=False)

    op.create_table('question_options',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('question_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('option_text', sa.Text(), nullable=False),
    sa.Column('display_order', sa.Integer(), nullable=False),
    sa.Column('is_correct', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('retired_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('(is_active = 1 AND retired_at IS NULL) OR (is_active = 0 AND retired_at IS NOT NULL)', name='ck_question_options_active_retired_consistency'),
    sa.CheckConstraint('display_order >= 0', name='ck_question_options_display_order_non_negative'),
    sa.ForeignKeyConstraint(['question_id'], ['quiz_questions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    with op.batch_alter_table('question_options', schema=None) as batch_op:
        batch_op.create_index('ix_question_options_question_active_order_id', ['question_id', 'is_active', 'display_order', 'id'], unique=False)


def downgrade():
    # Keep dependency order; MySQL releases each table's own indexes/FKs.
    if op.get_context().dialect.name != 'mysql':
        with op.batch_alter_table('question_options', schema=None) as batch_op:
            batch_op.drop_index('ix_question_options_question_active_order_id')
    op.drop_table('question_options')

    if op.get_context().dialect.name != 'mysql':
        with op.batch_alter_table('quiz_questions', schema=None) as batch_op:
            batch_op.drop_index('ix_quiz_questions_quiz_order_id')
    op.drop_table('quiz_questions')

    if op.get_context().dialect.name != 'mysql':
        with op.batch_alter_table('quizzes', schema=None) as batch_op:
            batch_op.drop_index('ix_quizzes_group_created_id')
    op.drop_table('quizzes')
