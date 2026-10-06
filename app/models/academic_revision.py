"""Append-only grade, attendance and feedback correction evidence."""
from sqlalchemy import event
from app.extensions import db
from app.models.submission_feedback import whole_second_utc


class AcademicRevision(db.Model):
    __tablename__ = "academic_revisions"
    __table_args__ = (
        db.CheckConstraint("(grade_record_id IS NOT NULL)+(attendance_record_id IS NOT NULL)+(submission_feedback_id IS NOT NULL)+(speaking_feedback_id IS NOT NULL)=1", name="ck_academic_revisions_one_target"),
        db.Index("ix_academic_revisions_episode_time", "enrollment_id", "created_at", "id"),
        *[db.Index("ix_academic_revisions_" + kind, column, "id") for kind, column in
          [("grade", "grade_record_id"), ("attendance", "attendance_record_id"), ("submission", "submission_feedback_id"), ("speaking", "speaking_feedback_id")]],
    )
    id = db.Column(db.BigInteger, primary_key=True)
    enrollment_id = db.Column(db.BigInteger, db.ForeignKey("enrollments.id"), nullable=False)
    actor_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    grade_record_id = db.Column(db.BigInteger, db.ForeignKey("grade_records.id"), nullable=True)
    attendance_record_id = db.Column(db.BigInteger, db.ForeignKey("attendance_records.id"), nullable=True)
    submission_feedback_id = db.Column(db.BigInteger, db.ForeignKey("submission_feedback.id"), nullable=True)
    speaking_feedback_id = db.Column(db.BigInteger, db.ForeignKey("speaking_feedback.id"), nullable=True)
    before_snapshot = db.Column(db.JSON, nullable=False)
    after_snapshot = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)


@event.listens_for(AcademicRevision, "before_update")
@event.listens_for(AcademicRevision, "before_delete")
def _preserve_academic_revision(_mapper, _connection, _target):
    raise ValueError("Academic revisions are append-only")
