"""Teacher authoring and review of Group-owned **Listening activities**
(Phase 4 / M05).

Group- and activity-centered routes only, every object addressed by
``public_id`` and no internal numeric id anywhere in a URL, a form value
or the rendered HTML::

    GET       /teacher/groups/<gp>/listening
    GET|POST  /teacher/groups/<gp>/listening/new
    GET       /teacher/groups/<gp>/listening/<lp>
    GET|POST  /teacher/groups/<gp>/listening/<lp>/edit
    GET|POST  /teacher/groups/<gp>/listening/<lp>/settings
    GET|POST  /teacher/groups/<gp>/listening/<lp>/questions/new
    GET|POST  /teacher/groups/<gp>/listening/<lp>/questions/<xp>/edit
    POST      /teacher/groups/<gp>/listening/<lp>/questions/<xp>/move-up
    POST      /teacher/groups/<gp>/listening/<lp>/questions/<xp>/move-down
    POST      /teacher/groups/<gp>/listening/<lp>/publish
    POST      /teacher/groups/<gp>/listening/<lp>/unpublish
    GET       /teacher/groups/<gp>/listening/<lp>/attempts
    GET       /teacher/groups/<gp>/listening/<lp>/attempts/<ap>
    GET       /teacher/groups/<gp>/listening/<lp>/audio
    GET       /teacher/groups/<gp>/listening/<lp>/audio/download

There is deliberately no flat ``/teacher/listening`` collection, no
delete, archive or duplicate action for an activity, a question, an
option, an attempt or a recording, no audio-replacement route, no score
override and no manual-grading path -- none of that exists server-side
either, and no placeholder is left for one.

**A Listening activity is one Quiz plus one extension row, and this
module is built on exactly that.** Every authorization helper, lock,
freeze, token shape, question rule, publication rule, grading rule and
attempt read comes from the accepted M04 implementation and is *called*
here rather than re-implemented -- ``_lock_quiz_chain``,
``_operational_block``, ``fresh_group_authorization``,
``_settings_defaults``, the question lock helpers, the option
aggregate comparison, ``publication_blockers``, ``settle_due_attempts``
and the attempt summary builder are all imported from the Quiz surface.
What this module adds is exactly what an ordinary Quiz has no concept
of: a validated recording created atomically with the Quiz, a
transcript, a transcript policy, and vocabulary support.

**Addressing an activity by the *extension row's* ``public_id`` is what
keeps the two surfaces apart structurally.** An ordinary Quiz has no
``listening_activities`` row, so its ``public_id`` resolves to nothing on
any route here; a Listening activity is excluded from
``quiz_queries.teacher_quiz`` and ``teacher_quizzes_page``, so it cannot
be reached or listed through the ordinary Quiz routes either. Neither
direction depends on a check somebody could forget.

**Authorization.** ``roles_required(TEACHER)`` handles anonymous (login
redirect) and non-Teacher (403). Object authorization is then server-side
and nested, reusing the exact helpers every other Teacher route uses:
``_teacher_group_or_404`` proves an **active**
``GroupTeacherAssignment`` to the Group in the URL, and every nested
lookup is constrained by that Group. A missing Group, a missing activity,
an activity belonging to another Group, an ordinary Quiz's public id, an
unassigned Teacher and a removed assignment all return the same
non-disclosing **404** -- never a 403, and never a hint that the object
exists. Multiple active assigned Teachers are equal collaborators.

**Reading is historical; writing is not.** The list, the detail page, the
attempt pages and the audio routes stay available to an actively assigned
Teacher even when the Group or an academic ancestor is archived, so an
activity and the attempts taken at it can always be read back. Creating
and editing additionally require an operational chain, re-checked against
the **locked** rows.

**Two freezes, unchanged from M04D.** Publishing makes the authored
activity read-only -- including its transcript, its transcript policy and
its vocabulary notes, because Students may already be reading exactly
that material. The **first attempt** freezes it permanently and forbids
withdrawal, because somebody's answers are now answers *to* it.

**The audio is immutable, and its bytes never touch a lock.** The upload
is streamed, size-checked and signature-checked to private storage
*before* any ``SELECT ... FOR UPDATE`` is taken; the Quiz, the extension
row, the ``UploadedFile`` and the ``upload`` access-log entry are then
committed in one transaction; and every non-success exit before that
commit deletes the file it just wrote. There is no replacement route: an
incorrect recording means creating another draft, which the create and
detail pages both say in as many words.
"""

import secrets

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.assignments import _private_no_store, _tz_name
from app.blueprints.teacher.listening_forms import (
    AUDIO_ACCEPT_ATTRIBUTE,
    SUPPORTED_AUDIO_EXTENSIONS,
    ListeningContentForm,
    ListeningCreateForm,
)
from app.blueprints.teacher.materials import _cleanup_orphan_upload
from app.blueprints.teacher.quiz_forms import (
    QuizQuestionForm,
    QuizSettingsForm,
    submitted_option_fields,
)
from app.blueprints.teacher.quizzes import (
    _DRAFT_STATUS,
    _PUBLISH_ACTION,
    _PUBLISHED,
    _PUBLICATION_ACTIONS,
    _QUESTION_INVALID_STATE_MESSAGE,
    _UNPUBLISH_ACTION,
    _aggregate_is_unchanged,
    _all_strings,
    _blank_option_rows,
    _build_attempt_summaries,
    _lock_active_options,
    _lock_question_rows,
    _lock_quiz_chain,
    _operational_block,
    _positive_int,
    _question_ownership_broken,
    _questions_error,
    _serializer,
    _settings_defaults,
    fresh_group_authorization,
)
from app.blueprints.teacher.units import (
    _archived_chain_labels,
    _authz_broken,
    _group_is_operational,
    _teacher_group_or_404,
)
from app.extensions import db
from app.models import (
    MAX_ACTIVE_OPTIONS,
    MAX_QUIZ_QUESTIONS,
    MIN_ACTIVE_OPTIONS,
    FileAccessAction,
    FileAccessLog,
    ListeningActivity,
    QuestionAnswerMode,
    QuestionOption,
    Quiz,
    QuizQuestion,
    UploadedFile,
    User,
    UserRole,
)
from app.security.decorators import roles_required
from app.services.file_storage import store_validated_upload
from app.services.file_validation import FileValidationError, extension_of
from app.services.listening_queries import (
    audio_upload_for_activity,
    build_audio_summary,
    build_listening_detail,
    build_teacher_listening_list_view,
    listening_publication_blockers,
    teacher_listening,
    teacher_listening_page,
)
from app.services.material_config import current_material_config
from app.services.material_serving import serve_uploaded_file
from app.services.quiz_attempts import (
    ATTEMPT_STATUS_LABELS,
    STATE_LABELS,
    availability_state,
    percentage,
    quiz_has_attempt_history,
)
from app.services.quiz_queries import (
    ATTEMPT_PAGE_SIZE,
    MOVE_DIRECTIONS,
    MOVE_DOWN,
    MOVE_UP,
    PAGE_SIZE,
    QUESTION_PAGE_SIZE,
    active_option_counts,
    active_option_set_is_invalid,
    active_options_ordered,
    attempt_review_rows,
    build_question_editor,
    build_question_summaries,
    duplicate_title_exists,
    neighbour_question,
    next_question_display_order,
    normalize_page,
    normalize_question_page,
    teacher_attempt,
    teacher_attempts_page,
    teacher_question,
    teacher_questions_page,
)
from app.services.quiz_transactions import settle_due_attempts
from app.services.schedule_occurrences import to_app_local, utc_reference_now

_AUDIO_CATEGORY = "audio"


def _write_moment():
    """The **authoritative** naive-UTC moment for one Listening write,
    truncated to whole seconds.

    Identical in rule and reasoning to the Quiz surface's own
    ``_write_moment``: ``DATETIME`` on MySQL carries fractional precision
    0 and *rounds* an excess fraction rather than truncating it, so a
    value carrying microseconds would be stored as a different instant
    from the one the request used. Read only **after** every lock that
    could have blocked, so a request that waited behind a competing
    co-teacher records the moment it actually wrote.

    Declared here rather than imported so this surface's clock can be
    injected on its own in tests, exactly as each blueprint already does.
    Timestamps are **not** the staleness signal -- ``Quiz.version`` is.
    """
    return utc_reference_now().replace(microsecond=0)


# ======================================================================
# Teacher-facing sentences, declared once each
# ======================================================================
#
# Every rule a Teacher can hit is stated in exactly one place, so the
# early courtesy check and the authoritative post-lock check can never
# explain the same rule differently -- the defect the M04B review found.

_STALE_MESSAGE = (
    "This listening activity was changed by someone else since this form was opened. Your "
    "changes were not saved. Please reload, read the current version, and make your change "
    "against it."
)
_NOT_OPERATIONAL_MESSAGE = (
    "Listening activities can only be created or edited while the group and its academic "
    "term, course, and level are all active. Existing activities stay readable."
)
_QUESTION_NOT_OPERATIONAL_MESSAGE = (
    "Listening questions can only be added, edited, or reordered while the group and its "
    "academic term, course, and level are all active. Existing questions stay readable."
)
_PUBLICATION_NOT_OPERATIONAL_MESSAGE = (
    "A listening activity can only be published or withdrawn while the group and its "
    "academic term, course, and level are all active. Existing attempts stay readable."
)

_LISTENING_BLOCK_WORDING = (
    "Listening activities can only be created or edited",
    "Existing activities stay readable.",
)
_QUESTION_BLOCK_WORDING = (
    "Listening questions can only be added, edited, or reordered",
    "Existing questions stay readable.",
)
_PUBLICATION_BLOCK_WORDING = (
    "Listening activities can only be published or withdrawn",
    "Existing attempts stay readable.",
)

#: The two freezes, phrased for this surface. The rule is exactly M04D's
#: and is evaluated by the same two facts; only the words differ, because
#: telling a Teacher their *quiz* is frozen on a page that never says
#: "quiz" would be confusing rather than precise.
_FROZEN_BY_ATTEMPTS_MESSAGE = (
    "Students have already started this listening activity, so its title, instructions, "
    "transcript, transcript setting, vocabulary notes, questions, options, answer key and "
    "settings can no longer be changed, and it can no longer be withdrawn. Students answered "
    "exactly this material, and rewriting it afterwards would change what their attempts were "
    "for. You can still read every attempt."
)
_FROZEN_BY_PUBLICATION_MESSAGE = (
    "This listening activity is published, so its title, instructions, transcript, transcript "
    "setting, vocabulary notes, questions, options, answer key and settings are read-only. "
    "Withdraw it first if you need to change something -- that is possible only while no "
    "student has started it."
)

