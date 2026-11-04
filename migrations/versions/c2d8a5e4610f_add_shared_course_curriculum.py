"""Allow one centrally administered curriculum per Course.

Revision ID: c2d8a5e4610f
Revises: a6f2c9d84e71
"""
import sqlalchemy as sa
from alembic import op


revision = "c2d8a5e4610f"
down_revision = "a6f2c9d84e71"
branch_labels = None
depends_on = None


def upgrade():
    # MySQL commits DDL implicitly. Keep the ownership change atomic.
    op.execute("""
        ALTER TABLE units
          ADD COLUMN course_id BIGINT NULL,
          MODIFY COLUMN group_id BIGINT NULL,
          ADD CONSTRAINT uq_units_course_title UNIQUE (course_id, title),
          ADD CONSTRAINT fk_units_course FOREIGN KEY (course_id) REFERENCES courses (id),
          ADD CONSTRAINT ck_units_exactly_one_owner
            CHECK ((group_id IS NULL) <> (course_id IS NULL))
    """)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT id FROM units WHERE course_id IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Downgrading would discard ownership of shared curriculum units.")
    op.execute("""
        ALTER TABLE units
          DROP CHECK ck_units_exactly_one_owner,
          DROP FOREIGN KEY fk_units_course,
          DROP INDEX uq_units_course_title,
          MODIFY COLUMN group_id BIGINT NOT NULL,
          DROP COLUMN course_id
    """)
