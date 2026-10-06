"""Attempt lifecycle, deadlines and grading for published Quizzes
(Phase 4 / M04D).

Flask-independent -- plain functions over the ORM, no ``request`` /
``flash`` / route decorators, mirroring the other service modules. The
locking write paths live in ``app/blueprints/student/quizzes.py`` and
``app/blueprints/teacher/quizzes.py``; this module owns the *rules* those
routes apply once they hold their locks.

**Authorization is never performed here.** Every function is handed an
identifier the calling route has already authorized.

**Every read is bounded.** A Quiz holds at most
:data:`~app.models.quiz.MAX_QUIZ_QUESTIONS` questions and a question at
most :data:`~app.models.question_option.MAX_ACTIVE_OPTIONS` active
options, so grading an attempt is three statements over at most a few
hundred rows -- never a query per question, never a relationship
iteration, and never an unbounded ``COUNT`` over a growing table.
"""

from collections import defaultdict
from datetime import timedelta

from app.extensions import db
from app.models import (
    MAX_ACTIVE_OPTIONS,
    MAX_QUIZ_QUESTIONS,
    QuestionOption,
    QuizAnswer,
    QuizAnswerSelection,
    QuizAttempt,
    QuizAttemptStatus,
    QuizQuestion,
    QuizStatus,
)

_IN_PROGRESS = QuizAttemptStatus.IN_PROGRESS.value
_SUBMITTED = QuizAttemptStatus.SUBMITTED.value
_EXPIRED = QuizAttemptStatus.EXPIRED.value
_PUBLISHED = QuizStatus.PUBLISHED.value

#: Derived, never-stored availability states of a published Quiz, computed
#: from one injected reference moment exactly like M01's Assignment
#: states. A draft has no availability state at all -- it is simply not
#: published to anybody.
STATE_SCHEDULED = "scheduled"
STATE_OPEN = "open"
STATE_CLOSED = "closed"

STATE_LABELS = {
    STATE_SCHEDULED: "Opens later",
    STATE_OPEN: "Open now",
    STATE_CLOSED: "Closed",
}

#: Human labels for the three attempt states, declared once so the Student
#: and Teacher pages can never describe the same attempt differently.
ATTEMPT_STATUS_LABELS = {
    _IN_PROGRESS: "In progress",
    _SUBMITTED: "Submitted",
    _EXPIRED: "Time expired",
}


class InvalidQuizAggregate(Exception):
    """A stored Quiz does not satisfy the rules its own routes validate.

    Raised instead of guessing: refusing loudly is the only safe answer
    when the data cannot be graded or published without inventing or
    discarding something a Teacher authored.
    """


# ---------------------------------------------------------------------------
# Derived availability
# ---------------------------------------------------------------------------


def availability_state(quiz, reference_utc):
    """The derived state of a **published** Quiz, or ``None`` for a draft.

    Never stored: computed from `reference_utc` so the passage of time can
    never leave a stale value in the database. The boundaries are exact
    and deliberately half-open -- ``now == opens_at`` is already open, and
    ``now == closes_at`` is already closed -- which is the same convention
    M01 uses for ``due_at`` and is what makes the "at exactly this second"
    tests meaningful.
    """
    if quiz.status != _PUBLISHED or quiz.opens_at is None or quiz.closes_at is None:
        return None
    if reference_utc < quiz.opens_at:
        return STATE_SCHEDULED
    if reference_utc < quiz.closes_at:
        return STATE_OPEN
    return STATE_CLOSED


def can_start_attempt_now(quiz, reference_utc):
    """Whether the availability window currently permits *starting*.

    Reading a published Quiz survives ``closes_at`` -- a Student must
    always be able to reach a receipt -- but starting does not.
    """
    return availability_state(quiz, reference_utc) == STATE_OPEN


