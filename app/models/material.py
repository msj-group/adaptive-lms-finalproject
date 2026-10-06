from app.models.code_types import CODE_COLLATION
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import AcademicStatus, MaterialKind


class Material(db.Model):
    """One ordered learning material inside a single Lesson (M12).

    A Material belongs **directly** to exactly one Lesson; Unit, Group,
    Course, Level and AcademicTerm are all reachable through
    ``material.lesson.unit.group``. Every active assigned Teacher of the
    owning Group is an equal collaborator -- there is no per-Material
    owner column.

    ``kind`` is one of ``rich_text`` / ``external_link`` / ``file`` and is
    **immutable after creation** (no route ever writes it, and the
    payload CHECK below would reject switching kind without also switching
    payload). Exactly one payload is present for each kind:

    - ``rich_text``     -> ``content_html`` set (server-sanitised via nh3),
      ``external_url`` NULL, ``uploaded_file_id`` NULL
    - ``external_link`` -> ``external_url`` set (HTTPS, validated),
      ``content_html`` NULL, ``uploaded_file_id`` NULL
    - ``file``          -> ``uploaded_file_id`` set, ``content_html`` NULL,
      ``external_url`` NULL

    Lifecycle reuses the shared ``AcademicStatus`` (``active`` /
    ``archived``). A new Material starts ``active``. Archiving retains the
    row **and** the physical file; reactivation appends it to the end of
    the active list. There is no hard delete and no cascade delete.

    ``display_order`` is server-owned, **positive** (``>= 1``), and scoped
    to the Lesson. Active-material order is deterministic
    (``display_order`` then ``id``); move-up / move-down operate only on
    active Materials and skip archived rows.

    ``search_keywords`` (M13) is an optional Teacher-authored, canonical
    comma-and-space separated string (or ``NULL``) used only to widen
    Student content search -- normalised by
    ``app.services.search_terms.normalize_search_keywords`` and rendered
    only as escaped plain text. It is editable for every kind, including
    ``file`` (whose bytes and ``kind`` stay immutable).

    ``title`` is unique within the Lesson, including archived Materials.
    ``creation_nonce`` is a random per-request value carried by the
    signed create token; it is ``UNIQUE`` so an ordinary or concurrent
    replay of the create request resolves to the single already-created
    Material instead of inserting a duplicate.
    """

    __tablename__ = "materials"
    __table_args__ = (
        db.UniqueConstraint("lesson_id", "title", name="uq_materials_lesson_title"),
        db.CheckConstraint("display_order >= 1", name="ck_materials_display_order_positive"),
        db.CheckConstraint(
            "kind IN ('rich_text', 'external_link', 'file')", name="ck_materials_kind_valid"
        ),
        db.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_materials_status_valid"
        ),
        db.CheckConstraint(
            "("
            "kind = 'rich_text' AND content_html IS NOT NULL "
            "AND external_url IS NULL AND uploaded_file_id IS NULL"
            ") OR ("
            "kind = 'external_link' AND external_url IS NOT NULL "
            "AND content_html IS NULL AND uploaded_file_id IS NULL"
            ") OR ("
            "kind = 'file' AND uploaded_file_id IS NOT NULL "
            "AND content_html IS NULL AND external_url IS NULL"
            ")",
            name="ck_materials_payload_matches_kind",
        ),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    lesson_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("lessons.id"),
        nullable=False,
        index=True,
    )
    title = db.Column(db.String(150), nullable=False)
    kind = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False)
    search_keywords = db.Column(db.String(500), nullable=True)
    content_html = db.Column(db.Text, nullable=True)
    external_url = db.Column(db.String(2048), nullable=True)
    uploaded_file_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("uploaded_files.id"),
        nullable=True,
        unique=True,
    )
    status = db.Column(
        db.String(32, collation=CODE_COLLATION), nullable=False, default=AcademicStatus.ACTIVE.value, index=True
    )
    display_order = db.Column(db.Integer, nullable=False, index=True)
    creation_nonce = db.Column(db.String(64), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    lesson = db.relationship("Lesson", back_populates="materials")
    uploaded_file = db.relationship("UploadedFile", back_populates="material")

    @validates("status")
    def validate_status(self, _key, value):
        if value not in {s.value for s in AcademicStatus}:
            raise ValueError(f"Invalid status: {value}")
        return value

    @validates("kind")
    def validate_kind(self, _key, value):
        if value not in {k.value for k in MaterialKind}:
            raise ValueError(f"Invalid material kind: {value}")
        return value
