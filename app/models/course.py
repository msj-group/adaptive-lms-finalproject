import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import AcademicStatus


class Course(db.Model):
    __tablename__ = "courses"
    __table_args__ = (
        db.UniqueConstraint("level_id", "title", name="uq_courses_level_id_title"),
        db.UniqueConstraint("level_id", "code", name="uq_courses_level_id_code"),
        db.CheckConstraint("price IS NULL OR (price >= 0 AND price <= 99999.999 AND MOD(price, 0.001) = 0)", name="ck_courses_price"),
        db.CheckConstraint("currency_code = 'LYD'", name="ck_courses_currency"),
        db.CheckConstraint("version > 0", name="ck_courses_version"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    level_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("levels.id"),
        nullable=False,
        index=True,
    )
    title = db.Column(db.String(150), nullable=False)
    code = db.Column(db.String(20), nullable=True)
    description = db.Column(db.Text, nullable=True)
    price = db.Column(db.Numeric(19, 4), nullable=True)
    currency_code = db.Column(db.String(3, collation="utf8mb4_0900_bin"), nullable=False, default="LYD")
    version = db.Column(db.Integer, nullable=False, default=1)
    display_order = db.Column(db.Integer, nullable=False, default=0, index=True)
    status = db.Column(db.String(32), nullable=False, default=AcademicStatus.ACTIVE.value, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    level = db.relationship("Level", back_populates="courses")
    groups = db.relationship("Group", back_populates="course")

    @validates("status")
    def validate_status(self, _key, value):
        if value not in {s.value for s in AcademicStatus}:
            raise ValueError(f"Invalid status: {value}")
        return value
