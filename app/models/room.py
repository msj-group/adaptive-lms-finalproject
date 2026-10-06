"""A physical scheduling resource with preserved active/archive history."""
import uuid
from app.extensions import db
from app.models.submission_feedback import whole_second_utc


class Room(db.Model):
    __tablename__ = "rooms"
    __table_args__ = (
        db.CheckConstraint("capacity > 0", name="ck_rooms_capacity"),
        db.CheckConstraint("version > 0", name="ck_rooms_version"),
        db.CheckConstraint("status IN ('active', 'archived')", name="ck_rooms_status"),
    )
    id = db.Column(db.BigInteger, primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), nullable=False, unique=True)
    capacity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(32, collation="utf8mb4_0900_bin"), nullable=False, default="active")
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
