"""Enrollment episodes retain each membership and immutable lifecycle event."""
import uuid
from sqlalchemy import event, select
from app.extensions import db
from app.models.submission_feedback import whole_second_utc


class EnrollmentMembership(db.Model):
    __tablename__ = "enrollment_memberships"
    __table_args__ = (
        db.UniqueConstraint("enrollment_id", "active_marker", name="uq_enrollment_memberships_current"),
        db.CheckConstraint("(active_marker IS NOT NULL AND active_marker = 1 AND left_at IS NULL) OR (active_marker IS NULL AND left_at IS NOT NULL)", name="ck_enrollment_memberships_state"),
        db.CheckConstraint("left_at IS NULL OR left_at >= joined_at", name="ck_enrollment_memberships_dates"),
        db.Index("ix_enrollment_memberships_group_episode", "group_id", "enrollment_id"),
    )
    id = db.Column(db.BigInteger, primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    enrollment_id = db.Column(db.BigInteger, db.ForeignKey("enrollments.id"), nullable=False)
    group_id = db.Column(db.BigInteger, db.ForeignKey("groups.id"), nullable=False)
    active_marker = db.Column(db.Integer, nullable=True)
    joined_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    left_at = db.Column(db.DateTime, nullable=True)


class EnrollmentEvent(db.Model):
    __tablename__ = "enrollment_events"
    __table_args__ = (db.UniqueConstraint("operation_key", name="uq_enrollment_events_operation_key"),
                     db.Index("ix_enrollment_events_episode", "enrollment_id", "id"))
    id = db.Column(db.BigInteger, primary_key=True)
    enrollment_id = db.Column(db.BigInteger, db.ForeignKey("enrollments.id"), nullable=False)
    previous_enrollment_id = db.Column(db.BigInteger, db.ForeignKey("enrollments.id"), nullable=True, index=True)
    actor_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    operation_key = db.Column(db.String(36), nullable=False)
    action = db.Column(db.String(32), nullable=False)
    before_snapshot = db.Column(db.JSON, nullable=True)
    after_snapshot = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)


@event.listens_for(EnrollmentEvent, "before_update")
@event.listens_for(EnrollmentEvent, "before_delete")
@event.listens_for(EnrollmentMembership, "before_delete")
def _preserve_enrollment_history(_mapper, _connection, _target):
    raise ValueError("Enrollment history is preserved")


@event.listens_for(EnrollmentMembership, "before_update")
def _close_membership_once(_mapper, connection, target):
    old = connection.execute(select(EnrollmentMembership.__table__).where(
        EnrollmentMembership.__table__.c.id == target.id)).mappings().one()
    if any(old[key] != getattr(target, key) for key in ("id", "public_id", "enrollment_id", "group_id", "joined_at")):
        raise ValueError("Membership identity is immutable")
    if old["active_marker"] is None:
        if target.left_at != old["left_at"] or target.active_marker is not None:
            raise ValueError("A closed membership cannot be changed or reopened")
    elif target.active_marker is None:
        if target.left_at is None or target.left_at < target.joined_at:
            raise ValueError("Closing membership requires a valid end time")
    elif target.active_marker != 1 or target.left_at is not None:
        raise ValueError("Membership can only be closed once")
