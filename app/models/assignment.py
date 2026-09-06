import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import AssignmentStatus

#: The exact allowed ``status`` values, rendered once into the database
#: CHECK constraint below so the application-level ``@validates`` guard
#: and the schema can never drift apart (the same technique M14 uses for
#: ``notifications.kind``).
_STATUS_VALUES = tuple(status.value for status in AssignmentStatus)
_STATUS_CHECK_SQL = "status IN (" + ", ".join(f"'{value}'" for value in _STATUS_VALUES) + ")"


class Assignment(db.Model):
    """One Group-owned, time-gated Assignment (Phase 4 / M01).

    An Assignment belongs **directly** to exactly one Group. Course,
    Level and AcademicTerm are all reachable through ``assignment.group``
    and are therefore not duplicated here -- the same single-source-of-
    truth reasoning already applied to Enrollment,
    GroupTeacherAssignment, Schedule, Unit and Lesson. There is likewise
    no ``unit_id`` / ``lesson_id`` (an Assignment is Group work, not a
    child of one teaching Lesson) and no ``teacher_id`` / ``created_by``:
    every **active** assigned Teacher of the Group is an equal
    collaborator on its Assignments.

    **Time is UTC in the database, local only at the edges.**
    ``opens_at``, ``due_at`` and ``published_at`` are naive UTC values.
    They are entered and rendered in ``APP_TIMEZONE`` through the M09
    timezone utilities (``to_app_local`` outbound, ``from_app_local``
    inbound), and every page that shows one also shows the timezone
    label. All three comparisons -- visibility, ordering, and the derived
    states below -- are made against **one** injected reference moment
    per request, never against a wall clock read separately in each
    layer.

    Assignment times are deliberately **not** constrained to the
    AcademicTerm's date range. A deadline that falls after the formal end
    of a term is legitimate, and no such business rule is approved.

    **Derived states are never stored** -- they are computed from the
    reference moment:

    - *Scheduled* (Teacher-only): ``published`` but ``now < opens_at``;
    - *Open*: ``opens_at <= now < due_at``;
    - *Past due*: ``now >= due_at``.

    A published Assignment is **not** Student-visible before
    ``opens_at``, and **remains** visible at and after ``due_at``. M01
    has no submission route, so "Past due" is informational only.

    Lifecycle is ``draft`` / ``published`` (the Assignment-specific
    :class:`AssignmentStatus`, never the academic active/archived enum).
    A new Assignment always starts ``draft`` with ``published_at`` NULL.
    Publishing stamps the current UTC moment; unpublishing returns it to
    ``draft`` and clears ``published_at``; republishing stamps a fresh
    one. There is no archived state and no hard delete. Editing never
    changes ``status`` or ``published_at``, and a Group or ancestor
    lifecycle change never rewrites either -- archiving hides an
    Assignment from Students without touching the row.

    ``title`` is unique within a Group -- **including** against drafts --
    so one Group never carries two same-named Assignments. The same title
    is fine in another Group. The DB ``UniqueConstraint`` is the final
    defense; the write path also checks it and catches ``IntegrityError``.

    ``instructions`` is required plain text (never HTML, never
    ``|safe``), rendered with line breaks preserved by CSS. The column is
    unbounded ``Text``; the finite boundary that actually protects the
    request is the form's ``Length(max=10000)`` in
    ``app.blueprints.teacher.forms.AssignmentForm``.

    There is deliberately **no** ``display_order``: Assignment
    presentation is time-driven, and the internal ``id`` is used only as
    a deterministic SQL tie-break -- it is never exposed in a URL, a form
    value, or the rendered HTML.

    Database invariants (final defense only):

    - one title per Group, drafts included;
    - ``opens_at < due_at`` -- equal or reversed times are rejected;
    - ``status`` limited to ``draft`` / ``published``;
    - a ``draft`` Assignment has ``published_at`` NULL;
    - a ``published`` Assignment has ``published_at`` NOT NULL.

    Two secondary indexes serve the two M01 read shapes:

    - ``ix_assignments_group_due_id`` (``group_id``, ``due_at``, ``id``)
      -- the Teacher list, which is a single-Group equality on
      ``group_id`` ordered by deadline, so the ordering columns follow
      the equality column directly. It shows draft **and** published rows
      together, which is why ``status`` is deliberately absent: a column
      between the equality column and the sort columns is exactly the
      shape M14 measured resolving as ``Using filesort`` on
      ``notifications``.
    - ``ix_assignments_group_status_opens_due`` (``group_id``,
      ``status``, ``opens_at``, ``due_at``) -- the Student visibility
      **filter**: equality on ``group_id`` + ``status``, then the
      ``opens_at <= now`` range.

    **What the second index does not do.** It is not claimed to supply
    the Student list's or dashboard's ``due_at`` ordering. ``opens_at``
    is a *range* predicate, so columns after it cannot generally be
    assumed to provide ordering; the Student list spans every Group the
    Student is enrolled in rather than one; and the list's current/history
    ordering is a ``CASE`` expression, which needs a sort of its own.
    Those reads are expected to sort, and the index earns its place by
    narrowing what has to be sorted.

    Both indexes, and ``uq_assignments_group_title``, start with
    ``group_id``, so the ``group_id`` foreign key already has a usable
    leftmost prefix and **no** separate single-column index is declared
    for it.

    **No MySQL execution plan has been measured for this table.** Unlike
    M14's ``notifications`` indexes, these are a reasoned design pending
    an authorized real ``EXPLAIN``.
    """

    __tablename__ = "assignments"
    __table_args__ = (
        db.UniqueConstraint("group_id", "title", name="uq_assignments_group_title"),
        db.CheckConstraint("opens_at < due_at", name="ck_assignments_opens_before_due"),
        db.CheckConstraint(_STATUS_CHECK_SQL, name="ck_assignments_status_valid"),
        db.CheckConstraint(
            "(status = 'draft' AND published_at IS NULL) "
            "OR (status = 'published' AND published_at IS NOT NULL)",
            name="ck_assignments_status_published_at_consistency",
        ),
        db.Index("ix_assignments_group_due_id", "group_id", "due_at", "id"),
        db.Index(
            "ix_assignments_group_status_opens_due",
            "group_id",
            "status",
            "opens_at",
            "due_at",
        ),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    group_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("groups.id"),
        nullable=False,
    )
    title = db.Column(db.String(150), nullable=False)
    instructions = db.Column(db.Text, nullable=False)
    opens_at = db.Column(db.DateTime, nullable=False)
    due_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(
        db.String(32), nullable=False, default=AssignmentStatus.DRAFT.value
    )
    published_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    group = db.relationship("Group", back_populates="assignments")

    @validates("status")
    def validate_status(self, _key, value):
        if value not in set(_STATUS_VALUES):
            raise ValueError(f"Invalid status: {value}")
        return value
