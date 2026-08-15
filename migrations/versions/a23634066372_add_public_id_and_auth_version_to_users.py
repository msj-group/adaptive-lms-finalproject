"""Add public_id and auth_version to users

Revision ID: a23634066372
Revises: 05f4be28f0ef
Create Date: 2026-08-15 01:08:26.063512

Safely adds two columns to the existing `users` table without recreating
or dropping it, and without breaking the existing Administrator (or any
other existing user):

1. `public_id` is added as NULLABLE first (a NOT NULL add would fail
   immediately against existing rows).
2. `auth_version` is added as NOT NULL with a server-side default of 1,
   which MySQL can apply to existing rows in the same ALTER TABLE.
3. Every existing row is backfilled with a fresh, unique UUID for
   `public_id` (one UPDATE per row, matched by primary key -- no bulk
   value collisions possible).
4. Only after every row has a value is `public_id` tightened to NOT NULL.
5. The uniqueness constraint is added last, once the column is fully
   populated and would not reject the alter for still being nullable.

No row is deleted, no table is recreated, and existing `password_hash`,
`email`, `role`, and `status` values are never touched.
"""
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a23634066372'
down_revision = '05f4be28f0ef'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('public_id', sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column('auth_version', sa.Integer(), nullable=False, server_default='1')
        )

    connection = op.get_bind()
    users_table = sa.table('users', sa.column('id', sa.BigInteger), sa.column('public_id', sa.String))
    existing_ids = connection.execute(sa.select(users_table.c.id)).fetchall()
    for (user_id,) in existing_ids:
        connection.execute(
            users_table.update()
            .where(users_table.c.id == user_id)
            .values(public_id=str(uuid.uuid4()))
        )

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('public_id', existing_type=sa.String(length=36), nullable=False)
        batch_op.create_unique_constraint('uq_users_public_id', ['public_id'])


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('uq_users_public_id', type_='unique')
        batch_op.drop_column('auth_version')
        batch_op.drop_column('public_id')