def compute_deadline(quiz, started_at):
    """The authoritative ``deadline_at`` for an attempt starting now.

    Without a Quiz time limit the attempt simply ends when the Quiz
    closes. With one, it ends at whichever comes **first** -- the limit
    must never let an attempt run past the window, and the window must
    never extend a limit. Computed once, at start, and then stored: a
    running attempt's deadline is a fact about that attempt, not a live
    reading of the Quiz.
    """
    if quiz.time_limit_minutes is None:
        return quiz.closes_at
    return min(quiz.closes_at, started_at + timedelta(minutes=quiz.time_limit_minutes))


def attempt_has_expired(attempt, reference_utc):
    """True when an in-progress attempt has reached or passed its
    deadline and must be finalized before anything else is decided."""
    return attempt.status == _IN_PROGRESS and reference_utc >= attempt.deadline_at


# ---------------------------------------------------------------------------
# Bounded reads over the authored aggregate
# ---------------------------------------------------------------------------


def quiz_question_ids(quiz_id, limit=None):
    """A Quiz's question ids in authored order, hard-limited.

    The default limit is ``MAX_QUIZ_QUESTIONS + 1`` so that a Quiz holding
    *more* than the approved maximum is **detected** rather than silently
    truncated -- the extra row is the evidence, not a row to use.

    Callers that need the *number* of questions take ``len()`` of this
    rather than issuing a ``COUNT(*)``: the count only ever matters
    against the 1..100 bounds, so reading at most 101 ids answers every
    question the application asks while staying bounded on a table that
    grows.
    """
    cap = MAX_QUIZ_QUESTIONS + 1 if limit is None else limit
    rows = (
        db.session.query(QuizQuestion.id)
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.display_order.asc(), QuizQuestion.id.asc())
        .limit(cap)
        .all()
    )
    return [row.id for row in rows]


def correct_option_ids_by_question(question_ids):
    """``{question_id: {option_id, ...}}`` -- the authored answer key of
    the **active** options, for at most one Quiz's worth of questions.

    One statement for the whole Quiz rather than a lookup per question.
    Questions with no correct active option are simply absent, which is
    what makes them detectable as structurally invalid rather than
    accidentally scoring as "correct when unanswered".
    """
    if not question_ids:
        return {}
    key = defaultdict(set)
    rows = (
        db.session.query(QuestionOption.question_id, QuestionOption.id)
        .filter(
            QuestionOption.question_id.in_(question_ids),
            QuestionOption.is_active.is_(True),
            QuestionOption.is_correct.is_(True),
        )
        .all()
    )
    for question_id, option_id in rows:
        key[question_id].add(option_id)
    return dict(key)


def active_option_counts_by_question(question_ids):
    """``{question_id: active_option_count}`` in one grouped statement."""
    if not question_ids:
        return {}
    from sqlalchemy import func

    rows = (
        db.session.query(QuestionOption.question_id, func.count(QuestionOption.id))
        .filter(
            QuestionOption.question_id.in_(question_ids),
            QuestionOption.is_active.is_(True),
        )
        .group_by(QuestionOption.question_id)
        .all()
    )
    return {question_id: int(count) for question_id, count in rows}


