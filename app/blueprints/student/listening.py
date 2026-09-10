"""Student Listening activities: opening one, listening, answering,
submitting and reading a result (Phase 4 / M05).

Routes, all Group- and activity-scoped by public identifier::

    GET   /student/listening
    GET   /student/groups/<g>/listening/<l>
    POST  /student/groups/<g>/listening/<l>/start
    GET   /student/groups/<g>/listening/<l>/attempts/<a>/questions/<x>
    POST  /student/groups/<g>/listening/<l>/attempts/<a>/questions/<x>/answer
    POST  /student/groups/<g>/listening/<l>/attempts/<a>/submit
    GET   /student/groups/<g>/listening/<l>/attempts/<a>/result
    GET   /student/groups/<g>/listening/<l>/audio

There is deliberately no delete route, no "reopen", no answer-key route,
no transcript route and no way for a Student to write anything except
their own selections on their own in-progress attempt.

**A Listening activity is one Quiz plus one extension row, and this
module is built on exactly that.** The attempt lifecycle, the deadline
arithmetic, request-driven expiry, the attempt limit, answer replacement,
exact-set grading and submission idempotence all come from the accepted
M04D implementation and are *called* here -- ``settle_due_attempts``,
``lock_quiz_row``, ``lock_attempt_rows``, ``lock_question_row``,
``lock_active_option_rows``, ``replace_answer_selections``,
``expire_if_due``, ``finalize_attempt``, ``compute_deadline`` and the
Student chain helpers are imported, not re-implemented. What this module
adds is exactly what an ordinary Quiz has no concept of: an authorized
audio stream, vocabulary support, and a transcript released strictly
according to the Teacher's configured policy.

**Authorization is SQL-scoped.** An activity reaches a Student only
through ``listening_queries.student_listening_query``, which *is*
``quiz_queries.student_visible_quiz_query`` scoped to Quizzes carrying
the extension -- the same effective-visibility formula the ordinary Quiz
surface uses, not a second copy of it. A draft, a not-yet-open activity,
another Group's public id, an **ordinary Quiz's** public id, a withdrawn
Enrollment, an archived ancestor and a nonexistent id all produce the
identical non-disclosing **404**. Every attempt lookup is additionally
scoped to **both** the authorized Quiz and the authenticated
``student_id``, so one Student can never reach another's attempt.

**Reading survives ``closes_at``; starting does not.** A closed activity
stays visible so a Student can always reach their receipt, but no attempt
may be started at or after the closing moment.

**The transcript is released by exactly one function.** Every page here
asks ``listening_queries.student_transcript`` and renders precisely what
it returns. Under ``hidden`` it is ``None`` everywhere; under
``after_submission`` it is released only on a **finalized** attempt's own
result page; under ``always`` it is available on the detail and question
pages too. It never appears in a URL, a signed token, a hidden form
field, a JavaScript value, a flash message or an audio response under any
policy -- pages are the only place it is ever rendered.

**The answer key never reaches this blueprint.** No Student-facing query
selects ``QuestionOption.is_correct``, no dict built here carries it, and
no token contains it -- so it cannot leak before, during or after the
activity's window. The results page reports right/wrong per question and
nothing more.

**The audio player decides nothing.** It is a small dependency-free
controller for play/pause, replay and playback speed. Authorization,
attempt state, deadlines, completion and grading are all decided on the
server, and the ``<audio>`` element works without it.

All responses carry ``Cache-Control: private, no-store`` and
``Vary: Cookie``: these pages are per-Student and time-gated, so a shared
or reused cache entry could show one Student another's attempt, or show a
transcript after the attempt that earned it ended.
"""

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature
from sqlalchemy.exc import IntegrityError

