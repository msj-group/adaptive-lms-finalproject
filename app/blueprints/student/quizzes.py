"""Student Quiz attempts: starting, answering, submitting and reading a
result (Phase 4 / M04D).

Routes, all Group- and Quiz-scoped by public identifier:

    GET   /student/quizzes
    GET   /student/groups/<g>/quizzes/<q>
    POST  /student/groups/<g>/quizzes/<q>/start
    GET   /student/groups/<g>/quizzes/<q>/attempts/<a>/questions/<x>
    POST  /student/groups/<g>/quizzes/<q>/attempts/<a>/questions/<x>/answer
    POST  /student/groups/<g>/quizzes/<q>/attempts/<a>/questions/<x>/bookmark
    POST  /student/groups/<g>/quizzes/<q>/attempts/<a>/submit
    GET   /student/groups/<g>/quizzes/<q>/attempts/<a>/result

There is deliberately no delete route, no draft-attempt route, no
"reopen", no answer-key route and no way for a Student to write anything
except their own selections and session review flags on their own in-progress
attempt. Review flags never affect grading, limits or deadlines.

**Authorization is SQL-scoped**, exactly as it is for M01 Assignments: a
Quiz reaches a Student only through
``quiz_queries.student_visible_quiz_query``, whose ``WHERE`` clause is
the whole effective-visibility formula -- this Student, with the Student
role and an active account, holding an **active** Enrollment in the Quiz's
Group, with the AcademicTerm / Level / Course / Group all active, the Quiz
``published``, and its ``opens_at`` reached. A draft, a not-yet-open Quiz,
another Group's public id, a withdrawn Enrollment, an archived ancestor
and a nonexistent id all produce the identical non-disclosing **404**.
Every attempt lookup is additionally scoped to **both** the authorized
Quiz and the authenticated ``student_id``, so one Student can never reach
another's attempt.

**Reading survives ``closes_at``; starting does not.** A closed Quiz stays
visible so a Student can always reach their receipt, but no attempt may be
started at or after the closing moment.

**Expiry is request-driven.** Every route that touches an attempt settles
it first through ``quiz_transactions.settle_due_attempts``, so no page can
show an attempt that claims to be running when its deadline has passed,
and two observers of the same expiry cannot disagree. There is no
background job.

**The answer key never reaches this blueprint.** No Student-facing query
selects ``QuestionOption.is_correct``, no dict built here carries it, and
no token contains it -- so it cannot leak through a page, a form, a URL or
a flash, before, during or after the Quiz's window. The results page
reports right/wrong per question and nothing more.

All responses carry ``Cache-Control: private, no-store`` and
``Vary: Cookie``: these pages are per-Student and time-gated, so a shared
or reused cache entry could show one Student another's attempt, or show a
question after the attempt that could answer it ended.
"""

from flask import abort, current_app, flash, jsonify, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError

from app.blueprints.collector.hooks import note_outcome
from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store, private_redirect
from app.services.request_arrival import submission_received_at
from app.services.timely_submission import accept_timely_submission, is_timely
from app.extensions import db
from app.models import (
    MAX_QUIZ_QUESTIONS,
    AcademicStatus,
    Enrollment,
    EnrollmentStatus,
    QuestionAnswerMode,
    QuizAttempt,
    QuizAttemptStatus,
    QuizStatus,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.quiz_attempts import (
    ATTEMPT_STATUS_LABELS,
    STATE_LABELS,
    availability_state,
    can_start_attempt_now,
    compute_deadline,
    expire_if_due,
    finalize_attempt,
    in_progress_attempt,
    next_attempt_number,
    percentage,
    quiz_question_ids,
    student_attempt_rows,
)
from app.services.quiz_queries import (
    PAGE_SIZE,
    active_options_ordered,
    answered_question_ids,
    attempt_counts_by_quiz,
    attempt_selected_option_ids,
    build_student_option_rows,
    build_student_quiz_view,
    first_question_public_id,
    normalize_page,
    question_navigation,
    student_quiz,
    student_question_index,
    student_quizzes_page,
    student_result_rows,
    teacher_question,
)
from app.services.quiz_transactions import (
    lock_active_option_rows,
    lock_attempt_rows,
    lock_question_row,
    lock_quiz_aggregate,
    lock_quiz_row,
    replace_answer_selections,
    settle_due_attempts,
)
from app.services.schedule_occurrences import to_app_local, utc_reference_now
from app.services.quiz_bookmarks import is_bookmarked, read_bookmarks, write_bookmark

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_PUBLISHED = QuizStatus.PUBLISHED.value
_IN_PROGRESS = QuizAttemptStatus.IN_PROGRESS.value
_SUBMITTED = QuizAttemptStatus.SUBMITTED.value

#: The one sentence for a stale or replayed form, so every rejection path
#: explains it the same way.
_STALE_MESSAGE = (
    "This page was out of date, so nothing was saved. Please read the current page and try "
    "again."
)


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _now():
    """The request's authoritative naive-UTC whole-second moment.

    Whole seconds because the ``DATETIME`` columns hold whole seconds on
    MySQL, and **one** value per request so a page's availability label,
    its deadline arithmetic and anything it writes can never straddle a
    boundary and contradict each other.
    """
    return utc_reference_now().replace(microsecond=0)


# ======================================================================
# URLs
# ======================================================================


def _quiz_url(group_public_id, quiz_public_id):
    return url_for(
        "student.quiz_detail",
        group_public_id=group_public_id,
        quiz_public_id=quiz_public_id,
    )


def _question_url(group_public_id, quiz_public_id, attempt_public_id, question_public_id):
    return url_for(
        "student.quiz_question",
        group_public_id=group_public_id,
        quiz_public_id=quiz_public_id,
        attempt_public_id=attempt_public_id,
        question_public_id=question_public_id,
    )


def _result_url(group_public_id, quiz_public_id, attempt_public_id):
    return url_for(
        "student.quiz_result",
        group_public_id=group_public_id,
        quiz_public_id=quiz_public_id,
        attempt_public_id=attempt_public_id,
    )


def _autosave_request():
    """Response negotiation only; never an authorization or CSRF bypass."""
    return request.headers.get("X-Quiz-Autosave") == "1"


def _quiz_json(status=200, **payload):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


# ======================================================================
# Signed answer and submission tokens
# ======================================================================
#
# Dedicated M04D salts and exact purpose markers. A token minted under any
# other salt -- including every Teacher-side one -- fails signature
# verification here.
#
# Payloads carry **public identifiers only**. A signed token is
# authenticated, not encrypted: anyone holding it can read it, so no
# prompt text, no option text, no selected answer and no correct-answer
# data is ever placed in one.

_ANSWER_SALT = "student.quiz-answer.phase4-m04d.v1"
_SUBMIT_SALT = "student.quiz-submit.phase4-m04d.v1"

_ANSWER_PURPOSE = "quiz-answer"
_SUBMIT_PURPOSE = "quiz-submit"

_ANSWER_FIELDS = (
    "purpose",
    "student_public_id",
    "group_public_id",
    "quiz_public_id",
    "attempt_public_id",
    "question_public_id",
    "quiz_version",
    "attempt_status",
)
_SUBMIT_FIELDS = (
    "purpose",
    "student_public_id",
    "group_public_id",
    "quiz_public_id",
    "attempt_public_id",
    "quiz_version",
    "attempt_status",
)


def _serializer(salt):
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=salt)


