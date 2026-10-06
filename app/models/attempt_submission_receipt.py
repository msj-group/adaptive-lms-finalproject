"""Append-only evidence of an accepted on-time server submission."""
from sqlalchemy import event
from sqlalchemy.dialects.mysql import DATETIME

from app.extensions import db
from app.models.code_types import CODE_COLLATION
from app.models.submission_feedback import whole_second_utc


class AttemptSubmissionReceipt(db.Model):
    __tablename__ = "attempt_submission_receipts"
    __table_args__ = (
        db.CheckConstraint("received_at < deadline_at", name="ck_attempt_receipt_timely"),
        db.CheckConstraint("previous_status IN ('in_progress', 'expired')", name="ck_attempt_receipt_status"),
        db.CheckConstraint("LENGTH(token_digest) = 64", name="ck_attempt_receipt_digest"),
    )
    id = db.Column(db.BigInteger, primary_key=True)
    attempt_id = db.Column(db.BigInteger, db.ForeignKey("quiz_attempts.id"), nullable=False, unique=True)
    actor_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False, index=True)
    received_at = db.Column(DATETIME(fsp=6), nullable=False)
    deadline_at = db.Column(db.DateTime, nullable=False)
    previous_status = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False)
    token_digest = db.Column(db.String(64, collation=CODE_COLLATION), nullable=False)
    processed_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)


def _immutable(*args):
    raise ValueError("Submission receipt evidence is append-only")


event.listen(AttemptSubmissionReceipt, "before_update", _immutable)
event.listen(AttemptSubmissionReceipt, "before_delete", _immutable)
