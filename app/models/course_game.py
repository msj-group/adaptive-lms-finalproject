"""Retained mapping for the applied, subsequently cancelled practice schema."""
from uuid import uuid4

from app.extensions import db


class CourseGame(db.Model):
    __tablename__ = "course_games"
    __table_args__ = (
        db.CheckConstraint("book IN ('main', 'workbook')", name="ck_course_game_book"),
        db.CheckConstraint("page_number BETWEEN 1 AND 1000", name="ck_course_game_page"),
        db.CheckConstraint("pin_x BETWEEN 0 AND 10000 AND pin_y BETWEEN 0 AND 10000", name="ck_course_game_position"),
        db.Index("ix_course_game_book_page", "course_id", "book", "document_sha", "page_number"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    course_id = db.Column(db.BigInteger, db.ForeignKey("courses.id"), nullable=False)
    group_id = db.Column(db.BigInteger, db.ForeignKey("groups.id"), index=True)
    book = db.Column(db.String(16), nullable=False)
    document_sha = db.Column(db.String(64), nullable=False)
    page_number = db.Column(db.Integer, nullable=False)
    pin_x = db.Column(db.Integer, nullable=False)
    pin_y = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(120), nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    items = db.Column(db.JSON, nullable=False)
    published = db.Column(db.Boolean, nullable=False, default=False)
    version = db.Column(db.Integer, nullable=False, default=1)