def _positive_int(value):
    """A genuine positive ``int``. ``bool`` is excluded explicitly -- it is
    an ``int`` subclass, and ``True`` must never pass as version 1."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _all_strings(values):
    return all(isinstance(value, str) for value in values)


def _make_answer_token(
    student_public_id, group_public_id, quiz_public_id, attempt_public_id,
    question_public_id, quiz_version, attempt_status,
):
    return _serializer(_ANSWER_SALT).dumps(
        {
            "purpose": _ANSWER_PURPOSE,
            "student_public_id": student_public_id,
            "group_public_id": group_public_id,
            "quiz_public_id": quiz_public_id,
            "attempt_public_id": attempt_public_id,
            "question_public_id": question_public_id,
            "quiz_version": quiz_version,
            "attempt_status": attempt_status,
        }
    )


def _make_submit_token(
    student_public_id, group_public_id, quiz_public_id, attempt_public_id,
    quiz_version, attempt_status,
):
    return _serializer(_SUBMIT_SALT).dumps(
        {
            "purpose": _SUBMIT_PURPOSE,
            "student_public_id": student_public_id,
            "group_public_id": group_public_id,
            "quiz_public_id": quiz_public_id,
            "attempt_public_id": attempt_public_id,
            "quiz_version": quiz_version,
            "attempt_status": attempt_status,
        }
    )


def _load_token(token, salt, purpose, fields, id_fields):
    """Exact-shape validation shared by both token kinds.

    The key set must match exactly, the purpose must be the expected one,
    every identifier must be a string, ``quiz_version`` must be a genuine
    positive ``int``, and ``attempt_status`` must be a known member. Any
    failure returns ``None`` and is treated exactly like an outdated
    token: rejected, never trusted, never silently upgraded.
    """
    if not token:
        return None
    try:
        payload = _serializer(salt).loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(fields):
        return None
    if payload["purpose"] != purpose:
        return None
    if not _all_strings([payload[name] for name in id_fields]):
        return None
    if not _positive_int(payload["quiz_version"]):
        return None
    if payload["attempt_status"] not in {status.value for status in QuizAttemptStatus}:
        return None
    return payload


def _answer_token_is_stale(
    token, student_public_id, group_public_id, quiz_public_id, attempt_public_id,
    question_public_id, quiz, attempt,
):
    """True when `token` does not exactly describe the **locked** rows.

    Binding ``attempt_status`` is what makes a form opened while the
    attempt was running fail closed once it has been submitted or has
    expired -- a lock alone would happily write into a finalized attempt.
    Binding ``quiz_version`` catches a Quiz that changed underneath, which
    the publication freeze should already prevent and which is therefore
    worth refusing loudly rather than absorbing.
    """
    payload = _load_token(
        token, _ANSWER_SALT, _ANSWER_PURPOSE, _ANSWER_FIELDS, _ANSWER_FIELDS[1:6]
    )
    if payload is None:
        return True
    return (
        payload["student_public_id"] != student_public_id
        or payload["group_public_id"] != group_public_id
        or payload["quiz_public_id"] != quiz_public_id
        or payload["attempt_public_id"] != attempt_public_id
        or payload["question_public_id"] != question_public_id
        or payload["quiz_version"] != quiz.version
        or payload["attempt_status"] != attempt.status
    )


def _submit_token_is_stale(
    token, student_public_id, group_public_id, quiz_public_id, attempt_public_id,
    quiz, attempt, *, expected_status=None,
):
    payload = _load_token(
        token, _SUBMIT_SALT, _SUBMIT_PURPOSE, _SUBMIT_FIELDS, _SUBMIT_FIELDS[1:5]
    )
    if payload is None:
        return True
    return (
        payload["student_public_id"] != student_public_id
        or payload["group_public_id"] != group_public_id
        or payload["quiz_public_id"] != quiz_public_id
        or payload["attempt_public_id"] != attempt_public_id
        or payload["quiz_version"] != quiz.version
        or payload["attempt_status"] != (expected_status or attempt.status)
    )


# ======================================================================
# Shared authorization
# ======================================================================


def _visible_quiz_or_404(group_public_id, quiz_public_id, reference_utc):
    """The Quiz row tuple this Student may currently see, or a
    non-disclosing 404. The query's ``WHERE`` clause is the
    authorization -- nothing is loaded broadly and filtered afterwards."""
    row = student_quiz(
        current_user.id, group_public_id, quiz_public_id, reference_utc
    )
    if row is None:
        abort(404)
    return row


def _own_attempt_or_404(quiz_id, attempt_public_id):
    """One attempt scoped to **both** the authorized Quiz and the
    authenticated Student. Another Student's attempt, an attempt under a
    different Quiz, and a nonexistent id all 404 identically."""
    attempt = QuizAttempt.query.filter_by(
        public_id=attempt_public_id, quiz_id=quiz_id, student_id=current_user.id
    ).filter(active_episode_record(QuizAttempt)).first()
    if attempt is None:
        abort(404)
    return attempt


def _lock_student_chain(group_public_id, term_id, level_id, course_id, student_id):
    """The shared academic prefix plus this Student's Enrollment.

    Returns ``(hierarchy, group, student, enrollment)``. Any ``None`` is
    an authorization rejection the caller must roll back and 404 -- never
    something to keep going past.
    """
    hierarchy, group, student = lock_quiz_aggregate(
        group_public_id, term_id, level_id, course_id, student_id
    )
    enrollment = None
    if group is not None:
        enrollment = (
            Enrollment.query.filter_by(group_id=group.id, student_id=student_id, status="active")
            .with_for_update()
            .first()
        )
    return hierarchy, group, student, enrollment


def _student_authz_broken(group, student, enrollment):
    """True when the locked rows no longer authorize this Student.

    A foreign key into ``users`` proves the row exists, never that it is
    still a Student's or still active -- both are re-read here, together
    with the Enrollment, because either may have changed since the page
    was rendered.
    """
    if group is None or student is None or enrollment is None:
        return True
    if student.role != _STUDENT or student.status != _USER_ACTIVE:
        return True
    return enrollment.status != _ENROLLMENT_ACTIVE


def _hierarchy_broken(hierarchy, group, term_id, level_id, course_id):
    """True when the locked academic chain is no longer operational or no
    longer matches the Group."""
    term = hierarchy.term(term_id)
    level = hierarchy.level(level_id)
    course = hierarchy.course(course_id)
    if (
        course is None
        or group.course_id != course.id
        or group.academic_term_id != term_id
        or course.level_id != level_id
    ):
        return True
    return any(
        row is None or row.status != _ACTIVE for row in (term, level, course, group)
    )


# ======================================================================
# List
# ======================================================================


@student_bp.get("/quizzes")
@roles_required(UserRole.STUDENT.value)
def quiz_list():
    """Preserve old bookmarks through the unified Activities hub."""
    return private_redirect(url_for("student.activities", type="quiz"))


# ======================================================================
# Detail -- the start page and the Student's own attempt history
# ======================================================================


def _own_attempt_summaries(quiz_id, student_id, tz_name):
    """This Student's attempts at this Quiz, newest first.

    Bounded to ``MAX_ATTEMPT_LIMIT + 1`` rows by
    ``quiz_attempts.student_attempt_rows``. Carries the derived percentage
    but **never** any per-question or answer-key detail.
    """
    return [
        {
            "public_id": attempt.public_id,
            "attempt_number": attempt.attempt_number,
            "status": attempt.status,
            "status_label": ATTEMPT_STATUS_LABELS.get(attempt.status, attempt.status),
            "started_local": to_app_local(tz_name, attempt.started_at),
            "deadline_local": to_app_local(tz_name, attempt.deadline_at),
            "submitted_local": (
                to_app_local(tz_name, attempt.submitted_at)
                if attempt.submitted_at is not None
                else None
            ),
            "correct_count": attempt.correct_count,
            "total_questions": attempt.total_questions,
            "percentage": percentage(
                attempt.correct_count or 0, attempt.total_questions
            ),
            "is_finalized": attempt.is_finalized,
        }
        for attempt in student_attempt_rows(quiz_id, student_id)
    ]


@student_bp.get("/groups/<group_public_id>/quizzes/<quiz_public_id>")
@roles_required(UserRole.STUDENT.value)
def quiz_detail(group_public_id, quiz_public_id):
    """What this Quiz is, when it is available, and this Student's own
    attempts at it.

    Settles any overdue attempt of this Student's first, so the page can
    never offer to continue an attempt whose deadline has passed.
    """
    reference_utc = _now()
    row = _visible_quiz_or_404(group_public_id, quiz_public_id, reference_utc)
    quiz_id = row[0].id

    settle_due_attempts(group_public_id, quiz_id, reference_utc)

    # The settle step performs its own deliberate reset, so everything
    # read before it is expired -- re-read the display copies.
    row = _visible_quiz_or_404(group_public_id, quiz_public_id, reference_utc)
    quiz, group = row[0], row[1]
    tz_name = _tz_name()

    attempts = _own_attempt_summaries(quiz.id, current_user.id, tz_name)
    open_attempt = next(
        (item for item in attempts if item["status"] == _IN_PROGRESS), None
    )
    state = availability_state(quiz, reference_utc)
    used = len(attempts)

    return private_no_store(
        "student/quizzes/detail.html",
        group_public_id=group.public_id,
        quiz={
            "public_id": quiz.public_id,
            "title": quiz.title,
            "instructions": quiz.instructions,
            "group_name": group.name,
            "opens_local": to_app_local(tz_name, quiz.opens_at),
            "closes_local": to_app_local(tz_name, quiz.closes_at),
            "time_limit_minutes": quiz.time_limit_minutes,
            "attempt_limit": quiz.attempt_limit,
            "state": state,
            "state_label": STATE_LABELS.get(state),
        },
        attempts=attempts,
        open_attempt=open_attempt,
        attempts_used=used,
        attempts_left=max(quiz.attempt_limit - used, 0),
        can_start=(
            can_start_attempt_now(quiz, reference_utc)
            and (open_attempt is not None or used < quiz.attempt_limit)
        ),
        question_total=len(quiz_question_ids(quiz.id)),
        tz_name=tz_name,
    )


# ======================================================================
# Start an attempt
# ======================================================================


@student_bp.post("/groups/<group_public_id>/quizzes/<quiz_public_id>/start")
@roles_required(UserRole.STUDENT.value)
def quiz_start(group_public_id, quiz_public_id):
    """Start -- or resume -- this Student's attempt at a published Quiz.

    **Nothing about the attempt comes from the request.** The attempt
    number is read from the locked rows, the deadline is computed from the
    locked Quiz and the server's own moment, ``public_id`` is
    server-generated and ``quiz_version`` is copied from the locked Quiz.
    A forged ``attempt_number`` / ``deadline_at`` / ``status`` /
    ``correct_count`` field has nowhere to land: the form carries only its
    CSRF token.

    **A repeated valid start returns the existing in-progress attempt**
    rather than creating a duplicate, and a genuine concurrent start that
    loses the unique-constraint race re-reads and returns the winner's
    attempt instead of reporting a failure the Student cannot act on.
    """
    reference_utc = _now()
    preview = _visible_quiz_or_404(group_public_id, quiz_public_id, reference_utc)
    preview_quiz, preview_group = preview[0], preview[1]

    student_id = current_user.id
    quiz_id = preview_quiz.id
    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id

    settle_due_attempts(group_public_id, quiz_id, reference_utc)

    hierarchy, group, student, enrollment = _lock_student_chain(
        group_public_id, term_id, level_id, course_id, student_id
    )
    if _student_authz_broken(group, student, enrollment):
        db.session.rollback()
        abort(404)
    if _hierarchy_broken(hierarchy, group, term_id, level_id, course_id):
        db.session.rollback()
        abort(404)

    quiz = lock_quiz_row(quiz_id)
    if quiz is None or quiz.group_id != group.id or quiz.public_id != quiz_public_id:
        db.session.rollback()
        abort(404)
    if quiz.status != _PUBLISHED:
        db.session.rollback()
        abort(404)

    if not can_start_attempt_now(quiz, reference_utc):
        db.session.rollback()
        note_outcome("quiz_start", "refused")
        flash(
            "This quiz is not open right now, so a new attempt cannot be started.", "danger"
        )
        return redirect(_quiz_url(group_public_id, quiz_public_id))

    attempts = student_attempt_rows(quiz.id, student_id)
    existing = in_progress_attempt(attempts)
    if existing is not None:
        # Already running: return it rather than creating a second one.
        # The at-most-one-in-progress rule is a cross-row invariant, so it
        # is enforced here against the locked rows, not by a constraint.
        note_outcome("quiz_start", "resumed")
        db.session.rollback()
        return redirect(_first_question_redirect(group_public_id, quiz_public_id, existing))

    if len(attempts) >= quiz.attempt_limit:
        db.session.rollback()
        note_outcome("quiz_start", "refused")
        flash(
            f"You have used all {quiz.attempt_limit} attempts at this quiz.", "danger"
        )
        return redirect(_quiz_url(group_public_id, quiz_public_id))

    attempt = QuizAttempt(
        quiz_id=quiz.id,
        student_id=student_id,
        attempt_number=next_attempt_number(attempts),
        status=_IN_PROGRESS,
        quiz_version=quiz.version,
        started_at=reference_utc,
        deadline_at=compute_deadline(quiz, reference_utc),
        submitted_at=None,
        correct_count=None,
        total_questions=None,
    )
    db.session.add(attempt)
    try:
        db.session.commit()
    except IntegrityError:
        # Roll back FIRST -- everything read is now discarded state. Then
        # re-authorize from scratch before revealing anything: whatever
        # raised the error may equally have been a concurrent change that
        # ended this Student's access.
        db.session.rollback()
        recovered = student_quiz(
            student_id, group_public_id, quiz_public_id, reference_utc
        )
        if recovered is None:
            abort(404)
        # The realistic cause is a genuine concurrent start that claimed
        # this attempt number first. Returning the winner's attempt is
        # both correct and what the Student wanted.
        rerun = in_progress_attempt(
            student_attempt_rows(recovered[0].id, student_id)
        )
        if rerun is not None:
            note_outcome("quiz_start", "resumed")
            return redirect(
                _first_question_redirect(group_public_id, quiz_public_id, rerun)
            )
        note_outcome("quiz_start", "refused")
        flash(
            "This attempt could not be started. Please reload the page and try again.",
            "danger",
        )
        return redirect(_quiz_url(group_public_id, quiz_public_id))

    note_outcome("quiz_start", "started")
    return redirect(_first_question_redirect(group_public_id, quiz_public_id, attempt))


def _first_question_redirect(group_public_id, quiz_public_id, attempt):
    """Where a started or resumed attempt lands.

    A Quiz with no questions cannot be published, so the absence of a
    first question means the aggregate is broken; the Student is returned
    to the Quiz page rather than to a URL that cannot exist.
    """
    first = first_question_public_id(attempt.quiz_id)
    if first is None:
        return _quiz_url(group_public_id, quiz_public_id)
    return _question_url(group_public_id, quiz_public_id, attempt.public_id, first)


# ======================================================================
# Taking the attempt -- one question at a time
# ======================================================================


@student_bp.get(
    "/groups/<group_public_id>/quizzes/<quiz_public_id>"
    "/attempts/<attempt_public_id>/questions/<question_public_id>"
)
@roles_required(UserRole.STUDENT.value)
def quiz_question(
    group_public_id, quiz_public_id, attempt_public_id, question_public_id
):
    """One question of one in-progress attempt.

    Fetches only what this page needs: the current question, its bounded
    active options, this attempt's saved selections for it, and bounded
    question identifiers/progress. No other question's prompt or
    options, and never a per-question query loop.
    """
    reference_utc = _now()
    preview = _visible_quiz_or_404(group_public_id, quiz_public_id, reference_utc)
    quiz_id = preview[0].id
    preview_attempt = _own_attempt_or_404(quiz_id, attempt_public_id)

    settle_due_attempts(
        group_public_id, quiz_id, reference_utc, attempt_ids=[preview_attempt.id]
    )

    row = _visible_quiz_or_404(group_public_id, quiz_public_id, reference_utc)
    quiz = row[0]
    attempt = _own_attempt_or_404(quiz.id, attempt_public_id)
    if attempt.is_finalized:
        return redirect(_result_url(group_public_id, quiz_public_id, attempt_public_id))

    question = teacher_question(quiz.id, question_public_id)
    if question is None:
        abort(404)
    index_rows = student_question_index(quiz.id)
    public_ids = [row.public_id for row in index_rows]
    if len(index_rows) > MAX_QUIZ_QUESTIONS or question_public_id not in public_ids:
        abort(404)
    index = public_ids.index(question_public_id)
    position, total = index + 1, len(public_ids)
    previous_id = public_ids[index - 1] if index else None
    next_id = public_ids[index + 1] if index + 1 < total else None

    options = active_options_ordered(question.id)
    selected = attempt_selected_option_ids(attempt.id, question.id)
    question_ids = [row.id for row in index_rows]
    answered = answered_question_ids(attempt.id, question_ids)
    bookmarks = read_bookmarks(current_user.public_id, current_user.auth_version)
    question_links = [
        {
            "public_id": item.public_id,
            "position": number,
            "answered": item.id in answered,
            "bookmarked": is_bookmarked(bookmarks, attempt_public_id, number),
        }
        for number, item in enumerate(index_rows, 1)
    ]
    tz_name = _tz_name()

    return private_no_store(
        "student/quizzes/question.html",
        group_public_id=group_public_id,
        quiz={
            "public_id": quiz.public_id,
            "title": quiz.title,
            "time_limit_minutes": quiz.time_limit_minutes,
        },
        attempt={
            "public_id": attempt.public_id,
            "attempt_number": attempt.attempt_number,
            "deadline_local": to_app_local(tz_name, attempt.deadline_at),
            # ISO-8601 UTC for the display-only countdown script. The
            # server remains the sole authority on expiry; this value only
            # renders a clock.
            "deadline_iso": attempt.deadline_at.isoformat() + "Z",
        },
        question={
            "public_id": question.public_id,
            "prompt": question.prompt,
            "answer_mode": question.answer_mode,
            "is_multiple": question.answer_mode == QuestionAnswerMode.MULTIPLE.value,
            "options": build_student_option_rows(options, selected),
        },
        position=position,
        total=total,
        previous_public_id=previous_id,
        next_public_id=next_id,
        answered_count=len(answered),
        unanswered_count=max(total - len(answered), 0),
        question_links=question_links,
        bookmarked=question_links[index]["bookmarked"],
        answer_token=_make_answer_token(
            current_user.public_id, group_public_id, quiz_public_id,
            attempt_public_id, question_public_id, quiz.version, attempt.status,
        ),
        submit_token=_make_submit_token(
            current_user.public_id, group_public_id, quiz_public_id,
            attempt_public_id, quiz.version, attempt.status,
        ),
        tz_name=tz_name,
    )


@student_bp.post(
    "/groups/<group_public_id>/quizzes/<quiz_public_id>"
    "/attempts/<attempt_public_id>/questions/<question_public_id>/answer"
)
@roles_required(UserRole.STUDENT.value)
def quiz_answer(
    group_public_id, quiz_public_id, attempt_public_id, question_public_id
):
    """Save this attempt's selections for one question, replacing whatever
    was saved before.

    **Only active options of this exact question are accepted**, proved
    against the locked rows. A retired option, an option of another
    question, an option of another Quiz and a repeated identifier are all
    refused identically, and nothing is written.

    Cardinality while saving: a single-answer question needs exactly one
    selection; a multiple-answer question needs at least one. Autosave also
    accepts an empty set to clear selections without deleting answer history.
    Such a question is unanswered, including for final submission. Final
    correctness still requires the complete exact set, which is a grading
    rule rather than a saving one -- a Student is allowed to save a
    partial answer and come back to it.
    """
    reference_utc = _now()
    preview = _visible_quiz_or_404(group_public_id, quiz_public_id, reference_utc)
    preview_quiz, preview_group = preview[0], preview[1]
    preview_attempt = _own_attempt_or_404(preview_quiz.id, attempt_public_id)

    student_id = current_user.id
    student_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    attempt_id = preview_attempt.id
    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id
    submitted_token = request.form.get("answer_state", "")
    submitted_options = request.form.getlist("option")

    settle_due_attempts(
        group_public_id, quiz_id, reference_utc, attempt_ids=[attempt_id]
    )

    hierarchy, group, student, enrollment = _lock_student_chain(
        group_public_id, term_id, level_id, course_id, student_id
    )
    if _student_authz_broken(group, student, enrollment):
        db.session.rollback()
        abort(404)
    if _hierarchy_broken(hierarchy, group, term_id, level_id, course_id):
        db.session.rollback()
        abort(404)

    quiz = lock_quiz_row(quiz_id)
    if quiz is None or quiz.group_id != group.id or quiz.public_id != quiz_public_id:
        db.session.rollback()
        abort(404)
    if quiz.status != _PUBLISHED:
        db.session.rollback()
        abort(404)

    attempt = lock_attempt_rows([attempt_id]).get(attempt_id)
    if (
        attempt is None
        or attempt.quiz_id != quiz.id
        or attempt.student_id != student_id
        or attempt.enrollment_id != enrollment.id
        or attempt.public_id != attempt_public_id
    ):
        db.session.rollback()
        abort(404)

    # Expiry is decided here, under the locks, before anything is
    # accepted -- a request that arrives one second late must not write.
    if expire_if_due(attempt, reference_utc):
        db.session.commit()
        note_outcome("quiz_answer", "expired")
        if _autosave_request():
            return _quiz_json(
                409, error="Your time ran out, so this attempt was submitted as it was.",
                redirect_url=_result_url(group_public_id, quiz_public_id, attempt_public_id),
            )
        flash("Your time ran out, so this attempt was submitted as it was.", "danger")
        return redirect(_result_url(group_public_id, quiz_public_id, attempt_public_id))
    if attempt.is_finalized:
        db.session.rollback()
        if _autosave_request():
            return _quiz_json(
                409, error="This attempt has ended.",
                redirect_url=_result_url(group_public_id, quiz_public_id, attempt_public_id),
            )
        return redirect(_result_url(group_public_id, quiz_public_id, attempt_public_id))

    question = teacher_question(quiz.id, question_public_id)
    if question is None:
        db.session.rollback()
        abort(404)
    locked_question = lock_question_row(question.id)
    if locked_question is None or locked_question.quiz_id != quiz.id:
        db.session.rollback()
        abort(404)

    if _answer_token_is_stale(
        submitted_token, student_public_id, group_public_id, quiz_public_id,
        attempt_public_id, question_public_id, quiz, attempt,
    ):
        db.session.rollback()
        note_outcome("quiz_answer", "rejected")
        if _autosave_request():
            return _quiz_json(409, error=_STALE_MESSAGE)
        flash(_STALE_MESSAGE, "danger")
        return redirect(
            _question_url(
                group_public_id, quiz_public_id, attempt_public_id, question_public_id
            )
        )

    # The authoritative option set: this question's ACTIVE options, locked
    # in ascending internal id. Nothing the browser sent is trusted until
    # it has been matched against these rows. Since Phase 4 / M05 the
    # locking itself lives in `quiz_transactions.lock_active_option_rows`,
    # shared verbatim with the Listening answer path so the two surfaces
    # cannot drift in what they accept.
    locked_options = lock_active_option_rows(locked_question.id)

    error = _validate_selection(
        submitted_options, locked_options, locked_question, allow_empty=_autosave_request()
    )
    if error is not None:
        db.session.rollback()
        note_outcome("quiz_answer", "rejected")
        if _autosave_request():
            return _quiz_json(422, error=error)
        flash(error, "danger")
        return redirect(
            _question_url(
                group_public_id, quiz_public_id, attempt_public_id, question_public_id
            )
        )

    chosen = [locked_options[public_id] for public_id in dict.fromkeys(submitted_options)]

    # Replacement, not accumulation, inside this one transaction -- see
    # `quiz_transactions.replace_answer_selections`, which owns that rule
    # for both the ordinary Quiz and the Listening surfaces.
    try:
        replace_answer_selections(attempt, locked_question, chosen, reference_utc)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if student_quiz(
            student_id, group_public_id, quiz_public_id, reference_utc
        ) is None:
            abort(404)
        note_outcome("quiz_answer", "rejected")
        if _autosave_request():
            return _quiz_json(
                409, error="This answer could not be saved. Please reload the page and try again."
            )
        flash(
            "This answer could not be saved. Please reload the page and try again.",
            "danger",
        )
        return redirect(
            _question_url(
                group_public_id, quiz_public_id, attempt_public_id, question_public_id
            )
        )

    note_outcome("quiz_answer", "saved")
    if _autosave_request():
        return _quiz_json(question_public_id=question_public_id, answered=bool(submitted_options))
    navigation = question_navigation(quiz_id, question_public_id)
    next_id = navigation[3] if navigation else None
    flash("Answer saved.", "success")
    if request.form.get("go") == "next" and next_id:
        return redirect(
            _question_url(group_public_id, quiz_public_id, attempt_public_id, next_id)
        )
    return redirect(
        _question_url(
            group_public_id, quiz_public_id, attempt_public_id, question_public_id
        )
    )


def _validate_selection(submitted, locked_options, question, *, allow_empty=False):
    """``None`` when the submitted option identifiers are acceptable, else
    a Student-facing sentence.

    Every rejection is deliberately generic about *why* an identifier is
    unknown: a retired option, another question's option and an invented
    string must not be distinguishable from each other.
    """
    if len(submitted) != len(set(submitted)):
        return "That answer could not be read. Please reload the page and try again."
    if any(public_id not in locked_options for public_id in submitted):
        return "That answer could not be read. Please reload the page and try again."
    if allow_empty and not submitted:
        return None
    if question.answer_mode == QuestionAnswerMode.SINGLE.value:
        if len(submitted) != 1:
            return "Choose exactly one answer for this question."
        return None
    if len(submitted) < 1:
        return "Choose at least one answer for this question."
    return None


@student_bp.post(
    "/groups/<group_public_id>/quizzes/<quiz_public_id>"
    "/attempts/<attempt_public_id>/questions/<question_public_id>/bookmark"
)
@roles_required(UserRole.STUDENT.value)
def quiz_bookmark(group_public_id, quiz_public_id, attempt_public_id, question_public_id):
    """Toggle an own ongoing question's session review flag, never its answer."""
    reference_utc = _now()
    preview = _visible_quiz_or_404(group_public_id, quiz_public_id, reference_utc)
    quiz_id = preview[0].id
    preview_attempt = _own_attempt_or_404(quiz_id, attempt_public_id)
    settle_due_attempts(
        group_public_id, quiz_id, reference_utc, attempt_ids=[preview_attempt.id]
    )
    quiz = _visible_quiz_or_404(group_public_id, quiz_public_id, reference_utc)[0]
    attempt = _own_attempt_or_404(quiz.id, attempt_public_id)
    if attempt.is_finalized:
        target = _result_url(group_public_id, quiz_public_id, attempt_public_id)
        if _autosave_request():
            return _quiz_json(409, error="This attempt has ended.", redirect_url=target)
        return private_redirect(target)
    if _answer_token_is_stale(
        request.form.get("answer_state", ""), current_user.public_id,
        group_public_id, quiz_public_id, attempt_public_id, question_public_id, quiz, attempt,
    ):
        if _autosave_request():
            return _quiz_json(409, error=_STALE_MESSAGE)
        flash(_STALE_MESSAGE, "danger")
        return private_redirect(
            _question_url(group_public_id, quiz_public_id, attempt_public_id, question_public_id)
        )
    rows = student_question_index(quiz.id)
    ids = [row.public_id for row in rows]
    if len(rows) > MAX_QUIZ_QUESTIONS or question_public_id not in ids:
        abort(404)
    marking = request.form.get("bookmark")
    if marking not in ("yes", "no"):
        abort(400)
    state = read_bookmarks(current_user.public_id, current_user.auth_version)
    marked = marking == "yes"
    response = (
        _quiz_json(question_public_id=question_public_id, bookmarked=marked)
        if _autosave_request() else private_redirect(
            _question_url(group_public_id, quiz_public_id, attempt_public_id, question_public_id)
        )
    )
    return write_bookmark(
        response, state, attempt_public_id, ids.index(question_public_id) + 1, marked
    )


