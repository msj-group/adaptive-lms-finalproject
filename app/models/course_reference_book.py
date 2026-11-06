"""Private complete books; Workbook exercises have explicit Student page maps."""
from datetime import datetime, timezone

from app.extensions import db
from app.models.code_types import CODE_COLLATION


class CourseReferenceBook(db.Model):
    __tablename__ = "course_reference_books"
    __table_args__ = (
        db.CheckConstraint("kind IN ('teacher_book', 'workbook')", name="ck_course_reference_books_kind"),
        db.CheckConstraint("page_count BETWEEN 1 AND 1000", name="ck_course_reference_books_pages"),
        db.CheckConstraint("revision >= 1", name="ck_course_reference_books_revision"),
    )

    course_id = db.Column(db.BigInteger, db.ForeignKey("courses.id"), primary_key=True)
    kind = db.Column(db.String(20, collation=CODE_COLLATION), primary_key=True)
    uploaded_file_id = db.Column(db.BigInteger, db.ForeignKey("uploaded_files.id"), nullable=False, unique=True)
    page_count = db.Column(db.Integer, nullable=False)
    revision = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    uploaded_file = db.relationship("UploadedFile")
