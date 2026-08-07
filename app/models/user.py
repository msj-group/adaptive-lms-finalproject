import enum
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
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(32), nullable=False, default=UserStatus.ACTIVE.value)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

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
