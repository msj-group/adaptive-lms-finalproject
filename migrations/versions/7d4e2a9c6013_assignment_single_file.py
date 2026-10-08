"""One final text or private file payload per Assignment/enrollment episode.

Revision ID: 7d4e2a9c6013
Revises: 6b3a8c2d9041
Owner-authorized Version A W2-D02. Existing assignments remain text.
"""
from alembic import op
import sqlalchemy as sa

revision = "7d4e2a9c6013"
down_revision = "6b3a8c2d9041"
branch_labels = depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "mysql":
        raise RuntimeError("Assignment file payloads require the approved MySQL database.")
    op.add_column("assignments", sa.Column("submission_type", sa.String(16, collation="utf8mb4_0900_bin"), nullable=False, server_default="text"))
    op.create_check_constraint("ck_assignments_submission_type", "assignments", "submission_type IN ('text', 'file')")
    op.alter_column("submissions", "answer_text", existing_type=sa.Text(), nullable=True)
    op.add_column("submissions", sa.Column("uploaded_file_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint("uq_submissions_uploaded_file", "submissions", ["uploaded_file_id"])
    op.create_foreign_key("fk_submissions_uploaded_file", "submissions", "uploaded_files", ["uploaded_file_id"], ["id"])
    op.create_check_constraint("ck_submissions_single_payload", "submissions", "(answer_text IS NOT NULL AND uploaded_file_id IS NULL) OR (answer_text IS NULL AND uploaded_file_id IS NOT NULL)")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM assignments WHERE submission_type = 'file' LIMIT 1")).first() or op.get_bind().execute(sa.text("SELECT 1 FROM submissions WHERE uploaded_file_id IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Cannot discard final file submissions; restore a consistent backup instead.")
    op.drop_constraint("ck_submissions_single_payload", "submissions", type_="check")
    op.drop_constraint("fk_submissions_uploaded_file", "submissions", type_="foreignkey")
    op.drop_constraint("uq_submissions_uploaded_file", "submissions", type_="unique")
    op.drop_column("submissions", "uploaded_file_id")
    op.alter_column("submissions", "answer_text", existing_type=sa.Text(), nullable=False)
    op.drop_constraint("ck_assignments_submission_type", "assignments", type_="check")
    op.drop_column("assignments", "submission_type")
