import uuid

from app.extensions import db
from app.models.submission_feedback import whole_second_utc


class QuizAnswer(db.Model):
    """One Student's saved answer to one question inside one attempt
    (Phase 4 / M04D).

    **An answer is a container, not a value.** Which options the Student
    picked lives in :class:`QuizAnswerSelection` rows beneath it, because
    a multiple-answer question legitimately holds several. Storing a
    delimited string of option ids in one column here would be a second,
    unconstrained encoding of a relationship the database can already
    express and enforce.

    **One answer per (attempt, question).**
    ``uq_quiz_answers_attempt_question`` is the final defense; the write
    path also checks it against the locked rows and catches the resulting
    ``IntegrityError``. Saving an answer **replaces** its selection set
    atomically inside one transaction rather than accumulating rows.

    **A row exists only where the Student actually answered.** An
    unanswered question has no ``QuizAnswer`` at all -- not a row with an
    empty selection set. That keeps "did not answer" and "answered with
    nothing" from becoming two spellings of the same thing, and it is why
    grading counts *questions*, not answers: a question with no answer row
    scores zero exactly like a wrong one.

    **Writable only while the attempt is in progress.** Once the attempt
    is ``submitted`` or ``expired``, this row and its selections are
    immutable. There is no delete route and no manual-grading path.

    ``question_id`` carries its own index: the uniqueness constraint leads
    with ``attempt_id``, so nothing above gives the question foreign key a
    usable leftmost prefix, and InnoDB requires one on a referencing
    column.
    """

    __tablename__ = "quiz_answers"
    __table_args__ = (
        db.UniqueConstraint(
            "attempt_id", "question_id", name="uq_quiz_answers_attempt_question"
        ),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    attempt_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("quiz_attempts.id"),
        nullable=False,
    )
    question_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("quiz_questions.id"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    attempt = db.relationship("QuizAttempt", back_populates="answers")
    question = db.relationship("QuizQuestion")
    #: Inverse only, no cascade, never iterated.
    selections = db.relationship("QuizAnswerSelection", back_populates="answer")


class QuizAnswerSelection(db.Model):
    """One option the Student selected for one saved answer
    (Phase 4 / M04D).

    A pure link row between :class:`QuizAnswer` and
    :class:`~app.models.question_option.QuestionOption`, with
    ``uq_quiz_answer_selections_answer_option`` making a duplicated pick
    impossible even if a tampered request submitted the same option twice.

    **Only *active* options of the answered question are ever accepted**,
    proved against the locked rows before anything is written -- a retired
    option, an option from another question, and an option from another
    Quiz entirely are all refused identically.

    **Replacement, not accumulation.** Saving an answer deletes this
    answer's existing selection rows and inserts the new set inside the
    **same** transaction, so a reader never observes a half-replaced set
    and a failure leaves the previous set intact. That delete is the one
    place in the Quiz aggregate where rows are removed, and it is
    deliberately confined to selections of an attempt that is still
    ``in_progress``: it discards a draft answer the same Student is still
    editing, never authored content and never a finalized result.

    ``created_at`` is kept because it is the only record of *when* a
    particular pick was made; there is no ``updated_at`` because a
    selection is never edited in place -- it is created or it is replaced.

    ``option_id`` carries its own index: the uniqueness constraint leads
    with ``answer_id``, so the option foreign key has no usable leftmost
    prefix of its own.
    """

    __tablename__ = "quiz_answer_selections"
    __table_args__ = (
        db.UniqueConstraint(
            "answer_id", "option_id", name="uq_quiz_answer_selections_answer_option"
        ),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    answer_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("quiz_answers.id"),
        nullable=False,
    )
    option_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("question_options.id"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    answer = db.relationship("QuizAnswer", back_populates="selections")
    option = db.relationship("QuestionOption")
