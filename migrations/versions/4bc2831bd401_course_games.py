"""Store scoped practice activities for book pages."""
from alembic import op
import sqlalchemy as sa

revision = "4bc2831bd401"
down_revision = "f8a012bc34de"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "course_games",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("public_id", sa.String(36), nullable=False, unique=True),
        sa.Column("course_id", sa.BigInteger, sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("group_id", sa.BigInteger, sa.ForeignKey("groups.id")),
        sa.Column("book", sa.String(16), nullable=False),
        sa.Column("document_sha", sa.String(64), nullable=False),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("pin_x", sa.Integer, nullable=False),
        sa.Column("pin_y", sa.Integer, nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("items", sa.JSON, nullable=False),
        sa.Column("published", sa.Boolean, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.CheckConstraint("book IN ('main', 'workbook')", name="ck_course_game_book"),
        sa.CheckConstraint("page_number BETWEEN 1 AND 1000", name="ck_course_game_page"),
        sa.CheckConstraint("pin_x BETWEEN 0 AND 10000 AND pin_y BETWEEN 0 AND 10000", name="ck_course_game_position"),
    )
    op.create_index("ix_course_games_group_id", "course_games", ["group_id"])
    op.create_index("ix_course_game_book_page", "course_games", ["course_id", "book", "document_sha", "page_number"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM course_games LIMIT 1")).first():
        raise RuntimeError("Preserve published games and their drafts before rolling back the schema.")
    op.drop_table("course_games")
