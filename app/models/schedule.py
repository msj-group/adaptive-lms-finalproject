import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import AcademicStatus


class Schedule(db.Model):
    """One recurring weekly meeting slot for a Group (M08).

    A Schedule is a child of Group and adds nothing that Group already
    determines: Course, Level, Academic Term, Students, and Teachers are
    all reachable through the Group, so none of them is duplicated here
    -- the same single-source-of-truth reasoning already applied to
    Enrollment and GroupTeacherAssignment.

    Recurrence convention: ``day_of_week`` is an integer, Monday=0 ..
    Sunday=6 -- the same numbering as ``datetime.date.weekday()``.

    Timezone: ``start_time`` / ``end_time`` are stored as local civil
    ``TIME`` values, interpreted through ``APP_TIMEZONE``. There is no
    timezone column and weekly wall-clock values are never converted to
    UTC -- only the audit timestamps (``created_at`` / ``updated_at``)
    are UTC, following the project convention.

    The exact-slot ``UniqueConstraint`` is the database's final defense
    against a byte-for-byte duplicate row. The richer operational
    "these two active slots overlap" rule (same weekday, half-open time
    overlap, and a shared actual calendar date of that weekday) lives in
    ``app/services/schedule_queries.py`` and is rechecked by the write
    path after locking -- a single-table constraint cannot express it.

    Status: reuses the shared ``AcademicStatus`` (``active`` /
    ``archived``). There is no hard delete; reactivation reuses the same
    row. Status is owned solely by the dedicated toggle route -- the
    create/edit form never carries it.
    """

    __tablename__ = "schedules"
    __table_args__ = (
        db.CheckConstraint(
            "day_of_week >= 0 AND day_of_week <= 6", name="ck_schedules_day_of_week_range"
        ),
        db.CheckConstraint("start_time < end_time", name="ck_schedules_time_order"),
        db.CheckConstraint(
            "effective_start_date <= effective_end_date",
            name="ck_schedules_effective_date_order",
        ),
        db.UniqueConstraint(
            "group_id",
            "day_of_week",
            "start_time",
            "end_time",
            "effective_start_date",
            "effective_end_date",
            name="uq_schedules_exact_slot",
        ),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    group_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("groups.id"),
        nullable=False,
        index=True,
    )
    day_of_week = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    effective_start_date = db.Column(db.Date, nullable=False)
    effective_end_date = db.Column(db.Date, nullable=False)
    location = db.Column(db.String(255), nullable=True)
    room_id = db.Column(db.BigInteger, db.ForeignKey("rooms.id", name="fk_schedules_room_id"), nullable=True, index=True)
    status = db.Column(db.String(32), nullable=False, default=AcademicStatus.ACTIVE.value, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    group = db.relationship("Group", back_populates="schedules")
    room = db.relationship("Room")

    @validates("status")
    def validate_status(self, _key, value):
        if value not in {s.value for s in AcademicStatus}:
            raise ValueError(f"Invalid status: {value}")
        return value
