"""Add group curriculum release schedules and deduplicated availability notices.

Revision ID: d49ac73eb621
Revises: c2d8a5e4610f
"""
from alembic import op
import sqlalchemy as sa

revision = "d49ac73eb621"
down_revision = "c2d8a5e4610f"
branch_labels = None
depends_on = None


def upgrade():
    for kind, target in (("lesson", "lessons"), ("material", "materials")):
        op.create_table(
            f"curriculum_{kind}_releases",
            sa.Column("group_id", sa.BigInteger(), sa.ForeignKey("groups.id"), primary_key=True),
            sa.Column(f"{kind}_id", sa.BigInteger(), sa.ForeignKey(f"{target}.id"), primary_key=True),
            sa.Column("opens_at", sa.DateTime()),
            sa.Column("notice_key", sa.String(36)),
            mysql_engine="InnoDB",
        )
    op.add_column("notifications", sa.Column("curriculum_notice_key", sa.String(36)))
    op.create_unique_constraint("uq_notifications_curriculum_notice",
                                "notifications", ["recipient_id", "curriculum_notice_key"])
    op.execute("""
        INSERT INTO curriculum_lesson_releases (group_id, lesson_id, opens_at)
        SELECT g.id, l.id, l.published_at
        FROM lessons l JOIN units u ON u.id = l.unit_id
        JOIN `groups` g ON g.course_id = u.course_id
        WHERE l.status = 'published'
    """)


def downgrade():
    raise RuntimeError("Downgrade would discard group curriculum visibility and notice history.")
