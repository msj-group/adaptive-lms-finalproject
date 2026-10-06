from app.models.code_types import CODE_COLLATION
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import FileCategory


class UploadedFile(db.Model):
    """Server-validated metadata for one physical file backing a ``file``
    Material (M12).

    The physical bytes live in private, non-executable storage outside
    ``app/static`` under a **random unguessable** ``storage_key`` -- the
    original filename is kept only for display and the download
    ``Content-Disposition`` and **never** contributes to a filesystem
    path. ``content_type`` / ``extension`` / ``category`` / ``byte_size``
    / ``sha256`` are all determined by the validation pipeline
    (extension + declared-MIME alias + binary signature/container), not
    by anything the browser sent.

    Lifecycle: there is no hard delete. Archiving the owning Material
    keeps this row and its physical file (see ``Material``). One
    ``UploadedFile`` backs exactly one Material
    (``materials.uploaded_file_id`` is ``UNIQUE``); replacing a wrong
    file means archiving the Material and creating a new one.
    """

    __tablename__ = "uploaded_files"
    __table_args__ = (
        db.CheckConstraint("byte_size > 0", name="ck_uploaded_files_positive_size"),
        db.CheckConstraint(
            "category IN ('document', 'image', 'audio', 'video')",
            name="ck_uploaded_files_category_valid",
        ),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    storage_key = db.Column(db.String(120), nullable=False, unique=True)
    original_filename = db.Column(db.String(255), nullable=False)
    extension = db.Column(db.String(16), nullable=False)
    category = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False)
    content_type = db.Column(db.String(128), nullable=False)
    byte_size = db.Column(db.BigInteger(), nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    uploaded_by_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )

    uploaded_by = db.relationship("User")
    material = db.relationship("Material", back_populates="uploaded_file", uselist=False)
    #: Phase 4 / M05. The Listening activity this recording backs, or
    #: ``None``. One-to-one (``listening_activities.audio_file_id`` is
    #: UNIQUE) and no cascade, exactly like ``material`` above: an
    #: UploadedFile backs at most one object, and replacing a wrong
    #: recording means creating a new draft, never rewriting this row.
    listening_activity = db.relationship(
        "ListeningActivity", back_populates="audio_file", uselist=False
    )
    access_logs = db.relationship("FileAccessLog", back_populates="uploaded_file")

    @validates("category")
    def validate_category(self, _key, value):
        if value not in {c.value for c in FileCategory}:
            raise ValueError(f"Invalid file category: {value}")
        return value
