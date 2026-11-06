"""Explicit Workbook page assignments to native curriculum Lessons.

Only mapped pages can be read by Students. Book replacement invalidates
the map by revision; Group copies keep their own Lesson identities.
"""
from app.extensions import db
from app.models.code_types import CODE_COLLATION


class WorkbookLessonPage(db.Model):
    __tablename__ = "workbook_lesson_pages"
    __table_args__ = (
        db.ForeignKeyConstraint(["course_id", "book_kind"],
            ["course_reference_books.course_id", "course_reference_books.kind"],
            name="fk_workbook_page_course_book"),
        db.CheckConstraint("book_kind = 'workbook'", name="ck_workbook_page_kind"),
        db.CheckConstraint("book_revision >= 1", name="ck_workbook_page_revision"),
        db.CheckConstraint("page_number BETWEEN 1 AND 1000", name="ck_workbook_page_number"),
        db.Index("ix_workbook_pages_course_revision_page", "course_id", "book_revision", "page_number"),
    )
    lesson_id = db.Column(db.BigInteger, db.ForeignKey("lessons.id"), primary_key=True)
    page_number = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.BigInteger, nullable=False)
    book_kind = db.Column(db.String(20, collation=CODE_COLLATION), nullable=False, default="workbook")
    book_revision = db.Column(db.Integer, nullable=False)
