import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app.extensions import db


class GroupTeacherAssignmentStatus(str, enum.Enum):
    ACTIVE = "active"
    REMOVED = "removed"


class GroupTeacherAssignment(db.Model):
    """A Teacher's assignment to teach one Group.

    Course, Level, and Academic Term are not stored here -- like
    Enrollment, they are already determined by the Group, so there is
    nothing to duplicate. This part deliberately does not add Primary/
    Assistant roles: every ACTIVE assignment is equal for now.

    Role integrity boundary: `teacher_id` is a foreign key into the
    shared `users` table, which can hold any role. The database can only
    guarantee the referenced row exists, not that its `role` is
    "teacher" or that its `status` is "active" -- a plain FK cannot
    express either condition portably (the same limitation already
    documented on Enrollment.student_id). Enforcing role="teacher" and an
    active account at write time is the responsibility of the
    application write path (`app/blueprints/admin/group_members.py`),
    not the schema -- a row that somehow references a non-Teacher (e.g.
    manually seeded/corrupted) is still technically permitted by this FK.
    Assign/reactivate independently re-verify role and account status
    before writing, exactly as Student role integrity is enforced for
    Enrollment's write path; removal deliberately does not require the
    referenced User to still be an eligible Teacher, so an obviously
    invalid assignment can always be cleaned up (see group_members.py).
    """

    __tablename__ = "group_teacher_assignments"
    __table_args__ = (
        db.UniqueConstraint(
            "group_id", "teacher_id", name="uq_group_teacher_assignments_group_teacher"
        ),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    group_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("groups.id"),
        nullable=False,
        index=True,
    )
    teacher_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    status = db.Column(
        db.String(32), nullable=False, default=GroupTeacherAssignmentStatus.ACTIVE.value, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    group = db.relationship("Group", back_populates="teacher_assignments")
    teacher = db.relationship("User", back_populates="teaching_assignments")

    @validates("status")
    def validate_status(self, _key, value):
        if value not in {s.value for s in GroupTeacherAssignmentStatus}:
            raise ValueError(f"Invalid status: {value}")
        return value
