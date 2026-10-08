"""Read-only query layer for Group-owned Listening activities
(Phase 4 / M05).

Flask-independent -- plain functions over the ORM, no ``request`` /
``flash`` / route decorators, mirroring ``app/services/quiz_queries.py``.
**Every function here is read-only**: no locks, no writes. The locking
write paths live in ``app/blueprints/teacher/listening.py`` and
``app/blueprints/student/listening.py``.

**Authorization is never performed here**, with one exception that is the
opposite of a loophole: :func:`student_listening_query` *is* the
authorization, because its ``WHERE`` clause is the complete Student
visibility formula -- it reuses
``quiz_queries.student_visible_quiz_query`` rather than restating it, so
the ordinary Quiz surface and this one can never drift about what an
eligible Student may see. Every other function is handed an internal id
the calling route has already proven.

**This module deliberately owns almost nothing.** A Listening activity is
one Quiz plus one extension row, so questions, options, attempts,
answers, selections, navigation, grading, attempt review and the Student
result all come from ``quiz_queries`` and ``quiz_attempts`` unchanged.
What is added here is exactly what those modules cannot answer: which
Quizzes carry the extension, what the extension holds, whether a Student
may read the transcript right now, and whether the attached recording is
still a valid one to publish.

**Pagination is reused, not re-declared.** ``quiz_queries`` owns the
Quiz aggregate's page sizes, and a Listening activity is part of that
aggregate: a Teacher should not meet two different pagination behaviours
inside one feature. That is a different situation from the one
``normalize_page`` describes, where two *independent* features would have
been coupled by sharing.

**The authored answer key never reaches a Student read**, exactly as in
``quiz_queries``: no Student-facing function here selects
``QuestionOption.is_correct``, and the Student builders are separate
functions rather than a shared one with a flag.

**The transcript never leaves this module except through
:func:`student_transcript`.** No other Student-facing builder carries it,
so it cannot reach a page, a form value, a URL, a signed token, a
JavaScript value or an audio response by accident.
"""

from app.extensions import db
from app.models import (
    FileCategory,
    Group,
    ListeningActivity,
    Quiz,
    TranscriptVisibility,
    UploadedFile,
)
from app.services.quiz_attempts import STATE_LABELS, availability_state
from app.services.quiz_queries import PAGE_SIZE, student_visible_quiz_query
from app.services.schedule_occurrences import to_app_local

_AUDIO = FileCategory.AUDIO.value

#: Human labels for the three transcript policies, declared once so the
#: Teacher editor, the Teacher detail page and the Student pages can never
#: describe the same policy differently.
TRANSCRIPT_VISIBILITY_LABELS = {
    TranscriptVisibility.HIDDEN.value: "Hidden from students",
    TranscriptVisibility.AFTER_SUBMISSION.value: "Shown after a student finishes an attempt",
    TranscriptVisibility.ALWAYS.value: "Shown to students at any time",
}

#: The same three, phrased for the Student who is reading the page. Only
#: ever rendered beside a transcript the policy has already released.
STUDENT_TRANSCRIPT_NOTE = {
    TranscriptVisibility.AFTER_SUBMISSION.value: (
        "Your teacher chose to show the transcript only after an attempt is finished."
    ),
    TranscriptVisibility.ALWAYS.value: (
        "Your teacher chose to make the transcript available while you listen."
    ),
}


# ---------------------------------------------------------------------------
# The extension row
# ---------------------------------------------------------------------------


def listening_activity_for_quiz(quiz_id):
    """The Listening extension of this Quiz, or ``None`` for an ordinary
    Quiz.

    One indexed read through ``listening_activities.quiz_id``'s own unique
    index. Used by the write paths, which have already locked the Quiz and
    need the extension as a row rather than as a query fragment.
    """
    return ListeningActivity.query.filter_by(quiz_id=quiz_id).first()


