import uuid

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import QuizStatus
from app.models.submission_feedback import whole_second_utc

#: The finite input boundary for the unbounded ``instructions`` Text
#: column (Phase 4 / M04A). Mirrored by
#: ``app.blueprints.teacher.quiz_forms.QuizForm.INSTRUCTIONS_MAX`` -- the
#: form is what actually rejects an oversized request; this constant is
#: declared beside the column so the two can be read together and cannot
#: drift silently. Same arrangement as M02's ``ANSWER_MAX_LENGTH`` and
#: M03's ``FEEDBACK_MAX_LENGTH``.
QUIZ_INSTRUCTIONS_MAX_LENGTH = 10000

#: The ``title`` column's own width, declared once so the column and the
#: form's ``Length`` validator cannot disagree.
QUIZ_TITLE_MAX_LENGTH = 150

#: The approved bounds of the optional per-attempt time limit, in whole
#: minutes (Phase 4 / M04D). 1 is the smallest limit that can mean
#: anything; 300 (five hours) is a deliberate ceiling rather than an
#: unbounded integer, so a typo cannot create an attempt that never ends.
#: Enforced by the form, by the write path, and by
#: ``ck_quizzes_time_limit_range`` as the final defense.
MIN_TIME_LIMIT_MINUTES = 1
MAX_TIME_LIMIT_MINUTES = 300

#: The approved bounds of ``attempt_limit``. 1 is the default -- one
#: attempt is the ordinary case -- and 10 is the ceiling. There is no
#: "unlimited" value: an unbounded attempt count is a decision nobody
#: made, and NULL would mean two different things at once.
MIN_ATTEMPT_LIMIT = 1
MAX_ATTEMPT_LIMIT = 10

#: The most questions one Quiz may hold. A row-count rule, so it is
#: enforced against the locked aggregate when a question is created and
#: again when the Quiz is published -- never by a CHECK, which cannot
#: count rows. A Quiz found holding more is refused safely and **never**
#: silently truncated.
MAX_QUIZ_QUESTIONS = 100

#: The exact allowed ``status`` values, rendered once into the database
#: CHECK below so the application ``@validates`` guard and the schema can
#: never drift apart -- the same technique M01 uses for
#: ``assignments.status``.
_STATUS_VALUES = tuple(status.value for status in QuizStatus)
_STATUS_CHECK_SQL = "status IN (" + ", ".join(
    f"'{value}'" for value in _STATUS_VALUES
) + ")"


