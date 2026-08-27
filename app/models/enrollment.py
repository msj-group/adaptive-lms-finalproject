import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app.extensions import db


class EnrollmentStatus(str, enum.Enum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


class Enrollment(db.Model):
    """A Student's membership in one Group.

    Course, Level, and Academic Term are deliberately NOT duplicated here
    -- they are already determined by the Group (Enrollment -> Group ->
    Course -> Level, and Enrollment -> Group -> AcademicTerm), so storing
    them again on Enrollment would let the two disagree with nothing in
    the schema to prevent it (the same reasoning already applied to Group
    not duplicating Course's level_id).

    Role integrity boundary: `student_id` is a foreign key into the
    shared `users` table, which can hold any role. The database can only
    guarantee the referenced row exists, not that its `role` is
    "student" -- a plain FK cannot express a conditional constraint like
    that portably. A row that somehow references a non-Student user (e.g.
    a manually seeded/corrupted row) is still technically permitted by
    this FK. Enrollment creation and reactivation
    (`app/blueprints/admin/enrollments.py`, nested under each Group's
    Manage Members page) independently re-verify `role == student` and an
    active account before writing. The Manage Members query
    (`app/blueprints/admin/group_members.py`) filters Enrollment rows to
    Student-role Users, so a corrupted row is excluded from that display.
    The nested withdrawal/reactivation lookup goes through
    `_get_enrollment_for_group_or_404`, which scopes the Enrollment to
    both the Group taken from the URL and to a Student-role User -- so a
    corrupted row 404s there too, consistently with being excluded from
    the display, rather than being reachable by any route. The FK itself
    still cannot enforce any of this at the schema level; enforcement is
    entirely the application layer's responsibility.
    """

    __tablename__ = "enrollments"
    __table_args__ = (
        db.UniqueConstraint("student_id", "group_id", name="uq_enrollments_student_group"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    student_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    group_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("groups.id"),
        nullable=False,
        index=True,
    )
    status = db.Column(db.String(32), nullable=False, default=EnrollmentStatus.ACTIVE.value, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    student = db.relationship("User", back_populates="student_enrollments")
    group = db.relationship("Group", back_populates="enrollments")

    @validates("status")
    def validate_status(self, _key, value):
        if value not in {s.value for s in EnrollmentStatus}:
            raise ValueError(f"Invalid status: {value}")
        return value
