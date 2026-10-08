from app.models.code_types import CODE_COLLATION
import uuid

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import QuestionAnswerMode
from app.models.submission_feedback import whole_second_utc

#: The finite input boundary for the unbounded ``prompt`` Text column
#: (Phase 4 / M04B). Mirrored by
#: ``app.blueprints.teacher.quiz_forms.QuizQuestionForm.PROMPT_MAX`` -- the
#: form is what actually rejects an oversized request; this constant is
#: declared beside the column so the two can be read together and cannot
#: drift silently. Same arrangement as M02's ``ANSWER_MAX_LENGTH``, M03's
#: ``FEEDBACK_MAX_LENGTH`` and M04A's ``QUIZ_INSTRUCTIONS_MAX_LENGTH``.
QUESTION_PROMPT_MAX_LENGTH = 5000

#: The exact allowed ``answer_mode`` values, rendered once into the
#: database CHECK constraint below so the application-level ``@validates``
#: guard and the schema can never drift apart -- the same technique M14
#: uses for ``notifications.kind`` and M01 for ``assignments.status``.
_ANSWER_MODE_VALUES = tuple(mode.value for mode in QuestionAnswerMode)
_ANSWER_MODE_CHECK_SQL = "answer_mode IN (" + ", ".join(
    f"'{value}'" for value in _ANSWER_MODE_VALUES
) + ")"


