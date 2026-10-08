"""Add sender display changes and member conversation clears.

Revision ID: 6b3a8c2d9041
Revises: 085b7a4e9012

Owner authorized this additive local MySQL upgrade on 2026-10-07.
Original messages, threads, memberships and all other business rows are retained.
No backfill, seed, cascade, or alteration of an existing table is performed.
"""
from alembic import op
import sqlalchemy as sa

revision = "6b3a8c2d9041"
down_revision = "085b7a4e9012"
branch_labels = depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "mysql":
        raise RuntimeError("Message display history requires the approved MySQL database.")
    op.create_table(
        "message_changes",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(8, collation="utf8mb4_0900_bin"), nullable=False),
        sa.Column("body", sa.String(5000), nullable=True),
        sa.Column("creation_nonce", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("(kind = 'edit' AND body IS NOT NULL AND LENGTH(TRIM(body)) > 0) OR (kind = 'hide' AND body IS NULL)", name="ck_message_changes_payload"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("creation_nonce"),
        mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("ix_message_changes_message_id", "message_changes", ["message_id", "id"])
    op.create_table(
        "message_thread_clears",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("through_message_id", sa.BigInteger(), nullable=False),
        sa.Column("creation_nonce", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["message_thread_members.id"]),
        sa.ForeignKeyConstraint(["through_message_id"], ["messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("creation_nonce"),
        mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("ix_message_thread_clears_member_id", "message_thread_clears", ["member_id", "id"])
    op.create_index("ix_message_thread_clears_message", "message_thread_clears", ["through_message_id"])


def downgrade():
    connection = op.get_bind()
    for table in ("message_changes", "message_thread_clears"):
        if connection.execute(sa.text("SELECT 1 FROM " + table + " LIMIT 1")).first():
            raise RuntimeError("Cannot discard message display history.")
    op.drop_table("message_thread_clears")
    op.drop_table("message_changes")
