import uuid
from decimal import Decimal

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.grade_item import (
    MAX_POINTS_CEILING,
    POINTS_PRECISION,
    POINTS_SCALE,
)
from app.models.submission_feedback import whole_second_utc

#: The finite input boundary for the unbounded ``comment`` Text column
#: (Phase 4 / M08). Mirrored by
#: ``app.blueprints.teacher.grade_forms.COMMENT_MAX`` -- the request
#: boundary is what actually rejects an oversized comment; this constant
#: is declared beside the column so the two can be read together and
#: cannot drift silently. Same arrangement as M02's
#: ``ANSWER_MAX_LENGTH``, M03's ``FEEDBACK_MAX_LENGTH`` and M07's
#: ``ATTENDANCE_NOTE_MAX_LENGTH``.
GRADE_COMMENT_MAX_LENGTH = 1000


class GradeRecord(db.Model):
    """One captured Student's score and comment for one
    :class:`~app.models.grade_item.GradeItem` (Phase 4 / M08).

    **The roster is captured once, at item creation, and then frozen.**
    Every Student who was an eligible active member of the Group at that
    moment -- active Enrollment, ``student`` role, active account,
    exactly this Group -- gets one row, inserted in the same transaction
    as the item itself, with a NULL score and no comment. After that the
    *set* of rows never changes: a Student who enrolls later gets **no**
    row on an existing item, and a Student who withdraws, is suspended,
    is moved, or whose Group or academic ancestor is archived **keeps**
    theirs. That is the whole point -- a grade record is a statement
    about who was being graded on one piece of work, and re-deriving it
    later from today's membership would rewrite history. A Group with no
    eligible active Student therefore cannot have a GradeItem created at
    all, rather than producing an empty one.

    This is exactly the M07 attendance rule, reused rather than
    reinvented, and the eligibility definition itself is literally the
    shared one in ``app/services/group_memberships.py`` -- there is no
    second answer to "who is in this Group?" anywhere in this project.

    **Ownership is the item + the Student, and nothing else.** The
    category, the Group, the Course, the Level, the AcademicTerm and the
    Teachers are all reachable through
    ``GradeRecord -> GradeItem -> GradeCategory -> Group -> ...``, so
    none of them is duplicated here.

    ``student_id`` is a plain foreign key into the shared ``users``
    table. As everywhere else in this project, that proves the row
    **exists** -- never that it is still a Student, still active, or
    still enrolled. The eligibility rules above are enforced by the
    application at capture time against the **locked** rows, and every
    presentation read re-checks ``role`` rather than trusting the
    reference.

    **The score is exact fixed-point, never a float**, and is nullable
    with exactly one meaning: *not graded yet*. See
    :data:`~app.models.grade_item.POINTS_PRECISION` for why
    ``DECIMAL(7, 2)`` rather than ``FLOAT``, and note that no arithmetic
    is ever done in SQL on this column -- every sum, percentage and
    weighted total is computed in Python ``Decimal`` by the one service
    in ``app/services/grade_calculations.py``.

    Two rules bound it, and each is enforced in a different place because
    each can be enforced only there:

    - ``score IS NULL OR score >= 0`` is a **database** CHECK. It is a
      single-row condition, so the schema can be the final defense.
    - ``score <= the item's max_points`` is a **cross-row** condition
      between this table and ``grade_items``, which no portable CHECK can
      express. It is proved by the application against the **locked**
      GradeItem before every write, never assumed.

    **A released item's score may be corrected, but never cleared.**
    While the item is a draft the score may be set, changed and blanked
    freely. Once ``GradeItem.released_at`` is set, an actively assigned
    Teacher may still correct a number they got wrong -- an honest
    gradebook has to allow that, and the Student sees the corrected
    result immediately -- but may not set it back to NULL. Release
    guaranteed that every captured record carried a real score, and a
    Student's already-published category percentage and weighted overall
    grade are computed from exactly those scores; clearing one would
    silently withdraw a result that had already been shown, with no
    record that it ever existed. The write path rejects it with a clear
    domain error.

    **``graded_by_id`` / ``graded_at`` name the last Teacher to change
    the score or the comment, and when.** Both are NULL on a freshly
    captured record and are set together, to the same authoritative
    post-lock whole-second moment, by the first meaningful write;
    ``ck_grade_records_graded_consistency`` makes "both NULL or both
    set" a schema rule rather than a convention. This is attribution for
    the staff surfaces, **not an audit trail**: only the most recent
    change is kept, no row is stored per change, and there is deliberately
    no history table -- M08 was not asked for one and a half-built one
    would be worse than none.

    **The comment is the Teacher's message to that one Student.** It is
    optional plain text, never HTML and never Markdown, rendered with
    line breaks preserved by CSS and never with ``|safe``. It is visible
    to Teachers assigned to the Group, and to **the Student it is about
    -- but only once the item is released**. It is deliberately *not*
    shown to an Administrator on any report route: the Administrator
    reports in M08 are configuration and availability summaries, and a
    private message between a Teacher and one Student is not a reporting
    field. No other Student ever sees it, on any route, in any page,
    token or URL. The column is unbounded ``Text`` (65,535 bytes on
    MySQL, comfortably above 1,000 utf8mb4 characters); the finite
    boundary that actually protects the request is the 1,000-character
    check applied at the request boundary
    (:data:`GRADE_COMMENT_MAX_LENGTH`) to the **raw** submitted value,
    before stripping.

    **``version`` is the concurrency signal, not a revision history.** It
    starts at 1 and increases by exactly one per *meaningful* update -- a
    change to ``score``, to ``comment``, or to both counts once. A save
    that leaves both identical is a no-op: neither this column nor
    ``updated_at`` nor ``graded_at`` moves, because re-saving the same
    number is not a change. The signed scores token carries every
    captured record's ``public_id`` and ``version``, which is what turns
    a losing co-teacher race into an explicit "reload and review"
    rejection rather than a silent overwrite -- including an A -> B -> A
    round trip and two saves inside the same whole second, neither of
    which a timestamp comparison could catch.

    **There is no hard delete, no Student self-edit and no grade appeal
    workflow** anywhere in M08, and no column for one.

    Database invariants (final defense only):

    - ``uq_grade_records_item_student`` -- exactly one record per item
      and Student. It is also the exact shape of every per-Student lookup
      within an item, and the leftmost prefix the ``grade_item_id``
      foreign key needs, so no separate single-column index is declared
      for it.
    - ``ck_grade_records_score_non_negative`` -- ``score IS NULL OR
      score >= 0``. Supported by MySQL 8 and by the SQLite test backend
      alike.
    - ``ck_grade_records_version_positive`` (``version > 0``).
    - ``ck_grade_records_graded_consistency`` -- ``graded_by_id`` and
      ``graded_at`` are both NULL or both set.

    Indexes -- three objects, each with a distinct read shape:

    - ``uq_grade_records_item_student`` (``grade_item_id``,
      ``student_id``) -- see above.
    - ``ix_grade_records_student_item`` (``student_id``,
      ``grade_item_id``) -- the Student's own grade history, which is an
      equality on ``student_id`` joined to the item. It leads with
      ``student_id``, so it is also the index that foreign key requires.
    - ``ix_grade_records_graded_by_id`` -- declared for the
      ``graded_by_id`` **foreign key**, which InnoDB requires and nothing
      else leads with.

    **No MySQL execution plan has been measured for this table.** As with
    every earlier index in this project, this is a reasoned design
    pending an authorized real ``EXPLAIN``.

    **No ORM relationship is declared in either direction**, for the same
    reason as on :class:`~app.models.grade_item.GradeItem`: every read is
    an explicit join returning plain presentation dicts, and no
    ``cascade`` / ``delete-orphan`` configuration exists that could
    remove grade history. All three foreign keys are plain references
    with **no** ``ondelete`` and **no** ``onupdate``.
    """

    __tablename__ = "grade_records"
    __table_args__ = (
        db.ForeignKeyConstraint(['enrollment_id', 'student_id'], ['enrollments.id', 'enrollments.student_id'], name='fk_grade_record_episode_student'),
        db.UniqueConstraint(
            "grade_item_id", "student_id", name="uq_grade_records_item_student"
        ),
        db.CheckConstraint(
            "score IS NULL OR score >= 0", name="ck_grade_records_score_non_negative"
        ),
        db.CheckConstraint("version > 0", name="ck_grade_records_version_positive"),
        db.CheckConstraint(
            "(graded_by_id IS NULL AND graded_at IS NULL)"
            " OR (graded_by_id IS NOT NULL AND graded_at IS NOT NULL)",
            name="ck_grade_records_graded_consistency",
        ),
        db.Index("ix_grade_records_student_item", "student_id", "grade_item_id"),
        db.Index("ix_grade_records_graded_by_id", "graded_by_id"),
    )

    enrollment_id = db.Column(db.BigInteger, db.ForeignKey('enrollments.id', name='fk_grade_record_episode'), nullable=False, index=True)
    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    grade_item_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("grade_items.id"),
        nullable=False,
    )
    student_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    #: NULL means "not graded yet" and nothing else. Exact fixed-point,
    #: never a float.
    score = db.Column(
        db.Numeric(POINTS_PRECISION, POINTS_SCALE, asdecimal=True), nullable=True
    )
    comment = db.Column(db.Text, nullable=True)
    #: The Teacher who last changed the score or the comment, and when.
    #: Both NULL until the first meaningful write; both set together
    #: afterwards.
    graded_by_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    graded_at = db.Column(db.DateTime, nullable=True)
    #: 1 on creation, +1 per meaningful update. See the class docstring --
    #: the stale-form signal, not a revision history.
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Defense in depth only; the write path always supplies its own
    #: post-lock whole-second moment, and uses the SAME value for both
    #: columns on creation. Deliberately no ``onupdate`` hook -- a no-op
    #: save must leave both alone.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("Grade record version must be a positive integer")
        return value

    @validates("score")
    def validate_score(self, _key, value):
        """Reject a float outright and require an exact, non-negative,
        two-decimal ``Decimal`` inside the column's range, or ``None``.

        ``None`` is legitimate: it is the "not graded yet" state. A
        ``float`` is refused rather than converted, for the reason
        :meth:`~app.models.grade_item.GradeItem.validate_max_points`
        gives -- this and that validator are the only two doors a number
        can enter the gradebook through, and neither opens for a binary
        value.

        The *upper* bound checked here is the column's, not the item's:
        ``score <= max_points`` is a cross-row rule the write path proves
        against the locked GradeItem.
        """
        if value is None:
            return None
        if isinstance(value, float):
            raise ValueError("Grade record score must not be a binary float")
        if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
            raise ValueError("Grade record score must be an exact decimal value")
        score = value if isinstance(value, Decimal) else Decimal(str(value))
        if not score.is_finite():
            raise ValueError("Grade record score must be a finite decimal value")
        if score < 0 or score > MAX_POINTS_CEILING:
            raise ValueError(
                f"Grade record score must be between 0 and {MAX_POINTS_CEILING}"
            )
        if -score.as_tuple().exponent > POINTS_SCALE:
            raise ValueError(
                f"Grade record score must have at most {POINTS_SCALE} decimal places"
            )
        return score

    def is_graded(self):
        """Whether this record carries a real score.

        The one place the "graded yet?" question is answered, so no
        caller can invent a second rule for it.
        """
        return self.score is not None
