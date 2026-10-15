"""Teacher-authored Listening playback speeds; existing activities retain all.

Revision ID: 8e5f3b0d7124
Revises: 7d4e2a9c6013
Prepared and separately authorized for execution/release on 2026-10-13.
Actual execution evidence is recorded in the private release report.
"""
from alembic import op
import sqlalchemy as sa

revision = "8e5f3b0d7124"
down_revision = "7d4e2a9c6013"
branch_labels = depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "mysql":
        raise RuntimeError("Listening playback settings require MySQL.")
    op.add_column("listening_activities", sa.Column("playback_speed_mask", sa.SmallInteger(), nullable=False, server_default="15"))
    op.create_check_constraint("ck_listening_playback_speed_mask", "listening_activities", "playback_speed_mask IN (1, 3, 5, 7, 9, 11, 13, 15)")


def downgrade():
    # Downgrade would discard teacher-authored settings. Recovery must use
    # the separately approved backup/release process, not a silent reset.
    raise RuntimeError("Cannot discard authored Listening playback settings automatically.")
