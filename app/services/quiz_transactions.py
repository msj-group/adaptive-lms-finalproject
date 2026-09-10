"""Shared locking primitives for the Quiz attempt aggregate
(Phase 4 / M04D).

Flask-independent -- no ``abort``, ``flash``, ``redirect`` or template
rendering, exactly like ``app/services/group_transactions.py``. Route-level
404 and error handling belongs in the Blueprint modules that call this.

**Phase 4 / M05 extends this without forking it.** A Listening activity
is one ordinary Quiz row plus a ``ListeningActivity`` extension row, so it
reuses every primitive here unchanged and inserts exactly one new link --
the extension row, locked immediately after its Quiz. Nothing about the
attempt, answer, selection, grading or expiry path is duplicated for it.

**This module exists so the Teacher and the Student surfaces can share one
lock order.** Both must settle an overdue attempt before they read or
write it, and both must take the same rows in the same sequence. Putting
that in a service is the alternative to one Blueprint importing another
Blueprint's private helpers, which would couple two independent surfaces
through their internals.

The established single-reset academic prefix is preserved and extended
deterministically:

    AcademicTerm -> Level -> Course      (lock_academic_hierarchy, which
                                          owns the one deliberate reset)
    -> Group
    -> acting User
    -> Enrollment / GroupTeacherAssignment
    -> Quiz
    -> ListeningActivity        (Phase 4 / M05, only on that surface)
    -> QuizAttempt rows, ascending internal id
    -> QuizQuestion rows, ascending internal id
    -> QuestionOption rows, ascending internal id
    -> QuizAnswer -> QuizAnswerSelection

Ascending internal id at every level, never visual or authored order: that
is the project-wide rule that keeps two concurrent writers from
deadlocking against each other.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so this code runs correctly in tests
without actually locking anything. Tests can assert the *requested* lock
set and order (structural); they prove nothing about real InnoDB blocking.
"""

from app.extensions import db
from app.models import (
    Group,
    QuestionOption,
    QuizAnswer,
    QuizAnswerSelection,
    QuizAttempt,
    QuizAttemptStatus,
    Quiz,
    QuizQuestion,
)
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.quiz_attempts import expire_if_due

#: How many overdue attempts one request will settle. A page shows at most
#: this many rows, so settling more would be work nobody is about to read
#: -- and an unbounded write triggered by a GET is exactly what a long
#: attempt history must never be able to cause.
SETTLE_BATCH = 20


def lock_quiz_row(quiz_id):
    """Lock one Quiz ``FOR UPDATE`` inside the already-open transaction.

    The Quiz is the serialization point for its whole aggregate: every
    question, option, attempt, answer and selection write locks it first,
    so concurrent authoring, publication, attempt starts and answer saves
    on one Quiz serialize instead of racing.
    """
    return Quiz.query.filter_by(id=quiz_id).with_for_update().first()


def lock_attempt_rows(attempt_ids):
    """Lock the given QuizAttempt rows ``FOR UPDATE``, ascending internal
    id. Returns ``{id: row_or_None}``; a ``None`` is a rejection the
    caller must handle, never something to keep going past."""
    rows = {}
    for attempt_id in sorted({a for a in attempt_ids if a is not None}):
        rows[attempt_id] = (
            QuizAttempt.query.filter_by(id=attempt_id).with_for_update().first()
        )
    return rows


def lock_answer_row(attempt_id, question_id):
    """Lock this attempt's existing answer for one question, or ``None``.

    Located by ``uq_quiz_answers_attempt_question``. For a question not
    yet answered there is no row, and the unique constraint remains the
    final defense behind the insert the caller then performs.
    """
    return (
        QuizAnswer.query.filter_by(attempt_id=attempt_id, question_id=question_id)
        .with_for_update()
        .first()
    )


def lock_question_row(question_id):
    """Lock one QuizQuestion ``FOR UPDATE`` inside the already-open
    transaction, or return ``None``.

    Taken after the Quiz row and before that question's options, which is
    the documented order. The caller must treat a ``None`` -- and a row
    whose ``quiz_id`` is not the locked Quiz -- as a rejection, roll back
    and 404; never as something to keep going past.
    """
    return QuizQuestion.query.filter_by(id=question_id).with_for_update().first()


def lock_active_option_rows(question_id):
    """Lock one question's **active** options and return them keyed by
    ``public_id``.

    Two statements' worth of shape, and deliberately so: an id-only read
    decides *which* rows to lock, then each row is locked individually in
    **ascending internal id** -- never in authored order -- which is the
    project-wide rule that keeps two concurrent writers from deadlocking
    against each other. The entities the caller then decides on are the
    ones the ``SELECT ... FOR UPDATE`` statements loaded, not ones an
    earlier read may have cached.

    A row that stopped being active between the id read and its own lock
    is dropped rather than silently treated as live. Keying by
    ``public_id`` is what lets the caller match submitted identifiers
    against the locked rows without ever exposing an internal id.

    Shared by the ordinary Quiz and the Listening answer paths so the two
    cannot drift in what they accept.
    """
    option_ids = [
        row.id
        for row in db.session.query(QuestionOption.id)
        .filter(
            QuestionOption.question_id == question_id,
            QuestionOption.is_active.is_(True),
        )
        .order_by(QuestionOption.id.asc())
        .all()
    ]
    locked = {}
    for option_id in option_ids:
        option = QuestionOption.query.filter_by(id=option_id).with_for_update().first()
        if option is not None and option.is_active:
            locked[option.public_id] = option
    return locked