#: The one sentence explaining why a recording cannot be swapped. Shown on
#: the create form and again on the detail page, because the moment a
#: Teacher discovers the wrong file is attached is the moment they need
#: to be told what to do instead.
_AUDIO_IMMUTABLE_MESSAGE = (
    "The recording cannot be changed after this activity is created. If you attach the wrong "
    "file, create another listening activity with the correct recording -- nothing is "
    "deleted, and the existing one stays readable."
)


def _authoring_block(quiz):
    """``None`` when this activity's authored content may still be
    changed, else the sentence explaining why not.

    The same two facts M04D's ``_authoring_block`` reads, in the same
    order and for the same reason: the attempt freeze is checked **first**
    because it is the stronger and permanent one, and a Teacher whose
    activity has attempts must not be told to "withdraw it first", which
    would send them at a door that is already locked.

    Must be called against the **locked** Quiz row on every write path.
    """
    if quiz_has_attempt_history(quiz.id):
        return _FROZEN_BY_ATTEMPTS_MESSAGE
    if quiz.status == _PUBLISHED:
        return _FROZEN_BY_PUBLICATION_MESSAGE
    return None


# ======================================================================
# URLs
# ======================================================================


def _list_url(group_public_id):
    return url_for("teacher.group_listening", group_public_id=group_public_id)


def _detail_url(group_public_id, listening_public_id):
    return url_for(
        "teacher.listening_detail",
        group_public_id=group_public_id,
        listening_public_id=listening_public_id,
    )


def _detail_page_url(group_public_id, listening_public_id, page=1):
    """The detail page, optionally on a given question page.

    ``page=1`` is rendered without the query argument so the canonical URL
    of an activity stays clean and a bookmark keeps working.
    """
    if page and page > 1:
        return url_for(
            "teacher.listening_detail",
            group_public_id=group_public_id,
            listening_public_id=listening_public_id,
            page=page,
        )
    return _detail_url(group_public_id, listening_public_id)


def _edit_url(group_public_id, listening_public_id):
    return url_for(
        "teacher.listening_edit",
        group_public_id=group_public_id,
        listening_public_id=listening_public_id,
    )


def _settings_url(group_public_id, listening_public_id):
    return url_for(
        "teacher.listening_settings",
        group_public_id=group_public_id,
        listening_public_id=listening_public_id,
    )


def _new_url(group_public_id):
    return url_for("teacher.listening_create", group_public_id=group_public_id)


def _question_create_url(group_public_id, listening_public_id):
    return url_for(
        "teacher.listening_question_create",
        group_public_id=group_public_id,
        listening_public_id=listening_public_id,
    )


def _question_edit_url(group_public_id, listening_public_id, question_public_id):
    return url_for(
        "teacher.listening_question_edit",
        group_public_id=group_public_id,
        listening_public_id=listening_public_id,
        question_public_id=question_public_id,
    )


# ======================================================================
# Signed exact-shape state tokens (Phase 4 / M05)
# ======================================================================
#
# Seven dedicated M05 salts and seven exact purpose markers. A token
# minted under any other salt -- the M01 assignment snapshot, the M03
# feedback state, every M04 quiz token -- fails signature verification
# here even though all of them are signed with the same application
# SECRET_KEY, and a token minted under one of these salts but for another
# M05 purpose fails the purpose check. The reverse holds too: an M05
# token is worthless on the M04 quiz routes.
#
# Every payload carries **public identifiers, versions and known
# enumerated values only**. A signed token is authenticated, not
# encrypted: anyone holding it can read its payload, so no transcript, no
# vocabulary text, no audio metadata, no storage key, no prompt, no option
# text, no selection and no answer key is ever placed in one -- and no
# internal database id either.
#
# The shape check below is exact and typed rather than merely "is a dict":
# the key set must match exactly, the purpose must be the expected one,
# identifiers must be strings, versions and page numbers must be genuine
# positive ints (``bool`` excluded explicitly, since it is an ``int``
# subclass and ``True`` must never pass as version 1), and each enumerated
# field must be a known member.

_SALTS = {
    "listening-create": "teacher.listening-create.phase4-m05.v1",
    "listening-content": "teacher.listening-content.phase4-m05.v1",
    "listening-settings": "teacher.listening-settings.phase4-m05.v1",
    "listening-publication": "teacher.listening-publication.phase4-m05.v1",
    "listening-question-create": "teacher.listening-question-create.phase4-m05.v1",
    "listening-question-edit": "teacher.listening-question-edit.phase4-m05.v1",
    "listening-question-move": "teacher.listening-question-move.phase4-m05.v1",
}

_FIELDS = {
    # Creation binds the Teacher, the Group and the Listening purpose --
    # and nothing else, because no activity exists yet to bind to.
    "listening-create": ("purpose", "teacher_public_id", "group_public_id", "nonce"),
    "listening-content": (
        "purpose", "teacher_public_id", "group_public_id", "listening_public_id",
        "quiz_version",
    ),
    "listening-settings": (
        "purpose", "teacher_public_id", "group_public_id", "listening_public_id",
        "quiz_version",
    ),
    "listening-publication": (
        "purpose", "action", "teacher_public_id", "group_public_id",
        "listening_public_id", "quiz_version", "quiz_status",
    ),
    "listening-question-create": (
        "purpose", "teacher_public_id", "group_public_id", "listening_public_id",
        "quiz_version",
    ),
    "listening-question-edit": (
        "purpose", "teacher_public_id", "group_public_id", "listening_public_id",
        "question_public_id", "quiz_version", "question_version", "option_public_ids",
    ),
    "listening-question-move": (
        "purpose", "direction", "teacher_public_id", "group_public_id",
        "listening_public_id", "question_public_id", "quiz_version",
        "question_version", "page",
    ),
}

#: Fields that must be genuine positive integers.
_INT_FIELDS = frozenset({"quiz_version", "question_version", "page"})
#: Fields whose value must be a member of a small known set.
_ENUM_FIELDS = {
    "action": frozenset(_PUBLICATION_ACTIONS),
    "direction": frozenset(MOVE_DIRECTIONS),
    "quiz_status": frozenset({_DRAFT_STATUS, _PUBLISHED}),
}


def _make_token(purpose, **payload):
    """Sign one exact-shape M05 token.

    Only ever called with freshly read persisted state or a
    server-generated nonce, never with attempted form values: a fresh
    token may only pair with fresh state, which is precisely the bypass
    the stale rejection exists to close.
    """
    body = dict(payload, purpose=purpose)
    if set(body) != set(_FIELDS[purpose]):  # pragma: no cover -- programming error
        raise ValueError(f"token payload does not match the {purpose} shape")
    return _serializer(_SALTS[purpose]).dumps(body)


