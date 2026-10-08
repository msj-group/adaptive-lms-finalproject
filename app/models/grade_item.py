from app.models.code_types import CODE_COLLATION
import uuid
from decimal import Decimal

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import GradeSourceKind
from app.models.submission_feedback import whole_second_utc

#: The ``title`` column's own width, declared once so the column and the
#: form's ``Length`` validator cannot disagree.
GRADE_ITEM_TITLE_MAX_LENGTH = 150

#: The exact numeric shape of every points column in this milestone:
#: seven significant digits with exactly two after the point, so
#: 0.01 .. 99999.99.
#:
#: **Never a float.** ``FLOAT`` / ``DOUBLE`` cannot represent 0.1
#: exactly, so a term's points would silently stop adding up; a grade is
#: a statement about a person and must be exact.
#:
#: SQLAlchemy compiles this to ``NUMERIC(7, 2)``, which MySQL treats as
#: an exact synonym for ``DECIMAL(7, 2)`` -- the same storage and the
#: same exact arithmetic -- so on MySQL it is exact fixed point end to
#: end. On the SQLite test backend SQLAlchemy binds through a C double
#: and reads back through a ``"%.2f"`` formatter, so the round trip is
#: still exact for every value this column can hold (seven significant
#: decimal digits is far inside a double's ~15), and the CHECKs below are
#: enforced numerically there too.
#:
#: **No arithmetic is ever done in SQL on these columns**: every sum,
#: percentage and weighted total is computed in Python ``Decimal`` by the
#: one service in ``app/services/grade_calculations.py``, so the
#: backend's arithmetic is never part of a grade.
POINTS_PRECISION = 7
POINTS_SCALE = 2

#: The largest ``max_points`` the column can hold, declared once so the
#: form boundary, the write path and the CHECK cannot drift apart.
MAX_POINTS_CEILING = Decimal("99999.99")

#: The smallest legitimate ``max_points``. Zero is deliberately excluded:
#: an item worth no points contributes nothing but a division by zero to
#: every category percentage.
MIN_POINTS = Decimal("0.01")

_SOURCE_KIND_VALUES = tuple(kind.value for kind in GradeSourceKind)
_SOURCE_KIND_CHECK_SQL = "source_kind IN (" + ", ".join(
    f"'{value}'" for value in _SOURCE_KIND_VALUES
) + ")"

#: The exact-one-source rule, rendered once into the database CHECK below
#: so the application validation and the schema can never drift apart --
#: the same technique M01 uses for ``assignments.status`` and M12 for the
#: ``materials`` payload CHECK. Read it as: each source kind names
#: exactly which of the three foreign keys must be present, and every
#: other one must be NULL.
_SOURCE_LINK_CHECK_SQL = (
    "(source_kind = 'assignment' AND assignment_id IS NOT NULL"
    " AND quiz_id IS NULL AND speaking_activity_id IS NULL)"
    " OR (source_kind = 'quiz' AND quiz_id IS NOT NULL"
    " AND assignment_id IS NULL AND speaking_activity_id IS NULL)"
    " OR (source_kind = 'speaking' AND speaking_activity_id IS NOT NULL"
    " AND assignment_id IS NULL AND quiz_id IS NULL)"
    " OR (source_kind IN ('activity', 'manual') AND assignment_id IS NULL"
    " AND quiz_id IS NULL AND speaking_activity_id IS NULL)"
)