from app.blueprints.student import student_bp
from app.blueprints.student.quizzes import (
    _STALE_MESSAGE,
    _all_strings,
    _hierarchy_broken,
    _lock_student_chain,
    _own_attempt_summaries,
    _positive_int,
    _serializer,
    _student_authz_broken,
    _validate_selection,
)
from app.blueprints.student.routes import private_no_store
from app.extensions import db
from app.models import (
    ListeningActivity,
    QuestionAnswerMode,
    QuizAttempt,
    QuizAttemptStatus,
    QuizStatus,
    UserRole,
)
from app.security.decorators import roles_required
from app.services.listening_queries import (
    STUDENT_TRANSCRIPT_NOTE,
    audio_upload_for_activity,
    build_student_listening_view,
    student_listening,
    student_listening_page,
    student_transcript,
    student_vocabulary,
)
from app.services.material_serving import serve_uploaded_file
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
    first_question_public_id,
    normalize_page,
    question_navigation,
    student_result_rows,
    teacher_question,
)
from app.services.quiz_transactions import (
    lock_active_option_rows,
    lock_attempt_rows,
    lock_question_row,
    lock_quiz_row,
    replace_answer_selections,
    settle_due_attempts,
)
from app.services.schedule_occurrences import to_app_local, utc_reference_now

_PUBLISHED = QuizStatus.PUBLISHED.value
_IN_PROGRESS = QuizAttemptStatus.IN_PROGRESS.value
_SUBMITTED = QuizAttemptStatus.SUBMITTED.value


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _now():
    """The request's authoritative naive-UTC whole-second moment.

    Whole seconds because the ``DATETIME`` columns hold whole seconds on
    MySQL, and **one** value per request so a page's availability label,
    its deadline arithmetic and anything it writes can never straddle a
    boundary and contradict each other.

    Declared here rather than imported so this surface's clock can be
    injected on its own in tests, exactly as each blueprint already does.
    """
    return utc_reference_now().replace(microsecond=0)


# ======================================================================
# URLs
# ======================================================================


def _activity_url(group_public_id, listening_public_id):
    return url_for(
        "student.listening_detail",
        group_public_id=group_public_id,
        listening_public_id=listening_public_id,
    )


def _question_url(
    group_public_id, listening_public_id, attempt_public_id, question_public_id
):
    return url_for(
        "student.listening_question",
        group_public_id=group_public_id,
        listening_public_id=listening_public_id,
        attempt_public_id=attempt_public_id,
        question_public_id=question_public_id,
    )


def _result_url(group_public_id, listening_public_id, attempt_public_id):
    return url_for(
        "student.listening_result",
        group_public_id=group_public_id,
        listening_public_id=listening_public_id,
        attempt_public_id=attempt_public_id,
    )


def _audio_url(group_public_id, listening_public_id):
    """The authorized inline audio endpoint.

    A Flask route, never a public static URL: the bytes live outside
    ``app/static`` under a random storage key, and every request for them
    is re-authorized and audited.
    """
    return url_for(
        "student.listening_audio",
        group_public_id=group_public_id,
        listening_public_id=listening_public_id,
    )


# ======================================================================
# Signed exact-shape state tokens (Phase 4 / M05)
# ======================================================================
#
# Two dedicated M05 salts and two exact purpose markers. A token minted
# under any other salt -- including every Teacher-side M05 token and every
# M04D Student token -- fails signature verification here.
#
# Payloads carry **public identifiers, one version and one known status
# only**. A signed token is authenticated, not encrypted: anyone holding
# it can read it, so no transcript, no vocabulary text, no audio metadata,
# no prompt, no option text, no selection and no correct-answer data is
# ever placed in one.

_SALTS = {
    "listening-answer": "student.listening-answer.phase4-m05.v1",
    "listening-submit": "student.listening-submit.phase4-m05.v1",
}

_FIELDS = {
    "listening-answer": (
        "purpose", "student_public_id", "group_public_id", "listening_public_id",
        "attempt_public_id", "question_public_id", "quiz_version", "attempt_status",
    ),
    "listening-submit": (
        "purpose", "student_public_id", "group_public_id", "listening_public_id",
        "attempt_public_id", "quiz_version", "attempt_status",
    ),
}

_ATTEMPT_STATUSES = frozenset(status.value for status in QuizAttemptStatus)


def _make_token(purpose, **payload):
    body = dict(payload, purpose=purpose)
    if set(body) != set(_FIELDS[purpose]):  # pragma: no cover -- programming error
        raise ValueError(f"token payload does not match the {purpose} shape")
    return _serializer(_SALTS[purpose]).dumps(body)


