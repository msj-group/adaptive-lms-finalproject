"""Independent curriculum documents for teaching groups.

Revision ID: f6b3a18d920e
Revises: 8a21f7d9c460
"""
from alembic import op
import sqlalchemy as sa

revision = "f6b3a18d920e"
down_revision = "8a21f7d9c460"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "group_curricula",
        sa.Column("group_id", sa.BigInteger(), sa.ForeignKey("groups.id"), primary_key=True),
        sa.Column("course_id", sa.BigInteger(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("uploaded_file_id", sa.BigInteger(), sa.ForeignKey("uploaded_files.id")),
        sa.Column("page_count", sa.Integer()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_group_curricula_revision"),
        sa.CheckConstraint(
            "(uploaded_file_id IS NULL AND page_count IS NULL) OR "
            "(uploaded_file_id IS NOT NULL AND page_count IS NOT NULL AND page_count BETWEEN 1 AND 1000)",
            name="ck_group_curricula_document",
        ),
        mysql_engine="InnoDB",
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT group_id FROM group_curricula LIMIT 1")).first():
        raise RuntimeError("Downgrade would remove independent group curriculum ownership.")
    op.drop_table("group_curricula")
