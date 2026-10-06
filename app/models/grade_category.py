import uuid

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.submission_feedback import whole_second_utc

#: The ``title`` column's own width, declared once so the column and the
#: form's ``Length`` validator cannot disagree -- the same arrangement
#: M04A uses for ``QUIZ_TITLE_MAX_LENGTH``.
GRADE_CATEGORY_TITLE_MAX_LENGTH = 150

#: 100.00% expressed in basis points. **Every weight in this project is an
#: integer number of basis points, never a float and never a percentage
#: stored as a decimal**: 10,000 is 100.00%, 2,500 is 25.00%, 1 is 0.01%.
#:
#: The reason is exactness. A weighted grade is a statement about a
#: Student's term, and ``0.1 + 0.2 != 0.3`` in binary floating point --
#: a term's weights would silently fail to add up to 100% on some
#: machines and add up on others. An integer count of basis points adds
#: up exactly, everywhere, and comparing a Group's total against
#: :data:`BASIS_POINTS_TOTAL` is an exact integer comparison rather than
#: a tolerance.
#:
#: 0.01% is the finest weight the owner approved. There is deliberately
#: no finer unit and no configurable precision.
BASIS_POINTS_TOTAL = 10000

#: The smallest weight a category may carry. Zero is deliberately **not**
#: allowed: a category worth nothing is not a configuration, it is a
#: category that should not exist, and allowing it would let a Group pass
#: the "every active category has at least one released item" rule with a
#: category that can never affect anybody's grade.
MIN_CATEGORY_BASIS_POINTS = 1


