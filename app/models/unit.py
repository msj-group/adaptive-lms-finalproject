import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.hybrid import hybrid_method
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import AcademicStatus


class Unit(db.Model):
    """An ordered Unit owned by one Course or by one legacy Group.

    Shared Units are administered on the Course and read through Groups
    without an independent curriculum copy. Group copies and legacy Units
    retain their Group ownership. Legacy Units retain their original
    and identifiers so existing material links and progress stay valid.
    """

    __tablename__ = "units"
    __table_args__ = (
        db.UniqueConstraint("group_id", "title", name="uq_units_group_title"),
        db.UniqueConstraint("course_id", "title", name="uq_units_course_title"),
        db.CheckConstraint(
            "(group_id IS NULL) <> (course_id IS NULL)", name="ck_units_exactly_one_owner"
        ),
        db.CheckConstraint("display_order >= 0", name="ck_units_display_order_non_negative"),
        db.CheckConstraint(
            "(page_start IS NULL AND page_end IS NULL) OR "
            "(page_start IS NOT NULL AND page_end IS NOT NULL "
            "AND page_start >= 1 AND page_end >= page_start)", name="ck_units_page_range"
        ),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    group_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("groups.id"),
        nullable=True,
        index=True,
    )
    course_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("courses.id", name="fk_units_course"),
        nullable=True,
    )
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    search_keywords = db.Column(db.String(500), nullable=True)
    page_start = db.Column(db.Integer())
    page_end = db.Column(db.Integer())
    display_order = db.Column(db.Integer, nullable=False, default=0, index=True)
    status = db.Column(db.String(32), nullable=False, default=AcademicStatus.ACTIVE.value, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    group = db.relationship("Group", back_populates="units")
    course = db.relationship("Course")
    lessons = db.relationship("Lesson", back_populates="unit")

    @hybrid_method
    def in_group(self, group_id, course_id):
        from app.models.group_curriculum import GroupCurriculum

        return self.group_id == group_id or (
            self.course_id == course_id and db.session.get(GroupCurriculum, group_id) is None
        )

    @in_group.expression
    def in_group(cls, group_id, course_id):
        from app.models.group_curriculum import GroupCurriculum

        copied = exists(select(1).where(GroupCurriculum.group_id == group_id)).correlate_except(GroupCurriculum)
        return or_(cls.group_id == group_id, and_(cls.course_id == course_id, ~copied))

    @validates("status")
    def validate_status(self, _key, value):
        if value not in {s.value for s in AcademicStatus}:
            raise ValueError(f"Invalid status: {value}")
        return value
