"""Add private curriculum documents, page ranges and positioned media.

Revision ID: 8a21f7d9c460
Revises: d49ac73eb621
"""
from alembic import op
import sqlalchemy as sa


revision = "8a21f7d9c460"
down_revision = "d49ac73eb621"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "course_documents",
        sa.Column("course_id", sa.BigInteger(), sa.ForeignKey("courses.id"), primary_key=True),
        sa.Column("uploaded_file_id", sa.BigInteger(), sa.ForeignKey("uploaded_files.id"),
                  nullable=False, unique=True),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("page_count BETWEEN 1 AND 1000", name="ck_course_documents_pages"),
        sa.CheckConstraint("revision >= 1", name="ck_course_documents_revision"),
        mysql_engine="InnoDB",
    )
    for table in ("units", "lessons"):
        op.execute(f"""
            ALTER TABLE {table}
              ADD COLUMN page_start INT NULL,
              ADD COLUMN page_end INT NULL,
              ADD CONSTRAINT ck_{table}_page_range CHECK (
                (page_start IS NULL AND page_end IS NULL) OR
                (page_start IS NOT NULL AND page_end IS NOT NULL
                 AND page_start >= 1 AND page_end >= page_start)
              )
        """)
    op.execute("""
        ALTER TABLE materials
          ADD COLUMN pin_page INT NULL,
          ADD COLUMN pin_x INT NULL,
          ADD COLUMN pin_y INT NULL,
          ADD COLUMN pin_show_name BOOL NOT NULL DEFAULT FALSE,
          ADD CONSTRAINT ck_materials_page_pin CHECK (
            (pin_page IS NULL AND pin_x IS NULL AND pin_y IS NULL AND pin_show_name = FALSE) OR
            (kind = 'file' AND pin_page IS NOT NULL AND pin_page >= 1
             AND pin_x IS NOT NULL AND pin_x BETWEEN 0 AND 10000
             AND pin_y IS NOT NULL AND pin_y BETWEEN 0 AND 10000)
          )
    """)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT course_id FROM course_documents LIMIT 1")).first():
        raise RuntimeError("Downgrade would discard saved curriculum divisions and media positions.")
    op.execute("""
        ALTER TABLE materials DROP CHECK ck_materials_page_pin,
          DROP COLUMN pin_page, DROP COLUMN pin_x, DROP COLUMN pin_y, DROP COLUMN pin_show_name
    """)
    for table in ("lessons", "units"):
        op.execute(f"""
            ALTER TABLE {table} DROP CHECK ck_{table}_page_range,
              DROP COLUMN page_start, DROP COLUMN page_end
        """)
    op.drop_table("course_documents")
