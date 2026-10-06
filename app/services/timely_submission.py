"""Locked acceptance of a timely form, including a competing expiry observer.

An expired attempt can only be acknowledged as submitted by an on-time,
server-received, signed and fully authorized submit request. Its frozen
answers and score remain unchanged. This is not a reopen operation.
"""
from hashlib import sha256

from app.extensions import db
from app.models.attempt_submission_receipt import AttemptSubmissionReceipt
from app.models.submission_feedback import whole_second_utc
from app.services.quiz_attempts import finalize_attempt


def is_timely(attempt, received_at):
    return attempt.started_at <= received_at < attempt.deadline_at


def accept_timely_submission(attempt, received_at, token, actor_id):
    """Caller holds the aggregate/attempt locks and has verified the form."""
    if actor_id != attempt.student_id or not is_timely(attempt, received_at):
        raise ValueError("This submission was not received within its attempt window")
    if attempt.status not in ("in_progress", "expired"):
        return False
    previous = attempt.status
    if previous == "in_progress":
        finalize_attempt(attempt, "submitted", received_at.replace(microsecond=0))
    else:
        # Expiry has already graded and frozen the exact same saved answers.
        # Never recalculate, change counts, or enable an answer write here.
        attempt.status = "submitted"
        attempt.submitted_at = received_at.replace(microsecond=0)
    db.session.add(AttemptSubmissionReceipt(
        attempt_id=attempt.id, actor_id=actor_id,
        received_at=received_at, deadline_at=attempt.deadline_at,
        previous_status=previous, token_digest=sha256(token.encode("utf-8")).hexdigest(),
        processed_at=whole_second_utc(),
    ))
    return True
