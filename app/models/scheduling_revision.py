"""Preserved old/new room and schedule facts."""
from sqlalchemy import event
from app.extensions import db
from app.models.submission_feedback import whole_second_utc


class SchedulingRevision(db.Model):
    __tablename__ = "scheduling_revisions"
    __table_args__ = (
        db.CheckConstraint("(room_id IS NULL) <> (schedule_id IS NULL)", name="ck_scheduling_revisions_one_target"),
        db.Index("ix_scheduling_revisions_schedule", "schedule_id", "id"),
        db.Index("ix_scheduling_revisions_room", "room_id", "id"),
    )
    id = db.Column(db.BigInteger, primary_key=True)
    room_id = db.Column(db.BigInteger, db.ForeignKey("rooms.id"), nullable=True)
    schedule_id = db.Column(db.BigInteger, db.ForeignKey("schedules.id"), nullable=True)
    actor_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    action = db.Column(db.String(32), nullable=False)
    before_snapshot = db.Column(db.JSON, nullable=True)
    after_snapshot = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)


@event.listens_for(SchedulingRevision, "before_update")
@event.listens_for(SchedulingRevision, "before_delete")
def _preserve_scheduling_revision(_mapper, _connection, _target):
    raise ValueError("Scheduling revisions are append-only")
