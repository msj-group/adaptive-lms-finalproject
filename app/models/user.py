import enum
import uuid
from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy.orm import validates

from app.extensions import db


class UserRole(str, enum.Enum):
    ADMINISTRATOR = "administrator"
    TEACHER = "teacher"
    STUDENT = "student"
    RESEARCHER = "researcher"


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(32), nullable=False, default=UserStatus.ACTIVE.value)
    auth_version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Named "student_enrollments", not "enrollments", so it reads
    # unambiguously as "Enrollment rows where this user is the student" --
    # a User of any role can technically be the FK target (the database
    # cannot express "only role=student" on a plain foreign key), and a
    # generic name here would wrongly imply Admin/Teacher/Researcher
    # enrollment semantics that do not exist.
    student_enrollments = db.relationship("Enrollment", back_populates="student")

    # Named "teaching_assignments", not "assignments", for the same reason
    # as "student_enrollments" above -- a User of any role can technically
    # be the FK target of GroupTeacherAssignment.teacher_id (the database
    # cannot express "only role=teacher" on a plain foreign key), and a
    # generic name here would wrongly imply this applies to every role.
    teaching_assignments = db.relationship("GroupTeacherAssignment", back_populates="teacher")

    @validates("role")
    def validate_role(self, _key, value):
        if value not in {r.value for r in UserRole}:
            raise ValueError(f"Invalid role: {value}")
        return value

    @validates("status")
    def validate_status(self, _key, value):
        if value not in {s.value for s in UserStatus}:
            raise ValueError(f"Invalid status: {value}")
        return value

    def is_active_account(self) -> bool:
        return self.status == UserStatus.ACTIVE.value

    @property
    def is_active(self):
        return self.is_active_account()

    def bump_auth_version(self):
        """Invalidate every existing login session for this user (e.g. on
        suspend, password reset, or email change) by advancing the version
        embedded in get_id(). Sessions signed with the old value stop
        resolving to a user the next time they are loaded.
        """
        self.auth_version = (self.auth_version or 1) + 1

    def get_id(self):
        return f"{self.id}.{self.auth_version}"