class GradeItem(db.Model):
    """One gradeable thing inside one
    :class:`~app.models.grade_category.GradeCategory` (Phase 4 / M08).

    **Ownership is the category, and through it the Group.** There is no
    ``group_id`` here: a GradeItem belongs to exactly one category, and
    that category belongs to exactly one Group, so storing the Group
    again would let the two disagree with nothing in the schema to
    prevent it -- the reasoning already applied to Lesson (which does not
    duplicate ``group_id`` from its Unit) and to every other nested row
    in this project. Every nested read joins through
    ``grade_items -> grade_categories -> groups`` and is constrained by
    the Group the route already proved the acting Teacher is assigned to.

    **The source link says what the grade is about; it never supplies the
    number.** ``source_kind`` is one of the five
    :class:`~app.models.enums.GradeSourceKind` members and decides which
    -- if any -- of the three real foreign keys is set:

    ===============  ======================================================
    ``source_kind``  required link
    ===============  ======================================================
    ``assignment``   exactly ``assignment_id``; other two NULL
    ``quiz``         exactly ``quiz_id``; other two NULL
    ``speaking``     exactly ``speaking_activity_id``; other two NULL
    ``activity``     all three NULL
    ``manual``       all three NULL
    ===============  ======================================================

    Three real, typed foreign keys rather than one generic
    ``source_type`` / ``source_id`` pair, deliberately: a polymorphic id
    is a reference the database cannot check, so a row could point at a
    Quiz that does not exist, or at a Quiz belonging to another Group,
    and nothing would notice. ``ck_grade_items_source_link`` is the final
    defense behind the same rule the write path applies, and it holds on
    MySQL and on the SQLite test backend identically.

    **A linked source must belong to the same Group as the category.**
    That is a cross-table condition no foreign key can express, so it is
    an application invariant proved against the **locked** rows before
    the insert and re-proved before release -- never assumed from the
    fact that a reference exists. The same rule that makes
    ``AttendanceSession.group_id`` and ``schedule_id`` agree.

    **Nothing is imported from the linked row.** A ``quiz`` GradeItem
    does not read :class:`~app.models.quiz_attempt.QuizAttempt` results,
    an ``assignment`` GradeItem does not read
    :class:`~app.models.submission.Submission`, and a ``speaking``
    GradeItem does not read
    :class:`~app.models.speaking_submission.SpeakingSubmission`. Quiz
    attempts already compute their own assessment result and that result
    stays where it is; a Teacher who wants it in the gradebook creates an
    item, looks at the attempt, and enters the number **deliberately**.
    There is no import button, no sync job and no automatic grade
    anywhere in M08, and equally no score column was added to any
    submission, attempt, recording, attendance or feedback table: this
    aggregate is the single source of truth for Teacher-entered grades.

    **Listening activities appear here as ``quiz``.** A Phase 4 / M05
    Listening activity *is* a Quiz carrying a ``listening_activities``
    extension row, exactly as a Speaking activity *is* an Assignment
    carrying a ``speaking_activities`` row. Giving it a fourth source
    kind would have meant two ways to name one row.

    **Draft versus released is ``released_at``, and nothing else.**
    ``NULL`` means draft; a value means released, and is never changed
    again -- a release replay returns the stored value untouched. There
    is deliberately no ``status`` enum, no ``is_released`` flag and no
    second state column that could disagree with the timestamp.

    While it is a **draft**, an actively assigned Teacher may change the
    item's ``title``, ``category_id`` (to another category of the same
    Group), ``source_kind``, source link and ``max_points``, and the
    Students see none of it. Once **released**:

    - every one of those structural fields is frozen permanently. A
      released item can never be moved to another category, to another
      Group, to another source, or onto a different roster;
    - its :class:`~app.models.grade_record.GradeRecord` scores and
      comments remain **correctable** by an actively assigned Teacher,
      and the Student sees the corrected result. A correction is not a
      structural change: it is the Teacher fixing a number they entered,
      which is exactly what an honest gradebook has to allow. What a
      correction may not do is *clear* a score -- see
      :class:`~app.models.grade_record.GradeRecord`;
    - there is **no unrelease, reopen, delete, archive, restore or
      duplicate route anywhere**, and no column for one. Immutability is
      enforced by the absence of write paths.

    **The roster is captured once, at creation.** Creating an item
    inserts one GradeRecord per eligible active Student of the Group in
    the same transaction, and the *set* of records never changes
    afterwards -- exactly the M07 attendance rule, for the same reason. A
    Group with no eligible active Student cannot have an item created at
    all, rather than producing one nobody can be graded on.

    **``version`` is the concurrency signal, not a revision history.** It
    starts at 1, increases by exactly one per meaningful draft edit, once
    more per *meaningful bulk score save* (whatever the number of records
    changed, and whether the item is a draft or already released), and
    once more at release. A save that changes nothing is a no-op:
    neither this column nor ``updated_at`` moves.

    Database invariants (final defense only):

    - ``ck_grade_items_source_kind`` -- the five
      :class:`~app.models.enums.GradeSourceKind` members as a literal
      ``IN`` list rather than a MySQL ``ENUM`` column, the convention
      every other closed-set column in this project uses, so the SQLite
      test backend enforces it identically and adding a member stays a
      visible schema change.
    - ``ck_grade_items_source_link`` -- the exact-one-source table above.
    - ``ck_grade_items_max_points_positive`` -- ``max_points > 0``.
    - ``ck_grade_items_version_positive`` (``version > 0``).

    Indexes -- four objects, each with a distinct read shape:

    - ``ix_grade_items_category_id`` (``category_id``, ``id``) -- every
      item list is a category equality (or an ``IN`` over one Group's
      categories) ordered by ``id``. It leads with ``category_id``, so it
      is also the index the ``category_id`` foreign key requires.
    - ``ix_grade_items_assignment_id`` / ``ix_grade_items_quiz_id`` /
      ``ix_grade_items_speaking_activity_id`` -- declared for the three
      **foreign keys**, which InnoDB requires and nothing above leads
      with. Each is also the shape of the "is this source already linked
      to a grade item?" lookup the item form uses.

    **No MySQL execution plan has been measured for this table.** As with
    every earlier index in this project, this is a reasoned design
    pending an authorized real ``EXPLAIN``.

    **No ORM relationship is declared in either direction**, for the same
    reason as on every other Phase 4 row: every read is an explicit join
    returning plain presentation dicts, and no ``cascade`` /
    ``delete-orphan`` configuration exists that could remove grade
    history. All four foreign keys are plain references with **no**
    ``ondelete`` and **no** ``onupdate``, so no Group, Assignment, Quiz,
    Speaking, account or academic lifecycle change can remove a grade.
    """

    __tablename__ = "grade_items"
    __table_args__ = (
        db.CheckConstraint(_SOURCE_KIND_CHECK_SQL, name="ck_grade_items_source_kind"),
        db.CheckConstraint(_SOURCE_LINK_CHECK_SQL, name="ck_grade_items_source_link"),
        db.CheckConstraint("max_points > 0", name="ck_grade_items_max_points_positive"),
        db.CheckConstraint("version > 0", name="ck_grade_items_version_positive"),
        db.Index("ix_grade_items_category_id", "category_id", "id"),
        db.Index("ix_grade_items_assignment_id", "assignment_id"),
        db.Index("ix_grade_items_quiz_id", "quiz_id"),
        db.Index("ix_grade_items_speaking_activity_id", "speaking_activity_id"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    category_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("grade_categories.id"),
        nullable=False,
    )
    title = db.Column(db.String(GRADE_ITEM_TITLE_MAX_LENGTH), nullable=False)
    source_kind = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False)
    #: Exact fixed-point points, never a float -- see
    #: :data:`POINTS_PRECISION`.
    max_points = db.Column(
        db.Numeric(POINTS_PRECISION, POINTS_SCALE, asdecimal=True), nullable=False
    )
    #: At most one of these three is ever set, decided by ``source_kind``
    #: and enforced by ``ck_grade_items_source_link``.
    assignment_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("assignments.id"),
        nullable=True,
    )
    quiz_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("quizzes.id"),
        nullable=True,
    )
    speaking_activity_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("speaking_activities.id"),
        nullable=True,
    )
    #: NULL while the item is a draft; one authoritative whole-second
    #: naive-UTC moment once released, and never changed again.
    released_at = db.Column(db.DateTime, nullable=True)
    #: 1 on creation, +1 per meaningful draft edit, +1 per meaningful
    #: bulk score save, +1 once at release.
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Defense in depth only; the write path always supplies its own
    #: post-lock whole-second moment and uses the SAME value for both
    #: columns on creation. Deliberately no ``onupdate`` hook -- a no-op
    #: save must leave both alone.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("source_kind")
    def validate_source_kind(self, _key, value):
        if value not in _SOURCE_KIND_VALUES:
            raise ValueError(f"Invalid grade source kind: {value}")
        return value

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("Grade item version must be a positive integer")
        return value

    @validates("max_points")
    def validate_max_points(self, _key, value):
        """Reject a float outright and require an exact, positive,
        two-decimal ``Decimal`` inside the column's range.

        A ``float`` is refused rather than converted: accepting one here
        would be the single place a binary value could enter the
        gradebook, and a silent ``Decimal(0.1)`` is
        ``0.1000000000000000055511151231257827``, not ``0.10``.
        """
        if isinstance(value, float):
            raise ValueError("Grade item max points must not be a binary float")
        if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
            raise ValueError("Grade item max points must be an exact decimal value")
        points = value if isinstance(value, Decimal) else Decimal(str(value))
        if not points.is_finite():
            raise ValueError("Grade item max points must be a finite decimal value")
        if points < MIN_POINTS or points > MAX_POINTS_CEILING:
            raise ValueError(
                f"Grade item max points must be between {MIN_POINTS} and "
                f"{MAX_POINTS_CEILING}"
            )
        if -points.as_tuple().exponent > POINTS_SCALE:
            raise ValueError(
                f"Grade item max points must have at most {POINTS_SCALE} decimal places"
            )
        return points

    def is_released(self):
        """Whether this item's grades are visible to their Students.

        The one place the draft / released question is answered, so no
        caller can invent a second rule for it.
        """
        return self.released_at is not None
