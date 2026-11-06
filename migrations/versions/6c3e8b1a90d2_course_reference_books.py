"""Private teacher books and workbooks for a course.

Revision ID: 6c3e8b1a90d2
Revises: f6b3a18d920e
"""
from alembic import op
import sqlalchemy as sa

revision = "6c3e8b1a90d2"
down_revision = "f6b3a18d920e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "course_reference_books",
        sa.Column("course_id", sa.BigInteger(), sa.ForeignKey("courses.id"), primary_key=True),
        sa.Column("kind", sa.String(20, collation="utf8mb4_0900_bin"), primary_key=True),
        sa.Column("uploaded_file_id", sa.BigInteger(), sa.ForeignKey("uploaded_files.id"), nullable=False, unique=True),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('teacher_book', 'workbook')", name="ck_course_reference_books_kind"),
        sa.CheckConstraint("page_count BETWEEN 1 AND 1000", name="ck_course_reference_books_pages"),
        sa.CheckConstraint("revision >= 1", name="ck_course_reference_books_revision"),
        mysql_engine="InnoDB",
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT course_id FROM course_reference_books LIMIT 1")).first():
        raise RuntimeError("Downgrade would remove teaching book references. Preserve them before reverting.")
    op.drop_table("course_reference_books")
