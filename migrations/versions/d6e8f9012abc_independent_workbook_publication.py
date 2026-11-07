"""Separate Workbook publication while retaining configured visibility.

Revision ID: d6e8f9012abc
Revises: c5d7e8f901ab
"""
from alembic import op
import sqlalchemy as sa

revision = "d6e8f9012abc"
down_revision = "c5d7e8f901ab"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("workbook_lesson_releases",
        sa.Column("group_id", sa.BigInteger(), sa.ForeignKey("groups.id"), primary_key=True),
        sa.Column("lesson_id", sa.BigInteger(), sa.ForeignKey("lessons.id"), primary_key=True),
        sa.Column("opens_at", sa.DateTime(), nullable=True),
        sa.Column("notice_key", sa.String(36), nullable=True), mysql_engine="InnoDB")
    op.create_index("ix_workbook_release_due", "workbook_lesson_releases", ["opens_at", "group_id"])
    # Deduplicate physical pages BEFORE computing the distinct book notice key.
    # No old published notices are replayed; scheduled batches retain grouping.
    op.execute(sa.text("""
        INSERT INTO workbook_lesson_releases (group_id, lesson_id, opens_at, notice_key)
        SELECT source.group_id, source.lesson_id, source.opens_at,
          CASE WHEN source.opens_at > UTC_TIMESTAMP() AND source.notice_key IS NOT NULL
            THEN LOWER(CONCAT(SUBSTR(source.key_hash,1,8),'-',SUBSTR(source.key_hash,9,4),'-',
              SUBSTR(source.key_hash,13,4),'-',SUBSTR(source.key_hash,17,4),'-',SUBSTR(source.key_hash,21,12)))
            ELSE NULL END
        FROM (
          SELECT DISTINCT g.id AS group_id, l.id AS lesson_id,
            CASE WHEN lr.lesson_id IS NOT NULL THEN lr.opens_at
              WHEN u.group_id IS NOT NULL THEN CAST('1970-01-01 00:00:00' AS DATETIME)
              ELSE NULL END AS opens_at,
            lr.notice_key, MD5(CONCAT('workbook:', lr.notice_key)) AS key_hash
          FROM workbook_lesson_pages p
          JOIN lessons l ON l.id=p.lesson_id
          JOIN units u ON u.id=l.unit_id
          JOIN course_reference_books b ON b.course_id=p.course_id AND b.kind=p.book_kind
            AND b.revision=p.book_revision AND p.page_number<=b.page_count
          JOIN `groups` g ON g.course_id=p.course_id
            AND (u.group_id=g.id OR (u.course_id=g.course_id AND NOT EXISTS
              (SELECT 1 FROM group_curricula gc WHERE gc.group_id=g.id)))
          LEFT JOIN curriculum_lesson_releases lr ON lr.group_id=g.id AND lr.lesson_id=l.id
          WHERE p.book_kind='workbook'
        ) AS source
    """))


def downgrade():
    raise RuntimeError("Independent publication must be preserved; use a reviewed source rollback, not a downgrade.")
