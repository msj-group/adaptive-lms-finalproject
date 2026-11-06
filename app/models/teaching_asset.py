"""Course-owned private teaching resources and explicit material-copy lineage."""
import uuid
from datetime import datetime, timezone

from app.extensions import db


class GroupMaterialSource(db.Model):
    __tablename__ = "group_material_sources"
    material_id = db.Column(db.BigInteger, db.ForeignKey("materials.id"), primary_key=True)
    course_material_id = db.Column(db.BigInteger, db.ForeignKey("materials.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class CourseContentImport(db.Model):
    __tablename__ = "course_content_imports"
    course_id = db.Column(db.BigInteger, db.ForeignKey("courses.id"), primary_key=True)
    manifest_sha256 = db.Column(db.String(64), nullable=False)
    imported_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class TeachingAsset(db.Model):
    __tablename__ = "teaching_assets"
    __table_args__ = (
        db.UniqueConstraint("id", "course_id", name="uq_teaching_assets_id_course"),
        db.CheckConstraint("display_order >= 1", name="ck_teaching_assets_order"),
        db.CheckConstraint("page_count IS NULL OR page_count BETWEEN 1 AND 1000", name="ck_teaching_assets_pages"),
    )
    id = db.Column(db.BigInteger, primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    course_id = db.Column(db.BigInteger, db.ForeignKey("courses.id"), nullable=False, index=True)
    uploaded_file_id = db.Column(db.BigInteger, db.ForeignKey("uploaded_files.id"), nullable=False, unique=True)
    title = db.Column(db.String(150), nullable=False)
    purpose = db.Column(db.String(32), nullable=False)
    page_count = db.Column(db.Integer)
    display_order = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    uploaded_file = db.relationship("UploadedFile")


class TeachingAssetPin(db.Model):
    __tablename__ = "teaching_asset_pins"
    __table_args__ = (
        db.ForeignKeyConstraint(["asset_id", "course_id"], ["teaching_assets.id", "teaching_assets.course_id"], name="fk_teaching_pin_asset_course"),
        db.ForeignKeyConstraint(["document_asset_id", "course_id"], ["teaching_assets.id", "teaching_assets.course_id"], name="fk_teaching_pin_document_course"),
        db.ForeignKeyConstraint(["course_id", "book_kind"], ["course_reference_books.course_id", "course_reference_books.kind"], name="fk_teaching_pin_book_course"),
        db.CheckConstraint("(book_kind IS NULL) <> (document_asset_id IS NULL)", name="ck_teaching_pin_one_document"),
        db.CheckConstraint("page_number BETWEEN 1 AND 1000 AND pin_x BETWEEN 0 AND 10000 AND pin_y BETWEEN 0 AND 10000", name="ck_teaching_pin_position"),
        db.UniqueConstraint("asset_id", "book_kind", "page_number", "pin_x", "pin_y", name="uq_teaching_pin_book_position"),
        db.UniqueConstraint("asset_id", "document_asset_id", "page_number", "pin_x", "pin_y", name="uq_teaching_pin_asset_position"),
    )
    id = db.Column(db.BigInteger, primary_key=True)
    course_id = db.Column(db.BigInteger, nullable=False)
    asset_id = db.Column(db.BigInteger, nullable=False)
    book_kind = db.Column(db.String(20, collation="utf8mb4_0900_bin"))
    document_asset_id = db.Column(db.BigInteger)
    page_number = db.Column(db.Integer, nullable=False)
    pin_x = db.Column(db.Integer, nullable=False)
    pin_y = db.Column(db.Integer, nullable=False)
