"""Bind a learning record to its actual enrollment episode at creation.

The activity keeps its original Group identity. Transfers never retarget old
work, and re-enrollment never acquires a previous episode's work by student id.
"""
from datetime import date, datetime
from decimal import Decimal
from flask import g, has_request_context
from flask_login import current_user
from sqlalchemy import event, inspect, text
from app.models.submission_feedback import whole_second_utc

GROUP_QUERIES = {
    "submissions": "SELECT group_id FROM assignments WHERE id=:owner",
    "speaking_submissions": "SELECT a.group_id FROM speaking_activities s JOIN assignments a ON a.id=s.assignment_id WHERE s.id=:owner",
    "quiz_attempts": "SELECT group_id FROM quizzes WHERE id=:owner",
    "attendance_records": "SELECT group_id FROM attendance_sessions WHERE id=:owner",
    "grade_records": "SELECT c.group_id FROM grade_items i JOIN grade_categories c ON c.id=i.category_id WHERE i.id=:owner",
}
OWNERS = {"submissions": "assignment_id", "speaking_submissions": "speaking_activity_id", "quiz_attempts": "quiz_id",
          "attendance_records": "attendance_session_id", "grade_records": "grade_item_id"}


def _group_id(connection, target):
    table = target.__tablename__
    if table == "lesson_progress":
        return target.group_id
    return connection.execute(text(GROUP_QUERIES[table]), {"owner": getattr(target, OWNERS[table])}).scalar()


def bind_episode(_mapper, connection, target):
    group_id = _group_id(connection, target)
    row = connection.execute(text("SELECT e.id FROM enrollments e JOIN users u ON u.id=e.student_id WHERE e.group_id=:group AND e.student_id=:student AND e.status='active' AND u.role='student' AND u.status='active' FOR UPDATE"),
                             {"group": group_id, "student": target.student_id}).first()
    if row is None or (target.enrollment_id is not None and target.enrollment_id != row[0]):
        raise ValueError("A new learning record requires the current active Student enrollment episode")
    target.enrollment_id = row[0]


def preserve_episode(_mapper, _connection, target):
    if inspect(target).attrs.enrollment_id.history.has_changes():
        raise ValueError("A learning record never moves to another enrollment episode")


def _snapshot(row):
    return {key: value.isoformat() if isinstance(value, (date, datetime)) else format(value, "f") if isinstance(value, Decimal) else value
            for key, value in row.items()}


def audit_academic_change(_mapper, connection, target):
    state = inspect(target)
    if not any(state.attrs[column.key].history.has_changes() for column in target.__table__.columns):
        return
    before = connection.execute(target.__table__.select().where(target.__table__.c.id == target.id)).mappings().one()
    after = {column.key: getattr(target, column.key) for column in target.__table__.columns}
    if _snapshot(before) == _snapshot(after):
        return
    from app.models.academic_revision import AcademicRevision
    table = target.__tablename__
    actor_id = current_user.id if has_request_context() and current_user.is_authenticated else (
        getattr(target, "graded_by_id", None) or getattr(target, "reviewer_id", None) or state.session.info.get("academic_actor_id"))
    actor = connection.execute(text("SELECT id, auth_version, role FROM users WHERE id=:id AND role IN ('administrator','teacher') AND status='active' FOR UPDATE"), {"id": actor_id}).first() if actor_id is not None else None
    proof = getattr(g, "authenticated_actor", None) if has_request_context() else None
    if actor is None or (proof is not None and tuple(actor) != proof):
        raise ValueError("An academic correction requires its actual active operator")
    episode_id = getattr(target, "enrollment_id", None)
    if episode_id is None:
        parent_table, parent_key = ("submissions", "submission_id") if table == "submission_feedback" else ("speaking_submissions", "speaking_submission_id")
        episode_id = connection.execute(text("SELECT enrollment_id FROM " + parent_table + " WHERE id=:id"), {"id": getattr(target, parent_key)}).scalar()
    target_key = {"grade_records": "grade_record_id", "attendance_records": "attendance_record_id",
                  "submission_feedback": "submission_feedback_id", "speaking_feedback": "speaking_feedback_id"}[table]
    connection.execute(AcademicRevision.__table__.insert().values(enrollment_id=episode_id, actor_id=actor_id,
        before_snapshot=_snapshot(before), after_snapshot=_snapshot(after), created_at=whole_second_utc(), **{target_key: target.id}))


def register_episode_events(models, revision_models):
    for model in models:
        event.listen(model, "before_insert", bind_episode)
        event.listen(model, "before_update", preserve_episode)
    for model in revision_models:
        event.listen(model, "before_update", audit_academic_change)
