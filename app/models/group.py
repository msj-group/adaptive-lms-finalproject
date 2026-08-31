import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import AcademicStatus


class Group(db.Model):
    __tablename__ = "groups"
    __table_args__ = (
        db.UniqueConstraint(
            "academic_term_id", "course_id", "name", name="uq_groups_term_course_name"
        ),
        db.UniqueConstraint(
            "academic_term_id", "course_id", "code", name="uq_groups_term_course_code"
        ),
        db.CheckConstraint("capacity > 0", name="ck_groups_positive_capacity"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    academic_term_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("academic_terms.id"),
        nullable=False,
        index=True,
    )
    course_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("courses.id"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), nullable=True)
    capacity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(32), nullable=False, default=AcademicStatus.ACTIVE.value, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    academic_term = db.relationship("AcademicTerm", back_populates="groups")
    course = db.relationship("Course", back_populates="groups")
    enrollments = db.relationship("Enrollment", back_populates="group")
    teacher_assignments = db.relationship("GroupTeacherAssignment", back_populates="group")
    schedules = db.relationship("Schedule", back_populates="group")

    @validates("status")
    def validate_status(self, _key, value):
        if value not in {s.value for s in AcademicStatus}:
            raise ValueError(f"Invalid status: {value}")
        return value
