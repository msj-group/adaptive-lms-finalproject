from app.models.code_types import CODE_COLLATION
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import FileAccessAction


class FileAccessLog(db.Model):
    """Append-only audit trail for uploaded-file access (M12).

    One row is written per authorised event:

    - ``upload``   -- created in the *same* transaction as the
      ``UploadedFile`` metadata and its Material;
    - ``inline``   -- an authorised open / media (incl. HTTP Range) request;
    - ``download`` -- an authorised attachment request.

    Append-only is an *application* property: there is no update or delete
    route. If this row cannot be persisted, the file is **not** served
    (fail closed). Deliberately stores **no** IP address and **no**
    user-agent string.
    """

    __tablename__ = "file_access_logs"
    __table_args__ = (
        db.CheckConstraint(
            "action IN ('upload', 'inline', 'download')",
            name="ck_file_access_logs_action_valid",
        ),
        db.Index("ix_file_access_logs_file_time", "uploaded_file_id", "occurred_at"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    uploaded_file_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("uploaded_files.id"),
        nullable=False,
        index=True,
    )
    actor_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    action = db.Column(db.String(16, collation=CODE_COLLATION), nullable=False)
    occurred_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )

    uploaded_file = db.relationship("UploadedFile", back_populates="access_logs")
    actor = db.relationship("User")

    @validates("action")
    def validate_action(self, _key, value):
        if value not in {a.value for a in FileAccessAction}:
            raise ValueError(f"Invalid file access action: {value}")
        return value
