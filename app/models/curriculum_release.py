"""Per-group visibility decisions for lessons and individual materials."""
from app.extensions import db


class LessonRelease(db.Model):
    __tablename__ = "curriculum_lesson_releases"

    group_id = db.Column(db.BigInteger, db.ForeignKey("groups.id"), primary_key=True)
    lesson_id = db.Column(db.BigInteger, db.ForeignKey("lessons.id"), primary_key=True)
    opens_at = db.Column(db.DateTime, nullable=True)
    notice_key = db.Column(db.String(36), nullable=True)


class MaterialRelease(db.Model):
    __tablename__ = "curriculum_material_releases"

    group_id = db.Column(db.BigInteger, db.ForeignKey("groups.id"), primary_key=True)
    material_id = db.Column(db.BigInteger, db.ForeignKey("materials.id"), primary_key=True)
    opens_at = db.Column(db.DateTime, nullable=True)
    notice_key = db.Column(db.String(36), nullable=True)
