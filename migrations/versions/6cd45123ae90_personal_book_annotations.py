"""Store private annotations on an exact book page."""
from alembic import op
import sqlalchemy as sa

revision = "6cd45123ae90"
down_revision = "4bc2831bd401"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "book_annotations",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("book_sha256", sa.String(64), nullable=False),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("marks", sa.JSON, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("user_id", "book_sha256", "page_number", name="uq_book_annotation_owner_page"),
        sa.CheckConstraint("page_number BETWEEN 1 AND 1000", name="ck_book_annotation_page"),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM book_annotations LIMIT 1")).first():
        raise RuntimeError("Back up personal annotations before reverting their schema.")
    op.drop_table("book_annotations")