def _load_token(token, purpose):
    """The token's payload, or ``None`` for a missing, malformed,
    invalidly signed, wrong-purpose, wrong-salt or wrong-shaped one.

    Every ``None`` is treated exactly like an outdated token: rejected,
    never trusted, and never silently upgraded into a claim about a row.
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
    for field in fields:
        value = payload[field]
        if field in _INT_FIELDS:
            if not _positive_int(value):
                return None
        elif field in _ENUM_FIELDS:
            if value not in _ENUM_FIELDS[field]:
                return None
        elif field == "option_public_ids":
            if not isinstance(value, list) or not _all_strings(value):
                return None
            # A repeated identifier would make "which options did this
            # form show" ambiguous, and the bound must stay inside the
            # approved active range.
            if len(set(value)) != len(value):
                return None
            if not MIN_ACTIVE_OPTIONS <= len(value) <= MAX_ACTIVE_OPTIONS:
                return None
        elif not isinstance(value, str):
            return None
    return payload


def _token_is_stale(token, purpose, **expected):
    """True when `token` does not exactly describe `expected`.

    The expected values must come from the rows this request **locked**,
    never from a pre-lock preview: that is what closes the window between
    the form's GET and the write, and what turns a losing co-teacher race
    into an explicit "reload and review" rejection rather than a silent
    overwrite.
    """
    payload = _load_token(token, purpose)
    if payload is None:
        return True
    return any(payload[field] != value for field, value in expected.items())


def _move_token_page(token):
    """The normalized return page a move token names, or 1.

    Read **before** the authoritative staleness check so a rejected move
    can still send the Teacher back to the page they were reading.
    """
    payload = _load_token(token, "listening-question-move")
    if payload is None:
        return 1
    return normalize_question_page(payload["page"])


# ======================================================================
# Locking -- the M04 chain, extended by exactly one link
# ======================================================================


def _lock_listening_chain(
    group_public_id, term_id, level_id, course_id, teacher_id, quiz_id,
    activity_id=None, question_ids=(),
):
    """Acquire the established lock order in one open transaction, with
    the ListeningActivity row taking its documented place::

        AcademicTerm -> Level -> Course   (lock_academic_hierarchy, which
        owns the single deliberate reset)
        -> Group -> acting Teacher User -> GroupTeacherAssignment
        -> Quiz
        -> ListeningActivity
        -> QuizQuestion rows, ascending internal id

    ``_lock_quiz_chain`` is reused verbatim for the whole prefix rather
    than restated, so the Group lock that already serializes a quiz write
    against an Administrator Group retarget serializes a Listening write
    against it too. The extension row is locked **after** its Quiz, never
    before: the Quiz remains the serialization point for its whole
    aggregate.

    Returns ``(hierarchy, group, teacher, assignment, quiz, activity,
    {id: question})``. Any of them may be ``None`` -- the caller must
    treat that as a business/authorization rejection, roll back, and
    404 or redirect; it must never "keep going".

    SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
    REPEATABLE READ snapshot isolation, so tests can assert the
    *requested* reset and lock order and nothing about real InnoDB
    blocking.
    """
    hierarchy, group, teacher, assignment, quizzes = _lock_quiz_chain(
        group_public_id, term_id, level_id, course_id, teacher_id,
        quiz_ids=[] if quiz_id is None else [quiz_id],
    )
    quiz = None if quiz_id is None else quizzes.get(quiz_id)
    activity = None
    if activity_id is not None:
        activity = (
            ListeningActivity.query.filter_by(id=activity_id).with_for_update().first()
        )
    questions = _lock_question_rows(question_ids) if question_ids else {}
    return hierarchy, group, teacher, assignment, quiz, activity, questions


def _listening_ownership_broken(group, quiz, activity, listening_public_id):
    """True when the **locked** rows are no longer the nested chain the URL
    claims.

    Re-proved against the locked current reads rather than trusted from
    the pre-lock preview, and proved at **every** level: the Quiz must
    still exist and still belong to this exact Group, the extension row
    must still exist, must still belong to that exact Quiz, and must still
    carry this exact ``public_id``. The caller turns a ``True`` into the
    same non-disclosing 404 the read routes produce, with no partial write
    of any kind.
    """
    if group is None or quiz is None or activity is None:
        return True
    if quiz.group_id != group.id:
        return True
    return activity.quiz_id != quiz.id or activity.public_id != listening_public_id


# ======================================================================
# Fresh authorization -- the only evidence a post-rollback path may use
# ======================================================================


def _fresh_listening_authorization(
    actor_id, group_public_id, listening_public_id=None, question_public_id=None
):
    """Prove from **current database state** that `actor_id` may read this
    Group -- and, when asked, this activity and this question -- *right
    now*, and return ``(group, quiz, activity, question)``.

    Exists for the reason M04A already documents: ``roles_required`` runs
    once, before the view, so a path that has rolled back and released its
    locks no longer holds current evidence, and the same concurrent change
    that forced the rollback may have ended this Teacher's access. The
    actor is identified by a **scalar id captured before the reset**,
    never by ``current_user``.

    The actor proof itself is ``fresh_group_authorization`` -- the exact
    same five reads the ordinary Quiz surface performs -- so the two
    surfaces cannot come to disagree about who is eligible. Only the
    nested lookup differs, and it is the Listening one, which an ordinary
    Quiz can never satisfy.

    Reading stays historical: the AcademicTerm / Level / Course / Group
    need not be active here. Callers about to render an *editable* form
    check that separately and downgrade to a redirect, so an archived
    chain never yields a form that cannot save.
    """
    group = fresh_group_authorization(actor_id, group_public_id)
    if listening_public_id is None:
        return group, None, None, None

    row = teacher_listening(group.id, listening_public_id)
    if row is None:
        abort(404)
    quiz, activity = row
    if question_public_id is None:
        return group, quiz, activity, None

    question = teacher_question(quiz.id, question_public_id)
    if question is None:
        abort(404)
    return group, quiz, activity, question


def _listening_or_404(group, listening_public_id):
    """``(quiz, activity)`` for this Group, or the established
    non-disclosing 404. An ordinary Quiz's ``public_id``, another Group's
    activity and a nonexistent id all fail identically here."""
    row = teacher_listening(group.id, listening_public_id)
    if row is None:
        abort(404)
    return row


def _hierarchy_context(group):
    return group.academic_term_id, group.course.level_id, group.course_id


# ======================================================================
# List
# ======================================================================


@teacher_bp.get("/groups/<group_public_id>/listening")
@roles_required(UserRole.TEACHER.value)
def group_listening(group_public_id):
    """One bounded page of a Group's Listening activities, newest first.

    Fixed page size, SQL ordering (``created_at DESC, id DESC``), SQL
    ``LIMIT PAGE_SIZE + 1`` for the has-next flag with no ``COUNT``, and
    the same page normalization and past-the-end fallback every other
    Teacher list uses. The rows carry no instructions, no transcript and
    no vocabulary notes, so the page's cost does not grow with how much
    has been written into each activity.

    Stays available under an archived Group or ancestor: an eligible
    assigned Teacher can always read back what was authored.
    """
    group = _teacher_group_or_404(group_public_id)
    operational = _group_is_operational(group)
    tz_name = _tz_name()
    reference_utc = _write_moment()
    page = normalize_page(request.args.get("page"))

    rows, has_next = teacher_listening_page(group.id, page)
    if not rows and page > 1:
        # A page past the end (a stale bookmark) shows page 1 rather than
        # a confusing empty page with a "Previous" button.
        page = 1
        rows, has_next = teacher_listening_page(group.id, page)

    return _private_no_store(
        "teacher/listening/list.html",
        group=group,
        activities=build_teacher_listening_list_view(rows, tz_name, reference_utc),
        operational=operational,
        archived_labels=[] if operational else _archived_chain_labels(group),
        tz_name=tz_name,
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


# ======================================================================
# Detail -- read only
# ======================================================================


@teacher_bp.get("/groups/<group_public_id>/listening/<listening_public_id>")
@roles_required(UserRole.TEACHER.value)
def listening_detail(group_public_id, listening_public_id):
    """One Listening activity and one bounded page of its questions, or a
    non-disclosing 404.

    **This route writes nothing.** Every control it renders is a POST to
    its own route with its own CSRF token, its own signed state and its
    own authoritative post-lock checks.

    The question list is bounded three ways, exactly as the Quiz detail
    page is: ``LIMIT QUESTION_PAGE_SIZE + 1`` rows for a next-page flag
    with no ``COUNT``, a SQL-truncated prompt preview instead of the whole
    prompt, and **one** grouped query for every row's active/correct
    option counts rather than a lookup per row.

    The readiness panel calls ``listening_publication_blockers``, which is
    the same function the publish route runs against the locked rows, so
    what a Teacher reads and the rule that decides can never disagree.
    """
    group = _teacher_group_or_404(group_public_id)
    quiz, activity = _listening_or_404(group, listening_public_id)
    tz_name = _tz_name()
    operational = _group_is_operational(group)
    page = normalize_question_page(request.args.get("page"))

    question_rows, has_next = teacher_questions_page(quiz.id, page)
    if not question_rows and page > 1:
        page = 1
        question_rows, has_next = teacher_questions_page(quiz.id, page)

    counts = active_option_counts([question.id for question in question_rows])
    questions = build_question_summaries(
        question_rows, counts, (page - 1) * QUESTION_PAGE_SIZE + 1
    )
    actor_public_id = current_user.public_id
    published = quiz.status == _PUBLISHED
    has_attempts = quiz_has_attempt_history(quiz.id)
    frozen_message = _authoring_block(quiz)
    if operational and frozen_message is None:
        for summary, question in zip(questions, question_rows):
            summary["move_up_token"] = _make_token(
                "listening-question-move", direction=MOVE_UP,
                teacher_public_id=actor_public_id, group_public_id=group_public_id,
                listening_public_id=listening_public_id,
                question_public_id=question.public_id,
                quiz_version=quiz.version, question_version=question.version, page=page,
            )
            summary["move_down_token"] = _make_token(
                "listening-question-move", direction=MOVE_DOWN,
                teacher_public_id=actor_public_id, group_public_id=group_public_id,
                listening_public_id=listening_public_id,
                question_public_id=question.public_id,
                quiz_version=quiz.version, question_version=question.version, page=page,
            )

    reference_utc = _write_moment()
    state = availability_state(quiz, reference_utc)

    return _private_no_store(
        "teacher/listening/detail.html",
        group=group,
        activity=build_listening_detail(quiz, activity, tz_name),
        audio=build_audio_summary(audio_upload_for_activity(activity)),
        audio_immutable_message=_AUDIO_IMMUTABLE_MESSAGE,
        questions=questions,
        operational=operational,
        archived_labels=[] if operational else _archived_chain_labels(group),
        tz_name=tz_name,
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=QUESTION_PAGE_SIZE,
        min_options=MIN_ACTIVE_OPTIONS,
        max_options=MAX_ACTIVE_OPTIONS,
        max_questions=MAX_QUIZ_QUESTIONS,
        published=published,
        has_attempts=has_attempts,
        frozen_message=frozen_message,
        availability={
            "opens_local": to_app_local(tz_name, quiz.opens_at) if quiz.opens_at else None,
            "closes_local": to_app_local(tz_name, quiz.closes_at) if quiz.closes_at else None,
            "time_limit_minutes": quiz.time_limit_minutes,
            "attempt_limit": quiz.attempt_limit,
            "published_local": (
                to_app_local(tz_name, quiz.published_at) if quiz.published_at else None
            ),
            "state": state,
            "state_label": STATE_LABELS.get(state),
        },
        readiness=[] if published else listening_publication_blockers(quiz, activity),
        publish_token=(
            _make_token(
                "listening-publication", action=_PUBLISH_ACTION,
                teacher_public_id=actor_public_id, group_public_id=group_public_id,
                listening_public_id=listening_public_id,
                quiz_version=quiz.version, quiz_status=quiz.status,
            )
            if operational and not published
            else None
        ),
        unpublish_token=(
            _make_token(
                "listening-publication", action=_UNPUBLISH_ACTION,
                teacher_public_id=actor_public_id, group_public_id=group_public_id,
                listening_public_id=listening_public_id,
                quiz_version=quiz.version, quiz_status=quiz.status,
            )
            if operational and published and not has_attempts
            else None
        ),
    )


# ======================================================================
# Create -- one activity, one recording, one transaction
# ======================================================================


def _render_create_page(form, actor_id, actor_public_id, group_public_id,
                        nonce=None, message=None):
    """Render the create form, redirect, or 404.

    Releases any write lock first (harmless on a GET or a plain form
    failure) and then re-proves the **whole** authorization chain --
    including the acting Teacher's own role and account status -- from
    current state, before anything is rendered or any token is minted.
    `message` is flashed **only after** that has passed, so a request
    whose access ended in the same window that caused the failure 404s
    silently instead of leaving a message behind for whatever page the
    actor reaches next.

    A rejected create keeps the **same** nonce, so the Teacher's retry is
    still covered by the duplicate-request protection rather than being
    handed a brand-new one that would let a double submission through.
    """
    db.session.rollback()
    group, _, _, _ = _fresh_listening_authorization(actor_id, group_public_id)
    if message is not None:
        flash(message, "danger")
    if not _group_is_operational(group):
        # The chain was archived while this request was in flight. Never
        # hand back a form that cannot save.
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_list_url(group_public_id))
    return _private_no_store(
        "teacher/listening/form.html",
        form=form,
        group=group,
        activity=None,
        state_token=_make_token(
            "listening-create",
            teacher_public_id=actor_public_id,
            group_public_id=group_public_id,
            nonce=nonce or secrets.token_hex(32),
        ),
        audio_accept=AUDIO_ACCEPT_ATTRIBUTE,
        supported_formats=", ".join(e.upper() for e in SUPPORTED_AUDIO_EXTENSIONS),
        audio_immutable_message=_AUDIO_IMMUTABLE_MESSAGE,
        tz_name=_tz_name(),
    )


def _replay_response(group_public_id, nonce):
    """An ordinary or concurrent replay of an already-succeeded create
    request: redirect to the activity that nonce already created, without
    creating anything new. Idempotent by design.

    Returns ``None`` when the nonce names nothing, which is the ordinary
    first-submission case.
    """
    existing = ListeningActivity.query.filter_by(creation_nonce=nonce).first()
    if existing is None:
        return None
    flash(f"Listening activity '{existing.quiz.title}' was already created.", "success")
    return redirect(_detail_url(group_public_id, existing.public_id))


@teacher_bp.route("/groups/<group_public_id>/listening/new", methods=["GET", "POST"])
@roles_required(UserRole.TEACHER.value)
def listening_create(group_public_id):
    """Create one Listening activity from one uploaded MP3 or WAV
    recording.

    **Nothing about identity, ownership or the stored file is taken from
    the request.** The Group is the authorized public identifier in the
    URL; ``version`` is the constant 1; ``status`` is ``draft``; every
    ``public_id`` is server-generated; both timestamps are the request's
    post-lock authoritative moment; and the stored extension, category,
    content type, byte size, SHA-256 and random storage key are all
    determined by the validation pipeline rather than by anything the
    browser sent. A forged ``group_id`` / ``quiz_id`` / ``audio_file_id``
    / ``status`` / ``storage_key`` / ``version`` field has nowhere to
    land.

    **The upload never touches a database lock.** It is streamed,
    size-checked, extension-checked, declared-MIME-checked and
    signature-checked to private storage *before* the lock chain is taken
    -- holding a write lock while streaming a 50 MB recording is exactly
    what Part M12 forbids. Everything the locked re-check needs is then
    proved against the locked rows, and every non-success exit before a
    confirmed commit deletes the file this request wrote.

    **The four rows commit together.** ``UploadedFile``, the ``upload``
    ``FileAccessLog`` entry, the ``Quiz`` and the ``ListeningActivity``
    are one transaction from the application's point of view: an activity
    can never exist without its recording's metadata, and a recording's
    metadata never exists without the activity that owns it.
    """
    preview_group = _teacher_group_or_404(group_public_id)
    actor_id = current_user.id
    actor_public_id = current_user.public_id

    if not _group_is_operational(preview_group):
        # Helpful early check: keeps a bookmarked URL from silently
        # rendering a form that can no longer save. Deliberately NOT the
        # enforcement point -- the post-lock check below is.
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_list_url(group_public_id))

    if request.method == "GET":
        return _render_create_page(
            ListeningCreateForm(group_id=preview_group.id),
            actor_id, actor_public_id, group_public_id,
        )

    submitted_token = request.form.get("listening_state", "")
    payload = _load_token(submitted_token, "listening-create")
    if (
        payload is None
        or payload["teacher_public_id"] != actor_public_id
        or payload["group_public_id"] != group_public_id
    ):
        db.session.rollback()
        flash(
            "This form could not be verified (it may be old or was opened in another tab). "
            "Please try again.",
            "danger",
        )
        return redirect(_new_url(group_public_id))
    nonce = payload["nonce"]

    # An ordinary replay -- the Teacher pressed Create twice, or reloaded
    # the POST -- resolves here, before anything is streamed or stored.
    replay = _replay_response(group_public_id, nonce)
    if replay is not None:
        return replay

    form = ListeningCreateForm(group_id=preview_group.id)
    if not form.validate_on_submit():
        return _render_create_page(
            form, actor_id, actor_public_id, group_public_id, nonce=nonce
        )

    title = form.normalized_title()
    instructions = form.normalized_instructions()
    transcript = form.normalized_transcript()
    visibility = form.transcript_visibility.data
    vocabulary = form.normalized_vocabulary_notes()

    # A cheap, safe pre-check on the claimed extension so a document, an
    # image or a video is refused *before* its bytes are streamed to disk
    # at all. It is not the authority -- `store_validated_upload` re-reads
    # the extension, checks the declared MIME and checks the binary
    # signature, and the stored category is re-asserted below -- but there
    # is no reason to write a file that can only be deleted again.
    upload = form.audio.data
    if extension_of((getattr(upload, "filename", "") or "").lower()) not in SUPPORTED_AUDIO_EXTENSIONS:
        form.audio.errors.append(
            "Choose an MP3 or WAV recording. Other file types are not supported for "
            "listening activities."
        )
        return _render_create_page(
            form, actor_id, actor_public_id, group_public_id, nonce=nonce
        )

    material_config = current_material_config()
    try:
        stored = store_validated_upload(material_config, upload, upload.filename)
    except FileValidationError as exc:
        # User-caused: this message is deliberately safe -- no path, no
        # SQL, no exception internals (see file_validation.py).
        form.audio.errors.append(str(exc))
        return _render_create_page(
            form, actor_id, actor_public_id, group_public_id, nonce=nonce
        )
    # Any other exception from store_validated_upload (containment,
    # OSError, ...) propagates: it has already deleted its own temp/final
    # files, Flask logs it server-side, and the generic 500 handler
    # responds without leaking details.

    # From here a final file exists on disk. Every non-success exit before
    # a confirmed commit must delete it; `committed` gates that so a
    # successfully-saved recording is never removed.
    committed = False
    activity_public_id = None
    try:
        if stored.category != _AUDIO_CATEGORY:  # pragma: no cover -- defense in depth
            # Unreachable while the extension pre-check and the configured
            # category map agree. Refused rather than trusted, because the
            # `audio` category is an invariant of the extension row.
            form.audio.errors.append(
                "Choose an MP3 or WAV recording. Other file types are not supported for "
                "listening activities."
            )
            return _render_create_page(
                form, actor_id, actor_public_id, group_public_id, nonce=nonce
            )

        term_id, level_id, course_id = _hierarchy_context(preview_group)
        hierarchy, group, teacher, assignment, _, _, _ = _lock_listening_chain(
            group_public_id, term_id, level_id, course_id, actor_id, quiz_id=None
        )
        if _authz_broken(group, teacher, assignment):
            db.session.rollback()
            abort(404)

        blocked = _operational_block(
            hierarchy, group, term_id, level_id, course_id,
            wording=_LISTENING_BLOCK_WORDING,
        )
        if blocked is not None:
            db.session.rollback()
            flash(blocked, "danger")
            return redirect(_list_url(group_public_id))

        # A genuinely concurrent replay: another request may have
        # committed this same nonce while this one was streaming its
        # (now redundant) recording.
        replay = _replay_response(group_public_id, nonce)
        if replay is not None:
            db.session.rollback()
            return replay  # loser -- `finally` cleans its own file

        # The authoritative duplicate-title check, taken against the
        # locked Group. The form's identical check ran before the locks
        # and is only a courtesy; `uq_quizzes_group_title` is the final
        # defense behind both, and it spans ordinary Quizzes too.
        if duplicate_title_exists(group.id, title):
            form.title.errors.append(
                "A quiz or listening activity with this title already exists in this group."
            )
            return _render_create_page(
                form, actor_id, actor_public_id, group_public_id, nonce=nonce
            )

        # The authoritative write moment: read only now, after every lock
        # that could have blocked, and truncated to the whole second the
        # columns can actually hold. Every row created here gets the SAME
        # value for both of its timestamps.
        now_utc = _write_moment()

        uploaded_file = UploadedFile(
            storage_key=stored.storage_key,
            original_filename=stored.original_filename,
            extension=stored.extension,
            category=stored.category,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            uploaded_by_id=actor_id,
        )
        quiz = Quiz(
            group_id=group.id,
            title=title,
            instructions=instructions,
            status=_DRAFT_STATUS,
            published_at=None,
            version=1,
            created_at=now_utc,
            updated_at=now_utc,
        )
        activity = ListeningActivity(
            quiz=quiz,
            audio_file=uploaded_file,
            transcript=transcript,
            transcript_visibility=visibility,
            vocabulary_notes=vocabulary,
            creation_nonce=nonce,
            created_at=now_utc,
            updated_at=now_utc,
        )
        upload_log = FileAccessLog(
            uploaded_file=uploaded_file,
            actor_id=actor_id,
            action=FileAccessAction.UPLOAD.value,
        )
        db.session.add_all([uploaded_file, quiz, activity, upload_log])
        try:
            db.session.commit()
        except IntegrityError:
            # Roll back FIRST -- everything read is now discarded state
            # and must not be used as evidence of anything, least of all
            # authorization.
            db.session.rollback()
            replay = _replay_response(group_public_id, nonce)
            if replay is not None:
                return replay  # concurrent winner committed -- `finally` cleans
            return _render_create_page(
                form, actor_id, actor_public_id, group_public_id, nonce=nonce,
                message=(
                    "This listening activity could not be saved. A quiz or listening "
                    "activity with this title may already exist in the group, or the group "
                    "may have just changed. Please reload and try again."
                ),
            )
        committed = True
        activity_public_id = activity.public_id
    except HTTPException:
        # An intentional abort() (e.g. the non-disclosing 404 from the
        # post-lock authorization re-check). Not an error to log -- but
        # the file has no activity, so `finally` cleans it.
        db.session.rollback()
        raise
    except Exception:
        # Unexpected (lock/DB failure, programming error). Roll back, log
        # server-side, and let the generic 500 handler respond -- never
        # surface the exception text; `finally` cleans the file.
        db.session.rollback()
        current_app.logger.exception(
            "Unexpected error while creating a Listening activity"
        )
        raise
    finally:
        if not committed:
            _cleanup_orphan_upload(material_config, stored.storage_key)

    flash(
        f"Listening activity '{title}' created as a draft. Students cannot see it yet.",
        "success",
    )
    return redirect(_detail_url(group_public_id, activity_public_id))


# ======================================================================
# Edit -- title, instructions, transcript, transcript policy, vocabulary
# ======================================================================


def _render_edit_page(
    actor_id, actor_public_id, group_public_id, listening_public_id,
    form=None, state_token=None, message=None,
):
    """Render the content editor, redirect, or 404.

    Rolls back first, re-proves the whole nested chain from current state,
    and only then flashes, mints a token or renders anything. `form` and
    `state_token` are supplied only by the ordinary-validation and
    ``IntegrityError`` paths, which must show the Teacher their attempted
    values again with the **original** token -- a freshly minted token may
    only ever pair with freshly loaded persisted values, which is exactly
    the bypass the stale rejection exists to close.
    """
    db.session.rollback()
    group, quiz, activity, _ = _fresh_listening_authorization(
        actor_id, group_public_id, listening_public_id
    )
    if message is not None:
        flash(message, "danger")
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    # A courtesy check with the same rule the write path enforces: never
    # hand back a form whose save is already refused.
    frozen = _authoring_block(quiz)
    if frozen is not None:
        flash(frozen, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    if form is None:
        form = ListeningContentForm(
            formdata=None,
            data={
                "title": quiz.title,
                "instructions": quiz.instructions,
                "transcript": activity.transcript,
                "transcript_visibility": activity.transcript_visibility,
                "vocabulary_notes": activity.vocabulary_notes,
            },
            group_id=group.id,
            quiz_id=quiz.id,
        )
    if state_token is None:
        state_token = _make_token(
            "listening-content",
            teacher_public_id=actor_public_id, group_public_id=group_public_id,
            listening_public_id=listening_public_id, quiz_version=quiz.version,
        )

    return _private_no_store(
        "teacher/listening/form.html",
        form=form,
        group=group,
        activity=build_listening_detail(quiz, activity, _tz_name()),
        state_token=state_token,
        audio=build_audio_summary(audio_upload_for_activity(activity)),
        audio_accept=AUDIO_ACCEPT_ATTRIBUTE,
        supported_formats=", ".join(e.upper() for e in SUPPORTED_AUDIO_EXTENSIONS),
        audio_immutable_message=_AUDIO_IMMUTABLE_MESSAGE,
        tz_name=_tz_name(),
    )


@teacher_bp.route(
    "/groups/<group_public_id>/listening/<listening_public_id>/edit",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def listening_edit(group_public_id, listening_public_id):
    """Revise this activity's title, instructions, transcript, transcript
    policy and vocabulary notes.

    **The recording is not editable here and cannot be.** The form carries
    no file field, the route assigns no ``audio_file_id``, and there is no
    replacement endpoint anywhere -- so a forged ``audio_file_id`` or a
    smuggled file part has nowhere to land.

    **Order of the post-lock checks.** Authorization and nested ownership
    first, so an unauthorized attempt fails identically whatever the
    activity contains; then the operational chain; then the authoring
    freeze; then the signed token against the locked rows; then ordinary
    field validation. Only then is the no-op compared, the duplicate title
    re-checked, and any field assigned -- so every rejection leaves the
    stored rows, their version and their timestamps exactly as they were.

    A meaningful change increments ``Quiz.version`` by exactly one and
    moves both rows' ``updated_at``. An authorized, non-stale save of
    unchanged normalized values is a **no-op**: nothing is written at all.
    """
    if request.method == "GET":
        # The actor's identity is captured as plain scalars HERE, before
        # `_render_edit_page` performs its rollback, and the page then
        # re-proves that actor against current state.
        return _render_edit_page(
            current_user.id, current_user.public_id,
            group_public_id, listening_public_id,
        )

    preview_group = _teacher_group_or_404(group_public_id)
    preview_quiz, preview_activity = _listening_or_404(preview_group, listening_public_id)

    if not _group_is_operational(preview_group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    form = ListeningContentForm(group_id=preview_group.id, quiz_id=preview_quiz.id)
    form_is_valid = form.validate_on_submit()

    # Every scalar the rest of this request needs is captured BEFORE the
    # transaction reset below, so nothing between that reset and the
    # required locks triggers a lazy ORM or `current_user` reload that
    # would establish a fresh read snapshot ahead of the locks.
    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    activity_id = preview_activity.id
    term_id, level_id, course_id = _hierarchy_context(preview_group)
    submitted_token = request.form.get("listening_state", "")

    hierarchy, group, teacher, assignment, quiz, activity, _ = _lock_listening_chain(
        group_public_id, term_id, level_id, course_id, teacher_id, quiz_id, activity_id
    )
    if _authz_broken(group, teacher, assignment):
        db.session.rollback()
        abort(404)
    if _listening_ownership_broken(group, quiz, activity, listening_public_id):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(
        hierarchy, group, term_id, level_id, course_id,
        wording=_LISTENING_BLOCK_WORDING,
    )
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    frozen = _authoring_block(quiz)
    if frozen is not None:
        db.session.rollback()
        flash(frozen, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    if _token_is_stale(
        submitted_token, "listening-content",
        teacher_public_id=teacher_public_id, group_public_id=group_public_id,
        listening_public_id=listening_public_id, quiz_version=quiz.version,
    ):
        db.session.rollback()
        flash(_STALE_MESSAGE, "danger")
        return redirect(_edit_url(group_public_id, listening_public_id))

    if not form_is_valid:
        return _render_edit_page(
            teacher_id, teacher_public_id, group_public_id, listening_public_id,
            form=form, state_token=submitted_token,
        )

    title = form.normalized_title()
    instructions = form.normalized_instructions()
    transcript = form.normalized_transcript()
    visibility = form.transcript_visibility.data
    vocabulary = form.normalized_vocabulary_notes()

    if (
        quiz.title == title
        and quiz.instructions == instructions
        and activity.transcript == transcript
        and activity.transcript_visibility == visibility
        and activity.vocabulary_notes == vocabulary
    ):
        db.session.rollback()
        flash("This listening activity is unchanged, so nothing was saved.", "info")
        return redirect(_detail_url(group_public_id, listening_public_id))

    if duplicate_title_exists(group.id, title, exclude_quiz_id=quiz.id):
        form.title.errors.append(
            "A quiz or listening activity with this title already exists in this group."
        )
        return _render_edit_page(
            teacher_id, teacher_public_id, group_public_id, listening_public_id,
            form=form, state_token=submitted_token,
        )

    now_utc = _write_moment()

    # No field is assigned until every check above has passed, so a
    # rejection never leaves a partial update. `id`, `public_id`,
    # `group_id`, `quiz_id`, `audio_file_id`, `creation_nonce`, `status`,
    # `published_at` and both `created_at` values are never assigned here.
    quiz.title = title
    quiz.instructions = instructions
    quiz.version = quiz.version + 1
    quiz.updated_at = now_utc
    activity.transcript = transcript
    activity.transcript_visibility = visibility
    activity.vocabulary_notes = vocabulary
    activity.updated_at = now_utc
    try:
        db.session.commit()
    except IntegrityError:
        return _render_edit_page(
            teacher_id, teacher_public_id, group_public_id, listening_public_id,
            form=form, state_token=submitted_token,
            message=(
                "This listening activity could not be saved. A quiz or listening activity "
                "with this title may already exist in the group, or someone may have just "
                "changed it. Please reload and try again."
            ),
        )

    flash(f"Listening activity '{title}' updated. It is still a draft.", "success")
    return redirect(_detail_url(group_public_id, listening_public_id))


# ======================================================================
# Availability, timing and attempt settings -- the M04D form, reused
# ======================================================================


def _render_settings_page(
    actor_id, actor_public_id, group_public_id, listening_public_id,
    form=None, state_token=None, message=None,
):
    """Render the settings form, redirect, or 404 -- the rule every
    post-rollback path in this module follows."""
    db.session.rollback()
    group, quiz, activity, _ = _fresh_listening_authorization(
        actor_id, group_public_id, listening_public_id
    )
    if message is not None:
        flash(message, "danger")
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    frozen = _authoring_block(quiz)
    if frozen is not None:
        flash(frozen, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    tz_name = _tz_name()
    if form is None:
        form = QuizSettingsForm(
            formdata=None, data=_settings_defaults(quiz, tz_name), tz_name=tz_name
        )
    if state_token is None:
        state_token = _make_token(
            "listening-settings",
            teacher_public_id=actor_public_id, group_public_id=group_public_id,
            listening_public_id=listening_public_id, quiz_version=quiz.version,
        )

    return _private_no_store(
        "teacher/listening/settings.html",
        form=form,
        group=group,
        activity=build_listening_detail(quiz, activity, tz_name),
        state_token=state_token,
        tz_name=tz_name,
    )


@teacher_bp.route(
    "/groups/<group_public_id>/listening/<listening_public_id>/settings",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def listening_settings(group_public_id, listening_public_id):
    """Set the availability window, optional time limit and attempt limit.

    The **same** ``QuizSettingsForm``, the same four columns, the same
    timezone conversion and the same pair rule as an ordinary Quiz --
    reused rather than restated, so a Teacher meets one behaviour and a
    future change reaches both surfaces.

    **Nothing about publication is taken from the request.** ``status``
    and ``published_at`` are never assigned here -- they belong solely to
    the publish/withdraw routes -- so a forged field of either name has
    nowhere to land.
    """
    if request.method == "GET":
        return _render_settings_page(
            current_user.id, current_user.public_id,
            group_public_id, listening_public_id,
        )

    preview_group = _teacher_group_or_404(group_public_id)
    preview_quiz, preview_activity = _listening_or_404(preview_group, listening_public_id)

    if not _group_is_operational(preview_group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    tz_name = _tz_name()
    form = QuizSettingsForm(tz_name=tz_name)
    form_is_valid = form.validate_on_submit()

    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    activity_id = preview_activity.id
    term_id, level_id, course_id = _hierarchy_context(preview_group)
    submitted_token = request.form.get("listening_state", "")

    hierarchy, group, teacher, assignment, quiz, activity, _ = _lock_listening_chain(
        group_public_id, term_id, level_id, course_id, teacher_id, quiz_id, activity_id
    )
    if _authz_broken(group, teacher, assignment):
        db.session.rollback()
        abort(404)
    if _listening_ownership_broken(group, quiz, activity, listening_public_id):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(
        hierarchy, group, term_id, level_id, course_id,
        wording=_LISTENING_BLOCK_WORDING,
    )
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    frozen = _authoring_block(quiz)
    if frozen is not None:
        db.session.rollback()
        flash(frozen, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    if _token_is_stale(
        submitted_token, "listening-settings",
        teacher_public_id=teacher_public_id, group_public_id=group_public_id,
        listening_public_id=listening_public_id, quiz_version=quiz.version,
    ):
        db.session.rollback()
        flash(_STALE_MESSAGE, "danger")
        return redirect(_settings_url(group_public_id, listening_public_id))

    if not form_is_valid:
        return _render_settings_page(
            teacher_id, teacher_public_id, group_public_id, listening_public_id,
            form=form, state_token=submitted_token,
        )

    opens_at = form.opens_at_utc
    closes_at = form.closes_at_utc
    time_limit = form.time_limit_minutes.data
    attempt_limit = form.attempt_limit.data

    if (
        quiz.opens_at == opens_at
        and quiz.closes_at == closes_at
        and quiz.time_limit_minutes == time_limit
        and quiz.attempt_limit == attempt_limit
    ):
        db.session.rollback()
        flash("These settings are unchanged, so nothing was saved.", "info")
        return redirect(_detail_url(group_public_id, listening_public_id))

    now_utc = _write_moment()
    quiz.opens_at = opens_at
    quiz.closes_at = closes_at
    quiz.time_limit_minutes = time_limit
    quiz.attempt_limit = attempt_limit
    quiz.version = quiz.version + 1
    quiz.updated_at = now_utc
    try:
        db.session.commit()
    except IntegrityError:
        return _render_settings_page(
            teacher_id, teacher_public_id, group_public_id, listening_public_id,
            form=form, state_token=submitted_token,
            message=(
                "These settings could not be saved. Someone may have just changed this "
                "listening activity. Please reload and try again."
            ),
        )

    flash("Listening settings saved. The activity is still a draft.", "success")
    return redirect(_detail_url(group_public_id, listening_public_id))


# ======================================================================
# Questions -- the M04B rules, unchanged
# ======================================================================


def _render_question_page(
    actor_id, actor_public_id, group_public_id, listening_public_id,
    question_public_id=None, form=None, state_token=None, message=None,
):
    """Render the question editor for a new or existing question,
    redirect, or 404.

    One function for both directions so create and edit cannot drift in
    their authorization, freeze or staleness handling -- only the token
    purpose and the seed rows differ. Rolls back, re-proves the whole
    nested chain from current state, and only then flashes, mints or
    renders.
    """
    db.session.rollback()
    group, quiz, activity, question = _fresh_listening_authorization(
        actor_id, group_public_id, listening_public_id, question_public_id
    )
    if message is not None:
        flash(message, "danger")
    if not _group_is_operational(group):
        flash(_QUESTION_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    frozen = _authoring_block(quiz)
    if frozen is not None:
        flash(frozen, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    editor = None
    if question is None:
        if form is None:
            form = QuizQuestionForm(
                formdata=None,
                data={"answer_mode": QuestionAnswerMode.SINGLE.value},
                seed_rows=_blank_option_rows(),
            )
        if state_token is None:
            state_token = _make_token(
                "listening-question-create",
                teacher_public_id=actor_public_id, group_public_id=group_public_id,
                listening_public_id=listening_public_id, quiz_version=quiz.version,
            )
    else:
        options = active_options_ordered(question.id)
        if active_option_set_is_invalid(options):
            # Refuse rather than render an editor whose save could not
            # possibly be valid. Nothing is truncated or "repaired".
            flash(_QUESTION_INVALID_STATE_MESSAGE, "danger")
            return redirect(_detail_url(group_public_id, listening_public_id))
        editor = build_question_editor(question, options)
        if form is None:
            form = QuizQuestionForm(
                formdata=None,
                data={"prompt": question.prompt, "answer_mode": question.answer_mode},
                seed_rows=[
                    {"key": row["public_id"], "text": row["text"],
                     "is_correct": row["is_correct"]}
                    for row in editor["options"]
                ],
            )
        if state_token is None:
            state_token = _make_token(
                "listening-question-edit",
                teacher_public_id=actor_public_id, group_public_id=group_public_id,
                listening_public_id=listening_public_id,
                question_public_id=question_public_id,
                quiz_version=quiz.version, question_version=question.version,
                option_public_ids=[row["public_id"] for row in editor["options"]],
            )

    return _private_no_store(
        "teacher/listening/question_form.html",
        form=form,
        group=group,
        activity=build_listening_detail(quiz, activity, _tz_name()),
        question=editor,
        state_token=state_token,
    )


@teacher_bp.route(
    "/groups/<group_public_id>/listening/<listening_public_id>/questions/new",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def listening_question_create(group_public_id, listening_public_id):
    """Add one ordered multiple-choice question to a Listening activity.

    Exactly the M04B rules, applied through the same form and the same
    locked-aggregate checks: 2..8 active options, no duplicate normalized
    option text, and the answer-mode cardinality rule, with nothing ever
    silently added, cleared or truncated.

    **Nothing about identity or placement is taken from the request.**
    ``display_order`` is read from the locked Quiz, ``version`` starts at
    1, every ``public_id`` is server-generated and the timestamps are the
    request's post-lock moment.
    """
    if request.method == "GET":
        return _render_question_page(
            current_user.id, current_user.public_id,
            group_public_id, listening_public_id,
        )

    preview_group = _teacher_group_or_404(group_public_id)
    preview_quiz, preview_activity = _listening_or_404(preview_group, listening_public_id)

    if not _group_is_operational(preview_group):
        flash(_QUESTION_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    keys, texts, correct = submitted_option_fields(request.form)

    form = QuizQuestionForm(option_keys=keys, option_texts=texts, correct_keys=correct)
    form_is_valid = form.validate_on_submit()

    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    activity_id = preview_activity.id
    term_id, level_id, course_id = _hierarchy_context(preview_group)
    submitted_token = request.form.get("listening_state", "")

    hierarchy, group, teacher, assignment, quiz, activity, _ = _lock_listening_chain(
        group_public_id, term_id, level_id, course_id, teacher_id, quiz_id, activity_id
    )
    if _authz_broken(group, teacher, assignment):
        db.session.rollback()
        abort(404)
    if _listening_ownership_broken(group, quiz, activity, listening_public_id):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(
        hierarchy, group, term_id, level_id, course_id,
        wording=_QUESTION_BLOCK_WORDING,
    )
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    frozen = _authoring_block(quiz)
    if frozen is not None:
        db.session.rollback()
        flash(frozen, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    if _token_is_stale(
        submitted_token, "listening-question-create",
        teacher_public_id=teacher_public_id, group_public_id=group_public_id,
        listening_public_id=listening_public_id, quiz_version=quiz.version,
    ):
        db.session.rollback()
        flash(_STALE_MESSAGE, "danger")
        return redirect(_question_create_url(group_public_id, listening_public_id))

    if not form_is_valid:
        return _render_question_page(
            teacher_id, teacher_public_id, group_public_id, listening_public_id,
            form=form, state_token=submitted_token,
        )

    # A brand-new question owns no persisted option, so every submitted
    # row must be a `new:` marker. A claimed public_id here belongs to
    # some other question (or to nothing at all) and is refused rather
    # than adopted.
    if any(option.public_id is not None for option in form.options):
        return _render_question_page(
            teacher_id, teacher_public_id, group_public_id, listening_public_id,
            form=_questions_error(
                form,
                "The answer options could not be read. Please reload the page and try again.",
            ),
            state_token=submitted_token,
        )

    now_utc = _write_moment()
    question = QuizQuestion(
        quiz_id=quiz.id,
        prompt=form.normalized_prompt(),
        answer_mode=form.answer_mode.data,
        display_order=next_question_display_order(quiz.id),
        version=1,
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.session.add(question)
    try:
        # One flush inside the SAME transaction, so the options can
        # reference the question's internal id. Nothing is committed yet,
        # and the flush is inside this `try` because it is itself a write
        # a constraint can refuse.
        db.session.flush()
        for index, option in enumerate(form.options):
            db.session.add(
                QuestionOption(
                    question_id=question.id,
                    option_text=option.text,
                    display_order=index,
                    is_correct=option.is_correct,
                    is_active=True,
                    retired_at=None,
                    created_at=now_utc,
                    updated_at=now_utc,
                )
            )
        # The parent draft changed, so its concurrency signal and its
        # "last changed" moment both move exactly once.
        quiz.version = quiz.version + 1
        quiz.updated_at = now_utc
        db.session.commit()
    except IntegrityError:
        return _render_question_page(
            teacher_id, teacher_public_id, group_public_id, listening_public_id,
            form=form, state_token=submitted_token,
            message=(
                "This question could not be saved. The listening activity may have just "
                "been changed by someone else. Please reload and try again."
            ),
        )

    flash("Question added. The listening activity is still a draft.", "success")
    return redirect(_detail_url(group_public_id, listening_public_id))


@teacher_bp.route(
    "/groups/<group_public_id>/listening/<listening_public_id>"
    "/questions/<question_public_id>/edit",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def listening_question_edit(group_public_id, listening_public_id, question_public_id):
    """Revise one question's prompt, answer mode and answer options.

    **Removal is retirement**, exactly as in M04B: an option the form no
    longer submits is deactivated in place with the request's moment,
    keeping its text, answer-key value, ``public_id``, creation time and
    stored order as history. Nothing is ever physically deleted, and there
    is no restoration route.
    """
    if request.method == "GET":
        return _render_question_page(
            current_user.id, current_user.public_id,
            group_public_id, listening_public_id, question_public_id,
        )

    preview_group = _teacher_group_or_404(group_public_id)
    preview_quiz, preview_activity = _listening_or_404(preview_group, listening_public_id)
    preview_question = teacher_question(preview_quiz.id, question_public_id)
    if preview_question is None:
        abort(404)

    if not _group_is_operational(preview_group):
        flash(_QUESTION_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    keys, texts, correct = submitted_option_fields(request.form)

    form = QuizQuestionForm(option_keys=keys, option_texts=texts, correct_keys=correct)
    form_is_valid = form.validate_on_submit()

    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    activity_id = preview_activity.id
    question_id = preview_question.id
    term_id, level_id, course_id = _hierarchy_context(preview_group)
    submitted_token = request.form.get("listening_state", "")

    hierarchy, group, teacher, assignment, quiz, activity, questions = (
        _lock_listening_chain(
            group_public_id, term_id, level_id, course_id, teacher_id,
            quiz_id, activity_id, question_ids=[question_id],
        )
    )
    if _authz_broken(group, teacher, assignment):
        db.session.rollback()
        abort(404)
    if _listening_ownership_broken(group, quiz, activity, listening_public_id):
        db.session.rollback()
        abort(404)
    question = questions.get(question_id)
    if _question_ownership_broken(quiz, question, question_public_id):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(
        hierarchy, group, term_id, level_id, course_id,
        wording=_QUESTION_BLOCK_WORDING,
    )
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    frozen = _authoring_block(quiz)
    if frozen is not None:
        db.session.rollback()
        flash(frozen, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    locked_options = _lock_active_options(question.id)
    if active_option_set_is_invalid(locked_options):
        db.session.rollback()
        flash(_QUESTION_INVALID_STATE_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    if _token_is_stale(
        submitted_token, "listening-question-edit",
        teacher_public_id=teacher_public_id, group_public_id=group_public_id,
        listening_public_id=listening_public_id,
        question_public_id=question_public_id,
        quiz_version=quiz.version, question_version=question.version,
        option_public_ids=[option.public_id for option in locked_options],
    ):
        db.session.rollback()
        flash(_STALE_MESSAGE, "danger")
        return redirect(
            _question_edit_url(group_public_id, listening_public_id, question_public_id)
        )

    if not form_is_valid:
        return _render_question_page(
            teacher_id, teacher_public_id, group_public_id, listening_public_id,
            question_public_id, form=form, state_token=submitted_token,
        )

    # Ownership of every claimed option, proved against the LOCKED rows.
    # The token already pinned this exact set, so reaching here with an
    # unknown identifier means a tampered body rather than a race.
    known = {option.public_id: option for option in locked_options}
    if any(
        option.public_id is not None and option.public_id not in known
        for option in form.options
    ):
        return _render_question_page(
            teacher_id, teacher_public_id, group_public_id, listening_public_id,
            question_public_id,
            form=_questions_error(
                form,
                "The answer options could not be read. Please reload the page and try again.",
            ),
            state_token=submitted_token,
        )

    prompt = form.normalized_prompt()
    answer_mode = form.answer_mode.data

    if _aggregate_is_unchanged(
        question, prompt, answer_mode, form.options, locked_options
    ):
        db.session.rollback()
        flash("This question is unchanged, so nothing was saved.", "info")
        return redirect(_detail_url(group_public_id, listening_public_id))

    now_utc = _write_moment()
    retained = {
        option.public_id for option in form.options if option.public_id is not None
    }

    # 1. Retire the options this save dropped -- in place, never deleted.
    for stored in locked_options:
        if stored.public_id not in retained:
            stored.is_active = False
            stored.retired_at = now_utc
            stored.updated_at = now_utc

    # 2. Apply the surviving and new rows in submitted order, normalizing
    #    the ACTIVE order to 0..n-1. A row whose text, answer-key value and
    #    order are all unchanged is left completely alone.
    for index, option in enumerate(form.options):
        if option.public_id is None:
            db.session.add(
                QuestionOption(
                    question_id=question.id,
                    option_text=option.text,
                    display_order=index,
                    is_correct=option.is_correct,
                    is_active=True,
                    retired_at=None,
                    created_at=now_utc,
                    updated_at=now_utc,
                )
            )
            continue
        stored = known[option.public_id]
        if (
            stored.option_text != option.text
            or bool(stored.is_correct) != option.is_correct
            or stored.display_order != index
        ):
            stored.option_text = option.text
            stored.is_correct = option.is_correct
            stored.display_order = index
            stored.updated_at = now_utc

    # 3. The question itself, then its parent activity. Both counters move
    #    exactly once for this one successful edit.
    if question.prompt != prompt:
        question.prompt = prompt
    if question.answer_mode != answer_mode:
        question.answer_mode = answer_mode
    question.version = question.version + 1
    question.updated_at = now_utc
    quiz.version = quiz.version + 1
    quiz.updated_at = now_utc

    try:
        db.session.commit()
    except IntegrityError:
        return _render_question_page(
            teacher_id, teacher_public_id, group_public_id, listening_public_id,
            question_public_id, form=form, state_token=submitted_token,
            message=(
                "This question could not be saved. It may have just been changed by someone "
                "else. Please reload and try again."
            ),
        )

    flash("Question updated. The listening activity is still a draft.", "success")
    return redirect(_detail_url(group_public_id, listening_public_id))


def _move_question(group_public_id, listening_public_id, question_public_id, direction):
    """Swap one question with its neighbour in the complete authored
    order.

    The neighbour is resolved with the same **bounded keyset lookup** the
    Quiz surface uses, against the same ``(display_order, id)`` ordering,
    so a move is correct across order gaps, across shared order values and
    across page boundaries. Both Question rows are then locked in
    **ascending internal id**, never in visual order, so two co-teachers
    moving adjacent questions cannot deadlock against each other.
    """
    preview_group = _teacher_group_or_404(group_public_id)
    preview_quiz, preview_activity = _listening_or_404(preview_group, listening_public_id)
    preview_question = teacher_question(preview_quiz.id, question_public_id)
    if preview_question is None:
        abort(404)

    submitted_token = request.form.get("listening_state", "")
    return_page = _move_token_page(submitted_token)

    if not _group_is_operational(preview_group):
        flash(_QUESTION_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(
            _detail_page_url(group_public_id, listening_public_id, return_page)
        )

    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    activity_id = preview_activity.id
    term_id, level_id, course_id = _hierarchy_context(preview_group)

    hierarchy, group, teacher, assignment, quiz, activity, _ = _lock_listening_chain(
        group_public_id, term_id, level_id, course_id, teacher_id, quiz_id, activity_id
    )
    if _authz_broken(group, teacher, assignment):
        db.session.rollback()
        abort(404)
    if _listening_ownership_broken(group, quiz, activity, listening_public_id):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(
        hierarchy, group, term_id, level_id, course_id,
        wording=_QUESTION_BLOCK_WORDING,
    )
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return redirect(
            _detail_page_url(group_public_id, listening_public_id, return_page)
        )

    frozen = _authoring_block(quiz)
    if frozen is not None:
        db.session.rollback()
        flash(frozen, "danger")
        return redirect(
            _detail_page_url(group_public_id, listening_public_id, return_page)
        )

    # A current read under the held Quiz lock, only to decide WHICH rows
    # to lock. Neither row is trusted until it has been locked below.
    target_preview = teacher_question(quiz.id, question_public_id)
    if target_preview is None:
        db.session.rollback()
        abort(404)
    neighbour_preview = neighbour_question(
        quiz.id, target_preview.display_order, target_preview.id, direction
    )

    locked = _lock_question_rows(
        [target_preview.id, None if neighbour_preview is None else neighbour_preview.id]
    )
    target = locked.get(target_preview.id)
    if _question_ownership_broken(quiz, target, question_public_id):
        db.session.rollback()
        abort(404)

    if _token_is_stale(
        submitted_token, "listening-question-move", direction=direction,
        teacher_public_id=teacher_public_id, group_public_id=group_public_id,
        listening_public_id=listening_public_id,
        question_public_id=question_public_id,
        quiz_version=quiz.version, question_version=target.version,
    ):
        db.session.rollback()
        flash(_STALE_MESSAGE, "danger")
        return redirect(
            _detail_page_url(group_public_id, listening_public_id, return_page)
        )

    neighbour = None if neighbour_preview is None else locked.get(neighbour_preview.id)
    if neighbour is None or neighbour.quiz_id != quiz.id:
        # A boundary move changes nothing at all: no order is rewritten
        # and no version moves. Reported as information, not as an error.
        db.session.rollback()
        flash(
            "This question is already {}.".format(
                "first" if direction == MOVE_UP else "last"
            ),
            "info",
        )
        return redirect(
            _detail_page_url(group_public_id, listening_public_id, return_page)
        )

    now_utc = _write_moment()

    if target.display_order != neighbour.display_order:
        target.display_order, neighbour.display_order = (
            neighbour.display_order,
            target.display_order,
        )
        changed = [target, neighbour]
    elif direction == MOVE_UP:
        # The two rows share a stored order, so they are currently
        # separated only by the internal-id tie-break and swapping equal
        # values would change nothing. Nudge exactly one row instead.
        neighbour.display_order = target.display_order + 1
        changed = [neighbour]
    else:
        target.display_order = neighbour.display_order + 1
        changed = [target]

    for row in changed:
        row.version = row.version + 1
        row.updated_at = now_utc
    quiz.version = quiz.version + 1
    quiz.updated_at = now_utc

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        _fresh_listening_authorization(
            teacher_id, group_public_id, listening_public_id, question_public_id
        )
        flash(
            "The questions could not be reordered. Someone may have just changed this "
            "listening activity. Please reload and try again.",
            "danger",
        )
        return redirect(
            _detail_page_url(group_public_id, listening_public_id, return_page)
        )

    flash("Question order updated.", "success")
    return redirect(_detail_page_url(group_public_id, listening_public_id, return_page))


@teacher_bp.post(
    "/groups/<group_public_id>/listening/<listening_public_id>"
    "/questions/<question_public_id>/move-up"
)
@roles_required(UserRole.TEACHER.value)
def listening_question_move_up(group_public_id, listening_public_id, question_public_id):
    return _move_question(
        group_public_id, listening_public_id, question_public_id, MOVE_UP
    )


@teacher_bp.post(
    "/groups/<group_public_id>/listening/<listening_public_id>"
    "/questions/<question_public_id>/move-down"
)
@roles_required(UserRole.TEACHER.value)
def listening_question_move_down(
    group_public_id, listening_public_id, question_public_id
):
    return _move_question(
        group_public_id, listening_public_id, question_public_id, MOVE_DOWN
    )


# ======================================================================
# Publish and withdraw
# ======================================================================


def _publication_transition(group_public_id, listening_public_id, action):
    """The shared body of Publish and Withdraw.

    One function so the two directions cannot drift in their
    authorization, locking, freeze or staleness handling -- only the
    decision they reach and the sentence they flash differ.

    **Publishing a Listening activity additionally requires a valid
    recording**: the extension row must still reference an
    ``uploaded_files`` row whose server-determined category is ``audio``.
    That is checked by ``listening_publication_blockers`` against the
    locked rows, together with every existing M04D readiness rule, and the
    detail page's readiness panel calls the identical function.
    """
    preview_group = _teacher_group_or_404(group_public_id)
    preview_quiz, preview_activity = _listening_or_404(preview_group, listening_public_id)

    if not _group_is_operational(preview_group):
        flash(_PUBLICATION_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    activity_id = preview_activity.id
    term_id, level_id, course_id = _hierarchy_context(preview_group)
    submitted_token = request.form.get("listening_state", "")

    hierarchy, group, teacher, assignment, quiz, activity, _ = _lock_listening_chain(
        group_public_id, term_id, level_id, course_id, teacher_id, quiz_id, activity_id
    )
    if _authz_broken(group, teacher, assignment):
        db.session.rollback()
        abort(404)
    if _listening_ownership_broken(group, quiz, activity, listening_public_id):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(
        hierarchy, group, term_id, level_id, course_id,
        wording=_PUBLICATION_BLOCK_WORDING,
    )
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    if _token_is_stale(
        submitted_token, "listening-publication", action=action,
        teacher_public_id=teacher_public_id, group_public_id=group_public_id,
        listening_public_id=listening_public_id,
        quiz_version=quiz.version, quiz_status=quiz.status,
    ):
        db.session.rollback()
        flash(_STALE_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, listening_public_id))

    now_utc = _write_moment()

    if action == _PUBLISH_ACTION:
        if quiz.status == _PUBLISHED:
            db.session.rollback()
            flash("This listening activity is already published.", "info")
            return redirect(_detail_url(group_public_id, listening_public_id))
        blockers = listening_publication_blockers(quiz, activity)
        if blockers:
            db.session.rollback()
            for sentence in blockers:
                flash(sentence, "danger")
            return redirect(_detail_url(group_public_id, listening_public_id))
        quiz.status = _PUBLISHED
        quiz.published_at = now_utc
    else:
        if quiz.status != _PUBLISHED:
            db.session.rollback()
            flash("This listening activity is already a draft.", "info")
            return redirect(_detail_url(group_public_id, listening_public_id))
        # Withdrawal is permitted only while nobody has started. Once an
        # attempt exists the activity is frozen permanently.
        if quiz_has_attempt_history(quiz.id):
            db.session.rollback()
            flash(_FROZEN_BY_ATTEMPTS_MESSAGE, "danger")
            return redirect(_detail_url(group_public_id, listening_public_id))
        quiz.status = _DRAFT_STATUS
        quiz.published_at = None

    quiz.version = quiz.version + 1
    quiz.updated_at = now_utc
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        _fresh_listening_authorization(teacher_id, group_public_id, listening_public_id)
        flash(
            "This listening activity could not be updated. Someone may have just changed "
            "it. Please reload and try again.",
            "danger",
        )
        return redirect(_detail_url(group_public_id, listening_public_id))

    flash(
        "Listening activity published. Enrolled students can open it once its opening time "
        "arrives."
        if action == _PUBLISH_ACTION
        else "Listening activity withdrawn. It is a draft again and students cannot see it.",
        "success",
    )
    return redirect(_detail_url(group_public_id, listening_public_id))


@teacher_bp.post("/groups/<group_public_id>/listening/<listening_public_id>/publish")
@roles_required(UserRole.TEACHER.value)
def listening_publish(group_public_id, listening_public_id):
    return _publication_transition(
        group_public_id, listening_public_id, _PUBLISH_ACTION
    )


@teacher_bp.post("/groups/<group_public_id>/listening/<listening_public_id>/unpublish")
@roles_required(UserRole.TEACHER.value)
def listening_unpublish(group_public_id, listening_public_id):
    return _publication_transition(
        group_public_id, listening_public_id, _UNPUBLISH_ACTION
    )


# ======================================================================
# Attempt review -- read only
# ======================================================================


@teacher_bp.get(
    "/groups/<group_public_id>/listening/<listening_public_id>/attempts"
)
@roles_required(UserRole.TEACHER.value)
def listening_attempts(group_public_id, listening_public_id):
    """One bounded page of this activity's attempts, newest first.

    Read-only: a Teacher can see what Students did and cannot change any
    of it. There is no score override, no manual grade and no delete.
    Every row is clearly labelled **In progress**, **Submitted** or
    **Time expired** through the shared ``ATTEMPT_STATUS_LABELS``, so the
    Student and Teacher pages can never describe one attempt differently.

    **Expiry is settled here before anything is shown**, through the same
    ``settle_due_attempts`` the Quiz surface uses, so a Teacher never
    reads a row that claims to be running when its deadline is behind it.
    """
    group = _teacher_group_or_404(group_public_id)
    quiz, _activity = _listening_or_404(group, listening_public_id)
    quiz_id = quiz.id
    tz_name = _tz_name()
    page = normalize_page(request.args.get("page"))

    # `settle_due_attempts` performs its own deliberate reset, so every
    # ORM object read before it is expired and the display copies are
    # re-read afterwards.
    settle_due_attempts(group_public_id, quiz_id, _write_moment())

    group = _teacher_group_or_404(group_public_id)
    quiz, activity = _listening_or_404(group, listening_public_id)

    rows, has_next = teacher_attempts_page(quiz.id, page)
    if not rows and page > 1:
        page = 1
        rows, has_next = teacher_attempts_page(quiz.id, page)

    return _private_no_store(
        "teacher/listening/attempts.html",
        group=group,
        activity=build_listening_detail(quiz, activity, tz_name),
        attempts=_build_attempt_summaries(rows, tz_name),
        tz_name=tz_name,
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=ATTEMPT_PAGE_SIZE,
    )


@teacher_bp.get(
    "/groups/<group_public_id>/listening/<listening_public_id>"
    "/attempts/<attempt_public_id>"
)
@roles_required(UserRole.TEACHER.value)
def listening_attempt_detail(
    group_public_id, listening_public_id, attempt_public_id
):
    """One attempt in full: the Student's saved selections **and** the
    authored answer key, side by side.

    All three public ids must name the same nested chain. A Teacher is
    authorized to see the key; a Student never is, which is why this page
    uses ``attempt_review_rows`` -- a separate builder from the Student
    result one rather than a flag on a shared function.

    Four bounded statements build the whole page: never one per question
    or per answer.
    """
    group = _teacher_group_or_404(group_public_id)
    quiz, _activity = _listening_or_404(group, listening_public_id)
    quiz_id = quiz.id
    tz_name = _tz_name()

    preview_attempt = teacher_attempt(quiz_id, attempt_public_id)
    if preview_attempt is None:
        abort(404)
    settle_due_attempts(
        group_public_id, quiz_id, _write_moment(), attempt_ids=[preview_attempt.id]
    )

    group = _teacher_group_or_404(group_public_id)
    quiz, activity = _listening_or_404(group, listening_public_id)
    attempt = teacher_attempt(quiz.id, attempt_public_id)
    if attempt is None:
        abort(404)
    student = db.session.query(User.full_name, User.role).filter(
        User.id == attempt.student_id
    ).first()
    if student is None or student.role != UserRole.STUDENT.value:
        # A foreign key proves the row exists, never its role. A
        # role-inconsistent attempt fails closed rather than being
        # presented as Student work.
        abort(404)

    return _private_no_store(
        "teacher/listening/attempt_detail.html",
        group=group,
        activity=build_listening_detail(quiz, activity, tz_name),
        attempt={
            "public_id": attempt.public_id,
            "student_name": student.full_name,
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
        },
        review=attempt_review_rows(quiz.id, attempt.id),
        tz_name=tz_name,
    )


# ======================================================================
# Authorized audio serving + audit
# ======================================================================


def _serve_activity_audio(group_public_id, listening_public_id, force_attachment):
    """Re-authorize, re-validate, then serve.

    Every audio request -- Teacher or Student, inline or download --
    proves the **complete** nested chain again from current state rather
    than trusting that a page once rendered a link: an active assigned
    Teacher, this Group, this activity under this Group, and an
    ``uploaded_files`` row that still exists and whose server-determined
    category is still ``audio``. Any break yields the identical
    non-disclosing 404.

    The bytes themselves go through the shared M12 serving core, which
    resolves a containment-checked path from the random ``storage_key``
    (so a traversal or a direct storage path can never be requested),
    persists the ``inline`` / ``download`` ``FileAccessLog`` entry
    **before** any byte is sent and refuses to serve at all if that audit
    row cannot be committed, sends the stored canonical content type with
    ``X-Content-Type-Options: nosniff``, and sets ``Cache-Control:
    private, no-store, max-age=0``.

    Nothing in the response discloses the ``storage_key``, the resolved
    filesystem path, the SHA-256 digest, the uploader's identity, any
    internal id, the transcript or the vocabulary notes.
    """
    group = _teacher_group_or_404(group_public_id)
    _quiz, activity = _listening_or_404(group, listening_public_id)
    uploaded_file = audio_upload_for_activity(activity)
    if uploaded_file is None:
        abort(404)
    return serve_uploaded_file(uploaded_file, current_user.id, force_attachment)


@teacher_bp.get("/groups/<group_public_id>/listening/<listening_public_id>/audio")
@roles_required(UserRole.TEACHER.value)
def listening_audio(group_public_id, listening_public_id):
    return _serve_activity_audio(
        group_public_id, listening_public_id, force_attachment=False
    )


@teacher_bp.get(
    "/groups/<group_public_id>/listening/<listening_public_id>/audio/download"
)
@roles_required(UserRole.TEACHER.value)
def listening_audio_download(group_public_id, listening_public_id):
    return _serve_activity_audio(
        group_public_id, listening_public_id, force_attachment=True
    )