def selected_option_ids_by_question(attempt_id):
    """``{question_id: {option_id, ...}}`` -- what this attempt saved.

    One statement joining answers to selections to options, restricted to
    **active** options so a retired one could never contribute to a score.
    A question the Student never answered is simply absent, which is
    exactly how "unanswered" reaches the grader.
    """
    selected = defaultdict(set)
    rows = (
        db.session.query(QuizAnswer.question_id, QuizAnswerSelection.option_id)
        .join(QuizAnswerSelection, QuizAnswerSelection.answer_id == QuizAnswer.id)
        .join(QuestionOption, QuestionOption.id == QuizAnswerSelection.option_id)
        .filter(
            QuizAnswer.attempt_id == attempt_id,
            QuestionOption.is_active.is_(True),
        )
        .all()
    )
    for question_id, option_id in rows:
        selected[question_id].add(option_id)
    return dict(selected)


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def grade_attempt(attempt_id, quiz_id):
    """``(correct_count, total_questions)`` for one attempt.

    **Exact-set matching, one point or zero.** A question is correct only
    when the set of **active** option ids the Student selected equals the
    authored correct **active** option id set, exactly. A subset, a
    superset, a different set of the same size and an unanswered question
    all score zero. A question whose authored key is empty scores zero
    too, and never "matches" an unanswered question -- an empty
    intersection of two empty sets must not be read as a right answer.

    There is deliberately no partial credit, no per-option point, no
    penalty, no weighting and no rounding rule: none of that was
    approved, and each would be a policy decision this Part is not
    entitled to invent.

    ``total_questions`` counts **questions**, not saved answers, which is
    what makes an unanswered question cost exactly what a wrong one does.

    Three bounded statements regardless of Quiz size. Raises
    :class:`InvalidQuizAggregate` if the Quiz somehow holds more than the
    approved maximum -- grading a Quiz that could never have been
    published is a data problem to report, not to round off.
    """
    question_ids = quiz_question_ids(quiz_id)
    if len(question_ids) > MAX_QUIZ_QUESTIONS:
        raise InvalidQuizAggregate(
            "quiz holds more questions than the approved maximum"
        )

    total = len(question_ids)
    if total == 0:
        return 0, 0

    answer_key = correct_option_ids_by_question(question_ids)
    selected = selected_option_ids_by_question(attempt_id)

    correct = 0
    for question_id in question_ids:
        expected = answer_key.get(question_id)
        if not expected:
            # No authored correct active option: cannot be answered
            # correctly, and must never score by matching "nothing".
            continue
        if selected.get(question_id) == expected:
            correct += 1
    return correct, total


def percentage(correct_count, total_questions):
    """The display percentage, derived rather than stored.

    Returns ``None`` when there is nothing to divide by, so a caller can
    render "--" instead of a fabricated 0% or a crash. Rounded to a whole
    number for display only; the integer counts beside it remain the
    record.
    """
    if not total_questions:
        return None
    return round(correct_count * 100 / total_questions)


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------


def finalize_attempt(attempt, status, moment):
    """Grade and freeze one in-progress attempt, in place.

    The single place both endings are written, so a submitted and an
    expired attempt can never be graded by two slightly different rules.
    ``submitted_at`` is stamped only for a real submission -- an expired
    attempt was never submitted, and recording otherwise would misreport
    what the Student did.

    **Idempotent by refusal, not by overwriting**: an already-finalized
    attempt is returned untouched, so a replayed submission and a second
    observer of the same expiry both leave the stored counters,
    timestamps, answers and selections exactly as they were.

    The caller must already hold the required locks and must pass the
    request's authoritative post-lock whole-second moment.
    """
    if attempt.is_finalized:
        return False

    correct, total = grade_attempt(attempt.id, attempt.quiz_id)
    attempt.correct_count = correct
    attempt.total_questions = total
    attempt.status = status
    if status == _SUBMITTED:
        attempt.submitted_at = moment
    return True


def expire_if_due(attempt, reference_utc):
    """Finalize an in-progress attempt whose deadline has passed.

    Called by **every** authorized read and write that touches an attempt,
    which is what makes expiry request-driven rather than a background
    job: no page can show a Student or a Teacher an attempt that is
    "still running" when its deadline is behind it.

    Returns True when this call is the one that finalized it. The caller
    must hold the required locks and commit.
    """
    if not attempt_has_expired(attempt, reference_utc):
        return False
    return finalize_attempt(attempt, _EXPIRED, reference_utc)


# ---------------------------------------------------------------------------
# Publication readiness
# ---------------------------------------------------------------------------


