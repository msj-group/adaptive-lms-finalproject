"""Private course resources and explicit Group-copy material origins.

Revision ID: 8e74c029ab61
Revises: 6c3e8b1a90d2
"""
from alembic import op
import sqlalchemy as sa

revision = "8e74c029ab61"
down_revision = "6c3e8b1a90d2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("group_material_sources",
        sa.Column("material_id", sa.BigInteger(), sa.ForeignKey("materials.id"), primary_key=True),
        sa.Column("course_material_id", sa.BigInteger(), sa.ForeignKey("materials.id"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False), mysql_engine="InnoDB")
    op.create_table("course_content_imports",
        sa.Column("course_id", sa.BigInteger(), sa.ForeignKey("courses.id"), primary_key=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False), mysql_engine="InnoDB")
    op.create_table("teaching_assets",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("public_id", sa.String(36), nullable=False, unique=True),
        sa.Column("course_id", sa.BigInteger(), sa.ForeignKey("courses.id"), nullable=False, index=True),
        sa.Column("uploaded_file_id", sa.BigInteger(), sa.ForeignKey("uploaded_files.id"), nullable=False, unique=True),
        sa.Column("title", sa.String(150), nullable=False), sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("page_count", sa.Integer()), sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("id", "course_id", name="uq_teaching_assets_id_course"),
        sa.CheckConstraint("display_order >= 1", name="ck_teaching_assets_order"),
        sa.CheckConstraint("page_count IS NULL OR page_count BETWEEN 1 AND 1000", name="ck_teaching_assets_pages"), mysql_engine="InnoDB")
    op.create_table("teaching_asset_pins",
        sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("course_id", sa.BigInteger(), nullable=False),
        sa.Column("asset_id", sa.BigInteger(), nullable=False),
        sa.Column("book_kind", sa.String(20, collation="utf8mb4_0900_bin")), sa.Column("document_asset_id", sa.BigInteger()),
        sa.Column("page_number", sa.Integer(), nullable=False), sa.Column("pin_x", sa.Integer(), nullable=False), sa.Column("pin_y", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id", "course_id"], ["teaching_assets.id", "teaching_assets.course_id"], name="fk_teaching_pin_asset_course"),
        sa.ForeignKeyConstraint(["document_asset_id", "course_id"], ["teaching_assets.id", "teaching_assets.course_id"], name="fk_teaching_pin_document_course"),
        sa.ForeignKeyConstraint(["course_id", "book_kind"], ["course_reference_books.course_id", "course_reference_books.kind"], name="fk_teaching_pin_book_course"),
        sa.CheckConstraint("(book_kind IS NULL) <> (document_asset_id IS NULL)", name="ck_teaching_pin_one_document"),
        sa.CheckConstraint("page_number BETWEEN 1 AND 1000 AND pin_x BETWEEN 0 AND 10000 AND pin_y BETWEEN 0 AND 10000", name="ck_teaching_pin_position"),
        sa.UniqueConstraint("asset_id", "book_kind", "page_number", "pin_x", "pin_y", name="uq_teaching_pin_book_position"),
        sa.UniqueConstraint("asset_id", "document_asset_id", "page_number", "pin_x", "pin_y", name="uq_teaching_pin_asset_position"), mysql_engine="InnoDB")


def downgrade():
    for table in ("teaching_asset_pins", "teaching_assets", "course_content_imports", "group_material_sources"):
        if op.get_bind().execute(sa.text("SELECT 1 FROM " + table + " LIMIT 1")).first():
            raise RuntimeError("Preserve populated course resources and lineage before reverting.")
    for table in ("teaching_asset_pins", "teaching_assets", "course_content_imports", "group_material_sources"):
        op.drop_table(table)
