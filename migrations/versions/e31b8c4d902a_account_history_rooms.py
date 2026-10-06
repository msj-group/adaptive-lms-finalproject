"""Account revisions, optimistic versions and scheduling resources.

Revision ID: e31b8c4d902a
Revises: c1a7e4d9b203
Source only until final verification and separately scoped database execution.
"""
from alembic import op
import sqlalchemy as sa

revision = "e31b8c4d902a"
down_revision = "c1a7e4d9b203"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint("ck_users_version_positive", "users", "version > 0")
    op.alter_column("users", "version", existing_type=sa.Integer(), server_default=None)
    op.create_table("rooms",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("public_id", sa.String(36), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32, collation="utf8mb4_0900_bin"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("capacity > 0", name="ck_rooms_capacity"),
        sa.CheckConstraint("version > 0", name="ck_rooms_version"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_rooms_status"))
    op.add_column("schedules", sa.Column("room_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_schedules_room_id", "schedules", "rooms", ["room_id"], ["id"])
    op.create_index("ix_schedules_room_id", "schedules", ["room_id"])
    op.create_table("account_revisions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("public_id", sa.String(36), nullable=False, unique=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=False),
        sa.Column("after_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "version", name="uq_account_revisions_user_version"),
        sa.CheckConstraint("version > 1", name="ck_account_revisions_version"))
    op.create_index("ix_account_revisions_user_created", "account_revisions", ["user_id", "created_at", "id"])
    op.create_table("scheduling_revisions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("room_id", sa.BigInteger(), sa.ForeignKey("rooms.id"), nullable=True),
        sa.Column("schedule_id", sa.BigInteger(), sa.ForeignKey("schedules.id"), nullable=True),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=True),
        sa.Column("after_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("(room_id IS NULL) <> (schedule_id IS NULL)", name="ck_scheduling_revisions_one_target"))
    op.create_index("ix_scheduling_revisions_schedule", "scheduling_revisions", ["schedule_id", "id"])
    op.create_index("ix_scheduling_revisions_room", "scheduling_revisions", ["room_id", "id"])


def downgrade():
    connection = op.get_bind()
    for table in ("account_revisions", "scheduling_revisions", "rooms"):
        if connection.execute(sa.text("SELECT 1 FROM `" + table + "` LIMIT 1")).first():
            raise RuntimeError("Downgrade refused: preserve account and scheduling history.")
    op.drop_table("scheduling_revisions")
    op.drop_table("account_revisions")
    op.drop_constraint("fk_schedules_room_id", "schedules", type_="foreignkey")
    op.drop_index("ix_schedules_room_id", table_name="schedules")
    op.drop_column("schedules", "room_id")
    op.drop_table("rooms")
    op.drop_constraint("ck_users_version_positive", "users", type_="check")
    op.drop_column("users", "version")
