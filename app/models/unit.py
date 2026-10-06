import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import AcademicStatus


class Unit(db.Model):
    """A structural teaching container inside one Group (M10).

    A Unit belongs **directly** to exactly one Group -- Course, Level and
    AcademicTerm are all reachable through ``unit.group``, so none of them
    is duplicated here (the same single-source-of-truth reasoning applied
    to Enrollment, GroupTeacherAssignment, and Schedule). There is no
    ``teacher_id`` / ``created_by``: every active assigned Teacher of the
    Group is an equal collaborator on its Units.

    Lifecycle is ``active`` / ``archived`` (the shared ``AcademicStatus``)
    -- a Unit is a container, so it has no draft/published state; that
    belongs to Lesson (M11). There is no hard delete; reactivation reuses
    the same row. Status is owned solely by the dedicated toggle route --
    the create/edit form never carries it.

    ``display_order`` is server-owned (never a trusted client field):
    new and reactivated Units append after the Group's current highest
    order, and move-up / move-down swap the ``display_order`` of adjacent
    **active** Units. Gaps are acceptable; there is deliberately **no**
    database uniqueness constraint on ``display_order``.

    ``title`` is unique within a Group -- including against archived Units
    -- so a teaching order never has two same-named Units. The same title
    is fine in a different Group. The DB ``UniqueConstraint`` is the final
    defense; the write path also checks it and catches ``IntegrityError``.

    ``search_keywords`` (M13) is an optional Teacher-authored, canonical
    comma-and-space separated string (or ``NULL``) used only to widen
    Student content search. It is normalised/validated by
    ``app.services.search_terms.normalize_search_keywords`` at the form
    layer and rendered only as escaped plain text -- never HTML.
    """

    __tablename__ = "units"
    __table_args__ = (
        db.UniqueConstraint("group_id", "title", name="uq_units_group_title"),
        db.CheckConstraint("display_order >= 0", name="ck_units_display_order_non_negative"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    group_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("groups.id"),
        nullable=False,
        index=True,
    )
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    search_keywords = db.Column(db.String(500), nullable=True)
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
    lessons = db.relationship("Lesson", back_populates="unit")

    @validates("status")
    def validate_status(self, _key, value):
        if value not in {s.value for s in AcademicStatus}:
            raise ValueError(f"Invalid status: {value}")
        return value
