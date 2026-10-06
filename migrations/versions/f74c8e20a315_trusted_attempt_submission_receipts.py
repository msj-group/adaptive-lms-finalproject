"""Trusted timed-submission receipt evidence.

Revision ID: f74c8e20a315
Revises: d68e4a1b720f
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "f74c8e20a315"
down_revision = "d68e4a1b720f"
branch_labels = depends_on = None


def upgrade():
    op.create_table("attempt_submission_receipts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("attempt_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("received_at", DATETIME(fsp=6), nullable=False),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("previous_status", sa.String(32, collation="utf8mb4_0900_bin"), nullable=False),
        sa.Column("token_digest", sa.String(64, collation="utf8mb4_0900_bin"), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["quiz_attempts.id"]),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.UniqueConstraint("attempt_id"),
        sa.CheckConstraint("received_at < deadline_at", name="ck_attempt_receipt_timely"),
        sa.CheckConstraint("previous_status IN ('in_progress', 'expired')", name="ck_attempt_receipt_status"),
        sa.CheckConstraint("LENGTH(token_digest) = 64", name="ck_attempt_receipt_digest"),
        mysql_engine="InnoDB", mysql_charset="utf8mb4")
    op.create_index("ix_attempt_submission_receipts_actor_id", "attempt_submission_receipts", ["actor_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM attempt_submission_receipts LIMIT 1")).first():
        raise RuntimeError("Cannot discard submission receipt evidence")
    op.drop_table("attempt_submission_receipts")
