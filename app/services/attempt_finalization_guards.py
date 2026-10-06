"""Freeze attempt identity/results; allow only evidenced timely acknowledgement."""
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import object_session

from app.models.quiz_attempt import QuizAttempt
from app.models.attempt_submission_receipt import AttemptSubmissionReceipt


@event.listens_for(QuizAttempt, "before_update")
def guard_attempt_update(mapper, connection, target):
    old = connection.execute(select(QuizAttempt.__table__).where(
        QuizAttempt.__table__.c.id == target.id)).mappings().one()
    immutable = ("id", "public_id", "quiz_id", "student_id", "enrollment_id",
                 "attempt_number", "quiz_version", "started_at", "deadline_at")
    if any(old[key] != getattr(target, key) for key in immutable):
        raise ValueError("Attempt identity and timing cannot change")
    if old["status"] == "in_progress":
        return
    changed = [key for key in ("status", "submitted_at", "correct_count", "total_questions")
               if old[key] != getattr(target, key)]
    if not changed:
        return
    session = object_session(target)
    receipt = next((row for row in session.new if isinstance(row, AttemptSubmissionReceipt)
                    and row.attempt_id == target.id), None)
    if (old["status"] != "expired" or target.status != "submitted"
            or old["correct_count"] != target.correct_count
            or old["total_questions"] != target.total_questions
            or receipt is None or receipt.previous_status != "expired"
            or receipt.actor_id != target.student_id
            or not target.started_at <= receipt.received_at < target.deadline_at
            or receipt.deadline_at != target.deadline_at
            or target.submitted_at != receipt.received_at.replace(microsecond=0)):
        raise ValueError("Finalized answers and scores are immutable")


@event.listens_for(QuizAttempt, "before_delete")
def guard_attempt_delete(*args):
    raise ValueError("Attempt history cannot be deleted")
