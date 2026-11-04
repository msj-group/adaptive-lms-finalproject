"""Private source document for the shared curriculum of one course."""
from app.extensions import db


class CourseDocument(db.Model):
    __tablename__ = "course_documents"
    __table_args__ = (
        db.CheckConstraint("page_count BETWEEN 1 AND 1000", name="ck_course_documents_pages"),
        db.CheckConstraint("revision >= 1", name="ck_course_documents_revision"),
    )

    course_id = db.Column(db.BigInteger(), db.ForeignKey("courses.id"), primary_key=True)
    uploaded_file_id = db.Column(
        db.BigInteger(), db.ForeignKey("uploaded_files.id"), nullable=False, unique=True
    )
    page_count = db.Column(db.Integer(), nullable=False)
    revision = db.Column(db.Integer(), nullable=False, default=1)
    uploaded_file = db.relationship("UploadedFile")
