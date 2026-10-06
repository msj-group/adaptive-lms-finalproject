"""Bind learning history to episodes and retain academic corrections.

Revision ID: b52f9d1a0e83
Revises: a47e25d90c61
Source only. Refuse ambiguous historical ownership before the first DDL.
"""
from alembic import op
import sqlalchemy as sa

revision = "b52f9d1a0e83"
down_revision = "a47e25d90c61"
branch_labels = None
depends_on = None

# The child record already owns its academic context; no Group is retargeted.
BINDINGS = [
    ("submission", "submissions", "JOIN assignments a ON a.id=r.assignment_id", "a.group_id"),
    ("speaking_submission", "speaking_submissions", "JOIN speaking_activities s ON s.id=r.speaking_activity_id JOIN assignments a ON a.id=s.assignment_id", "a.group_id"),
    ("quiz_attempt", "quiz_attempts", "JOIN quizzes q ON q.id=r.quiz_id", "q.group_id"),
    ("attendance_record", "attendance_records", "JOIN attendance_sessions s ON s.id=r.attendance_session_id", "s.group_id"),
    ("grade_record", "grade_records", "JOIN grade_items i ON i.id=r.grade_item_id JOIN grade_categories c ON c.id=i.category_id", "c.group_id"),
    ("lesson_progress", "lesson_progress", "", "r.group_id"),
]
UNIQUES = [
    ("submissions", "uq_submissions_assignment_student", ["assignment_id", "student_id"], "uq_submissions_assignment_episode", ["assignment_id", "enrollment_id"]),
    ("speaking_submissions", "uq_speaking_submissions_activity_student", ["speaking_activity_id", "student_id"], "uq_speaking_submissions_activity_episode", ["speaking_activity_id", "enrollment_id"]),
    ("quiz_attempts", "uq_quiz_attempts_quiz_student_number", ["quiz_id", "student_id", "attempt_number"], "uq_quiz_attempts_quiz_episode_number", ["quiz_id", "enrollment_id", "attempt_number"]),
    ("lesson_progress", "uq_lesson_progress_student_group_lesson", ["student_id", "group_id", "lesson_id"], "uq_lesson_progress_episode_group_lesson", ["enrollment_id", "group_id", "lesson_id"]),
]


def upgrade():
    connection = op.get_bind()
    for _name, table, joins, group in BINDINGS:
        query = "SELECT r.id FROM " + table + " r " + joins + " LEFT JOIN enrollments e ON e.student_id=r.student_id AND e.group_id=" + group + " GROUP BY r.id HAVING COUNT(e.id)<>1 LIMIT 1"
        if connection.execute(sa.text(query)).first():
            raise RuntimeError("Learning migration refused missing or ambiguous enrollment ownership. No DDL was started.")
    for name, table, joins, group in BINDINGS:
        op.add_column(table, sa.Column("enrollment_id", sa.BigInteger(), nullable=True))
        connection.execute(sa.text("UPDATE " + table + " r " + joins + " JOIN enrollments e ON e.student_id=r.student_id AND e.group_id=" + group + " SET r.enrollment_id=e.id"))
        op.alter_column(table, "enrollment_id", existing_type=sa.BigInteger(), nullable=False)
        op.create_index("ix_" + table + "_enrollment_id", table, ["enrollment_id"])
        op.create_foreign_key("fk_" + name + "_episode", table, "enrollments", ["enrollment_id"], ["id"])
        op.create_foreign_key("fk_" + name + "_episode_student", table, "enrollments", ["enrollment_id", "student_id"], ["id", "student_id"])
    for table, old_name, _old_cols, new_name, new_cols in UNIQUES:
        op.create_unique_constraint(new_name, table, new_cols)
        op.drop_constraint(old_name, table, type_="unique")
    op.create_table("academic_revisions",
        sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("enrollment_id", sa.BigInteger(), sa.ForeignKey("enrollments.id"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("grade_record_id", sa.BigInteger(), sa.ForeignKey("grade_records.id"), nullable=True),
        sa.Column("attendance_record_id", sa.BigInteger(), sa.ForeignKey("attendance_records.id"), nullable=True),
        sa.Column("submission_feedback_id", sa.BigInteger(), sa.ForeignKey("submission_feedback.id"), nullable=True),
        sa.Column("speaking_feedback_id", sa.BigInteger(), sa.ForeignKey("speaking_feedback.id"), nullable=True),
        sa.Column("before_snapshot", sa.JSON(), nullable=False), sa.Column("after_snapshot", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("(grade_record_id IS NOT NULL)+(attendance_record_id IS NOT NULL)+(submission_feedback_id IS NOT NULL)+(speaking_feedback_id IS NOT NULL)=1", name="ck_academic_revisions_one_target"))
    op.create_index("ix_academic_revisions_episode_time", "academic_revisions", ["enrollment_id", "created_at", "id"])
    for kind, column in [("grade", "grade_record_id"), ("attendance", "attendance_record_id"), ("submission", "submission_feedback_id"), ("speaking", "speaking_feedback_id")]:
        op.create_index("ix_academic_revisions_" + kind, "academic_revisions", [column, "id"])


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT id FROM academic_revisions LIMIT 1")).first():
        raise RuntimeError("Downgrade refused: academic revisions must be preserved.")
    for _name, table, _joins, _group in BINDINGS:
        if connection.execute(sa.text("SELECT id FROM " + table + " LIMIT 1")).first():
            raise RuntimeError("Downgrade refused: preserve learning episode ownership.")
    op.drop_table("academic_revisions")
    for table, old_name, old_cols, new_name, _new_cols in UNIQUES:
        op.create_unique_constraint(old_name, table, old_cols)
        op.drop_constraint(new_name, table, type_="unique")
    for name, table, _joins, _group in reversed(BINDINGS):
        op.drop_constraint("fk_" + name + "_episode_student", table, type_="foreignkey")
        op.drop_constraint("fk_" + name + "_episode", table, type_="foreignkey")
        op.drop_index("ix_" + table + "_enrollment_id", table_name=table)
        op.drop_column(table, "enrollment_id")