def _load_token(token, purpose):
    """Exact-shape validation.

    The key set must match exactly, the purpose must be the expected one,
    every identifier must be a string, ``quiz_version`` must be a genuine
    positive ``int`` (``bool`` excluded explicitly -- it is an ``int``
    subclass, and ``True`` must never pass as version 1), and
    ``attempt_status`` must be a known member. Any failure returns
    ``None`` and is treated exactly like an outdated token: rejected,
    never trusted, never silently upgraded.
    """
    if not token:
        return None
    try:
        payload = _serializer(_SALTS[purpose]).loads(token)
    except BadSignature:
        return None
    fields = _FIELDS[purpose]
    if not isinstance(payload, dict) or set(payload) != set(fields):
        return None
    if payload["purpose"] != purpose:
        return None
    if not _positive_int(payload["quiz_version"]):
        return None
    if payload["attempt_status"] not in _ATTEMPT_STATUSES:
        return None
    identifiers = [
        payload[field]
        for field in fields
        if field not in ("quiz_version", "attempt_status")
    ]
    if not _all_strings(identifiers):
        return None
    return payload


def _token_is_stale(token, purpose, **expected):
    """True when `token` does not exactly describe the **locked** rows.

    Binding ``attempt_status`` is what makes a form opened while the
    attempt was running fail closed once it has been submitted or has
    expired -- a lock alone would happily write into a finalized attempt.
    Binding ``quiz_version`` catches an activity that changed underneath,
    which the publication freeze should already prevent and which is
    therefore worth refusing loudly rather than absorbing.
    """
    payload = _load_token(token, purpose)
    if payload is None:
        return True
    return any(payload[field] != value for field, value in expected.items())


# ======================================================================
# Shared authorization
# ======================================================================


def _visible_activity_or_404(group_public_id, listening_public_id, reference_utc):
    """The row tuple ``(quiz, group, course, level, term, activity)`` this
    Student may currently see, or a non-disclosing 404. The query's
    ``WHERE`` clause is the authorization -- nothing is loaded broadly and
    filtered afterwards."""
    row = student_listening(
        current_user.id, group_public_id, listening_public_id, reference_utc
    )
    if row is None:
        abort(404)
    return row


def _own_attempt_or_404(quiz_id, attempt_public_id):
    """One attempt scoped to **both** the authorized activity and the
    authenticated Student. Another Student's attempt, an attempt under a
    different activity or Quiz, and a nonexistent id all 404
    identically."""
    attempt = QuizAttempt.query.filter_by(
        public_id=attempt_public_id, quiz_id=quiz_id, student_id=current_user.id
    ).first()
    if attempt is None:
        abort(404)
    return attempt


def _first_question_redirect(group_public_id, listening_public_id, attempt):
    """Where a started or resumed attempt lands.

    An activity with no questions cannot be published, so the absence of a
    first question means the aggregate is broken; the Student is returned
    to the activity page rather than to a URL that cannot exist.
    """
    first = first_question_public_id(attempt.quiz_id)
    if first is None:
        return _activity_url(group_public_id, listening_public_id)
    return _question_url(
        group_public_id, listening_public_id, attempt.public_id, first
    )


def _player(group_public_id, listening_public_id, title):
    """The context the shared audio-player partial needs.

    Only an authorized endpoint URL and a label. No storage key, no
    filesystem path, no digest, no byte size, no internal id and no
    transcript -- the player is presentation, and it is handed nothing it
    does not need to render controls.
    """
    return {
        "src": _audio_url(group_public_id, listening_public_id),
        "label": title,
    }


# ======================================================================
# List
# ======================================================================


