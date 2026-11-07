"""Add opt-in device notification subscriptions.

Revision ID: c5d7e8f901ab
Revises: a4b9d0c21e76
"""
from alembic import op
import sqlalchemy as sa

revision = "c5d7e8f901ab"
down_revision = "a4b9d0c21e76"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("device_push_subscriptions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("public_id", sa.String(36), nullable=False, unique=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("endpoint_hash", sa.String(64, collation="ascii_bin"), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(100, collation="ascii_bin"), nullable=False),
        sa.Column("auth_key", sa.String(32, collation="ascii_bin"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_notification_id", sa.BigInteger(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime()), sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("endpoint_hash", name="uq_device_push_endpoint"),
        sa.CheckConstraint("auth_version >= 1 AND version >= 1", name="ck_device_push_versions"),
        sa.CheckConstraint("last_notification_id >= 0 AND failures >= 0", name="ck_device_push_cursor"),
        mysql_engine="InnoDB")
    op.create_index("ix_device_push_delivery", "device_push_subscriptions", ["enabled", "retry_at", "id"])
    op.create_index("ix_device_push_user", "device_push_subscriptions", ["user_id", "enabled"])


def downgrade():
    count = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM device_push_subscriptions")).scalar_one()
    if count:
        raise RuntimeError("Refusing to remove existing device subscriptions; use a source rollback.")
    op.drop_table("device_push_subscriptions")
