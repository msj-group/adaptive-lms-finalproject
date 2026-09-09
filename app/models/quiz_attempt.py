import uuid

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import QuizAttemptStatus
from app.models.submission_feedback import whole_second_utc

#: The exact allowed ``status`` values, rendered once into the database
#: CHECK below so the application ``@validates`` guard and the schema can
#: never drift apart.
_ATTEMPT_STATUS_VALUES = tuple(status.value for status in QuizAttemptStatus)
_ATTEMPT_STATUS_CHECK_SQL = "status IN (" + ", ".join(
    f"'{value}'" for value in _ATTEMPT_STATUS_VALUES
) + ")"

#: The two terminal states, quoted for the finalization CHECK below.
_FINAL_STATUS_SQL = "'{}', '{}'".format(
    QuizAttemptStatus.SUBMITTED.value, QuizAttemptStatus.EXPIRED.value
)


class QuizAttempt(db.Model):
    """One Student's attempt at one published Quiz (Phase 4 / M04D).

    **Ownership is the Quiz and the Student, and nothing else.** Group,
    Course, Level and AcademicTerm are reachable through
    ``attempt.quiz.group`` and are not duplicated here -- the same
    single-source-of-truth reasoning applied to every row in this project.

    **``attempt_number`` is server-owned and dense per Student.** The
    first attempt is 1; each later one is the Student's current highest
    plus one, computed under the Quiz lock.
    ``uq_quiz_attempts_quiz_student_number`` is the final defense behind
    that: two concurrent starts cannot both claim the same number, and the
    write path catches the resulting ``IntegrityError`` and re-reads
    rather than inventing a second row.

    **At most one attempt is ``in_progress`` per Student and Quiz.** That
    is a cross-row rule, so it is enforced against the locked rows rather
    than by a constraint -- a partial unique index would be needed and is
    not portable. A repeated valid start therefore **returns the existing
    in-progress attempt** instead of creating a duplicate.

    **``quiz_version`` records which authored draft this attempt was
    taken against.** It is copied at start and never updated. Because a
    Quiz freezes permanently once any attempt exists, it can never
    legitimately diverge -- which is exactly why storing it is worth it:
    a mismatch is evidence that something bypassed the freeze, not a
    normal state to reconcile.

    **``deadline_at`` is authoritative and computed once, at start.**
    Without a Quiz time limit it equals ``closes_at``; with one it is the
    **earlier** of ``closes_at`` and ``started_at + time_limit_minutes``.
    It is stored rather than recomputed so that a later change to the Quiz
    could not move a running attempt's deadline -- and the Quiz cannot
    change anyway, which makes the stored value and the rule agree by
    construction. The client-side timer only *renders* this value; the
    server decides expiry.

    **Finalization happens exactly once, and grading with it.** When a
    request observes an ``in_progress`` attempt at or past
    ``deadline_at`` under the required locks, it finalizes the attempt as
    ``expired`` and grades whatever was saved, counting unanswered
    questions as incorrect. There is deliberately **no background job**:
    the state is derived by the next authorized reader or writer, so it
    can never be "pending" in a way a Student or Teacher would see
    differently. Re-running that path is idempotent.

    **Scores are two integers, not a float.** ``correct_count`` and
    ``total_questions`` are NULL exactly while the attempt is
    ``in_progress`` and both NOT NULL once it is finalized. The
    percentage a page shows is derived from them at read time -- storing a
    rounded float as well would create a second source of truth that could
    disagree with the counts beside it. There is no partial credit, no
    per-option points, no weighting, no penalty and no pass/fail column,
    because none of that was approved.

    **Frozen once finalized.** A ``submitted`` or ``expired`` attempt, and
    every answer and selection beneath it, is immutable. A replayed
    submission returns the existing result and changes no timestamp, no
    counter and no selection. There is no delete route, no score override
    and no manual-grading path anywhere.

    Database invariants (final defense only):

    - ``status`` limited to the three members of
      :class:`~app.models.enums.QuizAttemptStatus`;
    - one ``attempt_number`` per (Quiz, Student);
    - ``attempt_number >= 1`` and ``quiz_version > 0``;
    - an ``in_progress`` attempt has no ``submitted_at`` and no counts; a
      finalized one has both counts; only a ``submitted`` attempt has a
      ``submitted_at`` (an expired attempt was never submitted, and
      pretending otherwise would misreport what the Student did);
    - ``0 <= correct_count <= total_questions``.

    Indexes:

    - ``uq_quiz_attempts_quiz_student_number`` (``quiz_id``,
      ``student_id``, ``attempt_number``) -- the uniqueness invariant, and
      the exact shape of every "this Student's attempts at this Quiz"
      read, which is how the Student pages and the attempt-limit check
      resolve. Its leftmost ``quiz_id`` also serves that foreign key, so
      no separate index is declared for it.
    - ``ix_quiz_attempts_quiz_started_id`` (``quiz_id``, ``started_at``,
      ``id``) -- the Teacher attempt list: a single-Quiz equality ordered
      newest first, with the ordering columns following the equality
      column directly.
    - ``student_id`` carries its own index because nothing above leads
      with it and InnoDB requires one on a referencing column.

    **No MySQL execution plan has been measured for this table.** As with
    every earlier Part's indexes, this is a reasoned design pending an
    authorized real ``EXPLAIN``.
    """

    __tablename__ = "quiz_attempts"
    __table_args__ = (
        db.UniqueConstraint(
            "quiz_id",
            "student_id",
            "attempt_number",
            name="uq_quiz_attempts_quiz_student_number",
        ),
        db.CheckConstraint(
            _ATTEMPT_STATUS_CHECK_SQL, name="ck_quiz_attempts_status_valid"
        ),
        db.CheckConstraint(
            "attempt_number >= 1", name="ck_quiz_attempts_number_positive"
        ),
        db.CheckConstraint(
            "quiz_version > 0", name="ck_quiz_attempts_quiz_version_positive"
        ),
        db.CheckConstraint(
            "(status = 'in_progress' AND submitted_at IS NULL "
            "AND correct_count IS NULL AND total_questions IS NULL) "
            "OR (status = 'submitted' AND submitted_at IS NOT NULL "
            "AND correct_count IS NOT NULL AND total_questions IS NOT NULL) "
            "OR (status = 'expired' AND submitted_at IS NULL "
            "AND correct_count IS NOT NULL AND total_questions IS NOT NULL)",
            name="ck_quiz_attempts_status_finalization_consistency",
        ),
        db.CheckConstraint(
            "correct_count IS NULL "
            "OR (correct_count >= 0 AND total_questions >= 0 "
            "AND correct_count <= total_questions)",
            name="ck_quiz_attempts_counts_range",
        ),
        db.Index("ix_quiz_attempts_quiz_started_id", "quiz_id", "started_at", "id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    quiz_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("quizzes.id"),
        nullable=False,
    )
    student_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    #: 1-based and dense per (Quiz, Student). Server-owned.
    attempt_number = db.Column(db.Integer, nullable=False)
    status = db.Column(
        db.String(32), nullable=False, default=QuizAttemptStatus.IN_PROGRESS.value
    )
    #: The authored ``Quiz.version`` this attempt was started against.
    #: Copied once and never updated.
    quiz_version = db.Column(db.Integer, nullable=False)
    #: Naive UTC whole seconds throughout, like every other timestamp in
    #: this project.
    started_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    #: Authoritative. See the class docstring.
    deadline_at = db.Column(db.DateTime, nullable=False)
    #: Set only by a real submission; an expired attempt keeps it NULL.
    submitted_at = db.Column(db.DateTime, nullable=True)
    #: Both NULL while in progress, both set at finalization.
    correct_count = db.Column(db.Integer, nullable=True)
    total_questions = db.Column(db.Integer, nullable=True)

    quiz = db.relationship("Quiz", back_populates="attempts")
    student = db.relationship("User")
    #: Inverse only, no cascade, never iterated -- every answer read goes
    #: through the bounded queries in ``app/services/quiz_queries.py``.
    answers = db.relationship("QuizAnswer", back_populates="attempt")

    @validates("status")
    def validate_status(self, _key, value):
        if value not in set(_ATTEMPT_STATUS_VALUES):
            raise ValueError(f"Invalid status: {value}")
        return value

    @property
    def is_finalized(self):
        """True once the attempt is graded and frozen.

        Used by every write path as the single expression of "this
        aggregate is immutable now", so submitted and expired can never be
        treated differently by accident.
        """
        return self.status in (
            QuizAttemptStatus.SUBMITTED.value,
            QuizAttemptStatus.EXPIRED.value,
        )
