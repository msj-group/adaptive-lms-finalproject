"""The independent curriculum snapshot owned by one teaching Group."""
from datetime import datetime, timezone

from app.extensions import db


class GroupCurriculum(db.Model):
    __tablename__ = "group_curricula"
    __table_args__ = (
        db.CheckConstraint("revision >= 1", name="ck_group_curricula_revision"),
        db.CheckConstraint(
            "(uploaded_file_id IS NULL AND page_count IS NULL) OR "
            "(uploaded_file_id IS NOT NULL AND page_count IS NOT NULL AND page_count BETWEEN 1 AND 1000)",
            name="ck_group_curricula_document",
        ),
    )

    group_id = db.Column(db.BigInteger, db.ForeignKey("groups.id"), primary_key=True)
    course_id = db.Column(db.BigInteger, db.ForeignKey("courses.id"), nullable=False)
    uploaded_file_id = db.Column(db.BigInteger, db.ForeignKey("uploaded_files.id"))
    page_count = db.Column(db.Integer)
    revision = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    uploaded_file = db.relationship("UploadedFile")
