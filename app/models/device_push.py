"""Per-device opt-in Web Push subscriptions; no private content cache."""
import uuid
from datetime import datetime, timezone
from sqlalchemy.dialects.mysql import VARCHAR
from app.extensions import db


class DevicePushSubscription(db.Model):
    __tablename__ = "device_push_subscriptions"
    __table_args__ = (
        db.UniqueConstraint("endpoint_hash", name="uq_device_push_endpoint"),
        db.Index("ix_device_push_delivery", "enabled", "retry_at", "id"),
        db.Index("ix_device_push_user", "user_id", "enabled"),
        db.CheckConstraint("auth_version >= 1 AND version >= 1", name="ck_device_push_versions"),
        db.CheckConstraint("last_notification_id >= 0 AND failures >= 0", name="ck_device_push_cursor"),
        db.CheckConstraint("language IN ('en','ar')", name="ck_device_push_language"),
    )
    id = db.Column(db.BigInteger, primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    auth_version = db.Column(db.Integer, nullable=False)
    language = db.Column(VARCHAR(2, charset='ascii', collation='ascii_bin'), nullable=False, default='en', server_default='en')
    endpoint_hash = db.Column(db.String(64, collation="ascii_bin"), nullable=False)
    endpoint = db.Column(db.Text, nullable=False)
    p256dh = db.Column(db.String(100, collation="ascii_bin"), nullable=False)
    auth_key = db.Column(db.String(32, collation="ascii_bin"), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    last_notification_id = db.Column(db.BigInteger, nullable=False, default=0)
    failures = db.Column(db.Integer, nullable=False, default=0)
    retry_at = db.Column(db.DateTime)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_seen_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
