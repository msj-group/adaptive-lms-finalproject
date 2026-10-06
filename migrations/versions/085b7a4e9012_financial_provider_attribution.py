"""Preserve actual verified-provider attribution in the financial journal.

Revision ID: 085b7a4e9012
Revises: f74c8e20a315
"""
from alembic import op
import sqlalchemy as sa
revision = "085b7a4e9012"
down_revision = "f74c8e20a315"
branch_labels = depends_on = None


def upgrade():
    op.add_column("financial_revisions", sa.Column("service_principal", sa.String(32, collation="utf8mb4_0900_bin"), nullable=True))
    op.alter_column("financial_revisions", "actor_id", existing_type=sa.BigInteger(), nullable=True)
    op.create_check_constraint("ck_financial_revisions_actor", "financial_revisions",
        "(actor_id IS NOT NULL AND service_principal IS NULL) OR (actor_id IS NULL AND service_principal IS NOT NULL AND service_principal = 'verified_payment_provider')")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM financial_revisions WHERE service_principal IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Cannot discard actual payment-provider attribution")
    op.drop_constraint("ck_financial_revisions_actor", "financial_revisions", type_="check")
    op.alter_column("financial_revisions", "actor_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_column("financial_revisions", "service_principal")
