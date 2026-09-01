import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import LessonStatus


class Lesson(db.Model):
    """One ordered teaching Lesson inside a single Unit (M11).

    A Lesson belongs **directly** to exactly one Unit. Group, Course,
    Level, AcademicTerm are all reachable through ``lesson.unit.group``,
    and every active assigned Teacher of that Group is an equal
    collaborator, so there is no ``group_id`` / ``course_id`` /
    ``level_id`` / ``academic_term_id`` / ``teacher_id`` / ``created_by``
    column -- the same single-source-of-truth reasoning already applied to
    Enrollment, GroupTeacherAssignment, Schedule, and Unit.

    Lifecycle is ``draft`` / ``published`` (the Lesson-specific
    :class:`LessonStatus`, never the academic active/archived enum). A new
    Lesson always starts ``draft`` with ``published_at`` NULL. Publishing
    sets ``published_at`` to the current UTC moment; unpublishing returns
    the Lesson to ``draft`` and clears ``published_at``; republishing sets
    a fresh timestamp. There is no hard delete and no archived Lesson
    state in M11. Editing a Lesson never changes ``status``,
    ``published_at``, or ``display_order``.

    ``display_order`` is server-owned (never a trusted client field) and
    shared by every Lesson in the Unit -- draft and published alike. A new
    Lesson appends after the Unit's current highest order; move-up /
    move-down swap the ``display_order`` of the nearest Lesson regardless
    of draft/published status. Gaps are acceptable; there is deliberately
    **no** database uniqueness constraint on ``display_order``.

    ``title`` is unique within a Unit -- including against draft Lessons --
    so an ordered Unit never has two same-named Lessons. The same title is
    fine in a different Unit. The DB ``UniqueConstraint`` is the final
    defense; the write path also checks it and catches ``IntegrityError``.

    Database invariants (final defense only):

    - ``display_order >= 0``;
    - ``status`` limited to ``draft`` / ``published``;
    - a ``draft`` Lesson has ``published_at`` NULL;
    - a ``published`` Lesson has ``published_at`` NOT NULL.
    """

    __tablename__ = "lessons"
    __table_args__ = (
        db.UniqueConstraint("unit_id", "title", name="uq_lessons_unit_title"),
        db.CheckConstraint(
            "display_order >= 0", name="ck_lessons_display_order_non_negative"
        ),
        db.CheckConstraint(
            "status IN ('draft', 'published')", name="ck_lessons_status_valid"
        ),
        db.CheckConstraint(
            "(status = 'draft' AND published_at IS NULL) "
            "OR (status = 'published' AND published_at IS NOT NULL)",
            name="ck_lessons_status_published_at_consistency",
        ),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    unit_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("units.id"),
        nullable=False,
        index=True,
    )
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    display_order = db.Column(db.Integer, nullable=False, default=0, index=True)
    status = db.Column(
        db.String(32), nullable=False, default=LessonStatus.DRAFT.value, index=True
    )
    published_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    unit = db.relationship("Unit", back_populates="lessons")
    materials = db.relationship("Material", back_populates="lesson")

    @validates("status")
    def validate_status(self, _key, value):
        if value not in {s.value for s in LessonStatus}:
            raise ValueError(f"Invalid status: {value}")
        return value