class QuizQuestion(db.Model):
    """One ordered multiple-choice question inside a Group-owned quiz
    draft (Phase 4 / M04B).

    **Still a draft, and still nothing else.** A question exists only
    inside a Quiz that M04A made a draft by construction: there is no
    publication state anywhere in this chain, no Student route or query
    can reach a question, and no attempt, saved answer, timer, submission,
    result or score exists. Nothing here means graded, marked, released or
    available.

    **Ownership is the Quiz, and nothing else.** Group, Course, Level and
    AcademicTerm are all reachable through ``question.quiz.group`` and are
    therefore not duplicated here -- the same single-source-of-truth
    reasoning already applied to Enrollment, GroupTeacherAssignment,
    Schedule, Unit, Lesson, Assignment, Submission and Quiz. There is
    likewise no ``teacher_id`` / ``created_by``: every **active** assigned
    Teacher of the Quiz's Group is an equal collaborator on its questions,
    exactly as they already are on the Quiz itself.

    Because a question cannot exist without its Quiz, and **every** Quiz
    row already freezes the Group's academic identity (M04A), question and
    option rows need no identity-freeze integration of their own and the
    Administrator Group logic is untouched by M04B.

    **``answer_mode`` is a cardinality rule, not a question type.** Every
    question here is multiple choice. ``single`` requires exactly one
    correct active option; ``multiple`` requires at least two, and all of
    them being correct is legitimate -- no distractor is required, because
    no such business rule was approved. Switching modes never silently
    adds, clears or truncates a correct answer: the write path rejects a
    submission whose cardinality does not match the submitted mode and
    hands the Teacher their attempted values back to fix explicitly.

    **``display_order`` is server-owned authored order.** New questions
    append after the Quiz's current highest order; Move Up / Move Down
    swap two rows. There is deliberately **no** uniqueness constraint on
    it: gaps are acceptable (so a move never has to renumber a whole
    Quiz), and the internal ``id`` is used only as a deterministic SQL
    tie-break -- never exposed in a URL, a form value or the rendered
    HTML.

    **``prompt`` is not unique, on purpose.** Two questions may
    legitimately use similar or identical wording (a paired listening and
    reading item, a deliberate repetition across difficulty levels), so no
    constraint and no application check forbids it.

    **``version`` is the concurrency signal, not a revision history.** It
    starts at 1 and increases by exactly one per *meaningful* edit of this
    question -- its prompt, its mode, or its active option aggregate --
    and by exactly one for each move that actually changes this row's
    stored ``display_order``. It exists so a signed co-teacher form token
    can detect that the question changed under it, including an
    A -> B -> A round trip and two edits inside one whole second. It is
    never displayed as a revision number, and no row is kept per version.

    **The parent Quiz version moves too.** Since M04B, ``Quiz.version``
    represents the current complete Teacher-authored draft -- title,
    instructions, questions and options -- not only the metadata M04A
    could change. Each successful question creation, meaningful question
    edit and order-changing move increments it exactly once, so a Quiz
    metadata form opened before a question was added is correctly caught
    as stale.

    **Time.** Both timestamps are naive UTC truncated to whole seconds
    (:func:`~app.models.submission_feedback.whole_second_utc`, reused
    rather than re-implemented). On creation they are the **same**
    server-generated moment; on a meaningful change only ``updated_at``
    moves. There is deliberately **no** ``onupdate``: the write path
    samples one authoritative moment *after* its locks and assigns it
    explicitly to every row it changes in that request, and an implicit
    hook would both bypass that truncation and fire on writes this
    milestone defines as no-ops.

    Database invariants (final defense only):

    - ``answer_mode`` limited to ``single`` / ``multiple``
      (``ck_quiz_questions_answer_mode_valid``);
    - ``display_order >= 0``
      (``ck_quiz_questions_display_order_non_negative``);
    - ``version > 0`` (``ck_quiz_questions_version_positive``);
    - ``public_id`` unique and NOT NULL;
    - ``quiz_id`` NOT NULL, a plain reference with **no** ``ondelete``
      behaviour, so no Quiz or Group lifecycle change can remove a
      question.

    One index, with one justification: ``ix_quiz_questions_quiz_order_id``
    (``quiz_id``, ``display_order``, ``id``) is the **only** read shape in
    M04B -- a single-Quiz equality ordered ``display_order ASC, id ASC``,
    which serves the paginated list, the append-order lookup and the
    bounded previous/next neighbour lookup a move needs. The ordering
    columns follow the equality column directly, with nothing between
    them. It starts with ``quiz_id``, so the foreign key already has a
    usable leftmost prefix and **no** separate single-column index is
    declared. No speculative index is declared: there is no question
    search, no cross-Quiz listing and no counter in this milestone.

    **No MySQL execution plan has been measured for this table.** M04C adds
    the Alembic revision for the accepted Quiz, Question and Option models;
    that revision was applied to the development MySQL database during M04C.
    Measuring real query plans remains a separate operational action.

    **Relationships are for navigation, not for listing.** ``Quiz.questions``
    and ``QuizQuestion.options`` exist so the inverses are declared, and
    both carry **no** ``cascade`` / ``delete-orphan`` configuration, so
    nothing can be removed through them. Neither is ever iterated: every
    read goes through the bounded, column-projected queries in
    ``app/services/quiz_queries.py``, so rendering a page can never
    trigger an unbounded relationship load. Questions are never deleted,
    retired or hard-deleted in this Part -- there is no such route.
    """

    __tablename__ = "quiz_questions"
    __table_args__ = (
        db.CheckConstraint(
            _ANSWER_MODE_CHECK_SQL, name="ck_quiz_questions_answer_mode_valid"
        ),
        db.CheckConstraint(
            "display_order >= 0", name="ck_quiz_questions_display_order_non_negative"
        ),
        db.CheckConstraint("version > 0", name="ck_quiz_questions_version_positive"),
        db.Index("ix_quiz_questions_quiz_order_id", "quiz_id", "display_order", "id"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    quiz_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("quizzes.id"),
        nullable=False,
    )
    prompt = db.Column(db.Text, nullable=False)
    answer_mode = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False)
    #: Server-owned authored order. Gaps are acceptable; `id` is the SQL
    #: tie-break only.
    display_order = db.Column(db.Integer, nullable=False)
    #: 1 on creation, +1 per meaningful edit or order-changing move. See
    #: the class docstring -- this is the stale-form signal, not a
    #: revision history.
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Both defaults are defense in depth only; the write path always
    #: supplies its own post-lock whole-second moment, and uses the SAME
    #: value for both columns on creation.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    quiz = db.relationship("Quiz", back_populates="questions")
    options = db.relationship("QuestionOption", back_populates="question")

    @validates("answer_mode")
    def validate_answer_mode(self, _key, value):
        if value not in set(_ANSWER_MODE_VALUES):
            raise ValueError(f"Invalid answer_mode: {value}")
        return value