def replace_answer_selections(attempt, question, chosen_options, moment):
    """Persist one saved answer's complete selection set, replacing
    whatever was there.

    **Replacement, not accumulation**: the previous selection rows are
    deleted and the new ones inserted inside the **same** transaction, so
    no reader ever observes a half-replaced set and a failure leaves the
    previous set intact. That delete is the one place in the Quiz
    aggregate where rows are removed, and it is confined to the selections
    of an attempt the same Student is still editing -- never authored
    content and never a finalized result.

    A question with no answer row yet gets one; the unique constraint
    ``uq_quiz_answers_attempt_question`` remains the final defense behind
    the locked lookup, and the caller catches the resulting
    ``IntegrityError``.

    The caller must already hold the Quiz, attempt, question and option
    locks, must have proved every option belongs to this question's
    **active** set, and must commit. Nothing here authorizes anything.
    """
    answer = lock_answer_row(attempt.id, question.id)
    if answer is None:
        answer = QuizAnswer(
            attempt_id=attempt.id,
            question_id=question.id,
            created_at=moment,
            updated_at=moment,
        )
        db.session.add(answer)
        db.session.flush()
    else:
        QuizAnswerSelection.query.filter_by(answer_id=answer.id).delete(
            synchronize_session=False
        )
        answer.updated_at = moment

    for option in chosen_options:
        db.session.add(
            QuizAnswerSelection(
                answer_id=answer.id, option_id=option.id, created_at=moment
            )
        )
    return answer


def lock_quiz_aggregate(group_public_id, term_id, level_id, course_id, actor_id):
    """Take the shared academic prefix through the Group and the acting
    User, in one open transaction, with the single deliberate reset.

    Returns ``(hierarchy, group, actor)``. Either row may be ``None`` --
    the caller must treat that as an authorization rejection, roll back,
    and 404; it must never "keep going". The caller then adds its own
    membership row (Enrollment for a Student, GroupTeacherAssignment for a
    Teacher), the Quiz, and whatever else it will decide on, **without a
    second reset**.
    """
    from app.models import User

    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    actor = User.query.filter_by(id=actor_id).with_for_update().first()
    return hierarchy, group, actor


def settle_due_attempts(group_public_id, quiz_id, reference_utc, attempt_ids=None):
    """Finalize this Quiz's overdue in-progress attempts under the
    required locks, then commit.

    This is what makes expiry **request-driven rather than a background
    job**: every authorized read and write of an attempt calls it first,
    so no page can show a Student or a Teacher an attempt that claims to
    be running when its deadline is already behind it, and two observers
    of the same expiry cannot disagree.

    Bounded three ways: at most :data:`SETTLE_BATCH` attempts per call,
    at most one page's worth of grading queries each, and nothing at all
    when no attempt is due -- the common case costs one indexed read and
    a rollback.

    Idempotent. An attempt another request already finalized is left
    exactly as it is, and ``expire_if_due`` refuses rather than
    overwrites.

    **Performs its own deliberate reset** through
    :func:`lock_quiz_aggregate`, so the caller must treat every ORM object
    it read beforehand as expired and re-read anything it still needs.
    Callers pass plain scalars in for exactly that reason.

    Returns the number of attempts this call finalized.
    """
    due = QuizAttempt.query.filter(
        QuizAttempt.quiz_id == quiz_id,
        QuizAttempt.status == QuizAttemptStatus.IN_PROGRESS.value,
        QuizAttempt.deadline_at <= reference_utc,
    )
    if attempt_ids is not None:
        ids = [a for a in attempt_ids if a is not None]
        if not ids:
            return 0
        due = due.filter(QuizAttempt.id.in_(ids))
    candidate_ids = [
        row.id
        for row in due.with_entities(QuizAttempt.id)
        .order_by(QuizAttempt.id.asc())
        .limit(SETTLE_BATCH)
        .all()
    ]
    if not candidate_ids:
        return 0

    preview_group = Group.query.filter_by(public_id=group_public_id).first()
    if preview_group is None:
        return 0
    term_id = preview_group.academic_term_id
    course_id = preview_group.course_id
    level_id = preview_group.course.level_id

    lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    if group is None:
        db.session.rollback()
        return 0
    quiz = lock_quiz_row(quiz_id)
    if quiz is None or quiz.group_id != group.id:
        db.session.rollback()
        return 0

    finalized = 0
    for attempt in lock_attempt_rows(candidate_ids).values():
        if attempt is None or attempt.quiz_id != quiz.id:
            continue
        if expire_if_due(attempt, reference_utc):
            finalized += 1

    if not finalized:
        db.session.rollback()
        return 0
    try:
        db.session.commit()
    except Exception:
        # Settling is a best-effort housekeeping step on the way to a
        # read. A failure here must never turn an authorized page into an
        # error: roll back and let the caller render the attempt exactly
        # as it is still stored. The next request tries again.
        db.session.rollback()
        return 0
    return finalized