def publication_blockers(quiz):
    """Every reason this Quiz may not be published, as Teacher-facing
    sentences. An empty list means it is ready.

    Returns **all** of them rather than the first, so a Teacher fixes one
    Quiz instead of rediscovering the next problem on each attempt. The
    caller runs this against the **locked** rows; the same function backs
    the read-only readiness panel, so the page and the write path can
    never disagree about what "ready" means.

    Bounded: at most 101 question ids plus two grouped statements over
    them, whatever the size of the Quiz.
    """
    from app.models import MIN_ACTIVE_OPTIONS, QuestionAnswerMode

    blockers = []

    if quiz.opens_at is None or quiz.closes_at is None:
        blockers.append(
            "Set both an opening time and a closing time before publishing."
        )

    question_ids = quiz_question_ids(quiz.id)
    if not question_ids:
        blockers.append("Add at least one question before publishing.")
        return blockers
    if len(question_ids) > MAX_QUIZ_QUESTIONS:
        blockers.append(
            f"This quiz has more than {MAX_QUIZ_QUESTIONS} questions, which is more than "
            "can be published. Nothing has been changed or removed. Please ask an "
            "administrator to review it."
        )
        return blockers

    modes = dict(
        db.session.query(QuizQuestion.id, QuizQuestion.answer_mode)
        .filter(QuizQuestion.id.in_(question_ids))
        .all()
    )
    active_counts = active_option_counts_by_question(question_ids)
    answer_key = correct_option_ids_by_question(question_ids)

    invalid_structure = []
    invalid_single = []
    invalid_multiple = []
    for position, question_id in enumerate(question_ids, start=1):
        active = active_counts.get(question_id, 0)
        if not MIN_ACTIVE_OPTIONS <= active <= MAX_ACTIVE_OPTIONS:
            invalid_structure.append(position)
            continue
        correct = len(answer_key.get(question_id, ()))
        if modes.get(question_id) == QuestionAnswerMode.SINGLE.value:
            if correct != 1:
                invalid_single.append(position)
        elif correct < 2:
            invalid_multiple.append(position)

    if invalid_structure:
        blockers.append(
            "These questions do not have between "
            f"{MIN_ACTIVE_OPTIONS} and {MAX_ACTIVE_OPTIONS} answer options: "
            + _positions(invalid_structure)
            + "."
        )
    if invalid_single:
        blockers.append(
            "These single-answer questions do not have exactly one correct option: "
            + _positions(invalid_single)
            + "."
        )
    if invalid_multiple:
        blockers.append(
            "These multiple-answer questions do not have at least two correct options: "
            + _positions(invalid_multiple)
            + "."
        )
    return blockers


def _positions(numbers):
    return ", ".join(str(number) for number in numbers)


# ---------------------------------------------------------------------------
# Attempt bookkeeping
# ---------------------------------------------------------------------------


def quiz_has_attempt_history(quiz_id):
    """True if **any** attempt row exists for this Quiz.

    The permanent authoring freeze. Bounded by construction: it asks for
    one id and stops. An expired attempt with no saved answers counts
    exactly like a submitted one -- a Student still read that exact
    wording, and rewriting it afterwards would change what their attempt
    was an attempt at.
    """
    return (
        db.session.query(QuizAttempt.id).filter_by(quiz_id=quiz_id).first() is not None
    )


def student_attempt_rows(quiz_id, student_id):
    """One Student's attempts at one Quiz, newest first, hard-limited to
    ``MAX_ATTEMPT_LIMIT + 1`` rows.

    The ``+ 1`` detects a Quiz whose stored attempts already exceed its
    own limit without an unbounded read. Resolves through
    ``uq_quiz_attempts_quiz_student_number``'s leftmost columns.
    """
    from app.models import MAX_ATTEMPT_LIMIT

    return (
        QuizAttempt.query.filter_by(quiz_id=quiz_id, student_id=student_id).filter(active_episode_record(QuizAttempt))
        .order_by(QuizAttempt.attempt_number.desc())
        .limit(MAX_ATTEMPT_LIMIT + 1)
        .all()
    )


def next_attempt_number(attempts):
    """The number a new attempt should take, from an already-read list."""
    return 1 + max((attempt.attempt_number for attempt in attempts), default=0)


def in_progress_attempt(attempts):
    """The Student's single open attempt from an already-read list, or
    ``None``. A repeated valid start returns this rather than creating a
    duplicate."""
    for attempt in attempts:
        if attempt.status == _IN_PROGRESS:
            return attempt
    return None

from app.services.episode_queries import active_episode_record
