"""Map Student Workbook exercise pages to native Lesson releases.

Revision ID: a4b9d0c21e76
Revises: 8e74c029ab61
"""
from alembic import op
import sqlalchemy as sa

revision = "a4b9d0c21e76"
down_revision = "8e74c029ab61"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("workbook_lesson_pages",
        sa.Column("lesson_id", sa.BigInteger(), sa.ForeignKey("lessons.id"), primary_key=True),
        sa.Column("page_number", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.BigInteger(), nullable=False),
        sa.Column("book_kind", sa.String(20, collation="utf8mb4_0900_bin"), nullable=False),
        sa.Column("book_revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["course_id", "book_kind"],
            ["course_reference_books.course_id", "course_reference_books.kind"], name="fk_workbook_page_course_book"),
        sa.CheckConstraint("book_kind = 'workbook'", name="ck_workbook_page_kind"),
        sa.CheckConstraint("book_revision >= 1", name="ck_workbook_page_revision"),
        sa.CheckConstraint("page_number BETWEEN 1 AND 1000", name="ck_workbook_page_number"),
        mysql_engine="InnoDB")
    op.create_index("ix_workbook_pages_course_revision_page", "workbook_lesson_pages",
                    ["course_id", "book_revision", "page_number"])


def downgrade():
    # A downgrade destroys authored mappings; operators must explicitly
    # authorize it. Source rollback does not require dropping this table.
    op.drop_table("workbook_lesson_pages")
