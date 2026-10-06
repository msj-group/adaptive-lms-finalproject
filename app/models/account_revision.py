"""Append-only account changes; passwords and hashes are never audit data."""
import uuid
from sqlalchemy import event
from app.extensions import db
from app.models.submission_feedback import whole_second_utc


class AccountRevision(db.Model):
    __tablename__ = "account_revisions"
    __table_args__ = (
        db.UniqueConstraint("user_id", "version", name="uq_account_revisions_user_version"),
        db.CheckConstraint("version > 1", name="ck_account_revisions_version"),
        db.Index("ix_account_revisions_user_created", "user_id", "created_at", "id"),
    )
    id = db.Column(db.BigInteger, primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    actor_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(32), nullable=False)
    before_snapshot = db.Column(db.JSON, nullable=False)
    after_snapshot = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)


@event.listens_for(AccountRevision, "before_update")
@event.listens_for(AccountRevision, "before_delete")
def _preserve_account_revision(_mapper, _connection, _target):
    raise ValueError("Account revisions are append-only")