def audio_upload_for_activity(activity):
    """The activity's recording, but **only** if it is still a valid one:
    the row exists and its server-determined category is ``audio``.

    Returns ``None`` otherwise, and the caller turns that into the same
    non-disclosing 404 (or the same publication blocker) a missing
    activity produces. A foreign key proves the ``uploaded_files`` row
    exists; it never proves the row is a recording -- exactly as a foreign
    key into ``users`` never proves a role. That condition is therefore
    re-checked on **every** audio request and on **every** publication
    check, not only when the association was created.
    """
    if activity is None or activity.audio_file_id is None:
        return None
    uploaded_file = UploadedFile.query.filter_by(id=activity.audio_file_id).first()
    if uploaded_file is None or uploaded_file.category != _AUDIO:
        return None
    return uploaded_file


# ---------------------------------------------------------------------------
# Transcript policy -- the single place a Student's transcript is decided
# ---------------------------------------------------------------------------


def student_transcript(activity, on_finalized_result=False):
    """The transcript this Student may read **right now**, or ``None``.

    Every Student-facing page asks this function and renders exactly what
    it returns, so the policy is applied in one place instead of being
    re-derived in three templates:

    - ``hidden`` -- always ``None``. The transcript is not rendered, not
      placed in a URL, a token, a hidden field or a JavaScript value, and
      not returned by any audio response.
    - ``after_submission`` -- the text **only** when
      `on_finalized_result` is true, which callers pass only for a
      Student's own attempt that is already ``submitted`` or ``expired``.
      An attempt still in progress is not a finished one.
    - ``always`` -- the text on the detail and question pages too.

    An empty transcript returns the empty string under a permissive
    policy, which every template renders as nothing at all. That is
    deliberate: "released but not written" and "withheld" are different
    facts, and only the second is ``None``.

    An unknown stored policy -- which the CHECK constraint and the model
    validator both forbid -- fails **closed** rather than open.
    """
    if activity is None:
        return None
    policy = activity.transcript_visibility
    if policy == TranscriptVisibility.ALWAYS.value:
        return activity.transcript
    if policy == TranscriptVisibility.AFTER_SUBMISSION.value and on_finalized_result:
        return activity.transcript
    return None


def student_vocabulary(activity):
    """The vocabulary support an eligible Student may read.

    Unconditional by design: vocabulary notes are teaching support a
    Teacher wrote *for* the Student to use while listening, so they have
    no policy of their own. Returns ``""`` when none was authored.
    """
    return "" if activity is None else activity.vocabulary_notes


# ---------------------------------------------------------------------------
# Teacher reads -- already authorized by the route's active assignment check
# ---------------------------------------------------------------------------