# ======================================================================
# Submit
# ======================================================================


@student_bp.post(
    "/groups/<group_public_id>/quizzes/<quiz_public_id>"
    "/attempts/<attempt_public_id>/submit"
)
@roles_required(UserRole.STUDENT.value)
def quiz_submit(group_public_id, quiz_public_id, attempt_public_id):
    """Finalize and grade this attempt.

    The trusted complete-body receipt time controls deadline acceptance.
    A competing expiry observer cannot discard a timely signed submission;
    its frozen score is preserved and acceptance evidence is appended.

    **Idempotent.** A replayed submission returns the existing result and
    changes no timestamp, counter, answer, selection or grade -- the
    attempt is already finalized, and ``finalize_attempt`` refuses rather
    than overwriting.

    Unanswered questions are allowed and grade as incorrect, but only
    after an explicit confirmation, so nobody submits a half-finished
    attempt by reflex.
    """
    received_at = submission_received_at()
    reference_utc = _now()
    preview = _visible_quiz_or_404(group_public_id, quiz_public_id, reference_utc)
    preview_quiz, preview_group = preview[0], preview[1]
    preview_attempt = _own_attempt_or_404(preview_quiz.id, attempt_public_id)

    student_auth_version = current_user.auth_version
    student_id = current_user.id
    student_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    attempt_id = preview_attempt.id
    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id
    submitted_token = request.form.get("submit_state", "")
    confirmed = request.form.get("confirm_unanswered") == "yes"
    return_question = request.form.get("from_question", "")

    settle_due_attempts(
        group_public_id, quiz_id, reference_utc, attempt_ids=[attempt_id]
    )

    hierarchy, group, student, enrollment = _lock_student_chain(
        group_public_id, term_id, level_id, course_id, student_id
    )
    if (_student_authz_broken(group, student, enrollment)
            or student.auth_version != student_auth_version):
        db.session.rollback()
        abort(404)
    if _hierarchy_broken(hierarchy, group, term_id, level_id, course_id):
        db.session.rollback()
        abort(404)

    quiz = lock_quiz_row(quiz_id)
    if quiz is None or quiz.group_id != group.id or quiz.public_id != quiz_public_id:
        db.session.rollback()
        abort(404)

    attempt = lock_attempt_rows([attempt_id]).get(attempt_id)
    if (
        attempt is None
        or attempt.quiz_id != quiz.id
        or attempt.student_id != student_id
        or attempt.enrollment_id != enrollment.id
        or attempt.public_id != attempt_public_id
    ):
        db.session.rollback()
        abort(404)

    timely = is_timely(attempt, received_at)
    if attempt.is_finalized and not (attempt.status == 'expired' and timely):
        # A replay. Return the existing result untouched.
        db.session.rollback()
        return redirect(_result_url(group_public_id, quiz_public_id, attempt_public_id))

    if expire_if_due(attempt, received_at):
        db.session.commit()
        note_outcome("quiz_submission", "expired")
        flash("Your time ran out, so this attempt was submitted as it was.", "danger")
        return redirect(_result_url(group_public_id, quiz_public_id, attempt_public_id))

    if _submit_token_is_stale(
        submitted_token, student_public_id, group_public_id, quiz_public_id,
        attempt_public_id, quiz, attempt,
        expected_status='in_progress',
    ):
        db.session.rollback()
        note_outcome("quiz_submission", "rejected")
        flash(_STALE_MESSAGE, "danger")
        return redirect(_quiz_url(group_public_id, quiz_public_id))

    if not timely:
        db.session.rollback()
        flash(_STALE_MESSAGE, 'danger')
        return redirect(_quiz_url(group_public_id, quiz_public_id))

    question_ids = quiz_question_ids(quiz.id)
    answered = answered_question_ids(attempt.id, question_ids)
    unanswered = len(question_ids) - len(answered)
    if unanswered > 0 and not confirmed:
        db.session.rollback()
        note_outcome("quiz_submission", "rejected")
        flash(
            f"You have not answered {unanswered} "
            f"{'question' if unanswered == 1 else 'questions'}. Unanswered questions are "
            "marked incorrect. Confirm below if you want to submit anyway.",
            "warning",
        )
        target = return_question or first_question_public_id(quiz_id)
        if target:
            return redirect(
                _question_url(
                    group_public_id, quiz_public_id, attempt_public_id, target
                )
            )
        return redirect(_quiz_url(group_public_id, quiz_public_id))

    accept_timely_submission(attempt, received_at, submitted_token, student_id)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if student_quiz(
            student_id, group_public_id, quiz_public_id, reference_utc
        ) is None:
            abort(404)
        note_outcome("quiz_submission", "rejected")
        flash(
            "This attempt could not be submitted. Please reload the page and try again.",
            "danger",
        )
        return redirect(_quiz_url(group_public_id, quiz_public_id))

    note_outcome("quiz_submission", "submitted")
    flash("Attempt submitted.", "success")
    return redirect(_result_url(group_public_id, quiz_public_id, attempt_public_id))


