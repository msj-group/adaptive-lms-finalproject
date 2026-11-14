"""Personal markup belongs to one account and one exact PDF version."""
from datetime import datetime, timezone

from app.extensions import db


class BookAnnotation(db.Model):
    __tablename__ = "book_annotations"
    __table_args__ = (
        db.UniqueConstraint("user_id", "book_sha256", "page_number", name="uq_book_annotation_owner_page"),
        db.CheckConstraint("page_number BETWEEN 1 AND 1000", name="ck_book_annotation_page"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    book_sha256 = db.Column(db.String(64), nullable=False)
    page_number = db.Column(db.Integer, nullable=False)
    marks = db.Column(db.JSON, nullable=False, default=list)
    revision = db.Column(db.Integer, nullable=False, default=1)
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