class Quiz(db.Model):
    """One Group-owned **draft** quiz (Phase 4 / M04A).

    **Superseded in part by Phase 4 / M04D.** In M04A every Quiz was a
    draft *by construction*: there was no ``status`` column, no
    ``published_at``, no availability window and no timer, because nothing
    could publish anything. M04D adds the approved lifecycle -- ``status``,
    ``opens_at`` / ``closes_at``, ``time_limit_minutes``,
    ``attempt_limit`` and ``published_at`` -- so a Quiz is now a draft
    because its ``status`` says so, and it becomes Student-visible only
    once a Teacher publishes it and its opening moment arrives.

    Everything else M04A decided is unchanged: a **draft** Quiz is still
    Teacher-only working material that no Student route, query, search
    projection, notification or dashboard section can reach.

    A **draft** with no questions at all is a legitimate state. Publishing
    is what requires a complete, valid Quiz -- between 1 and
    :data:`MAX_QUIZ_QUESTIONS` questions, each with a structurally valid
    active option set satisfying its answer mode -- and a complete
    availability window.

    **Once published, the authored Quiz is read-only**: metadata,
    availability, questions, ordering, prompts, options, option order and
    answer keys all stop being editable, because Students may already be
    reading exactly that wording. Unpublishing is permitted **only while
    no attempt exists**; the moment the first attempt is started the Quiz
    is frozen permanently, since rewriting it afterwards would change what
    somebody's answers were answers *to*.

    **Since Phase 4 / M04B a Quiz owns ordered multiple-choice
    questions** (:class:`~app.models.quiz_question.QuizQuestion`) and
    their answer options. That supersedes M04A's statement that no
    question surface exists; everything else above is unchanged, and a
    Quiz with questions is still a draft that no Student can reach.
    ``version`` correspondingly now represents the whole authored draft
    -- title, instructions, questions and options -- not only the
    metadata M04A could change (see the ``version`` paragraph below).

    **Ownership is the Group, and nothing else.** A Quiz belongs directly
    to exactly one Group. Course, Level and AcademicTerm are all reachable
    through ``quiz.group`` and are therefore not duplicated here -- the
    same single-source-of-truth reasoning already applied to Enrollment,
    GroupTeacherAssignment, Schedule, Unit, Lesson, Assignment and
    Submission. There is likewise no ``unit_id`` / ``lesson_id`` (a Quiz
    is Group work, not a child of one teaching Lesson) and no
    ``teacher_id`` / ``created_by``: every **active** assigned Teacher of
    the Group is an equal collaborator on its Quizzes, exactly as they
    already are on its Assignments.

    **``version`` is the concurrency signal, not a revision history.** It
    starts at 1 and increases by exactly one per *meaningful* edit, so a
    signed co-teacher form token can detect that the row changed under it
    -- including an A -> B -> A round trip that leaves the values
    identical to what a third form was opened against, and including two
    edits landing inside the same whole second, neither of which a
    timestamp comparison could catch. It is never displayed as a revision
    number, and no row is kept per version. A save whose normalized title
    **and** instructions equal the stored ones is a no-op: ``version``
    and ``updated_at`` are both left alone, because re-saving unchanged
    values is not an edit.

    Since M04B the same counter also moves for the draft's **questions**:
    each successful question creation, meaningful question edit and
    order-changing move increments it exactly once, so a metadata form
    opened before a question was added is correctly caught as stale.
    Rejected, stale and no-op question operations leave it alone, and
    ``updated_at`` still moves only when this row's own title or
    instructions change.

    **Title uniqueness.** ``uq_quizzes_group_title`` keeps one Group from
    carrying two same-named Quizzes, exactly as
    ``uq_assignments_group_title`` does for Assignments. The same title in
    **another** Group is allowed. The constraint is the final defense; the
    write path also checks it before the form is accepted *and* again
    against the locked rows, and catches the resulting ``IntegrityError``.

    **``instructions``** is required plain text (never HTML, never
    ``|safe``), rendered with line breaks preserved by CSS. The column is
    unbounded ``Text``; the finite boundary that actually protects the
    request is the form's ``Length(max=QUIZ_INSTRUCTIONS_MAX_LENGTH)``,
    applied to the **raw** value before trimming, so trimming can never be
    used to slip a longer body past it.

    **Time.** Both timestamps are naive UTC truncated to whole seconds
    (:func:`~app.models.submission_feedback.whole_second_utc`, reused
    rather than re-implemented). On creation they are the **same**
    server-generated moment; on a meaningful edit only ``updated_at``
    moves, and ``created_at``, ``id``, ``public_id`` and ``group_id`` are
    preserved. There is deliberately **no** ``onupdate`` on
    ``updated_at``: the write path samples one authoritative moment
    *after* its locks and assigns it explicitly, and an implicit hook
    would both bypass that whole-second truncation and fire on writes this
    milestone does not want timestamped. Timestamps are **not** the
    stale-form mechanism -- ``version`` is.

    Database invariants (final defense only):

    - one title per Group (``uq_quizzes_group_title``);
    - ``version > 0`` (``ck_quizzes_version_positive``) -- a plain
      comparison CHECK, supported by MySQL 8 and the SQLite test backend
      alike;
    - ``public_id`` unique and NOT NULL;
    - ``group_id`` NOT NULL, a plain reference with **no** ``ondelete``
      behaviour, so no Group lifecycle change can remove a Quiz.

    Indexes -- two objects, each with a distinct justification and no
    redundancy between them:

    - ``uq_quizzes_group_title`` (``group_id``, ``title``) -- the required
      uniqueness invariant, and the exact shape of the duplicate-title
      check the write path performs twice.
    - ``ix_quizzes_group_created_id`` (``group_id``, ``created_at``,
      ``id``) -- the **only** list read in this milestone: a single-Group
      equality on ``group_id`` ordered ``created_at DESC, id DESC``, so
      the ordering columns follow the equality column directly. No column
      sits between them, which is exactly the shape M14 measured
      resolving as ``Using filesort`` on ``notifications`` when one did.

    Both start with ``group_id``, so the ``group_id`` foreign key already
    has a usable leftmost prefix and **no** separate single-column index
    is declared for it. No speculative index is declared: there is no
    quiz-by-title search, no cross-Group listing and no counter in this
    milestone, so there is no read shape for one to serve.

    **No MySQL execution plan has been measured for this table.** M04C adds
    the Alembic revision for the accepted Quiz, Question and Option models;
    that revision was applied to the development MySQL database during M04C.
    Measuring real query plans remains a separate operational action.

    **The relationship is for navigation, not for listing.**
    ``Group.quizzes`` exists so the inverse is declared and so a single
    Quiz can reach its Group, and it carries no ``cascade`` /
    ``delete-orphan`` configuration. Nothing in M04A ever iterates it: the
    Teacher list goes through the bounded, column-projected query in
    ``app/services/quiz_queries.py`` instead, so rendering a page can
    never trigger an unbounded relationship load.
    """

    __tablename__ = "quizzes"
    __table_args__ = (
        db.UniqueConstraint("group_id", "title", name="uq_quizzes_group_title"),
        db.CheckConstraint("version > 0", name="ck_quizzes_version_positive"),
        db.CheckConstraint(_STATUS_CHECK_SQL, name="ck_quizzes_status_valid"),
        # Availability is a *pair*: either the Quiz has no window at all
        # (a draft still being written) or it has a complete, ordered one.
        # A half-configured window is the state that would let a
        # publication check pass while leaving "until when?" undecided.
        db.CheckConstraint(
            "(opens_at IS NULL AND closes_at IS NULL) "
            "OR (opens_at IS NOT NULL AND closes_at IS NOT NULL AND opens_at < closes_at)",
            name="ck_quizzes_availability_window",
        ),
        db.CheckConstraint(
            f"time_limit_minutes IS NULL OR (time_limit_minutes >= {MIN_TIME_LIMIT_MINUTES} "
            f"AND time_limit_minutes <= {MAX_TIME_LIMIT_MINUTES})",
            name="ck_quizzes_time_limit_range",
        ),
        db.CheckConstraint(
            f"attempt_limit >= {MIN_ATTEMPT_LIMIT} AND attempt_limit <= {MAX_ATTEMPT_LIMIT}",
            name="ck_quizzes_attempt_limit_range",
        ),
        db.CheckConstraint(
            "(status = 'draft' AND published_at IS NULL) "
            "OR (status = 'published' AND published_at IS NOT NULL)",
            name="ck_quizzes_status_published_at_consistency",
        ),
        db.Index("ix_quizzes_group_created_id", "group_id", "created_at", "id"),
        # The Student list read: equality on `group_id` + `status`, then
        # the `opens_at <= now` range, then the deterministic tie-break.
        # Same shape as M01's `ix_assignments_group_status_opens_due`.
        db.Index(
            "ix_quizzes_group_status_opens_id", "group_id", "status", "opens_at", "id"
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
    title = db.Column(db.String(QUIZ_TITLE_MAX_LENGTH), nullable=False)
    instructions = db.Column(db.Text, nullable=False)
    #: Phase 4 / M04D. `draft` until a Teacher publishes a complete,
    #: valid Quiz. Never `archived` -- see :class:`QuizStatus`.
    status = db.Column(db.String(32), nullable=False, default=QuizStatus.DRAFT.value)
    #: The availability window, naive UTC whole seconds. Both NULL while
    #: the Quiz is still being written; both required to publish.
    opens_at = db.Column(db.DateTime, nullable=True)
    closes_at = db.Column(db.DateTime, nullable=True)
    #: Optional per-attempt limit in whole minutes. NULL means "no limit
    #: of its own" -- the attempt still ends at `closes_at`.
    time_limit_minutes = db.Column(db.Integer, nullable=True)
    #: How many attempts one Student may start. Never NULL and never
    #: unlimited.
    attempt_limit = db.Column(
        db.Integer, nullable=False, default=MIN_ATTEMPT_LIMIT
    )
    #: Stamped when publication commits; cleared on an authorized
    #: unpublish. NULL exactly while `status` is `draft`.
    published_at = db.Column(db.DateTime, nullable=True)
    #: 1 on creation, +1 per meaningful edit. See the class docstring --
    #: this is the stale-form signal, not a revision history.
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Both defaults are defense in depth only; the write path always
    #: supplies its own post-lock whole-second moment, and uses the SAME
    #: value for both columns on creation.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    group = db.relationship("Group", back_populates="quizzes")
    #: Phase 4 / M04B. Declared so the QuizQuestion -> Quiz relationship
    #: has its inverse; it carries no cascade, so nothing can be deleted
    #: through it, and it is deliberately never iterated. Every question
    #: read goes through the bounded, column-projected queries in
    #: `app/services/quiz_queries.py`, so no page can trigger an
    #: unbounded load of a draft's whole question set.
    questions = db.relationship("QuizQuestion", back_populates="quiz")
    #: Phase 4 / M04D. Declared for the inverse only, with no cascade, and
    #: deliberately never iterated: every attempt read goes through the
    #: bounded queries in `app/services/quiz_queries.py`.
    attempts = db.relationship("QuizAttempt", back_populates="quiz")
    #: Phase 4 / M05. The Listening extension, or ``None`` for an ordinary
    #: Quiz -- the presence of this row is exactly what makes this Quiz a
    #: Listening activity. Declared for the inverse only, one-to-one, with
    #: no cascade: nothing can be deleted through it, and no list read
    #: iterates it. Every "is this a Listening activity?" decision on a
    #: read path goes through the bounded, SQL-scoped queries in
    #: ``app/services/listening_queries.py`` instead, so a page can never
    #: pay a lazy load per row to answer it.
    listening_activity = db.relationship(
        "ListeningActivity", back_populates="quiz", uselist=False
    )

    @validates("status")
    def validate_status(self, _key, value):
        if value not in set(_STATUS_VALUES):
            raise ValueError(f"Invalid status: {value}")
        return value
