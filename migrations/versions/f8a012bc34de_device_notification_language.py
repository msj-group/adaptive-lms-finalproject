"""Remember each device's platform language for background Web Push.

Revision ID: f8a012bc34de
Revises: e7f9012abc34
"""
from alembic import op
import sqlalchemy as sa

revision = 'f8a012bc34de'
down_revision = 'e7f9012abc34'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("ALTER TABLE device_push_subscriptions "
        "ADD COLUMN language VARCHAR(2) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'en', "
        "ADD CONSTRAINT ck_device_push_language CHECK (language IN ('en','ar'))"))


def downgrade():
    raise RuntimeError('Preserve per-device language preferences; use a reviewed source rollback.')