# ======================================================================
# Result
# ======================================================================


@student_bp.get(
    "/groups/<group_public_id>/quizzes/<quiz_public_id>"
    "/attempts/<attempt_public_id>/result"
)
@roles_required(UserRole.STUDENT.value)
def quiz_result(group_public_id, quiz_public_id, attempt_public_id):
    """This Student's own result for one finalized attempt.

    Shows the status, the correct count, the total, the derived
    percentage and a per-question right/wrong list. It shows **no option
    text, no selected/unselected marking and no answer key** -- the
    builder behind it never reads ``is_correct`` on an option, so nothing
    here can leak the key, and that stays true after the Quiz closes.
    """
    reference_utc = _now()
    preview = _visible_quiz_or_404(group_public_id, quiz_public_id, reference_utc)
    quiz_id = preview[0].id
    preview_attempt = _own_attempt_or_404(quiz_id, attempt_public_id)

    settle_due_attempts(
        group_public_id, quiz_id, reference_utc, attempt_ids=[preview_attempt.id]
    )

    row = _visible_quiz_or_404(group_public_id, quiz_public_id, reference_utc)
    quiz = row[0]
    attempt = _own_attempt_or_404(quiz.id, attempt_public_id)
    if not attempt.is_finalized:
        # Still running: send the Student back to the question they were
        # on rather than showing a result that does not exist yet.
        first = first_question_public_id(quiz.id)
        if first is None:
            return redirect(_quiz_url(group_public_id, quiz_public_id))
        return redirect(
            _question_url(group_public_id, quiz_public_id, attempt_public_id, first)
        )

    tz_name = _tz_name()
    return private_no_store(
        "student/quizzes/result.html",
        group_public_id=group_public_id,
        quiz={"public_id": quiz.public_id, "title": quiz.title},
        attempt={
            "public_id": attempt.public_id,
            "attempt_number": attempt.attempt_number,
            "status": attempt.status,
            "status_label": ATTEMPT_STATUS_LABELS.get(attempt.status, attempt.status),
            "submitted_local": (
                to_app_local(tz_name, attempt.submitted_at)
                if attempt.submitted_at is not None
                else None
            ),
            "correct_count": attempt.correct_count,
            "total_questions": attempt.total_questions,
            "percentage": percentage(
                attempt.correct_count or 0, attempt.total_questions
            ),
        },
        results=student_result_rows(quiz.id, attempt.id),
        tz_name=tz_name,
    )

from app.services.episode_queries import active_episode_record
