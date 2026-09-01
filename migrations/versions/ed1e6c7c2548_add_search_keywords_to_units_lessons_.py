"""add search_keywords to units, lessons, materials

Revision ID: ed1e6c7c2548
Revises: 8319232a5609
Create Date: 2026-09-01 23:01:18.836287

Additive only (M13): adds a single nullable ``search_keywords
VARCHAR(500)`` column to ``units``, ``lessons``, and ``materials`` -- an
optional Teacher-authored, canonical comma-and-space separated keyword
string used only to widen the Student learning-content search. No column
is added to ``courses``. Existing rows remain valid with ``NULL``; there
is no content backfill and no destructive transformation. No other table,
constraint, or index is touched.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ed1e6c7c2548'
down_revision = '8319232a5609'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('units', schema=None) as batch_op:
        batch_op.add_column(sa.Column('search_keywords', sa.String(length=500), nullable=True))

    with op.batch_alter_table('lessons', schema=None) as batch_op:
        batch_op.add_column(sa.Column('search_keywords', sa.String(length=500), nullable=True))

    with op.batch_alter_table('materials', schema=None) as batch_op:
        batch_op.add_column(sa.Column('search_keywords', sa.String(length=500), nullable=True))


def downgrade():
    with op.batch_alter_table('materials', schema=None) as batch_op:
        batch_op.drop_column('search_keywords')

    with op.batch_alter_table('lessons', schema=None) as batch_op:
        batch_op.drop_column('search_keywords')

    with op.batch_alter_table('units', schema=None) as batch_op:
        batch_op.drop_column('search_keywords')