def teacher_listening_page(group_id, page):
    """One bounded page of a Group's Listening activities, newest first.

    Returns ``(rows, has_next)``. Same contract as
    ``quiz_queries.teacher_quizzes_page``: ``created_at DESC, id DESC``,
    ``LIMIT PAGE_SIZE + 1`` for the has-next flag with no ``COUNT`` and no
    disclosed total, and explicit **columns** rather than entities.

    ``instructions``, ``transcript`` and ``vocabulary_notes`` are all
    deliberately **not** selected: a list row shows a title, a status and
    two timestamps, and loading tens of thousands of characters of
    authored body text per row to render none of it would make the page's
    cost grow with how much Teachers have written.

    The join to ``listening_activities`` is what makes this list Listening
    activities *only* -- an ordinary Quiz has no row to join to, so it can
    never appear here.
    """
    rows = (
        db.session.query(
            ListeningActivity.public_id,
            ListeningActivity.transcript_visibility,
            Quiz.title,
            Quiz.status,
            Quiz.opens_at,
            Quiz.closes_at,
            Quiz.created_at,
            Quiz.updated_at,
        )
        .join(Quiz, ListeningActivity.quiz_id == Quiz.id)
        .filter(Quiz.group_id == group_id)
        .order_by(Quiz.created_at.desc(), Quiz.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    has_next = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_next


def build_teacher_listening_list_view(rows, tz_name, reference_utc):
    """Plain presentation dicts for the Teacher Listening list -- public
    ids, localized times and display strings only.

    No ORM row and no internal id is carried, so rendering the list can
    never trigger a lazy load or an ORM-driven authorization decision.
    """
    items = []
    for row in rows:
        state = availability_state(row, reference_utc)
        items.append(
        {
            "public_id": row.public_id,
            "title": row.title,
            "status": row.status,
            "state": state,
            "state_label": STATE_LABELS.get(state),
            "transcript_visibility": row.transcript_visibility,
            "transcript_visibility_label": TRANSCRIPT_VISIBILITY_LABELS.get(
                row.transcript_visibility, row.transcript_visibility
            ),
            "created_local": to_app_local(tz_name, row.created_at),
            "updated_local": to_app_local(tz_name, row.updated_at),
        }
        )
    return items


def teacher_listening(group_id, listening_public_id):
    """``(quiz, activity)`` for one Listening activity by **its own**
    ``public_id``, constrained to `group_id`, or ``None``.

    Addressing the activity by the extension row's identifier rather than
    the Quiz's is what makes "Listening routes can never reach an ordinary
    Quiz" a structural property instead of a check somebody could forget:
    an ordinary Quiz has no ``listening_activities`` row, so its
    ``public_id`` resolves to nothing here at all. The Group constraint
    does the same for a Listening activity belonging to another Group, and
    the route turns both into the identical non-disclosing 404.

    Returns ORM rows: the detail page needs ``instructions``, the
    transcript and the vocabulary notes, and the write paths need
    ``version``.
    """
    return (
        db.session.query(Quiz, ListeningActivity)
        .join(ListeningActivity, ListeningActivity.quiz_id == Quiz.id)
        .filter(
            ListeningActivity.public_id == listening_public_id,
            Quiz.group_id == group_id,
        )
        .first()
    )


def build_listening_detail(quiz, activity, tz_name):
    """One plain presentation dict for a Listening activity, for a
    **Teacher** page.

    Carries the transcript and the vocabulary notes because a Teacher
    assigned to the Group may always read what they configured -- the
    policy governs Student access, not authoring. ``version`` is
    deliberately excluded exactly as it is from
    ``quiz_queries.build_quiz_detail``: it is a concurrency signal, not a
    revision number, and the signed tokens carry it instead.
    """
    return {
        "public_id": activity.public_id,
        "title": quiz.title,
        "instructions": quiz.instructions,
        "transcript": activity.transcript,
        "transcript_visibility": activity.transcript_visibility,
        "transcript_visibility_label": TRANSCRIPT_VISIBILITY_LABELS.get(
            activity.transcript_visibility, activity.transcript_visibility
        ),
        "vocabulary_notes": activity.vocabulary_notes,
        "created_local": to_app_local(tz_name, quiz.created_at),
        "updated_local": to_app_local(tz_name, quiz.updated_at),
    }


def build_audio_summary(uploaded_file):
    """What a Teacher may be told about the attached recording.

    Deliberately **only** the original filename, the format label and the
    size. ``storage_key``, the resolved filesystem path, the SHA-256
    digest, the uploader's identity and every internal id are absent --
    none of them is information a page needs, and each of them is
    something an audio route must never disclose.
    """
    if uploaded_file is None:
        return None
    return {
        "filename": uploaded_file.original_filename,
        "format": uploaded_file.extension.upper(),
        "byte_size": uploaded_file.byte_size,
    }


# ---------------------------------------------------------------------------
# Publication readiness
# ---------------------------------------------------------------------------


def listening_publication_blockers(quiz, activity):
    """Every reason this Listening activity may not be published, as
    Teacher-facing sentences. An empty list means it is ready.

    The existing ``quiz_attempts.publication_blockers`` supplies the
    complete M04D rule set -- a full availability window, 1..100
    questions, a structurally valid active option set per question and the
    answer-cardinality rule -- and is **called**, not re-implemented, so a
    change to Quiz publication readiness reaches this surface
    automatically. M05 adds exactly one further requirement on top: the
    activity must still hold a valid recording.

    Returns **all** failures rather than the first, and the read-only
    readiness panel calls exactly this function, so the page a Teacher
    reads and the rule that decides can never disagree. The caller runs it
    against the **locked** rows.
    """
    from app.services.quiz_attempts import publication_blockers

    blockers = []
    if audio_upload_for_activity(activity) is None:
        blockers.append(
            "This listening activity has no valid audio recording attached, so it cannot be "
            "published. Nothing has been changed or removed. Please create a new draft with "
            "the correct MP3 or WAV file."
        )
    blockers.extend(publication_blockers(quiz))
    return blockers


# ---------------------------------------------------------------------------
# Student reads -- the WHERE clause IS the authorization
# ---------------------------------------------------------------------------


def student_listening_query(student_id, reference_utc):
    """The one fully scoped base query behind every Student Listening
    read.

    It is ``quiz_queries.student_visible_quiz_query`` with
    ``listening=True`` -- the *same* effective-visibility formula the
    ordinary Quiz surface uses (this Student, with the Student role and an
    active account, an **active** Enrollment in the Group, the whole
    academic chain active, the Quiz published, ``opens_at`` reached),
    scoped to Quizzes that carry the extension. Reusing it rather than
    restating it is the point: a future tightening of "visible" cannot
    reach one surface and miss the other.

    The extension row is joined so the transcript policy and the recording
    come back in the same statement -- never as a second lookup per row.

    A **closed** activity stays visible on purpose: what ``closes_at``
    withdraws is the ability to *start* an attempt, never the ability to
    reach a receipt.
    """
    return (
        student_visible_quiz_query(student_id, reference_utc, listening=True)
        .add_entity(ListeningActivity)
        .join(ListeningActivity, ListeningActivity.quiz_id == Quiz.id)
    )


def student_listening_page(student_id, reference_utc, page):
    """One bounded page of the Listening activities this Student may
    currently see.

    Ordering is ``closes_at DESC, id DESC`` and the has-next flag comes
    from ``LIMIT PAGE_SIZE + 1``, exactly as on the Student Quiz list --
    no ``COUNT`` and no disclosed total.
    """
    rows = (
        student_listening_query(student_id, reference_utc)
        .order_by(Quiz.closes_at.desc(), Quiz.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    has_next = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_next


def student_listening(student_id, group_public_id, listening_public_id, reference_utc):
    """One visible Listening activity plus its authorized hierarchy
    context, or ``None``.

    Adds only the two nested public-id predicates to the shared visibility
    query, so a draft, a not-yet-open activity, an activity whose
    ``public_id`` belongs to another Group, an **ordinary Quiz's**
    ``public_id``, a withdrawn or missing Enrollment, an archived ancestor
    and a simply non-existent id all produce no row -- and the route turns
    every one of them into the identical non-disclosing 404.
    """
    return (
        student_listening_query(student_id, reference_utc)
        .filter(
            ListeningActivity.public_id == listening_public_id,
            Group.public_id == group_public_id,
        )
        .first()
    )


def build_student_listening_item(row, tz_name, reference_utc, attempts_used=0):
    """One plain presentation dict for a Student-visible Listening
    activity in the list.

    Display strings, localized times, public ids and the Student's own
    attempt count. **No transcript, no vocabulary, no answer key, no
    question content and no internal id** -- the list has no use for any
    of them, so none is carried and none can leak from it.
    """
    quiz, group, course, level, term, _activity = row
    state = availability_state(quiz, reference_utc)
    return {
        "public_id": _activity.public_id,
        "group_public_id": group.public_id,
        "title": quiz.title,
        "group_name": group.name,
        "course_title": course.title,
        "level_name": level.name,
        "term_name": term.name,
        "opens_local": to_app_local(tz_name, quiz.opens_at),
        "closes_local": to_app_local(tz_name, quiz.closes_at),
        "time_limit_minutes": quiz.time_limit_minutes,
        "attempt_limit": quiz.attempt_limit,
        "attempts_used": attempts_used,
        "attempts_left": max(quiz.attempt_limit - attempts_used, 0),
        "state": state,
        "state_label": STATE_LABELS.get(state),
        "is_open": state == "open",
    }


def build_student_listening_view(rows, tz_name, reference_utc, counts):
    """:func:`build_student_listening_item` over one page of rows."""
    return [
        build_student_listening_item(row, tz_name, reference_utc, counts.get(row[0].id, 0))
        for row in rows
    ]