class GradeCategory(db.Model):
    """One weighted section of one Group's gradebook (Phase 4 / M08).

    **Ownership is the Group, and nothing else.** A GradeCategory belongs
    directly to exactly one Group. Course, Level and AcademicTerm are all
    reachable through ``category.group`` and are therefore not duplicated
    here -- the same single-source-of-truth reasoning already applied to
    Enrollment, GroupTeacherAssignment, Schedule, Unit, Lesson,
    Assignment, Quiz and AttendanceSession. There is likewise no
    ``teacher_id`` / ``created_by``: every **active** assigned Teacher of
    the Group is an equal collaborator on its gradebook, exactly as they
    already are on its Assignments, Quizzes, Listening activities,
    Speaking activities and attendance.

    **The weight is an integer count of basis points, never a float.**
    ``weight_basis_points`` is 1..:data:`BASIS_POINTS_TOTAL` inclusive,
    where 10,000 means 100.00%. See :data:`BASIS_POINTS_TOTAL` for why
    this is not a percentage column, not a ``FLOAT`` and not a
    ``DECIMAL``: a Group's total has to be compared against 100% exactly,
    and only integers do that on every machine.

    **A Group's total may be below 100% and may never exceed it.** The
    per-row CHECK below can only bound one row; "the *sum* of this
    Group's categories is at most 10,000" is a cross-row condition no
    CHECK can express, so it is an application invariant proved against
    the **locked** category rows before every category insert and update
    (``app/services/grade_transactions.py``). A partially configured
    gradebook -- 60% across two categories while a Teacher is still
    setting it up -- is a legitimate, deliberate state: it simply cannot
    produce a weighted overall grade yet, and the pages say so rather
    than showing a misleading partial total.

    **A category freezes once one of its GradeItems is released.**
    ``title`` and ``weight_basis_points`` stop being editable the moment
    any :class:`~app.models.grade_item.GradeItem` in this category has a
    non-NULL ``released_at``. Students have already been shown a result
    computed against that weight and under that name; changing either
    afterwards would retroactively rewrite what somebody's released grade
    meant, with nothing in the schema to notice. The write path returns a
    clear domain error instead, and the Teacher creates a new category if
    the plan really changed. Adding a **draft** item to a frozen category
    stays allowed -- it changes no released result.

    **There is no archived / inactive category state, and no hard
    delete.** M08 adds no delete endpoint for a category, no ``status``
    column and no ``deleted_at``: "the Group's active categories" is
    therefore exactly "the Group's categories", one answer rather than
    two that could disagree. A category a Teacher no longer wants is left
    where it is, because its weight is part of how already-released
    grades were computed.

    **``version`` is the concurrency signal, not a revision history.** It
    starts at 1 and increases by exactly one per *meaningful* edit, so a
    signed co-teacher form token can detect that the row changed under it
    -- including an A -> B -> A round trip and two edits landing inside
    the same whole second, neither of which a timestamp comparison could
    catch. A save whose normalized title **and** weight equal the stored
    ones is a no-op: neither this column nor ``updated_at`` moves. No row
    is kept per version.

    Database invariants (final defense only):

    - ``uq_grade_categories_group_title`` -- one category title per
      Group. The same title in another Group is fine. Comparison is left
      to the database, so the effective case- and accent-sensitivity is
      the column's collation (``utf8mb4_0900_ai_ci`` on MySQL, binary on
      the SQLite test backend) -- the difference is inherited from this
      project's existing title rules (``uq_units_group_title``,
      ``uq_assignments_group_title``, ``uq_quizzes_group_title``) rather
      than introduced here, and **it has not been measured against real
      MySQL in this Part**. The write path additionally checks for a
      duplicate against the locked rows and catches the resulting
      ``IntegrityError``.
    - ``ck_grade_categories_weight_range`` -- the per-row bound
      ``1 <= weight_basis_points <= 10000``. It cannot bound the Group's
      sum; see above.
    - ``ck_grade_categories_version_positive`` (``version > 0``).

    Indexes -- one object, because one shape covers every read:

    - ``uq_grade_categories_group_title`` (``group_id``, ``title``) is
      the uniqueness invariant, the exact shape of the duplicate-title
      lookup, and the leftmost prefix the ``group_id`` foreign key
      needs, so **no** separate single-column index is declared for it.
      Every category list is a single-Group equality ordered by
      ``title, id``, which this index leads.

    **No MySQL execution plan has been measured for this table.** As with
    every earlier index in this project, this is a reasoned design
    pending an authorized real ``EXPLAIN``.

    **No ORM relationship is declared in either direction.** Not an
    oversight, and the same choice M02 / M03 / M06 / M07 made: every read
    goes through explicit joins in ``app/services/grade_queries.py`` and
    returns plain presentation dicts, so rendering a gradebook can never
    trigger a lazy load or an ORM-driven authorization decision, and
    there is no ``cascade`` / ``delete-orphan`` configuration anywhere
    that could remove grade history when a Group or an academic ancestor
    is touched. The ``group_id`` foreign key is a plain reference with
    **no** ``ondelete`` and **no** ``onupdate``.
    """

    __tablename__ = "grade_categories"
    __table_args__ = (
        db.UniqueConstraint(
            "group_id", "title", name="uq_grade_categories_group_title"
        ),
        db.CheckConstraint(
            f"weight_basis_points >= {MIN_CATEGORY_BASIS_POINTS}"
            f" AND weight_basis_points <= {BASIS_POINTS_TOTAL}",
            name="ck_grade_categories_weight_range",
        ),
        db.CheckConstraint("version > 0", name="ck_grade_categories_version_positive"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    group_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("groups.id"),
        nullable=False,
    )
    title = db.Column(db.String(GRADE_CATEGORY_TITLE_MAX_LENGTH), nullable=False)
    #: 10,000 = 100.00%. An integer, never a float -- see
    #: :data:`BASIS_POINTS_TOTAL`.
    weight_basis_points = db.Column(db.Integer, nullable=False)
    #: 1 on creation, +1 per meaningful edit. See the class docstring --
    #: the stale-form signal, not a revision history.
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Defense in depth only; the write path always supplies its own
    #: post-lock whole-second moment and uses the SAME value for both
    #: columns on creation. There is deliberately no ``onupdate`` hook:
    #: an implicit one would bypass that truncation and would fire on a
    #: no-op save, which must leave every timestamp alone.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("weight_basis_points")
    def validate_weight(self, _key, value):
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < MIN_CATEGORY_BASIS_POINTS
            or value > BASIS_POINTS_TOTAL
        ):
            raise ValueError(
                "Grade category weight must be an integer number of basis points "
                f"between {MIN_CATEGORY_BASIS_POINTS} and {BASIS_POINTS_TOTAL}"
            )
        return value

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("Grade category version must be a positive integer")
        return value
