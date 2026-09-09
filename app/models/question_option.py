import uuid

from app.extensions import db
from app.models.submission_feedback import whole_second_utc

#: The finite input boundary for the unbounded ``option_text`` Text column
#: (Phase 4 / M04B). Mirrored by
#: ``app.blueprints.teacher.quiz_forms.QuizQuestionForm.OPTION_TEXT_MAX``
#: -- the form is what actually rejects an oversized request; this
#: constant is declared beside the column so the two can be read together
#: and cannot drift silently.
OPTION_TEXT_MAX_LENGTH = 1000

#: The approved size of a valid question's **active** option set. Both
#: bounds are enforced by the application against the locked rows, never
#: by a database constraint -- a row-count rule is not expressible as a
#: CHECK, and the parent Quiz lock is what makes the application check
#: authoritative (see the class docstring).
MIN_ACTIVE_OPTIONS = 2
MAX_ACTIVE_OPTIONS = 8


class QuestionOption(db.Model):
    """One answer option of one multiple-choice question (Phase 4 / M04B).

    **``is_correct`` is the Teacher's authored answer key for a draft, and
    nothing more.** It carries no score, no weight, no partial credit and
    no pass/fail meaning; no Student can reach it; and no attempt,
    submission, marking or answer-release path exists to give it one.
    Reading any scoring semantics into this column would be inventing a
    rule nobody approved.

    **Ownership is the Question, and nothing else.** Quiz, Group, Course,
    Level and AcademicTerm are all reachable through
    ``option.question.quiz.group`` and are therefore not duplicated here.

    **Removal is retirement in place, never deletion.** When a Teacher
    removes an option while editing a question, the row is kept:

    - ``is_active`` becomes ``False``;
    - ``retired_at`` receives the request's authoritative post-lock
      whole-second moment;
    - ``option_text``, ``is_correct``, ``public_id``, ``created_at`` and
      the stored ``display_order`` are all **preserved** exactly as they
      were, as history;
    - it is never physically deleted, never rendered again, and never
      accepted as an active answer;
    - M04B provides **no** restoration UI, and deliberately no
      "un-retire" route -- re-adding the wording creates a new option, so
      the record of what was actually authored stays intact.

    ``ck_question_options_active_retired_consistency`` is what stops those
    two columns from disagreeing: an active option must have
    ``retired_at`` NULL, and an inactive one must have it set. There is no
    third state.

    Question rows themselves are never removed, retired or hard-deleted in
    this Part; there is no such route and no such column.

    **``display_order`` is server-owned authored order**, normalized to
    ``0..n-1`` across the **active** options on each successful aggregate
    save. Retired rows keep whatever order they had when they were
    retired -- renumbering history would be a lie about what was authored.
    There is deliberately no uniqueness constraint on it: a retired row
    can legitimately share an order value with an active one, and the
    internal ``id`` is the deterministic SQL tie-break, never exposed.

    **Option text is not unique in the database, on purpose, and that is
    stated rather than hidden.** Two duplicate *active* options in one
    question are rejected by the application, comparing the **normalized**
    (trimmed) text of the complete submitted aggregate. That rule is a
    cross-row one, so it is **not** enforced by a database constraint: a
    ``UNIQUE(question_id, option_text)`` would also forbid a retired row
    from sharing wording with a live one, which is exactly the history
    this model is designed to keep. What makes the application check
    authoritative instead of merely hopeful is the lock order -- **every**
    question-option write locks the parent Quiz first, so competing
    co-teacher writes on the same Quiz serialize rather than interleave.
    That serialization is a reasoned property of the documented lock
    order; SQLite proves none of it, and no real-MySQL concurrency test
    has been run.

    **``option_text``** is required plain text (never HTML, never
    ``|safe``), autoescaped everywhere it is rendered. The column is
    unbounded ``Text``; the finite boundary that actually protects the
    request is the form's ``Length(max=OPTION_TEXT_MAX_LENGTH)``, applied
    to the **raw** value before trimming, so trimming can never be used to
    slip a longer body past it.

    **Time.** All three timestamps are naive UTC truncated to whole
    seconds. On creation ``created_at`` and ``updated_at`` are the **same**
    server-generated moment; a meaningful change moves ``updated_at``
    only; retirement sets ``retired_at`` to that same request moment.
    There is deliberately **no** ``onupdate`` -- see
    :class:`~app.models.quiz_question.QuizQuestion`.

    Database invariants (final defense only):

    - ``display_order >= 0``
      (``ck_question_options_display_order_non_negative``);
    - active/retired consistency
      (``ck_question_options_active_retired_consistency``);
    - ``public_id`` unique and NOT NULL;
    - ``question_id`` NOT NULL, a plain reference with **no** ``ondelete``
      behaviour, so no lifecycle change anywhere can remove an option.

    The 2..8 active-option rule is **not** a database constraint: it
    counts rows, which a CHECK cannot do. It is enforced against the
    locked aggregate, and a question found holding a structurally invalid
    active set is refused safely rather than silently truncated or
    "repaired".

    One index, with one justification:
    ``ix_question_options_question_active_order_id`` (``question_id``,
    ``is_active``, ``display_order``, ``id``) is the exact shape of the
    only read in M04B -- one question's **active** options in authored
    order -- with the two equality columns first and the ordering columns
    following them directly. It starts with ``question_id``, so the
    foreign key already has a usable leftmost prefix and **no** separate
    single-column index is declared. No retired-option report exists, so
    no index is declared for one.

    **No MySQL execution plan has been measured for this table.** M04C adds
    the Alembic revision for the accepted Quiz, Question and Option models;
    that revision was applied to the development MySQL database during M04C.
    Measuring real query plans remains a separate operational action.
    """

    __tablename__ = "question_options"
    __table_args__ = (
        db.CheckConstraint(
            "display_order >= 0", name="ck_question_options_display_order_non_negative"
        ),
        db.CheckConstraint(
            "(is_active = 1 AND retired_at IS NULL) "
            "OR (is_active = 0 AND retired_at IS NOT NULL)",
            name="ck_question_options_active_retired_consistency",
        ),
        db.Index(
            "ix_question_options_question_active_order_id",
            "question_id",
            "is_active",
            "display_order",
            "id",
        ),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    question_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("quiz_questions.id"),
        nullable=False,
    )
    option_text = db.Column(db.Text, nullable=False)
    #: Server-owned authored order, normalized to 0..n-1 across the ACTIVE
    #: options on each successful aggregate save. Retired rows keep their
    #: stored value.
    display_order = db.Column(db.Integer, nullable=False)
    #: The Teacher's authored answer key for a draft. NOT a score, a
    #: weight, or any grading signal -- see the class docstring.
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    #: True until the option is retired in place; never deleted.
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    #: NULL exactly while `is_active` is true (enforced by CHECK).
    retired_at = db.Column(db.DateTime, nullable=True)
    #: Both defaults are defense in depth only; the write path always
    #: supplies its own post-lock whole-second moment, and uses the SAME
    #: value for both columns on creation.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    question = db.relationship("QuizQuestion", back_populates="options")