@student_bp.get("/listening")
@roles_required(UserRole.STUDENT.value)
def listening_list():
    """One bounded page of the Listening activities this Student may
    currently see.

    ``LIMIT PAGE_SIZE + 1`` supplies the next-page flag with no ``COUNT``,
    and one grouped query resolves every row's attempt count -- so the
    page costs a fixed number of statements however many activities it
    shows. No transcript and no vocabulary text is selected for it.
    """
    reference_utc = _now()
    tz_name = _tz_name()
    page = normalize_page(request.args.get("page"))

    rows, has_next = student_listening_page(current_user.id, reference_utc, page)
    if not rows and page > 1:
        page = 1
        rows, has_next = student_listening_page(current_user.id, reference_utc, page)

    counts = attempt_counts_by_quiz(current_user.id, [row[0].id for row in rows])
    return private_no_store(
        "student/listening/list.html",
        activities=build_student_listening_view(rows, tz_name, reference_utc, counts),
        tz_name=tz_name,
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
        active_nav="listening",
    )


# ======================================================================
# Detail -- the player, the vocabulary, and this Student's own attempts
# ======================================================================


@student_bp.get("/groups/<group_public_id>/listening/<listening_public_id>")
@roles_required(UserRole.STUDENT.value)
def listening_detail(group_public_id, listening_public_id):
    """What this activity is, when it is available, its recording, and
    this Student's own attempts at it.

    Settles any overdue attempt of this Student's first, so the page can
    never offer to continue an attempt whose deadline has passed.

    The transcript appears here **only** under the ``always`` policy --
    ``student_transcript`` is asked with ``on_finalized_result=False``,
    because a detail page is not a finished attempt's result page.
    """
    reference_utc = _now()
    row = _visible_activity_or_404(group_public_id, listening_public_id, reference_utc)
    quiz_id = row[0].id

    settle_due_attempts(group_public_id, quiz_id, reference_utc)

    # The settle step performs its own deliberate reset, so everything
    # read before it is expired -- re-read the display copies.
    row = _visible_activity_or_404(group_public_id, listening_public_id, reference_utc)
    quiz, group, activity = row[0], row[1], row[5]
    tz_name = _tz_name()

    attempts = _own_attempt_summaries(quiz.id, current_user.id, tz_name)
    open_attempt = next(
        (item for item in attempts if item["status"] == _IN_PROGRESS), None
    )
    state = availability_state(quiz, reference_utc)
    used = len(attempts)

    return private_no_store(
        "student/listening/detail.html",
        group_public_id=group.public_id,
        activity={
            "public_id": activity.public_id,
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
        player=_player(group.public_id, activity.public_id, quiz.title),
        transcript=student_transcript(activity),
        transcript_note=STUDENT_TRANSCRIPT_NOTE.get(activity.transcript_visibility),
        vocabulary=student_vocabulary(activity),
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
        active_nav="listening",
    )


# ======================================================================
# Start an attempt
# ======================================================================


@student_bp.post("/groups/<group_public_id>/listening/<listening_public_id>/start")
@roles_required(UserRole.STUDENT.value)
def listening_start(group_public_id, listening_public_id):
    """Start -- or resume -- this Student's attempt at a published
    Listening activity.

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
    preview = _visible_activity_or_404(
        group_public_id, listening_public_id, reference_utc
    )
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
    if quiz is None or quiz.group_id != group.id:
        db.session.rollback()
        abort(404)
    if quiz.status != _PUBLISHED:
        db.session.rollback()
        abort(404)
    # The extension row is locked after its Quiz and re-proved, so a
    # request that arrived through a Listening URL can never end up
    # starting an attempt at an ordinary Quiz.
    activity = _lock_activity_row(quiz, listening_public_id)
    if activity is None:
        db.session.rollback()
        abort(404)

    if not can_start_attempt_now(quiz, reference_utc):
        db.session.rollback()
        flash(
            "This listening activity is not open right now, so a new attempt cannot be "
            "started.",
            "danger",
        )
        return redirect(_activity_url(group_public_id, listening_public_id))

    attempts = student_attempt_rows(quiz.id, student_id)
    existing = in_progress_attempt(attempts)
    if existing is not None:
        # Already running: return it rather than creating a second one.
        # The at-most-one-in-progress rule is a cross-row invariant, so it
        # is enforced here against the locked rows, not by a constraint.
        db.session.rollback()
        return redirect(
            _first_question_redirect(group_public_id, listening_public_id, existing)
        )

    if len(attempts) >= quiz.attempt_limit:
        db.session.rollback()
        flash(
            f"You have used all {quiz.attempt_limit} attempts at this listening activity.",
            "danger",
        )
        return redirect(_activity_url(group_public_id, listening_public_id))

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
        recovered = student_listening(
            student_id, group_public_id, listening_public_id, reference_utc
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
            return redirect(
                _first_question_redirect(group_public_id, listening_public_id, rerun)
            )
        flash(
            "This attempt could not be started. Please reload the page and try again.",
            "danger",
        )
        return redirect(_activity_url(group_public_id, listening_public_id))

    return redirect(
        _first_question_redirect(group_public_id, listening_public_id, attempt)
    )


def _lock_activity_row(quiz, listening_public_id):
    """Lock this Quiz's extension row ``FOR UPDATE`` and re-prove it is
    the one the URL names, or return ``None``.

    Taken **after** the Quiz row, which stays the serialization point for
    the whole aggregate, and re-proved rather than trusted: the row must
    still exist, still belong to this exact Quiz, and still carry this
    exact ``public_id``. The caller turns a ``None`` into the established
    non-disclosing 404.
    """
    activity = (
        ListeningActivity.query.filter_by(quiz_id=quiz.id).with_for_update().first()
    )
    if activity is None or activity.public_id != listening_public_id:
        return None
    return activity


# ======================================================================
# Taking the attempt -- one question at a time, with the player
# ======================================================================


@student_bp.get(
    "/groups/<group_public_id>/listening/<listening_public_id>"
    "/attempts/<attempt_public_id>/questions/<question_public_id>"
)
@roles_required(UserRole.STUDENT.value)
def listening_question(
    group_public_id, listening_public_id, attempt_public_id, question_public_id
):
    """One question of one in-progress attempt, beside the recording.

    Fetches only what this page needs: the current question, its bounded
    active options, this attempt's saved selections for it, the two
    neighbour identifiers and a bounded progress count. Never the whole
    activity, never another question's options, and never a per-question
    query loop.

    The transcript appears here **only** under the ``always`` policy; the
    vocabulary notes appear whenever the Teacher wrote any, because they
    are teaching support with no policy of their own.
    """
    reference_utc = _now()
    preview = _visible_activity_or_404(
        group_public_id, listening_public_id, reference_utc
    )
    quiz_id = preview[0].id
    preview_attempt = _own_attempt_or_404(quiz_id, attempt_public_id)

    settle_due_attempts(
        group_public_id, quiz_id, reference_utc, attempt_ids=[preview_attempt.id]
    )

    row = _visible_activity_or_404(group_public_id, listening_public_id, reference_utc)
    quiz, activity = row[0], row[5]
    attempt = _own_attempt_or_404(quiz.id, attempt_public_id)
    if attempt.is_finalized:
        return redirect(
            _result_url(group_public_id, listening_public_id, attempt_public_id)
        )

    question = teacher_question(quiz.id, question_public_id)
    if question is None:
        abort(404)
    navigation = question_navigation(quiz.id, question_public_id)
    if navigation is None:
        abort(404)
    position, total, previous_id, next_id = navigation

    options = active_options_ordered(question.id)
    selected = attempt_selected_option_ids(attempt.id, question.id)
    question_ids = quiz_question_ids(quiz.id)
    answered = answered_question_ids(attempt.id, question_ids)
    tz_name = _tz_name()

    return private_no_store(
        "student/listening/question.html",
        group_public_id=group_public_id,
        activity={
            "public_id": activity.public_id,
            "title": quiz.title,
            "time_limit_minutes": quiz.time_limit_minutes,
        },
        player=_player(group_public_id, activity.public_id, quiz.title),
        transcript=student_transcript(activity),
        transcript_note=STUDENT_TRANSCRIPT_NOTE.get(activity.transcript_visibility),
        vocabulary=student_vocabulary(activity),
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
        answer_token=_make_token(
            "listening-answer",
            student_public_id=current_user.public_id,
            group_public_id=group_public_id,
            listening_public_id=listening_public_id,
            attempt_public_id=attempt_public_id,
            question_public_id=question_public_id,
            quiz_version=quiz.version,
            attempt_status=attempt.status,
        ),
        submit_token=_make_token(
            "listening-submit",
            student_public_id=current_user.public_id,
            group_public_id=group_public_id,
            listening_public_id=listening_public_id,
            attempt_public_id=attempt_public_id,
            quiz_version=quiz.version,
            attempt_status=attempt.status,
        ),
        tz_name=tz_name,
        active_nav="listening",
    )


@student_bp.post(
    "/groups/<group_public_id>/listening/<listening_public_id>"
    "/attempts/<attempt_public_id>/questions/<question_public_id>/answer"
)
@roles_required(UserRole.STUDENT.value)
def listening_answer(
    group_public_id, listening_public_id, attempt_public_id, question_public_id
):
    """Save this attempt's selections for one question, replacing whatever
    was saved before.

    **Only active options of this exact question are accepted**, proved
    against the locked rows through the shared
    ``quiz_transactions.lock_active_option_rows``. A retired option, an
    option of another question, an option of another activity and a
    repeated identifier are all refused identically and generically --
    which one it was must not be distinguishable.

    Cardinality while saving is the shared ``_validate_selection`` rule: a
    single-answer question needs exactly one selection, a multiple-answer
    question at least one. Final correctness still requires the complete
    exact set, which is a grading rule rather than a saving one.
    """
    reference_utc = _now()
    preview = _visible_activity_or_404(
        group_public_id, listening_public_id, reference_utc
    )
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
    if quiz is None or quiz.group_id != group.id or quiz.status != _PUBLISHED:
        db.session.rollback()
        abort(404)
    if _lock_activity_row(quiz, listening_public_id) is None:
        db.session.rollback()
        abort(404)

    attempt = lock_attempt_rows([attempt_id]).get(attempt_id)
    if (
        attempt is None
        or attempt.quiz_id != quiz.id
        or attempt.student_id != student_id
        or attempt.public_id != attempt_public_id
    ):
        db.session.rollback()
        abort(404)

    # Expiry is decided here, under the locks, before anything is
    # accepted -- a request that arrives one second late must not write.
    if expire_if_due(attempt, reference_utc):
        db.session.commit()
        flash("Your time ran out, so this attempt was submitted as it was.", "danger")
        return redirect(
            _result_url(group_public_id, listening_public_id, attempt_public_id)
        )
    if attempt.is_finalized:
        db.session.rollback()
        return redirect(
            _result_url(group_public_id, listening_public_id, attempt_public_id)
        )

    question = teacher_question(quiz.id, question_public_id)
    if question is None:
        db.session.rollback()
        abort(404)
    locked_question = lock_question_row(question.id)
    if locked_question is None or locked_question.quiz_id != quiz.id:
        db.session.rollback()
        abort(404)

    if _token_is_stale(
        submitted_token, "listening-answer",
        student_public_id=student_public_id, group_public_id=group_public_id,
        listening_public_id=listening_public_id, attempt_public_id=attempt_public_id,
        question_public_id=question_public_id, quiz_version=quiz.version,
        attempt_status=attempt.status,
    ):
        db.session.rollback()
        flash(_STALE_MESSAGE, "danger")
        return redirect(
            _question_url(
                group_public_id, listening_public_id, attempt_public_id,
                question_public_id,
            )
        )

    locked_options = lock_active_option_rows(locked_question.id)
    error = _validate_selection(submitted_options, locked_options, locked_question)
    if error is not None:
        db.session.rollback()
        flash(error, "danger")
        return redirect(
            _question_url(
                group_public_id, listening_public_id, attempt_public_id,
                question_public_id,
            )
        )

    chosen = [
        locked_options[public_id] for public_id in dict.fromkeys(submitted_options)
    ]
    replace_answer_selections(attempt, locked_question, chosen, reference_utc)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if student_listening(
            student_id, group_public_id, listening_public_id, reference_utc
        ) is None:
            abort(404)
        flash(
            "This answer could not be saved. Please reload the page and try again.",
            "danger",
        )
        return redirect(
            _question_url(
                group_public_id, listening_public_id, attempt_public_id,
                question_public_id,
            )
        )

    navigation = question_navigation(quiz_id, question_public_id)
    next_id = navigation[3] if navigation else None
    flash("Answer saved.", "success")
    if request.form.get("go") == "next" and next_id:
        return redirect(
            _question_url(
                group_public_id, listening_public_id, attempt_public_id, next_id
            )
        )
    return redirect(
        _question_url(
            group_public_id, listening_public_id, attempt_public_id, question_public_id
        )
    )


# ======================================================================
# Submit
# ======================================================================


@student_bp.post(
    "/groups/<group_public_id>/listening/<listening_public_id>"
    "/attempts/<attempt_public_id>/submit"
)
@roles_required(UserRole.STUDENT.value)
def listening_submit(group_public_id, listening_public_id, attempt_public_id):
    """Finalize and grade this attempt.

    **Idempotent.** A replayed submission returns the existing result and
    changes no timestamp, counter, answer, selection or grade -- the
    attempt is already finalized, and ``finalize_attempt`` refuses rather
    than overwriting.

    Unanswered questions are allowed and grade as incorrect, but only
    after an explicit confirmation, so nobody submits a half-finished
    attempt by reflex.
    """
    reference_utc = _now()
    preview = _visible_activity_or_404(
        group_public_id, listening_public_id, reference_utc
    )
    preview_quiz, preview_group = preview[0], preview[1]
    preview_attempt = _own_attempt_or_404(preview_quiz.id, attempt_public_id)

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
    if _student_authz_broken(group, student, enrollment):
        db.session.rollback()
        abort(404)
    if _hierarchy_broken(hierarchy, group, term_id, level_id, course_id):
        db.session.rollback()
        abort(404)

    quiz = lock_quiz_row(quiz_id)
    if quiz is None or quiz.group_id != group.id:
        db.session.rollback()
        abort(404)
    if _lock_activity_row(quiz, listening_public_id) is None:
        db.session.rollback()
        abort(404)

    attempt = lock_attempt_rows([attempt_id]).get(attempt_id)
    if (
        attempt is None
        or attempt.quiz_id != quiz.id
        or attempt.student_id != student_id
        or attempt.public_id != attempt_public_id
    ):
        db.session.rollback()
        abort(404)

    if attempt.is_finalized:
        # A replay. Return the existing result untouched.
        db.session.rollback()
        return redirect(
            _result_url(group_public_id, listening_public_id, attempt_public_id)
        )

    if expire_if_due(attempt, reference_utc):
        db.session.commit()
        flash("Your time ran out, so this attempt was submitted as it was.", "danger")
        return redirect(
            _result_url(group_public_id, listening_public_id, attempt_public_id)
        )

    if _token_is_stale(
        submitted_token, "listening-submit",
        student_public_id=student_public_id, group_public_id=group_public_id,
        listening_public_id=listening_public_id, attempt_public_id=attempt_public_id,
        quiz_version=quiz.version, attempt_status=attempt.status,
    ):
        db.session.rollback()
        flash(_STALE_MESSAGE, "danger")
        return redirect(_activity_url(group_public_id, listening_public_id))

    question_ids = quiz_question_ids(quiz.id)
    answered = answered_question_ids(attempt.id, question_ids)
    unanswered = len(question_ids) - len(answered)
    if unanswered > 0 and not confirmed:
        db.session.rollback()
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
                    group_public_id, listening_public_id, attempt_public_id, target
                )
            )
        return redirect(_activity_url(group_public_id, listening_public_id))

    finalize_attempt(attempt, _SUBMITTED, reference_utc)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if student_listening(
            student_id, group_public_id, listening_public_id, reference_utc
        ) is None:
            abort(404)
        flash(
            "This attempt could not be submitted. Please reload the page and try again.",
            "danger",
        )
        return redirect(_activity_url(group_public_id, listening_public_id))

    flash("Attempt submitted.", "success")
    return redirect(
        _result_url(group_public_id, listening_public_id, attempt_public_id)
    )


# ======================================================================
# Result
# ======================================================================


@student_bp.get(
    "/groups/<group_public_id>/listening/<listening_public_id>"
    "/attempts/<attempt_public_id>/result"
)
@roles_required(UserRole.STUDENT.value)
def listening_result(group_public_id, listening_public_id, attempt_public_id):
    """This Student's own result for one finalized attempt.

    Shows the status, the correct count, the total, the derived
    percentage and a per-question right/wrong list. It shows **no option
    text, no selected/unselected marking and no answer key** -- the
    builder behind it never reads ``is_correct`` on an option, so nothing
    here can leak the key, and that stays true after the activity closes.

    **This is the one page where the ``after_submission`` policy releases
    the transcript**, and only because the attempt being rendered is
    finalized: ``student_transcript`` is asked with
    ``on_finalized_result=True`` strictly after ``is_finalized`` has been
    proved. An attempt still in progress redirects away before this point
    is ever reached.
    """
    reference_utc = _now()
    preview = _visible_activity_or_404(
        group_public_id, listening_public_id, reference_utc
    )
    quiz_id = preview[0].id
    preview_attempt = _own_attempt_or_404(quiz_id, attempt_public_id)

    settle_due_attempts(
        group_public_id, quiz_id, reference_utc, attempt_ids=[preview_attempt.id]
    )

    row = _visible_activity_or_404(group_public_id, listening_public_id, reference_utc)
    quiz, activity = row[0], row[5]
    attempt = _own_attempt_or_404(quiz.id, attempt_public_id)
    if not attempt.is_finalized:
        # Still running: send the Student back to the question they were
        # on rather than showing a result that does not exist yet.
        first = first_question_public_id(quiz.id)
        if first is None:
            return redirect(_activity_url(group_public_id, listening_public_id))
        return redirect(
            _question_url(
                group_public_id, listening_public_id, attempt_public_id, first
            )
        )

    tz_name = _tz_name()
    return private_no_store(
        "student/listening/result.html",
        group_public_id=group_public_id,
        activity={"public_id": activity.public_id, "title": quiz.title},
        transcript=student_transcript(activity, on_finalized_result=True),
        transcript_note=STUDENT_TRANSCRIPT_NOTE.get(activity.transcript_visibility),
        vocabulary=student_vocabulary(activity),
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
        active_nav="listening",
    )


# ======================================================================
# Authorized inline audio
# ======================================================================


@student_bp.get("/groups/<group_public_id>/listening/<listening_public_id>/audio")
@roles_required(UserRole.STUDENT.value)
def listening_audio(group_public_id, listening_public_id):
    """Stream one activity's recording to an eligible Student.

    **Every request is re-authorized from current state**, never trusted
    because a page once rendered a ``<source>``: the whole visibility
    formula is re-evaluated in SQL (this Student, active account and role,
    active Enrollment, active academic chain, published activity, opening
    moment reached), the activity's ownership by the Group in the URL is
    proved by the same query, and the ``uploaded_files`` row must still
    exist with the server-determined category ``audio``. Any break yields
    the identical non-disclosing 404 -- a Student of another Group, a
    withdrawn Enrollment and an ordinary Quiz's public id are
    indistinguishable from a nonexistent id.

    The bytes go through the shared M12 serving core, which resolves a
    containment-checked path from the random storage key, writes the
    ``inline`` ``FileAccessLog`` entry **before** any byte is sent and
    refuses to serve at all if that audit row cannot be committed, sends
    the stored canonical content type with ``nosniff``, and sets
    ``Cache-Control: private, no-store, max-age=0``. Range requests are
    supported so seeking works, and each one is one authorized request
    with one audit row.

    Nothing in the response discloses the storage key, the resolved path,
    the digest, the uploader, any internal id, the transcript or the
    vocabulary notes.
    """
    row = _visible_activity_or_404(group_public_id, listening_public_id, _now())
    uploaded_file = audio_upload_for_activity(row[5])
    if uploaded_file is None:
        abort(404)
    return serve_uploaded_file(uploaded_file, current_user.id, force_attachment=False)
