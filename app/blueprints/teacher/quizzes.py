"""Teacher management of Group-owned quiz **drafts** and their
multiple-choice questions (Phase 4 / M04A and M04B).

Group- and Quiz-centered routes only:

    GET       /teacher/groups/<gid>/quizzes
    GET|POST  /teacher/groups/<gid>/quizzes/new
    GET       /teacher/groups/<gid>/quizzes/<qid>
    GET|POST  /teacher/groups/<gid>/quizzes/<qid>/edit
    GET|POST  /teacher/groups/<gid>/quizzes/<qid>/questions/new
    GET|POST  /teacher/groups/<gid>/quizzes/<qid>/questions/<xid>/edit
    POST      /teacher/groups/<gid>/quizzes/<qid>/questions/<xid>/move-up
    POST      /teacher/groups/<gid>/quizzes/<qid>/questions/<xid>/move-down

There is deliberately no flat ``/teacher/quizzes`` collection, no delete
or archive action for a Quiz or a Question, no publication route, and no
Student, Administrator or Researcher quiz surface of any kind. Every
object is addressed by ``public_id``; no internal numeric id ever appears
in a URL, a form value, or the rendered HTML.

**Everything here is a draft.** A Quiz cannot be published, attempted,
timed, scored or released, because none of that exists -- not as a
disabled control, not as a status value, not as a nullable column. Every
page says "Draft" and says plainly that Students cannot see it. A draft
with no questions is a legitimate state and is never described as ready,
complete or graded. Publication, Student attempts, timers, grading,
answer release and results are all deferred.

Since Phase 4 / M04B a draft owns **ordered multiple-choice questions**
with two approved answer modes (exactly one correct active option, or at
least two). That supersedes M04A's statement that no question or option
route exists; everything else above is unchanged. ``is_correct`` is the
Teacher's authored answer key for a draft and carries no scoring meaning.

**Authorization.** ``roles_required(TEACHER)`` handles anonymous (login
redirect) and non-Teacher (403). Object authorization is then server-side
and reuses the exact helpers every other nested Teacher route uses:
``_teacher_group_or_404`` proves an **active**
``GroupTeacherAssignment`` to the Group in the URL, and every nested Quiz
lookup is constrained with ``Quiz.group_id == group.id``. A missing
Group, a missing Quiz, a Quiz ``public_id`` belonging to another Group, a
numeric id in place of a public id, an unassigned Teacher, and a removed
assignment all return the same non-disclosing **404** -- never a 403, and
never a hint that the object exists. Multiple active assigned Teachers
are equal collaborators: there is no creator-owner column and no
per-Teacher restriction anywhere.

**Reading is historical; writing is not.** The list and the detail page
stay available to an actively assigned Teacher even when the Group or an
academic ancestor is archived, so a draft can always be read back.
Creating and editing additionally require an operational Group -- an
active AcademicTerm, Level, Course and Group -- re-checked against the
*locked* rows. Archiving neither deletes nor rewrites a draft, and there
is no delete or archive-Quiz action to reach for instead.

**A path that has rolled back needs its own evidence.**
``roles_required`` runs once, before the view, and a cached
``current_user`` is not a current read. Once locks are released the
acting Teacher's account or assignment may have changed inside exactly
that window, and ``_teacher_group_or_404`` alone would not notice: it
proves an active assignment but never re-reads the actor's own ``role``
and ``status``. Every post-rollback path here therefore goes through
:func:`_fresh_quiz_authorization`, which re-proves the actor from current
state using a **scalar id captured before the reset** -- the edit render,
the ordinary-validation re-render, and the ``IntegrityError`` recovery
alike -- before any authored content is rendered or any token is minted.

**Concurrency.** Every mutation follows the established Teacher authoring
lock order

    AcademicTerm -> Level -> Course -> Group -> acting Teacher User ->
    GroupTeacherAssignment -> Quiz row (when it already exists)

via ``lock_academic_hierarchy`` (which owns the single deliberate reset)
then the Group / User / assignment / Quiz ``SELECT ... FOR UPDATE`` in
the same open transaction, with no second reset. The Group lock is what
serializes same-Group quiz creation against an Administrator Group
retarget, which takes the same Group lock -- so a new draft can never slip
past the identity freeze. All authoritative conditions are re-checked
post-lock and **no model field is assigned until every one of them has
passed**. A signed token bound to the *version* -- not to a timestamp --
protects the edit form against a time-separated co-teacher overwrite.
``IntegrityError`` is caught, rolled back, re-authorized from scratch, and
reported generically with no SQL or driver text.
"""

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.assignments import _private_no_store, _tz_name
from app.blueprints.teacher.quiz_forms import (
    QuizForm,
    QuizQuestionForm,
    QuizSettingsForm,
    submitted_option_fields,
)
from app.blueprints.teacher.units import (
    _archived_chain_labels,
    _authz_broken,
    _group_is_operational,
    _join_labels,
    _teacher_group_or_404,
)
from app.extensions import db
from app.models import (
    MAX_ACTIVE_OPTIONS,
    MAX_QUIZ_QUESTIONS,
    MIN_ACTIVE_OPTIONS,
    AcademicStatus,
    Course,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    QuestionAnswerMode,
    QuestionOption,
    Quiz,
    QuizQuestion,
    QuizStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.quiz_queries import (
    ATTEMPT_PAGE_SIZE,
    MOVE_DIRECTIONS,
    MOVE_DOWN,
    MOVE_UP,
    PAGE_SIZE,
    QUESTION_PAGE_SIZE,
    active_option_counts,
    active_option_ids,
    active_option_set_is_invalid,
    active_options_ordered,
    build_question_editor,
    build_question_summaries,
    build_quiz_detail,
    build_teacher_list_view,
    duplicate_title_exists,
    neighbour_question,
    next_question_display_order,
    normalize_page,
    normalize_question_page,
    attempt_review_rows,
    teacher_attempt,
    teacher_attempts_page,
    teacher_question,
    teacher_questions_page,
    teacher_quiz,
    teacher_quizzes_page,
)
from app.services.quiz_attempts import (
    ATTEMPT_STATUS_LABELS,
    STATE_LABELS,
    availability_state,
    percentage,
    publication_blockers,
    quiz_has_attempt_history,
)
from app.services.quiz_transactions import settle_due_attempts
from app.services.schedule_occurrences import to_app_local, utc_reference_now

_ACTIVE = AcademicStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_TEACHER = UserRole.TEACHER.value
_USER_ACTIVE = UserStatus.ACTIVE.value

#: The one Teacher-facing sentence for a stale edit form. Written to send
#: the Teacher back to the current values rather than to encourage a retry
#: of what they typed.
_STALE_MESSAGE = (
    "This quiz was changed by someone else since this form was opened. Your changes were not "
    "saved. Please reload, read the current draft, and make your edit against it."
)

#: The one sentence for a mutation attempted under an archived chain,
#: used by the early check so it can never explain the rule differently
#: from the authoritative post-lock message built by
#: :func:`_operational_block`.
_NOT_OPERATIONAL_MESSAGE = (
    "Quizzes can only be created or edited while the group and its academic term, course, and "
    "level are all active. Existing drafts stay readable."
)

#: The two clauses that differ between the Quiz and the Question versions of
#: the archived-chain sentence :func:`_operational_block` builds -- the
#: opening rule clause and the closing "what stays readable" clause.
#:
#: Everything else about that check is shared and identical for both,
#: including the **distinct structural-change message** it returns when the
#: locked hierarchy no longer matches the Group. That message is about a
#: concurrent Group retarget rather than an archived ancestor, so it is
#: deliberately NOT parameterized: both surfaces must report it the same way.
#:
#: This pair is the default, so M04A's Quiz create/edit wording and behaviour
#: are unchanged by the M04B addition.
_QUIZ_BLOCK_WORDING = (
    "Quizzes can only be created or edited",
    "Existing drafts stay readable.",
)


# ``_private_no_store`` and ``_tz_name`` are imported from
# ``app.blueprints.teacher.assignments`` rather than re-implemented, exactly
# as ``feedback.py`` already imports them, so the Teacher pages' header set
# and timezone lookup cannot drift between surfaces.
#
# ``private, no-store`` matters here because an unpublished draft is one
# Group's Teachers' working material: a shared or reused cache entry could
# serve it to somebody whose assignment has since been removed. ``Vary:
# Cookie`` stops a cache handing one session's page to another. EVERY
# content-bearing response in this module carries both -- including a form
# re-rendered with validation errors, which carries the draft's title and
# instructions too.


def _write_moment():
    """The **authoritative** naive-UTC moment for one quiz write,
    truncated to whole seconds.

    ``created_at`` / ``updated_at`` are plain ``DateTime`` columns, which
    on MySQL are ``DATETIME`` with fractional precision **0**; MySQL
    *rounds* an excess fraction rather than truncating it, so a value
    carrying microseconds would be stored as a different instant from the
    one the request used. Truncating here makes the two the same on every
    backend -- the same reasoning M02's ``submitted_at`` and M03's
    feedback timestamps already apply.

    Read only **after** every lock that could have blocked, so a request
    that waited behind a competing co-teacher records the moment it
    actually wrote, not the moment it arrived.

    Timestamps are **not** the staleness signal -- ``version`` is. Two
    edits inside one whole second are still distinguishable.
    """
    return utc_reference_now().replace(microsecond=0)


# ======================================================================
# URLs and nested lookup
# ======================================================================


def _quizzes_url(group_public_id):
    return url_for("teacher.group_quizzes", group_public_id=group_public_id)


def _quiz_detail_url(group_public_id, quiz_public_id):
    return url_for(
        "teacher.quiz_detail",
        group_public_id=group_public_id,
        quiz_public_id=quiz_public_id,
    )


def _quiz_edit_url(group_public_id, quiz_public_id):
    return url_for(
        "teacher.quiz_edit",
        group_public_id=group_public_id,
        quiz_public_id=quiz_public_id,
    )


def _redirect_quizzes(group_public_id):
    return redirect(_quizzes_url(group_public_id))


def _quiz_for_group_or_404(group, quiz_public_id):
    """A Quiz by its own public_id, constrained to `group`. A Quiz
    public_id valid only for another Group -- or an internal numeric id
    submitted in its place -- finds no row and 404s here."""
    row = teacher_quiz(group.id, quiz_public_id)
    if row is None:
        abort(404)
    return row


# ======================================================================
# Signed quiz-edit state token (Phase 4 / M04A)
# ======================================================================

#: A dedicated M04 salt. A token minted for any other purpose -- the M01
#: assignment snapshot, the M03 feedback state -- fails signature
#: verification here even though all of them are signed with the same
#: application SECRET_KEY.
_QUIZ_EDIT_STATE_SALT = "teacher.quiz-edit-state.phase4-m04.v1"

#: The token's own purpose marker, checked exactly. It is what stops a
#: future M04 token of a different shape (a question-edit token, say) from
#: ever being replayed against this route, even under the same salt.
_QUIZ_EDIT_PURPOSE = "quiz-edit"

_QUIZ_EDIT_STATE_FIELDS = (
    "purpose",
    "teacher_public_id",
    "group_public_id",
    "quiz_public_id",
    "version",
)


def _edit_state_serializer():
    return URLSafeSerializer(
        current_app.config["SECRET_KEY"], salt=_QUIZ_EDIT_STATE_SALT
    )


def _edit_state_payload(teacher_public_id, group_public_id, quiz_public_id, version):
    """The exact state a Teacher's open edit form was written against.

    Row locks alone cannot protect this form: a co-teacher may have
    rewritten the draft minutes after this form was rendered, and the lock
    the POST takes would happily overwrite their wording with an edit of
    an older one. Binding the save to the row **and the version** the
    Teacher actually read is what closes that.

    ``updated_at`` is deliberately **not** bound: whole-second timestamps
    cannot separate two edits inside one second, and ``version`` can.
    ``title`` and ``instructions`` are not bound either -- instructions can
    be 10,000 characters, the token is readable, and the version already
    identifies the exact row state.

    Only **public** identifiers appear, and no authored text. A signed
    token is authenticated, not encrypted: anyone holding it can read its
    payload, so no internal database id and no quiz content is ever placed
    in it.
    """
    return {
        "purpose": _QUIZ_EDIT_PURPOSE,
        "teacher_public_id": teacher_public_id,
        "group_public_id": group_public_id,
        "quiz_public_id": quiz_public_id,
        "version": version,
    }


def _make_edit_state_token(teacher_public_id, group_public_id, quiz_public_id, version):
    """A token for the state a freshly loaded Quiz row describes.

    Only ever called with a freshly read persisted version, never with
    attempted form values: a fresh token may only pair with fresh state.
    """
    return _edit_state_serializer().dumps(
        _edit_state_payload(
            teacher_public_id, group_public_id, quiz_public_id, version
        )
    )


def _load_edit_state(token):
    """The token's payload, or ``None`` for a missing, malformed,
    invalidly signed, wrong-purpose or wrong-shaped one.

    The shape check is exact and typed, not merely "is a dict": the key
    set must match exactly, the purpose must be the M04A edit purpose, the
    three identifiers must be strings, and ``version`` must be a positive
    ``int``. ``bool`` is excluded explicitly -- it is a subclass of ``int``
    in Python, and ``True`` must not be accepted as version 1.

    Every ``None`` returned here is treated exactly like an outdated
    token: rejected, never trusted, and never silently upgraded into a
    claim about the row.
    """
    if not token:
        return None
    try:
        payload = _edit_state_serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_QUIZ_EDIT_STATE_FIELDS):
        return None
    if payload["purpose"] != _QUIZ_EDIT_PURPOSE:
        return None
    for field in ("teacher_public_id", "group_public_id", "quiz_public_id"):
        if not isinstance(payload[field], str):
            return None
    version = payload["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return None
    return payload


def _edit_state_is_stale(
    token, teacher_public_id, group_public_id, quiz_public_id, current_quiz
):
    """True when `token` does not exactly describe `current_quiz` as read
    by **this** Teacher under **this** Group.

    A cross-Teacher, cross-Group or cross-Quiz token fails on the three
    identifier fields, so one co-teacher's token cannot be replayed by
    another and a token for a Quiz in a different Group is worthless here.
    A token naming an older version fails, which is what makes an
    A -> B -> A round trip and two edits inside the same second
    detectable, and what makes replaying a successful save a no-write
    rejection rather than a second increment.

    `current_quiz` must be the **locked** row on the write path: that is
    the authoritative comparison, and the pre-lock one is only a courtesy.
    """
    payload = _load_edit_state(token)
    if payload is None:
        return True
    if payload["teacher_public_id"] != teacher_public_id:
        return True
    if payload["group_public_id"] != group_public_id:
        return True
    if payload["quiz_public_id"] != quiz_public_id:
        return True
    return payload["version"] != current_quiz.version


def _reject_stale(group_public_id, quiz_public_id):
    """Post/Redirect/Get rejection for a missing, malformed, wrong-shaped,
    wrong-purpose, invalidly signed, cross-object, or genuinely outdated
    token.

    Discards every submitted value and reloads the current persisted ones
    through a fresh GET -- it never pairs a freshly generated token with
    the attempted values, which is precisely the bypass this rejection
    exists to close (the same reasoning as the M01 assignment snapshot and
    the M03 feedback state token).
    """
    db.session.rollback()
    flash(_STALE_MESSAGE, "danger")
    return redirect(_quiz_edit_url(group_public_id, quiz_public_id))


# ======================================================================
# Locking + post-lock re-checks
# ======================================================================


def _lock_quiz_chain(
    group_public_id, term_id, level_id, course_id, teacher_id, quiz_ids=()
):
    """Acquire the established Teacher authoring lock order in one open
    transaction:

        AcademicTerm -> Level -> Course  (via lock_academic_hierarchy,
        which owns the single deliberate reset)
        -> Group -> acting Teacher User -> GroupTeacherAssignment
        -> Quiz rows (ascending internal id)

    This is the exact prefix M01's ``_lock_assignment_chain`` and M10's
    ``_lock_unit_chain`` already use, with the target Quiz taking the
    place of the target Assignment / Unit. No existing route's contract is
    redesigned here, and the project-wide "User rows in ascending internal
    id" rule is preserved (there is only one User row in this chain: the
    acting Teacher).

    Because a Unit write, an Assignment write, a feedback write, every
    membership mutation and the Administrator Group retarget all lock the
    **same** Group row, a quiz write serializes against all of them rather
    than racing -- which is what stops a new draft from slipping past the
    Group identity freeze.

    Returns ``(hierarchy, group, teacher, teacher_assignment, {id: quiz})``.
    Any of group / teacher / teacher_assignment / a quiz may be ``None`` --
    the caller must treat that as a business/authorization rejection, roll
    back, and 404 or redirect; it must never "keep going".

    SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
    REPEATABLE READ snapshot isolation, so tests can assert the
    *requested* reset and lock order and nothing about real InnoDB
    blocking.
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    teacher = User.query.filter_by(id=teacher_id).with_for_update().first()
    teacher_assignment = None
    if group is not None:
        teacher_assignment = (
            GroupTeacherAssignment.query.filter_by(
                group_id=group.id, teacher_id=teacher_id
            )
            .with_for_update()
            .first()
        )
    quizzes = {}
    for quiz_id in sorted({q for q in quiz_ids if q is not None}):
        quizzes[quiz_id] = Quiz.query.filter_by(id=quiz_id).with_for_update().first()
    return hierarchy, group, teacher, teacher_assignment, quizzes


def _operational_block(hierarchy, group, term_id, level_id, course_id, wording=None):
    """``None`` if the locked Group and its locked AcademicTerm / Level /
    Course all exist and are active (so a create or edit may proceed),
    else a Teacher-facing message.

    Same shape as the M01 Assignment and M03 feedback checks: what is
    blocked is *writing*, never reading -- work already saved stays visible
    to its Group's Teachers under an archived chain.

    Two different messages come out of here, and only one of them is
    surface-specific:

    - **A structural change** -- the locked Course / AcademicTerm no longer
      match the locked Group, i.e. an Administrator retargeted the Group
      inside this window. That is the same event whatever the Teacher was
      writing, so its sentence is shared verbatim and is deliberately
      **not** parameterized.
    - **An archived ancestor** -- which has to name the rule the Teacher
      just hit. Since Phase 4 / M04B two surfaces share this check with
      genuinely different rules ("quizzes can be created or edited" versus
      "questions can be added, edited, or reordered"), `wording` supplies
      that surface's opening and closing clauses. It defaults to
      :data:`_QUIZ_BLOCK_WORDING`, so M04A's Quiz paths are unchanged.

    The archived ancestors themselves are named identically in both cases,
    so a Teacher is always told exactly *what* is archived.
    """
    term = hierarchy.term(term_id)
    level = hierarchy.level(level_id)
    course = hierarchy.course(course_id)
    if (
        course is None
        or group.course_id != course.id
        or group.academic_term_id != term_id
        or course.level_id != level_id
    ):
        return "This group changed while you were working. Reload the page and try again."
    labels = [
        label
        for label, row in (
            ("academic term", term),
            ("level", level),
            ("course", course),
            ("group", group),
        )
        if row is None or row.status != _ACTIVE
    ]
    if labels:
        lead, tail = wording or _QUIZ_BLOCK_WORDING
        verb = "is" if len(labels) == 1 else "are"
        return (
            f"{lead} while the group and its academic term, "
            f"course, and level are all active. The {_join_labels(labels)} {verb} archived. "
            f"{tail}"
        )
    return None


def _nested_ownership_broken(group, quiz, quiz_public_id):
    """True when the **locked** Quiz row is no longer the one the URL
    claims.

    Re-proved against the locked current read rather than trusted from the
    pre-lock preview: the row must still exist, must still belong to this
    exact Group, and must still carry this exact ``public_id``. The caller
    turns a ``True`` into the same non-disclosing 404 the read routes
    produce, with no partial write of any kind.
    """
    if quiz is None:
        return True
    return quiz.group_id != group.id or quiz.public_id != quiz_public_id


# ======================================================================
# Fresh authorization -- the only evidence a post-rollback path may use
# ======================================================================


def _fresh_quiz_authorization(actor_id, group_public_id, quiz_public_id=None):
    """Prove from **current database state** that `actor_id` may read this
    Group -- and, when `quiz_public_id` is given, this Quiz -- *right
    now*, and return ``(group, quiz)``, or abort with the established
    non-disclosing 404. `quiz` is ``None`` when no Quiz was requested.

    **Why this exists.** ``roles_required`` runs once, before the view. A
    path that rolls back has released its locks, so neither that decorator
    nor a cached ``current_user`` object is current evidence any more.
    ``_teacher_group_or_404`` alone is not enough either -- it proves an
    active ``GroupTeacherAssignment`` but never re-reads the actor's own
    ``role`` and ``status``.

    The actor is identified by a **scalar id captured before the reset**,
    never by ``current_user``, so nothing here depends on a lazily
    reloaded proxy or on which session object happens to be cached.

    What is proved, in order, all as current reads:

    1. the acting User row exists;
    2. its ``role`` is ``teacher``;
    3. its ``status`` is ``active``;
    4. an **active** ``GroupTeacherAssignment`` links it to the exact
       Group named in the URL;
    5. the Quiz belongs to that exact Group.

    What is deliberately **not** proved, because M04A reading is
    historical: the AcademicTerm / Level / Course / Group need not be
    active. Callers that are about to render an *editable* form check that
    separately and downgrade to a redirect, so an archived chain never
    yields a form that cannot save.

    Scoped to M04A rather than folded into ``_teacher_group_or_404``,
    which every other Teacher route shares: widening that helper would
    change behaviour well outside this Part.
    """
    group = fresh_group_authorization(actor_id, group_public_id)
    if quiz_public_id is None:
        return group, None

    quiz = teacher_quiz(group.id, quiz_public_id)
    if quiz is None:
        abort(404)
    return group, quiz


def fresh_group_authorization(actor_id, group_public_id):
    """Steps 1-4 of :func:`_fresh_quiz_authorization` on their own: prove
    from **current database state** that `actor_id` is an active Teacher
    actively assigned to this Group, and return the eagerly loaded Group,
    or abort with the established non-disclosing 404.

    Split out in Phase 4 / M05 so the Listening surface can layer its own
    nested lookup on the identical actor proof instead of re-implementing
    it. ``_fresh_quiz_authorization`` above is unchanged in behaviour:
    it is this function plus the ordinary-Quiz lookup.
    """
    if actor_id is None:
        abort(404)

    # The actor's own row, read fresh and projected to the two columns
    # that decide eligibility -- never `current_user`, and never a whole
    # entity the identity map could answer from a pre-rollback read.
    actor = db.session.query(User.role, User.status).filter(User.id == actor_id).first()
    if actor is None or actor.role != _TEACHER or actor.status != _USER_ACTIVE:
        abort(404)

    group = (
        Group.query.options(
            joinedload(Group.course).joinedload(Course.level),
            joinedload(Group.academic_term),
        )
        .filter_by(public_id=group_public_id)
        .first()
    )
    if group is None:
        abort(404)

    still_assigned = db.session.query(
        GroupTeacherAssignment.query.filter_by(
            group_id=group.id, teacher_id=actor_id, status=_ASSIGNMENT_ACTIVE
        ).exists()
    ).scalar()
    if not still_assigned:
        abort(404)
    return group


# ======================================================================
# List -- bounded, paginated draft history
# ======================================================================


@teacher_bp.get("/groups/<group_public_id>/quizzes")
@roles_required(UserRole.TEACHER.value)
def group_quizzes(group_public_id):
    """One bounded page of a Group's quiz drafts, newest first.

    Fixed page size, SQL ordering (``created_at DESC, id DESC``), SQL
    ``LIMIT PAGE_SIZE + 1`` for the has-next flag, and the same page
    normalization and past-the-end fallback the other Teacher lists use.
    The rows carry no ``instructions``, so the page's cost does not grow
    with how much has been written into each draft.

    Stays available under an archived Group or ancestor: an eligible
    assigned Teacher can always read back what was authored.
    """
    group = _teacher_group_or_404(group_public_id)
    operational = _group_is_operational(group)
    tz_name = _tz_name()
    page = normalize_page(request.args.get("page"))

    rows, has_next = teacher_quizzes_page(group.id, page)
    if not rows and page > 1:
        # A page past the end (a stale bookmark) shows page 1 rather than
        # a confusing empty page with a "Previous" button.
        page = 1
        rows, has_next = teacher_quizzes_page(group.id, page)

    return _private_no_store(
        "teacher/quizzes/list.html",
        group=group,
        quizzes=build_teacher_list_view(rows, tz_name),
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


@teacher_bp.get("/groups/<group_public_id>/quizzes/<quiz_public_id>")
@roles_required(UserRole.TEACHER.value)
def quiz_detail(group_public_id, quiz_public_id):
    """One quiz draft and one bounded page of its questions, or a
    non-disclosing 404.

    Both public ids must name the same nested chain: the Group must be one
    this Teacher is actively assigned to, and the Quiz must belong to that
    Group. Any other combination -- including a Quiz public id that is
    valid under a *different* Group -- produces no row and the same 404.

    **This route still writes nothing.** Since Phase 4 / M04B it is the
    question-management surface, so it links to the question editor and
    carries Move Up / Move Down forms, but every one of those is a POST to
    its own route with its own CSRF token, its own signed state, and its
    own authoritative post-lock checks. There is no publish, delete,
    archive, duplicate, attempt, score or answer-release control, because
    none exists server-side either.

    **The question list is bounded three ways**: ``LIMIT
    QUESTION_PAGE_SIZE + 1`` rows for a next-page flag with no ``COUNT``,
    a SQL-truncated prompt preview instead of the whole prompt, and
    **one** grouped query for every row's active/correct option counts
    rather than a lookup per row. Nothing iterates ``Quiz.questions`` or
    ``QuizQuestion.options``, so the page's cost does not grow with the
    size of the draft.

    Move tokens are minted only when the chain is operational -- there is
    nothing to authorize a move against otherwise -- and carry public
    identifiers, the two expected versions, the direction and the return
    page, never any authored text.
    """
    group = _teacher_group_or_404(group_public_id)
    row = _quiz_for_group_or_404(group, quiz_public_id)
    tz_name = _tz_name()
    operational = _group_is_operational(group)
    page = normalize_question_page(request.args.get("page"))

    question_rows, has_next = teacher_questions_page(row.id, page)
    if not question_rows and page > 1:
        # A page past the end (a stale bookmark, or the last question on a
        # page having moved) shows page 1 rather than a confusing empty
        # page with a "Previous" button.
        page = 1
        question_rows, has_next = teacher_questions_page(row.id, page)

    counts = active_option_counts([question.id for question in question_rows])
    questions = build_question_summaries(
        question_rows, counts, (page - 1) * QUESTION_PAGE_SIZE + 1
    )
    if operational:
        actor_public_id = current_user.public_id
        for summary, question in zip(questions, question_rows):
            summary["move_up_token"] = _make_question_move_token(
                MOVE_UP, actor_public_id, group_public_id, quiz_public_id,
                question.public_id, row.version, question.version, page,
            )
            summary["move_down_token"] = _make_question_move_token(
                MOVE_DOWN, actor_public_id, group_public_id, quiz_public_id,
                question.public_id, row.version, question.version, page,
            )

    # Phase 4 / M04D publication panel. `publication_blockers` is the
    # SAME function the publish route runs against the locked rows, so
    # the readiness a Teacher reads and the rule that decides can never
    # disagree -- this call is read-only and simply renders it early.
    published = row.status == _PUBLISHED
    has_attempts = quiz_has_attempt_history(row.id)
    frozen_message = _authoring_block(row)
    reference_utc = utc_reference_now().replace(microsecond=0)
    state = availability_state(row, reference_utc)
    actor_public_id = current_user.public_id

    return _private_no_store(
        "teacher/quizzes/detail.html",
        group=group,
        quiz=build_quiz_detail(row, tz_name),
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
        published=published,
        has_attempts=has_attempts,
        frozen_message=frozen_message,
        availability={
            "opens_local": (
                to_app_local(tz_name, row.opens_at) if row.opens_at else None
            ),
            "closes_local": (
                to_app_local(tz_name, row.closes_at) if row.closes_at else None
            ),
            "time_limit_minutes": row.time_limit_minutes,
            "attempt_limit": row.attempt_limit,
            "published_local": (
                to_app_local(tz_name, row.published_at) if row.published_at else None
            ),
            "state": state,
            "state_label": STATE_LABELS.get(state),
        },
        readiness=[] if published else publication_blockers(row),
        max_questions=MAX_QUIZ_QUESTIONS,
        publish_token=(
            _make_publication_token(
                _PUBLISH_ACTION, actor_public_id, group_public_id, quiz_public_id,
                row.version, row.status,
            )
            if operational and not published
            else None
        ),
        unpublish_token=(
            _make_publication_token(
                _UNPUBLISH_ACTION, actor_public_id, group_public_id, quiz_public_id,
                row.version, row.status,
            )
            if operational and published and not has_attempts
            else None
        ),
    )


# ======================================================================
# Create -- always a draft
# ======================================================================


def _render_quiz_create_form(form, actor_id, group_public_id, message=None):
    """Re-render the create form.

    Releases any write lock first (harmless on a GET or a plain form
    failure) and then re-proves the **whole** authorization chain --
    including the acting Teacher's own role and account status -- through
    :func:`_fresh_quiz_authorization`, before anything is rendered. The
    rollback is exactly why that is necessary: the locks are gone, and the
    decorator that ran at the start of the request is no longer current
    evidence.

    `actor_id` is a **scalar captured before any reset** in this request,
    passed in rather than read from ``current_user`` here so this function
    cannot accidentally depend on a proxy reloaded after the rollback.

    `message` is flashed **only after** that re-authorization has passed,
    so a request whose access ended in the same window that caused the
    failure 404s silently instead of leaving a message behind for whatever
    page the actor reaches next.
    """
    db.session.rollback()
    group, _ = _fresh_quiz_authorization(actor_id, group_public_id)
    if message is not None:
        flash(message, "danger")
    if not _group_is_operational(group):
        # The chain was archived while this request was in flight. Never
        # hand back a form that cannot save.
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return _redirect_quizzes(group_public_id)
    return _private_no_store(
        "teacher/quizzes/form.html",
        form=form,
        group=group,
        quiz=None,
        edit_state_token=None,
        tz_name=_tz_name(),
    )


@teacher_bp.route("/groups/<group_public_id>/quizzes/new", methods=["GET", "POST"])
@roles_required(UserRole.TEACHER.value)
def quiz_create(group_public_id):
    """Create one quiz draft.

    **Nothing about ownership is taken from the request.** The Group is
    the authorized public identifier in the URL, ``version`` is the
    constant 1, ``public_id`` is server-generated, and both timestamps are
    the request's post-lock authoritative moment -- so a forged
    ``group_id`` / ``version`` / ``public_id`` / ``created_at`` field in
    the POST body has nowhere to land. The form carries only ``title`` and
    ``instructions``.
    """
    preview_group = _teacher_group_or_404(group_public_id)

    if not _group_is_operational(preview_group):
        # Helpful early check: keeps a bookmarked URL from silently
        # rendering a form that can no longer save. Deliberately NOT the
        # enforcement point -- the post-lock check below is.
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return _redirect_quizzes(group_public_id)

    form = QuizForm(group_id=preview_group.id)
    actor_id = current_user.id

    if form.validate_on_submit():
        title = form.normalized_title()
        instructions = form.normalized_instructions()
        # Every scalar the rest of this request needs is captured BEFORE
        # the transaction reset below, so nothing between that reset and
        # the required locks triggers a lazy ORM or `current_user` reload
        # that would establish a fresh read snapshot ahead of the locks.
        term_id = preview_group.academic_term_id
        level_id = preview_group.course.level_id
        course_id = preview_group.course_id

        hierarchy, group, teacher, teacher_assignment, _ = _lock_quiz_chain(
            group_public_id, term_id, level_id, course_id, actor_id
        )
        if _authz_broken(group, teacher, teacher_assignment):
            db.session.rollback()
            abort(404)

        blocked = _operational_block(hierarchy, group, term_id, level_id, course_id)
        if blocked is not None:
            db.session.rollback()
            flash(blocked, "danger")
            return _redirect_quizzes(group_public_id)

        # The authoritative duplicate-title check, taken against the
        # locked Group. The form's identical check ran before the locks
        # and is only a courtesy; the unique constraint below is the final
        # defense behind both.
        if duplicate_title_exists(group.id, title):
            form.title.errors.append(
                "A quiz with this title already exists in this group."
            )
            return _render_quiz_create_form(form, actor_id, group_public_id)

        # The authoritative write moment: read only now, after every lock
        # that could have blocked, and truncated to the whole second the
        # columns can actually hold. Both timestamps get the SAME value on
        # creation.
        now_utc = _write_moment()

        # No field is assigned until every check above has passed, so a
        # rejection never leaves a partial write.
        quiz = Quiz(
            group_id=group.id,
            title=title,
            instructions=instructions,
            version=1,
            created_at=now_utc,
            updated_at=now_utc,
        )
        db.session.add(quiz)
        try:
            db.session.commit()
        except IntegrityError:
            # Roll back FIRST -- everything read before this point is now
            # discarded state and must not be used as evidence of
            # anything, least of all authorization.
            # `_render_quiz_create_form` owns that rollback and the fresh
            # re-authorization that follows it, and flashes the message
            # only once that has passed: whatever raised the error may
            # well have been a concurrent change that ALSO ended this
            # Teacher's access. The message is generic -- no SQL, driver
            # text, parameter or internal id ever reaches the page -- and
            # the failed save is never reported as success.
            return _render_quiz_create_form(
                form, actor_id, group_public_id,
                message=(
                    "This quiz could not be saved. A quiz with this title may already "
                    "exist in the group, or the group may have just changed. Please "
                    "reload and try again."
                ),
            )

        created_title, created_public_id = quiz.title, quiz.public_id
        flash(
            f"Quiz '{created_title}' created as a draft. Students cannot see it.", "success"
        )
        return redirect(_quiz_detail_url(group_public_id, created_public_id))

    return _render_quiz_create_form(form, actor_id, group_public_id)


# ======================================================================
# Edit -- with a signed, version-bound state token
# ======================================================================


def _render_quiz_edit_page(
    actor_id, actor_public_id, group_public_id, quiz_public_id,
    form=None, state_token=None, message=None,
):
    """Render the edit form, redirect, or 404.

    Releases any write lock first (harmless on a GET or a plain form
    failure) and then re-proves the **whole** authorization chain --
    including the acting Teacher's own role and account status, and the
    Quiz's ownership by this Group -- through
    :func:`_fresh_quiz_authorization`, before any authored content is
    rendered or any token is minted.

    `actor_id` / `actor_public_id` are **scalars captured before any
    reset** in this request. They are passed in rather than read from
    ``current_user`` here so this function cannot accidentally depend on a
    proxy reloaded after the rollback.

    `form` and `state_token` are supplied only by the ordinary-validation
    and ``IntegrityError`` paths, which must show the Teacher their
    attempted values again with the **original** token, never a freshly
    minted one -- a fresh token may only ever pair with freshly loaded
    persisted values.

    `message` is flashed **only after** that re-authorization has passed,
    so a request whose access ended in the same window that caused the
    failure 404s silently instead of leaving a message behind for whatever
    page the actor reaches next.
    """
    db.session.rollback()
    group, quiz = _fresh_quiz_authorization(actor_id, group_public_id, quiz_public_id)
    if message is not None:
        flash(message, "danger")
    if not _group_is_operational(group):
        # The chain was archived while this request was in flight. The
        # draft stays readable, but never hand back a form that cannot
        # save.
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    # A courtesy check with the same rule the write path enforces:
    # never hand back a form whose save is already refused.
    frozen = _authoring_block(quiz)
    if frozen is not None:
        flash(frozen, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    tz_name = _tz_name()
    if form is None:
        form = QuizForm(
            formdata=None,
            data={"title": quiz.title, "instructions": quiz.instructions},
            group_id=group.id,
            quiz_id=quiz.id,
        )
    if state_token is None:
        state_token = _make_edit_state_token(
            actor_public_id, group_public_id, quiz_public_id, quiz.version
        )

    return _private_no_store(
        "teacher/quizzes/form.html",
        form=form,
        group=group,
        quiz=build_quiz_detail(quiz, tz_name),
        edit_state_token=state_token,
        tz_name=tz_name,
    )


@teacher_bp.route(
    "/groups/<group_public_id>/quizzes/<quiz_public_id>/edit", methods=["GET", "POST"]
)
@roles_required(UserRole.TEACHER.value)
def quiz_edit(group_public_id, quiz_public_id):
    """Revise one quiz draft's title and instructions.

    **Order of the post-lock checks.** Authorization and nested ownership
    come first, so an unauthorized attempt fails identically whatever the
    draft contains. The operational chain, the signed token, and finally
    ordinary field validation follow. Only then is the no-op compared, the
    duplicate title re-checked, and any field assigned -- so every
    rejection leaves the stored row, its version and its timestamps
    exactly as they were.

    A meaningful change increments ``version`` by exactly one and moves
    ``updated_at``. An authorized, non-stale save of unchanged normalized
    values is a **no-op**: nothing is written, and neither ``version`` nor
    ``updated_at`` moves.
    """
    if request.method == "GET":
        # The actor's identity is captured as plain scalars HERE, before
        # `_render_quiz_edit_page` performs its rollback, and the page then
        # re-proves that actor against current state.
        return _render_quiz_edit_page(
            current_user.id, current_user.public_id, group_public_id, quiz_public_id
        )

    # ------------------------------------------------------------------
    # Pre-lock preview: the same object authorization every nested Teacher
    # route uses. Friendly and fast, but NOT authoritative -- everything
    # it proves is proved again below against locked rows.
    # ------------------------------------------------------------------
    preview_group = _teacher_group_or_404(group_public_id)
    preview_quiz = _quiz_for_group_or_404(preview_group, quiz_public_id)

    if not _group_is_operational(preview_group):
        # Helpful early check: keeps a bookmarked editor URL from silently
        # accepting a write the post-lock check would reject anyway.
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    # Ordinary field validation (empty / whitespace-only / too long /
    # duplicate title) runs here, while the session is still cheap to
    # touch. Its RESULT is applied only after the locks, so a rejection
    # order can never depend on it.
    form = QuizForm(group_id=preview_group.id, quiz_id=preview_quiz.id)
    form_is_valid = form.validate_on_submit()
    title = form.normalized_title()
    instructions = form.normalized_instructions()

    # Every scalar the rest of this request needs is captured BEFORE the
    # transaction reset below, so nothing between that reset and the
    # required locks triggers a lazy ORM or `current_user` reload that
    # would establish a fresh read snapshot ahead of the locks.
    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id
    submitted_token = request.form.get("edit_state", "")

    # ------------------------------------------------------------------
    # The single deliberate reset + the established lock order.
    # ------------------------------------------------------------------
    hierarchy, group, teacher, teacher_assignment, quizzes = _lock_quiz_chain(
        group_public_id, term_id, level_id, course_id, teacher_id, quiz_ids=[quiz_id]
    )
    if _authz_broken(group, teacher, teacher_assignment):
        db.session.rollback()
        abort(404)

    quiz = quizzes.get(quiz_id)
    if _nested_ownership_broken(group, quiz, quiz_public_id):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(hierarchy, group, term_id, level_id, course_id)
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    # THE authoring freeze, against the LOCKED Quiz row. Publishing
    # makes the authored content read-only; the first attempt freezes
    # it permanently. Checked here, not in the template, so a
    # bookmarked or forged request is refused too -- and BEFORE any
    # field is assigned, so a rejection leaves every row untouched.
    frozen = _authoring_block(quiz)
    if frozen is not None:
        db.session.rollback()
        flash(frozen, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    # The token is re-checked against the LOCKED current row -- this is
    # what closes the window between the form's GET and these locks, and
    # what turns a losing co-teacher race into an explicit "reload and
    # review" rejection rather than a silent overwrite.
    if _edit_state_is_stale(
        submitted_token, teacher_public_id, group_public_id, quiz_public_id, quiz
    ):
        return _reject_stale(group_public_id, quiz_public_id)

    if not form_is_valid:
        # Ordinary validation failure with a still-valid context: show the
        # attempted values again with the ORIGINAL token, so the Teacher
        # can fix them without losing what they wrote and without the
        # expected version being silently refreshed underneath them.
        return _render_quiz_edit_page(
            teacher_id, teacher_public_id, group_public_id, quiz_public_id,
            form=form, state_token=submitted_token,
        )

    # An authorized save of identical normalized values is a no-op: not an
    # edit, so no version increment and no new timestamp. It still had to
    # pass every check above.
    if quiz.title == title and quiz.instructions == instructions:
        db.session.rollback()
        flash("This quiz is unchanged, so nothing was saved.", "info")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    # The authoritative duplicate-title check, taken against the locked
    # Group and excluding this row. The form's identical check ran before
    # the locks and is only a courtesy.
    if duplicate_title_exists(group.id, title, exclude_quiz_id=quiz.id):
        form.title.errors.append("A quiz with this title already exists in this group.")
        return _render_quiz_edit_page(
            teacher_id, teacher_public_id, group_public_id, quiz_public_id,
            form=form, state_token=submitted_token,
        )

    # The authoritative write moment: read only now, after every lock that
    # could have blocked.
    now_utc = _write_moment()

    # No field is assigned until every check above has passed, so a
    # rejection never leaves a partial update. `id`, `public_id`,
    # `group_id` and `created_at` are never assigned here: a revision is
    # the same record, not a new one, and a draft never changes Group.
    quiz.title = title
    quiz.instructions = instructions
    quiz.version = quiz.version + 1
    quiz.updated_at = now_utc
    try:
        db.session.commit()
    except IntegrityError:
        # Roll back FIRST, then re-authorize from scratch and re-render.
        # `_render_quiz_edit_page` owns both, using the pre-reset actor
        # scalars, and flashes the message only once that has passed:
        # whatever raised the error may well have been a concurrent change
        # that ALSO ended this Teacher's access. The ORIGINAL token is
        # re-embedded, never a fresh one -- the stored row was not changed
        # by this failed transaction, so that token is still the correct
        # expectation, and if the row does change before the next
        # submission it will correctly be caught as stale then. The message
        # is generic: no SQL, driver text, parameter or internal id reaches
        # the page, and the failed save is never reported as success.
        return _render_quiz_edit_page(
            teacher_id, teacher_public_id, group_public_id, quiz_public_id,
            form=form, state_token=submitted_token,
            message=(
                "This quiz could not be saved. A quiz with this title may already exist "
                "in the group, or someone may have just changed it. Please reload and "
                "try again."
            ),
        )

    flash(f"Quiz '{title}' updated. It is still a draft.", "success")
    return redirect(_quiz_detail_url(group_public_id, quiz_public_id))


# ======================================================================
# Phase 4 / M04B -- ordered multiple-choice questions
# ======================================================================
#
# Four more Quiz-scoped routes, all under the same Group and Quiz public
# identifiers the M04A surface already uses:
#
#     GET|POST  .../quizzes/<quiz_public_id>/questions/new
#     GET|POST  .../quizzes/<quiz_public_id>/questions/<question_public_id>/edit
#     POST      .../quizzes/<quiz_public_id>/questions/<question_public_id>/move-up
#     POST      .../quizzes/<quiz_public_id>/questions/<question_public_id>/move-down
#
# There is still no publication route, no Student route, no attempt, no
# score, no answer release, and no question delete or archive action --
# none of that exists server-side, and no placeholder is left for it. A
# question is Teacher working material inside a draft that no Student can
# reach, and `is_correct` is the authored answer key of that draft and
# nothing more.
#
# **Everything M04A established still holds and is reused rather than
# re-implemented**: the same non-disclosing 404 for every missing or
# foreign object, the same `_teacher_group_or_404` active-assignment
# proof, the same `_lock_quiz_chain` prefix under one deliberate reset,
# the same `_fresh_quiz_authorization` for every post-rollback path, the
# same `private, no-store` + `Vary: Cookie` on every content-bearing
# response including form-error renders, and the same rule that no field
# is assigned until every authoritative check has passed.
#
# **The parent Quiz is the serialization point.** Every question mutation
# locks the Quiz row before it reads anything it will decide on, so
# concurrent creates, edits and moves on one draft serialize on that
# parent instead of racing. That is what makes the application-level
# rules a locked aggregate can express -- 2..8 active options, unique
# active option text, answer cardinality -- authoritative rather than
# hopeful. It is a reasoned property of the documented lock order;
# SQLite demonstrates none of it and no real-MySQL concurrency test has
# been run.


#: The one Teacher-facing sentence for a stale question form.
_QUESTION_STALE_MESSAGE = (
    "This quiz was changed by someone else since this form was opened. Your changes were not "
    "saved. Please reload, read the current questions, and make your change against them."
)

#: The one sentence for a question mutation attempted under an archived
#: chain, so the early check and the authoritative post-lock message
#: cannot explain the same rule differently.
_QUESTION_NOT_OPERATIONAL_MESSAGE = (
    "Questions can only be added, edited, or reordered while the group and its academic term, "
    "course, and level are all active. Existing questions stay readable."
)

#: The same two clauses, handed to :func:`_operational_block` by every
#: question write path so its **authoritative post-lock** rejection states
#: the question rule rather than the Quiz one. Without this the early check
#: above and the post-lock check would explain the same rule differently --
#: which is exactly the defect the M04B review found.
_QUESTION_BLOCK_WORDING = (
    "Questions can only be added, edited, or reordered",
    "Existing questions stay readable.",
)

#: The one sentence for a question whose STORED active option set does not
#: satisfy the 2..8 rule the editor validates against. Refusing is
#: deliberate: silently dropping options or inventing one would destroy or
#: fabricate authored work.
_QUESTION_INVALID_STATE_MESSAGE = (
    "This question's answer options are in a state this editor cannot safely change. Nothing "
    "has been deleted or altered. Please ask an administrator to review this quiz."
)


def _questions_error(form, message):
    """Attach one option-block error and report the form as failed."""
    form.option_errors.append(message)
    return form


# ----------------------------------------------------------------------
# URLs
# ----------------------------------------------------------------------


def _question_create_url(group_public_id, quiz_public_id):
    return url_for(
        "teacher.quiz_question_create",
        group_public_id=group_public_id,
        quiz_public_id=quiz_public_id,
    )


def _question_edit_url(group_public_id, quiz_public_id, question_public_id):
    return url_for(
        "teacher.quiz_question_edit",
        group_public_id=group_public_id,
        quiz_public_id=quiz_public_id,
        question_public_id=question_public_id,
    )


def _quiz_detail_page_url(group_public_id, quiz_public_id, page=1):
    """The Quiz detail page, optionally on a given question page.

    ``page=1`` is rendered without the query argument so the canonical URL
    of a draft stays clean and a bookmark keeps working.
    """
    if page and page > 1:
        return url_for(
            "teacher.quiz_detail",
            group_public_id=group_public_id,
            quiz_public_id=quiz_public_id,
            page=page,
        )
    return _quiz_detail_url(group_public_id, quiz_public_id)


# ======================================================================
# Signed question tokens (Phase 4 / M04B)
# ======================================================================
#
# Three dedicated salts and three exact purpose markers. A token minted
# for any other salt -- the M01 assignment snapshot, the M03 feedback
# state, the M04A quiz edit state -- fails signature verification here
# even though every one of them is signed with the same application
# SECRET_KEY, and a token minted under one of these salts but for another
# M04B purpose fails the purpose check.
#
# Every payload carries **public identifiers only**. A signed token is
# authenticated, not encrypted: anyone holding it can read it, so no
# prompt text, no option text, no answer key, no internal database id and
# no private data is ever placed in one.

_QUESTION_CREATE_SALT = "teacher.quiz-question-create.phase4-m04b.v1"
_QUESTION_EDIT_SALT = "teacher.quiz-question-edit.phase4-m04b.v1"
_QUESTION_MOVE_SALT = "teacher.quiz-question-move.phase4-m04b.v1"

_QUESTION_CREATE_PURPOSE = "quiz-question-create"
_QUESTION_EDIT_PURPOSE = "quiz-question-edit"
_QUESTION_MOVE_PURPOSE = "quiz-question-move"

_QUESTION_CREATE_FIELDS = (
    "purpose",
    "teacher_public_id",
    "group_public_id",
    "quiz_public_id",
    "quiz_version",
)
_QUESTION_EDIT_FIELDS = (
    "purpose",
    "teacher_public_id",
    "group_public_id",
    "quiz_public_id",
    "question_public_id",
    "quiz_version",
    "question_version",
    "option_public_ids",
)
_QUESTION_MOVE_FIELDS = (
    "purpose",
    "direction",
    "teacher_public_id",
    "group_public_id",
    "quiz_public_id",
    "question_public_id",
    "quiz_version",
    "question_version",
    "page",
)


def _serializer(salt):
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=salt)


def _positive_int(value):
    """True for a genuine positive ``int``.

    ``bool`` is excluded explicitly: it is a subclass of ``int`` in
    Python, and ``True`` must never be accepted as version 1.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _all_strings(values):
    return all(isinstance(value, str) for value in values)


# -- create -------------------------------------------------------------


def _make_question_create_token(
    teacher_public_id, group_public_id, quiz_public_id, quiz_version
):
    """A token for the Quiz state a "new question" form was opened
    against.

    Binding the *Quiz* version is what makes a create stale: if a
    co-teacher added, edited or reordered a question while this form was
    open, the draft the Teacher was writing against no longer exists, and
    appending blindly would silently place the new question after work
    they never saw.
    """
    return _serializer(_QUESTION_CREATE_SALT).dumps(
        {
            "purpose": _QUESTION_CREATE_PURPOSE,
            "teacher_public_id": teacher_public_id,
            "group_public_id": group_public_id,
            "quiz_public_id": quiz_public_id,
            "quiz_version": quiz_version,
        }
    )


def _load_question_create_token(token):
    if not token:
        return None
    try:
        payload = _serializer(_QUESTION_CREATE_SALT).loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_QUESTION_CREATE_FIELDS):
        return None
    if payload["purpose"] != _QUESTION_CREATE_PURPOSE:
        return None
    if not _all_strings(
        [payload[field] for field in _QUESTION_CREATE_FIELDS[1:4]]
    ):
        return None
    if not _positive_int(payload["quiz_version"]):
        return None
    return payload


def _create_token_is_stale(
    token, teacher_public_id, group_public_id, quiz_public_id, quiz
):
    payload = _load_question_create_token(token)
    if payload is None:
        return True
    if payload["teacher_public_id"] != teacher_public_id:
        return True
    if payload["group_public_id"] != group_public_id:
        return True
    if payload["quiz_public_id"] != quiz_public_id:
        return True
    return payload["quiz_version"] != quiz.version


# -- edit ---------------------------------------------------------------


def _make_question_edit_token(
    teacher_public_id,
    group_public_id,
    quiz_public_id,
    question_public_id,
    quiz_version,
    question_version,
    option_public_ids,
):
    """A token for the exact question state an edit form was rendered
    against -- including **which** active options it showed, in order.

    The option list is the part row locks alone cannot supply. Two
    co-teachers editing the same question can both hold a valid
    ``question_version``-shaped expectation while one of them has already
    retired an option; replaying the other's form would then either
    re-create wording that was deliberately removed or silently drop a row
    the second Teacher never saw. Binding the ordered set makes that a
    detectable change rather than a silent one.

    Option *text* and the answer key are deliberately absent: the token is
    readable, the text can be 1,000 characters per row, and the versions
    plus the identifier set already pin the exact state.
    """
    return _serializer(_QUESTION_EDIT_SALT).dumps(
        {
            "purpose": _QUESTION_EDIT_PURPOSE,
            "teacher_public_id": teacher_public_id,
            "group_public_id": group_public_id,
            "quiz_public_id": quiz_public_id,
            "question_public_id": question_public_id,
            "quiz_version": quiz_version,
            "question_version": question_version,
            "option_public_ids": list(option_public_ids),
        }
    )


def _load_question_edit_token(token):
    if not token:
        return None
    try:
        payload = _serializer(_QUESTION_EDIT_SALT).loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_QUESTION_EDIT_FIELDS):
        return None
    if payload["purpose"] != _QUESTION_EDIT_PURPOSE:
        return None
    if not _all_strings([payload[field] for field in _QUESTION_EDIT_FIELDS[1:5]]):
        return None
    if not _positive_int(payload["quiz_version"]):
        return None
    if not _positive_int(payload["question_version"]):
        return None
    option_ids = payload["option_public_ids"]
    if not isinstance(option_ids, list) or not _all_strings(option_ids):
        return None
    # A repeated identifier would make "which options did this form show"
    # ambiguous, and the bound must stay inside the approved active range.
    if len(set(option_ids)) != len(option_ids):
        return None
    if not MIN_ACTIVE_OPTIONS <= len(option_ids) <= MAX_ACTIVE_OPTIONS:
        return None
    return payload


def _edit_token_is_stale(
    token,
    teacher_public_id,
    group_public_id,
    quiz_public_id,
    question_public_id,
    quiz,
    question,
    locked_option_public_ids,
):
    """True when `token` does not exactly describe the **locked** rows.

    Every identity field, both versions, and the ordered active option
    set must match. ``locked_option_public_ids`` must come from the rows
    this request locked, never from the pre-lock preview.
    """
    payload = _load_question_edit_token(token)
    if payload is None:
        return True
    if payload["teacher_public_id"] != teacher_public_id:
        return True
    if payload["group_public_id"] != group_public_id:
        return True
    if payload["quiz_public_id"] != quiz_public_id:
        return True
    if payload["question_public_id"] != question_public_id:
        return True
    if payload["quiz_version"] != quiz.version:
        return True
    if payload["question_version"] != question.version:
        return True
    return payload["option_public_ids"] != list(locked_option_public_ids)


# -- move ---------------------------------------------------------------


def _make_question_move_token(
    direction,
    teacher_public_id,
    group_public_id,
    quiz_public_id,
    question_public_id,
    quiz_version,
    question_version,
    page,
):
    """A token for one Move Up / Move Down control on one rendered row.

    ``direction`` is bound so a Move Up token cannot be replayed as a Move
    Down, and ``page`` is bound so the Post/Redirect/Get lands the Teacher
    back on the page they were reading rather than on page 1.
    """
    return _serializer(_QUESTION_MOVE_SALT).dumps(
        {
            "purpose": _QUESTION_MOVE_PURPOSE,
            "direction": direction,
            "teacher_public_id": teacher_public_id,
            "group_public_id": group_public_id,
            "quiz_public_id": quiz_public_id,
            "question_public_id": question_public_id,
            "quiz_version": quiz_version,
            "question_version": question_version,
            "page": page,
        }
    )


def _load_question_move_token(token):
    if not token:
        return None
    try:
        payload = _serializer(_QUESTION_MOVE_SALT).loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_QUESTION_MOVE_FIELDS):
        return None
    if payload["purpose"] != _QUESTION_MOVE_PURPOSE:
        return None
    if payload["direction"] not in MOVE_DIRECTIONS:
        return None
    if not _all_strings([payload[field] for field in _QUESTION_MOVE_FIELDS[2:6]]):
        return None
    for field in ("quiz_version", "question_version", "page"):
        if not _positive_int(payload[field]):
            return None
    return payload


def _move_token_is_stale(
    token,
    direction,
    teacher_public_id,
    group_public_id,
    quiz_public_id,
    question_public_id,
    quiz,
    question,
):
    payload = _load_question_move_token(token)
    if payload is None:
        return True
    if payload["direction"] != direction:
        return True
    if payload["teacher_public_id"] != teacher_public_id:
        return True
    if payload["group_public_id"] != group_public_id:
        return True
    if payload["quiz_public_id"] != quiz_public_id:
        return True
    if payload["question_public_id"] != question_public_id:
        return True
    if payload["quiz_version"] != quiz.version:
        return True
    return payload["question_version"] != question.version


def _move_token_page(token):
    """The normalized return page a move token names, or 1.

    Read **before** the authoritative staleness check so a rejected move
    can still send the Teacher back to the page they were on.
    """
    payload = _load_question_move_token(token)
    if payload is None:
        return 1
    return normalize_question_page(payload["page"])


# ======================================================================
# Question locking helpers -- same open transaction, no second reset
# ======================================================================


def _lock_question_rows(question_ids):
    """Lock the given QuizQuestion rows ``FOR UPDATE`` in **ascending
    internal id**, inside the already-open transaction.

    Ascending id, never visual order: that is the project-wide rule that
    keeps two co-teachers moving adjacent questions from deadlocking
    against each other. Returns ``{id: row_or_None}``; the caller must
    treat a ``None`` as a rejection and never "keep going".
    """
    rows = {}
    for question_id in sorted({q for q in question_ids if q is not None}):
        rows[question_id] = (
            QuizQuestion.query.filter_by(id=question_id).with_for_update().first()
        )
    return rows


def _lock_active_options(question_id):
    """Lock one Question's **active** options and return them in authored
    order.

    Bounded twice over: the id read is capped at ``MAX_ACTIVE_OPTIONS + 1``
    rows, and each row is then locked individually in **ascending internal
    id**. The entities the caller decides on are the ones the
    ``SELECT ... FOR UPDATE`` statements loaded, so their values are the
    locked ones rather than anything an earlier read may have cached; the
    authored order is recomputed from those locked values.

    A row that stopped being active between the id read and its lock is
    dropped here rather than silently treated as live.
    """
    locked = []
    for option_id in sorted(active_option_ids(question_id)):
        row = QuestionOption.query.filter_by(id=option_id).with_for_update().first()
        if row is not None and row.question_id == question_id and row.is_active:
            locked.append(row)
    locked.sort(key=lambda option: (option.display_order, option.id))
    return locked


def _question_ownership_broken(quiz, question, question_public_id):
    """True when the **locked** Question is not the one the URL claims."""
    if question is None:
        return True
    return (
        question.quiz_id != quiz.id or question.public_id != question_public_id
    )


# ======================================================================
# Fresh authorization for the question surface
# ======================================================================


def _fresh_question_authorization(
    actor_id, group_public_id, quiz_public_id, question_public_id=None
):
    """:func:`_fresh_quiz_authorization`, extended one nesting level.

    Returns ``(group, quiz, question)``; ``question`` is ``None`` when
    none was requested. Every post-rollback path in this section uses it
    for the reason M04A already documents: once the locks are released,
    neither ``roles_required`` nor a cached ``current_user`` is current
    evidence, and the same concurrent change that forced the rollback may
    have ended this Teacher's access. The actor is identified by a
    **scalar id captured before the reset**.

    Reading stays historical: the hierarchy need not be active here.
    Callers about to render an *editable* form check that separately and
    downgrade to a redirect, so an archived chain never yields a form that
    cannot save.
    """
    group, quiz = _fresh_quiz_authorization(actor_id, group_public_id, quiz_public_id)
    if question_public_id is None:
        return group, quiz, None
    question = teacher_question(quiz.id, question_public_id)
    if question is None:
        abort(404)
    return group, quiz, question


# ======================================================================
# Create a question
# ======================================================================


def _blank_option_rows():
    """The two empty rows a brand-new question starts from -- the approved
    minimum, so the Teacher is never shown a form that cannot be saved as
    it stands. Nothing is pre-checked."""
    return [
        {"key": f"new:{index}", "text": "", "is_correct": False}
        for index in range(MIN_ACTIVE_OPTIONS)
    ]


def _render_question_create_page(
    actor_id, actor_public_id, group_public_id, quiz_public_id,
    form=None, state_token=None, message=None,
):
    """Render the new-question form, redirect, or 404.

    Rolls back first, re-proves the whole chain from current state, and
    only then flashes `message`, mints a token, or renders anything.
    """
    db.session.rollback()
    group, quiz, _ = _fresh_question_authorization(
        actor_id, group_public_id, quiz_public_id
    )
    if message is not None:
        flash(message, "danger")
    if not _group_is_operational(group):
        flash(_QUESTION_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    # A courtesy check with the same rule the write path enforces:
    # never hand back a form whose save is already refused.
    frozen = _authoring_block(quiz)
    if frozen is not None:
        flash(frozen, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    if form is None:
        form = QuizQuestionForm(
            formdata=None,
            data={"answer_mode": QuestionAnswerMode.SINGLE.value},
            seed_rows=_blank_option_rows(),
        )
    if state_token is None:
        state_token = _make_question_create_token(
            actor_public_id, group_public_id, quiz_public_id, quiz.version
        )

    return _private_no_store(
        "teacher/quizzes/question_form.html",
        form=form,
        group=group,
        quiz=build_quiz_detail(quiz, _tz_name()),
        question=None,
        state_token=state_token,
    )


@teacher_bp.route(
    "/groups/<group_public_id>/quizzes/<quiz_public_id>/questions/new",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def quiz_question_create(group_public_id, quiz_public_id):
    """Add one ordered multiple-choice question to a quiz draft.

    **Nothing about identity or placement is taken from the request.** The
    Quiz is the authorized public identifier in the URL, ``display_order``
    is read from the locked Quiz, ``version`` starts at the constant 1,
    every ``public_id`` is server-generated, and the timestamps are the
    request's post-lock moment -- so a forged ``quiz_id`` /
    ``display_order`` / ``version`` / ``public_id`` / ``is_active`` field
    has nowhere to land.
    """
    if request.method == "GET":
        return _render_question_create_page(
            current_user.id, current_user.public_id, group_public_id, quiz_public_id
        )

    preview_group = _teacher_group_or_404(group_public_id)
    preview_quiz = _quiz_for_group_or_404(preview_group, quiz_public_id)

    if not _group_is_operational(preview_group):
        flash(_QUESTION_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    keys, texts, correct = submitted_option_fields(request.form)
    form = QuizQuestionForm(
        option_keys=keys, option_texts=texts, correct_keys=correct
    )
    form_is_valid = form.validate_on_submit()

    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id
    submitted_token = request.form.get("question_state", "")

    hierarchy, group, teacher, teacher_assignment, quizzes = _lock_quiz_chain(
        group_public_id, term_id, level_id, course_id, teacher_id, quiz_ids=[quiz_id]
    )
    if _authz_broken(group, teacher, teacher_assignment):
        db.session.rollback()
        abort(404)

    quiz = quizzes.get(quiz_id)
    if _nested_ownership_broken(group, quiz, quiz_public_id):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(
        hierarchy, group, term_id, level_id, course_id,
        wording=_QUESTION_BLOCK_WORDING,
    )
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    # THE authoring freeze, against the LOCKED Quiz row. Publishing
    # makes the authored content read-only; the first attempt freezes
    # it permanently. Checked here, not in the template, so a
    # bookmarked or forged request is refused too -- and BEFORE any
    # field is assigned, so a rejection leaves every row untouched.
    frozen = _authoring_block(quiz)
    if frozen is not None:
        db.session.rollback()
        flash(frozen, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    if _create_token_is_stale(
        submitted_token, teacher_public_id, group_public_id, quiz_public_id, quiz
    ):
        db.session.rollback()
        flash(_QUESTION_STALE_MESSAGE, "danger")
        return redirect(_question_create_url(group_public_id, quiz_public_id))

    if not form_is_valid:
        return _render_question_create_page(
            teacher_id, teacher_public_id, group_public_id, quiz_public_id,
            form=form, state_token=submitted_token,
        )

    # A brand-new question owns no persisted option, so every submitted
    # row must be a `new:` marker. A claimed public_id here belongs to
    # some other question (or to nothing at all) and is refused rather
    # than adopted.
    if any(option.public_id is not None for option in form.options):
        return _render_question_create_page(
            teacher_id, teacher_public_id, group_public_id, quiz_public_id,
            form=_questions_error(
                form,
                "The answer options could not be read. Please reload the page and try again.",
            ),
            state_token=submitted_token,
        )

    now_utc = _write_moment()

    # No field is assigned until every check above has passed. The order
    # is read under the held Quiz lock, which is what makes two concurrent
    # creates append rather than collide.
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
        # that a constraint can refuse.
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
        return _render_question_create_page(
            teacher_id, teacher_public_id, group_public_id, quiz_public_id,
            form=form, state_token=submitted_token,
            message=(
                "This question could not be saved. The quiz may have just been changed by "
                "someone else. Please reload and try again."
            ),
        )

    flash("Question added. The quiz is still a draft.", "success")
    if request.form.get("after_save") == "add_another":
        # Fresh GET: reauthorize and mint a token for the new parent version.
        return redirect(_question_create_url(group_public_id, quiz_public_id))
    return redirect(_quiz_detail_url(group_public_id, quiz_public_id))


# ======================================================================
# Edit a question
# ======================================================================


def _render_question_edit_page(
    actor_id, actor_public_id, group_public_id, quiz_public_id, question_public_id,
    form=None, state_token=None, message=None,
):
    """Render the question editor, redirect, or 404.

    Rolls back, re-proves the whole nested chain from current state, and
    only then flashes, mints or renders. A freshly minted token is paired
    only with freshly loaded persisted rows -- never with attempted
    values, which is exactly the bypass the stale rejection exists to
    close.
    """
    db.session.rollback()
    group, quiz, question = _fresh_question_authorization(
        actor_id, group_public_id, quiz_public_id, question_public_id
    )
    if message is not None:
        flash(message, "danger")
    if not _group_is_operational(group):
        flash(_QUESTION_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    # A courtesy check with the same rule the write path enforces:
    # never hand back a form whose save is already refused.
    frozen = _authoring_block(quiz)
    if frozen is not None:
        flash(frozen, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    options = active_options_ordered(question.id)
    if active_option_set_is_invalid(options):
        # Refuse rather than render an editor whose save could not
        # possibly be valid. Nothing is truncated or "repaired".
        flash(_QUESTION_INVALID_STATE_MESSAGE, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    editor = build_question_editor(question, options)
    if form is None:
        form = QuizQuestionForm(
            formdata=None,
            data={"prompt": question.prompt, "answer_mode": question.answer_mode},
            seed_rows=[
                {"key": row["public_id"], "text": row["text"], "is_correct": row["is_correct"]}
                for row in editor["options"]
            ],
        )
    if state_token is None:
        state_token = _make_question_edit_token(
            actor_public_id, group_public_id, quiz_public_id, question_public_id,
            quiz.version, question.version,
            [row["public_id"] for row in editor["options"]],
        )

    return _private_no_store(
        "teacher/quizzes/question_form.html",
        form=form,
        group=group,
        quiz=build_quiz_detail(quiz, _tz_name()),
        question=editor,
        state_token=state_token,
    )


def _aggregate_is_unchanged(question, prompt, answer_mode, submitted, locked_options):
    """True when this save would leave the complete question aggregate
    exactly as stored.

    "Unchanged" is deliberately strict: the prompt, the answer mode, the
    **set** of surviving options, each survivor's text, each survivor's
    answer-key value, and the final normalized ``0..n-1`` order must all
    already match. Anything else -- a new row, a retirement, a reordering,
    a single flipped checkbox -- is a real edit.

    A no-op writes nothing at all: no version moves, no timestamp moves,
    no option is retired, and no order is renumbered.
    """
    if question.prompt != prompt or question.answer_mode != answer_mode:
        return False
    if any(option.public_id is None for option in submitted):
        return False
    if len(submitted) != len(locked_options):
        return False

    by_public_id = {option.public_id: option for option in locked_options}
    for index, option in enumerate(submitted):
        stored = by_public_id.get(option.public_id)
        if stored is None:
            return False
        if stored.option_text != option.text:
            return False
        if bool(stored.is_correct) != option.is_correct:
            return False
        if stored.display_order != index:
            return False
    return True


@teacher_bp.route(
    "/groups/<group_public_id>/quizzes/<quiz_public_id>"
    "/questions/<question_public_id>/edit",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def quiz_question_edit(group_public_id, quiz_public_id, question_public_id):
    """Revise one question's prompt, answer mode, and answer options.

    **Order of the post-lock checks.** Authorization and nested ownership
    first, so an unauthorized attempt fails identically whatever the
    question contains; then the operational chain; then the stored
    aggregate's structural validity; then the signed token against the
    locked rows; then ordinary field and cardinality validation; then
    option ownership; and only then the no-op comparison and the write.
    Every rejection leaves the stored rows, their versions, their
    timestamps and their retirement state exactly as they were.

    **Removal is retirement.** An option the form no longer submits is
    deactivated in place with the request's moment, keeping its text,
    answer-key value, public_id, creation time and stored order as
    history. Nothing is ever physically deleted, and there is no
    restoration route.
    """
    if request.method == "GET":
        return _render_question_edit_page(
            current_user.id, current_user.public_id,
            group_public_id, quiz_public_id, question_public_id,
        )

    preview_group = _teacher_group_or_404(group_public_id)
    preview_quiz = _quiz_for_group_or_404(preview_group, quiz_public_id)
    preview_question = teacher_question(preview_quiz.id, question_public_id)
    if preview_question is None:
        abort(404)

    if not _group_is_operational(preview_group):
        flash(_QUESTION_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    keys, texts, correct = submitted_option_fields(request.form)
    form = QuizQuestionForm(
        option_keys=keys, option_texts=texts, correct_keys=correct
    )
    form_is_valid = form.validate_on_submit()

    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    question_id = preview_question.id
    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id
    submitted_token = request.form.get("question_state", "")

    # M04A's helper is reused unchanged, then the question row is locked
    # after it in the SAME open transaction -- extending the established
    # prefix by one level rather than redesigning it, and with no second
    # reset.
    hierarchy, group, teacher, teacher_assignment, quizzes = _lock_quiz_chain(
        group_public_id, term_id, level_id, course_id, teacher_id, quiz_ids=[quiz_id]
    )
    if _authz_broken(group, teacher, teacher_assignment):
        db.session.rollback()
        abort(404)

    quiz = quizzes.get(quiz_id)
    if _nested_ownership_broken(group, quiz, quiz_public_id):
        db.session.rollback()
        abort(404)

    question = _lock_question_rows([question_id]).get(question_id)
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
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    # THE authoring freeze, against the LOCKED Quiz row. Publishing
    # makes the authored content read-only; the first attempt freezes
    # it permanently. Checked here, not in the template, so a
    # bookmarked or forged request is refused too -- and BEFORE any
    # field is assigned, so a rejection leaves every row untouched.
    frozen = _authoring_block(quiz)
    if frozen is not None:
        db.session.rollback()
        flash(frozen, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    locked_options = _lock_active_options(question.id)
    if active_option_set_is_invalid(locked_options):
        db.session.rollback()
        flash(_QUESTION_INVALID_STATE_MESSAGE, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    if _edit_token_is_stale(
        submitted_token, teacher_public_id, group_public_id, quiz_public_id,
        question_public_id, quiz, question,
        [option.public_id for option in locked_options],
    ):
        db.session.rollback()
        flash(_QUESTION_STALE_MESSAGE, "danger")
        return redirect(
            _question_edit_url(group_public_id, quiz_public_id, question_public_id)
        )

    if not form_is_valid:
        return _render_question_edit_page(
            teacher_id, teacher_public_id, group_public_id, quiz_public_id,
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
        return _render_question_edit_page(
            teacher_id, teacher_public_id, group_public_id, quiz_public_id,
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
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

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
    #    order are all unchanged is left completely alone, so its
    #    `updated_at` does not move.
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

    # 3. The question itself, then its parent draft. Both counters move
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
        return _render_question_edit_page(
            teacher_id, teacher_public_id, group_public_id, quiz_public_id,
            question_public_id, form=form, state_token=submitted_token,
            message=(
                "This question could not be saved. It may have just been changed by someone "
                "else. Please reload and try again."
            ),
        )

    flash("Question updated. The quiz is still a draft.", "success")
    return redirect(_quiz_detail_url(group_public_id, quiz_public_id))


# ======================================================================
# Reorder questions
# ======================================================================


def _move_question(group_public_id, quiz_public_id, question_public_id, direction):
    """Swap one question with its neighbour in the complete Quiz order.

    The neighbour is resolved with a **bounded keyset lookup** against the
    same ``(display_order, id)`` ordering the list uses, so a move is
    correct across order gaps, across shared order values, and across page
    boundaries -- it is always the real previous/next question, never
    merely the adjacent row on the rendered page. Nothing loads the Quiz's
    whole question set.

    Both Question rows are then locked in **ascending internal id**, never
    in visual order, so two co-teachers moving adjacent questions cannot
    deadlock against each other.
    """
    preview_group = _teacher_group_or_404(group_public_id)
    preview_quiz = _quiz_for_group_or_404(preview_group, quiz_public_id)
    preview_question = teacher_question(preview_quiz.id, question_public_id)
    if preview_question is None:
        abort(404)

    submitted_token = request.form.get("question_state", "")
    return_page = _move_token_page(submitted_token)

    if not _group_is_operational(preview_group):
        flash(_QUESTION_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(
            _quiz_detail_page_url(group_public_id, quiz_public_id, return_page)
        )

    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    question_id = preview_question.id
    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id

    hierarchy, group, teacher, teacher_assignment, quizzes = _lock_quiz_chain(
        group_public_id, term_id, level_id, course_id, teacher_id, quiz_ids=[quiz_id]
    )
    if _authz_broken(group, teacher, teacher_assignment):
        db.session.rollback()
        abort(404)

    quiz = quizzes.get(quiz_id)
    if _nested_ownership_broken(group, quiz, quiz_public_id):
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
            _quiz_detail_page_url(group_public_id, quiz_public_id, return_page)
        )

    # THE authoring freeze, against the LOCKED Quiz row. Publishing
    # makes the authored content read-only; the first attempt freezes
    # it permanently. Checked here, not in the template, so a
    # bookmarked or forged request is refused too -- and BEFORE any
    # field is assigned, so a rejection leaves every row untouched.
    frozen = _authoring_block(quiz)
    if frozen is not None:
        db.session.rollback()
        flash(frozen, "danger")
        return redirect(
            _quiz_detail_page_url(group_public_id, quiz_public_id, return_page)
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

    if _move_token_is_stale(
        submitted_token, direction, teacher_public_id, group_public_id,
        quiz_public_id, question_public_id, quiz, target,
    ):
        db.session.rollback()
        flash(_QUESTION_STALE_MESSAGE, "danger")
        return redirect(
            _quiz_detail_page_url(group_public_id, quiz_public_id, return_page)
        )

    neighbour = None if neighbour_preview is None else locked.get(neighbour_preview.id)
    if neighbour is None or neighbour.quiz_id != quiz.id:
        # A boundary move changes nothing at all: no order is rewritten and
        # no version moves. It is reported as information, not as an error.
        db.session.rollback()
        flash(
            "This question is already {}.".format(
                "first" if direction == MOVE_UP else "last"
            ),
            "info",
        )
        return redirect(
            _quiz_detail_page_url(group_public_id, quiz_public_id, return_page)
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
        # values would change nothing. Nudge exactly one row instead, which
        # is what actually makes the requested order real. Gaps are
        # acceptable, and the value stays non-negative.
        neighbour.display_order = target.display_order + 1
        changed = [neighbour]
    else:
        target.display_order = neighbour.display_order + 1
        changed = [target]

    # Only the rows whose stored order really changed get a new version
    # and a new timestamp; the parent draft's counter moves exactly once.
    for row in changed:
        row.version = row.version + 1
        row.updated_at = now_utc
    quiz.version = quiz.version + 1
    quiz.updated_at = now_utc

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        _fresh_question_authorization(
            teacher_id, group_public_id, quiz_public_id, question_public_id
        )
        flash(
            "The questions could not be reordered. Someone may have just changed this quiz. "
            "Please reload and try again.",
            "danger",
        )
        return redirect(
            _quiz_detail_page_url(group_public_id, quiz_public_id, return_page)
        )

    flash("Question order updated.", "success")
    return redirect(_quiz_detail_page_url(group_public_id, quiz_public_id, return_page))


@teacher_bp.post(
    "/groups/<group_public_id>/quizzes/<quiz_public_id>"
    "/questions/<question_public_id>/move-up"
)
@roles_required(UserRole.TEACHER.value)
def quiz_question_move_up(group_public_id, quiz_public_id, question_public_id):
    return _move_question(
        group_public_id, quiz_public_id, question_public_id, MOVE_UP
    )


@teacher_bp.post(
    "/groups/<group_public_id>/quizzes/<quiz_public_id>"
    "/questions/<question_public_id>/move-down"
)
@roles_required(UserRole.TEACHER.value)
def quiz_question_move_down(group_public_id, quiz_public_id, question_public_id):
    return _move_question(
        group_public_id, quiz_public_id, question_public_id, MOVE_DOWN
    )


# ======================================================================
# Phase 4 / M04D -- publication, availability settings, attempt review
# ======================================================================
#
#     GET|POST  .../quizzes/<quiz_public_id>/settings
#     POST      .../quizzes/<quiz_public_id>/publish
#     POST      .../quizzes/<quiz_public_id>/unpublish
#     GET       .../quizzes/<quiz_public_id>/attempts
#     GET       .../quizzes/<quiz_public_id>/attempts/<attempt_public_id>
#
# There is still no delete route for a Quiz, a question, an option, an
# attempt, an answer or a result; no score override; and no manual-grading
# or answer-release path. A Teacher publishes, withdraws before anybody
# has started, and reads what Students did -- nothing else.
#
# **Two freezes, and they are different.** Publishing makes the authored
# Quiz read-only because Students may already be reading exactly that
# wording. The **first attempt** freezes it permanently, because from then
# on somebody's answers are answers *to* that wording; unpublishing is
# refused from that moment on. Both are re-checked against the locked rows
# in every mutation route, never only in a template.

_PUBLISHED = QuizStatus.PUBLISHED.value
_DRAFT_STATUS = QuizStatus.DRAFT.value

#: Refusal sentences. Declared once so the early courtesy check and the
#: authoritative post-lock check can never explain the same rule
#: differently.
_FROZEN_BY_ATTEMPTS_MESSAGE = (
    "Students have already started this quiz, so its settings, questions, options, and answer "
    "key can no longer be changed and it can no longer be withdrawn. Students answered exactly "
    "this wording, and rewriting it afterwards would change what their attempts were for. You "
    "can still read every attempt."
)
_FROZEN_BY_PUBLICATION_MESSAGE = (
    "This quiz is published, so its settings, questions, options, and answer key are read-only. "
    "Withdraw it first if you need to change something -- that is possible only while no "
    "student has started it."
)
_ATTEMPTS_NOT_OPERATIONAL_MESSAGE = (
    "This quiz can only be published or withdrawn while the group and its academic term, "
    "course, and level are all active. Existing attempts stay readable."
)

_PUBLICATION_BLOCK_WORDING = (
    "Quizzes can only be published or withdrawn",
    "Existing attempts stay readable.",
)


def _authoring_block(quiz):
    """``None`` when this Quiz's authored content may still be changed,
    else the sentence explaining why not.

    The attempt freeze is checked **first** because it is the stronger and
    permanent one: a Teacher whose Quiz has attempts must not be told to
    "withdraw it first", which would send them at a door that is already
    locked.

    Must be called against the **locked** Quiz row on every write path.
    """
    if quiz_has_attempt_history(quiz.id):
        return _FROZEN_BY_ATTEMPTS_MESSAGE
    if quiz.status == _PUBLISHED:
        return _FROZEN_BY_PUBLICATION_MESSAGE
    return None


# ----------------------------------------------------------------------
# URLs
# ----------------------------------------------------------------------


def _quiz_settings_url(group_public_id, quiz_public_id):
    return url_for(
        "teacher.quiz_settings",
        group_public_id=group_public_id,
        quiz_public_id=quiz_public_id,
    )


# ======================================================================
# Signed settings and publication tokens
# ======================================================================

_QUIZ_SETTINGS_SALT = "teacher.quiz-settings.phase4-m04d.v1"
_QUIZ_PUBLICATION_SALT = "teacher.quiz-publication.phase4-m04d.v1"

_QUIZ_SETTINGS_PURPOSE = "quiz-settings"
_QUIZ_PUBLICATION_PURPOSE = "quiz-publication"

#: The two publication directions, bound into the token so a Publish
#: control can never be replayed as a Withdraw.
_PUBLISH_ACTION = "publish"
_UNPUBLISH_ACTION = "unpublish"
_PUBLICATION_ACTIONS = (_PUBLISH_ACTION, _UNPUBLISH_ACTION)

_QUIZ_SETTINGS_FIELDS = (
    "purpose",
    "teacher_public_id",
    "group_public_id",
    "quiz_public_id",
    "quiz_version",
)
_QUIZ_PUBLICATION_FIELDS = (
    "purpose",
    "action",
    "teacher_public_id",
    "group_public_id",
    "quiz_public_id",
    "quiz_version",
    "quiz_status",
)


def _make_settings_token(teacher_public_id, group_public_id, quiz_public_id, version):
    return _serializer(_QUIZ_SETTINGS_SALT).dumps(
        {
            "purpose": _QUIZ_SETTINGS_PURPOSE,
            "teacher_public_id": teacher_public_id,
            "group_public_id": group_public_id,
            "quiz_public_id": quiz_public_id,
            "quiz_version": version,
        }
    )


def _load_settings_token(token):
    if not token:
        return None
    try:
        payload = _serializer(_QUIZ_SETTINGS_SALT).loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_QUIZ_SETTINGS_FIELDS):
        return None
    if payload["purpose"] != _QUIZ_SETTINGS_PURPOSE:
        return None
    if not _all_strings([payload[f] for f in _QUIZ_SETTINGS_FIELDS[1:4]]):
        return None
    if not _positive_int(payload["quiz_version"]):
        return None
    return payload


def _settings_token_is_stale(
    token, teacher_public_id, group_public_id, quiz_public_id, quiz
):
    payload = _load_settings_token(token)
    if payload is None:
        return True
    if payload["teacher_public_id"] != teacher_public_id:
        return True
    if payload["group_public_id"] != group_public_id:
        return True
    if payload["quiz_public_id"] != quiz_public_id:
        return True
    return payload["quiz_version"] != quiz.version


def _make_publication_token(
    action, teacher_public_id, group_public_id, quiz_public_id, version, status
):
    """A token for one Publish or Withdraw control on one rendered page.

    ``action`` **and** the Quiz's current ``status`` are both bound: the
    first stops a Publish token being replayed as a Withdraw, and the
    second stops either being replayed once the Quiz has already moved.
    """
    return _serializer(_QUIZ_PUBLICATION_SALT).dumps(
        {
            "purpose": _QUIZ_PUBLICATION_PURPOSE,
            "action": action,
            "teacher_public_id": teacher_public_id,
            "group_public_id": group_public_id,
            "quiz_public_id": quiz_public_id,
            "quiz_version": version,
            "quiz_status": status,
        }
    )


def _load_publication_token(token):
    if not token:
        return None
    try:
        payload = _serializer(_QUIZ_PUBLICATION_SALT).loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_QUIZ_PUBLICATION_FIELDS):
        return None
    if payload["purpose"] != _QUIZ_PUBLICATION_PURPOSE:
        return None
    if payload["action"] not in _PUBLICATION_ACTIONS:
        return None
    if payload["quiz_status"] not in (_DRAFT_STATUS, _PUBLISHED):
        return None
    if not _all_strings([payload[f] for f in _QUIZ_PUBLICATION_FIELDS[2:5]]):
        return None
    if not _positive_int(payload["quiz_version"]):
        return None
    return payload


def _publication_token_is_stale(
    token, action, teacher_public_id, group_public_id, quiz_public_id, quiz
):
    payload = _load_publication_token(token)
    if payload is None:
        return True
    if payload["action"] != action:
        return True
    if payload["teacher_public_id"] != teacher_public_id:
        return True
    if payload["group_public_id"] != group_public_id:
        return True
    if payload["quiz_public_id"] != quiz_public_id:
        return True
    if payload["quiz_version"] != quiz.version:
        return True
    return payload["quiz_status"] != quiz.status


# ======================================================================
# Availability settings
# ======================================================================


def _settings_defaults(quiz, tz_name):
    """The local wall-clock values a GET of the settings form starts from
    -- the stored UTC instants rendered through ``APP_TIMEZONE``, so what
    the Teacher sees is what they originally entered."""
    return {
        "opens_at": to_app_local(tz_name, quiz.opens_at) if quiz.opens_at else None,
        "closes_at": to_app_local(tz_name, quiz.closes_at) if quiz.closes_at else None,
        "time_limit_minutes": quiz.time_limit_minutes,
        "attempt_limit": quiz.attempt_limit,
    }


def _render_quiz_settings_page(
    actor_id, actor_public_id, group_public_id, quiz_public_id,
    form=None, state_token=None, message=None,
):
    """Render the settings form, redirect, or 404.

    Rolls back first, re-proves the whole chain from current state, and
    only then flashes, mints a token, or renders -- the rule every
    post-rollback path in this module follows.
    """
    db.session.rollback()
    group, quiz = _fresh_quiz_authorization(actor_id, group_public_id, quiz_public_id)
    if message is not None:
        flash(message, "danger")
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    frozen = _authoring_block(quiz)
    if frozen is not None:
        flash(frozen, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    tz_name = _tz_name()
    if form is None:
        form = QuizSettingsForm(
            formdata=None, data=_settings_defaults(quiz, tz_name), tz_name=tz_name
        )
    if state_token is None:
        state_token = _make_settings_token(
            actor_public_id, group_public_id, quiz_public_id, quiz.version
        )

    return _private_no_store(
        "teacher/quizzes/settings.html",
        form=form,
        group=group,
        quiz=build_quiz_detail(quiz, tz_name),
        state_token=state_token,
        tz_name=tz_name,
    )


@teacher_bp.route(
    "/groups/<group_public_id>/quizzes/<quiz_public_id>/settings",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def quiz_settings(group_public_id, quiz_public_id):
    """Set the availability window, optional time limit and attempt limit.

    **Nothing about publication is taken from the request.** ``status``
    and ``published_at`` are never assigned here -- they belong solely to
    the publish/withdraw routes -- so a forged field of either name has
    nowhere to land. Draft-only: a published Quiz's settings are read-only
    because Students may already be planning around them.
    """
    if request.method == "GET":
        return _render_quiz_settings_page(
            current_user.id, current_user.public_id, group_public_id, quiz_public_id
        )

    preview_group = _teacher_group_or_404(group_public_id)
    preview_quiz = _quiz_for_group_or_404(preview_group, quiz_public_id)

    if not _group_is_operational(preview_group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    tz_name = _tz_name()
    form = QuizSettingsForm(tz_name=tz_name)
    form_is_valid = form.validate_on_submit()

    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id
    submitted_token = request.form.get("quiz_state", "")

    hierarchy, group, teacher, teacher_assignment, quizzes = _lock_quiz_chain(
        group_public_id, term_id, level_id, course_id, teacher_id, quiz_ids=[quiz_id]
    )
    if _authz_broken(group, teacher, teacher_assignment):
        db.session.rollback()
        abort(404)

    quiz = quizzes.get(quiz_id)
    if _nested_ownership_broken(group, quiz, quiz_public_id):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(hierarchy, group, term_id, level_id, course_id)
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    frozen = _authoring_block(quiz)
    if frozen is not None:
        db.session.rollback()
        flash(frozen, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    if _settings_token_is_stale(
        submitted_token, teacher_public_id, group_public_id, quiz_public_id, quiz
    ):
        db.session.rollback()
        flash(_STALE_MESSAGE, "danger")
        return redirect(_quiz_settings_url(group_public_id, quiz_public_id))

    if not form_is_valid:
        return _render_quiz_settings_page(
            teacher_id, teacher_public_id, group_public_id, quiz_public_id,
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
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

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
        return _render_quiz_settings_page(
            teacher_id, teacher_public_id, group_public_id, quiz_public_id,
            form=form, state_token=submitted_token,
            message=(
                "These settings could not be saved. Someone may have just changed this quiz. "
                "Please reload and try again."
            ),
        )

    flash("Quiz settings saved. The quiz is still a draft.", "success")
    return redirect(_quiz_detail_url(group_public_id, quiz_public_id))


# ======================================================================
# Publish and withdraw
# ======================================================================


def _publication_transition(group_public_id, quiz_public_id, action):
    """The shared body of Publish and Withdraw.

    One function so the two directions cannot drift in their
    authorization, locking, freeze or staleness handling -- only the
    decision they reach and the sentence they flash differ.
    """
    preview_group = _teacher_group_or_404(group_public_id)
    preview_quiz = _quiz_for_group_or_404(preview_group, quiz_public_id)

    if not _group_is_operational(preview_group):
        flash(_ATTEMPTS_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    quiz_id = preview_quiz.id
    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id
    submitted_token = request.form.get("quiz_state", "")

    hierarchy, group, teacher, teacher_assignment, quizzes = _lock_quiz_chain(
        group_public_id, term_id, level_id, course_id, teacher_id, quiz_ids=[quiz_id]
    )
    if _authz_broken(group, teacher, teacher_assignment):
        db.session.rollback()
        abort(404)

    quiz = quizzes.get(quiz_id)
    if _nested_ownership_broken(group, quiz, quiz_public_id):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(
        hierarchy, group, term_id, level_id, course_id,
        wording=_PUBLICATION_BLOCK_WORDING,
    )
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    if _publication_token_is_stale(
        submitted_token, action, teacher_public_id, group_public_id,
        quiz_public_id, quiz,
    ):
        db.session.rollback()
        flash(_STALE_MESSAGE, "danger")
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    now_utc = _write_moment()

    if action == _PUBLISH_ACTION:
        if quiz.status == _PUBLISHED:
            db.session.rollback()
            flash("This quiz is already published.", "info")
            return redirect(_quiz_detail_url(group_public_id, quiz_public_id))
        # THE authoritative readiness check, against the locked rows. The
        # detail page runs the same function read-only, so the panel a
        # Teacher reads and the rule that decides can never disagree.
        blockers = publication_blockers(quiz)
        if blockers:
            db.session.rollback()
            for sentence in blockers:
                flash(sentence, "danger")
            return redirect(_quiz_detail_url(group_public_id, quiz_public_id))
        quiz.status = _PUBLISHED
        quiz.published_at = now_utc
    else:
        if quiz.status != _PUBLISHED:
            db.session.rollback()
            flash("This quiz is already a draft.", "info")
            return redirect(_quiz_detail_url(group_public_id, quiz_public_id))
        # Withdrawal is permitted only while nobody has started. Once an
        # attempt exists the Quiz is frozen permanently.
        if quiz_has_attempt_history(quiz.id):
            db.session.rollback()
            flash(_FROZEN_BY_ATTEMPTS_MESSAGE, "danger")
            return redirect(_quiz_detail_url(group_public_id, quiz_public_id))
        quiz.status = _DRAFT_STATUS
        quiz.published_at = None

    quiz.version = quiz.version + 1
    quiz.updated_at = now_utc
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        _fresh_quiz_authorization(teacher_id, group_public_id, quiz_public_id)
        flash(
            "This quiz could not be updated. Someone may have just changed it. Please reload "
            "and try again.",
            "danger",
        )
        return redirect(_quiz_detail_url(group_public_id, quiz_public_id))

    flash(
        "Quiz published. Enrolled students can open it once its opening time arrives."
        if action == _PUBLISH_ACTION
        else "Quiz withdrawn. It is a draft again and students cannot see it.",
        "success",
    )
    return redirect(_quiz_detail_url(group_public_id, quiz_public_id))


@teacher_bp.post("/groups/<group_public_id>/quizzes/<quiz_public_id>/publish")
@roles_required(UserRole.TEACHER.value)
def quiz_publish(group_public_id, quiz_public_id):
    return _publication_transition(group_public_id, quiz_public_id, _PUBLISH_ACTION)


@teacher_bp.post("/groups/<group_public_id>/quizzes/<quiz_public_id>/unpublish")
@roles_required(UserRole.TEACHER.value)
def quiz_unpublish(group_public_id, quiz_public_id):
    return _publication_transition(group_public_id, quiz_public_id, _UNPUBLISH_ACTION)


# ======================================================================
# Attempt review -- read only
# ======================================================================


def _build_attempt_summaries(rows, tz_name):
    """Plain presentation dicts for the Teacher attempt list.

    Public ids and display strings only. The percentage is derived from
    the two stored integers at render time rather than stored as a third,
    disagreeable copy.
    """
    summaries = []
    for row in rows:
        summaries.append(
            {
                "public_id": row.public_id,
                "student_name": row.student_name,
                "attempt_number": row.attempt_number,
                "status": row.status,
                "status_label": ATTEMPT_STATUS_LABELS.get(row.status, row.status),
                "started_local": to_app_local(tz_name, row.started_at),
                "deadline_local": to_app_local(tz_name, row.deadline_at),
                "submitted_local": (
                    to_app_local(tz_name, row.submitted_at)
                    if row.submitted_at is not None
                    else None
                ),
                "correct_count": row.correct_count,
                "total_questions": row.total_questions,
                "percentage": percentage(row.correct_count or 0, row.total_questions),
            }
        )
    return summaries


@teacher_bp.get("/groups/<group_public_id>/quizzes/<quiz_public_id>/attempts")
@roles_required(UserRole.TEACHER.value)
def quiz_attempts(group_public_id, quiz_public_id):
    """One bounded page of a Quiz's attempts, newest first.

    Read-only: a Teacher can see what Students did and cannot change any
    of it. There is no score override, no manual grade and no delete.

    **Expiry is settled here before anything is shown.** Any in-progress
    attempt on this page whose deadline has passed is finalized and graded
    under the required locks first, so a Teacher never reads a row that
    claims to be running when its deadline is behind it.
    """
    # Authorize first, capture the scalars the settle step needs, and
    # only then settle -- `settle_due_attempts` performs its own
    # deliberate reset, so every ORM object read before it is expired and
    # the display copies are re-read afterwards.
    group = _teacher_group_or_404(group_public_id)
    quiz = _quiz_for_group_or_404(group, quiz_public_id)
    quiz_id = quiz.id
    tz_name = _tz_name()
    page = normalize_page(request.args.get("page"))

    settle_due_attempts(group_public_id, quiz_id, _write_moment())

    group = _teacher_group_or_404(group_public_id)
    quiz = _quiz_for_group_or_404(group, quiz_public_id)

    rows, has_next = teacher_attempts_page(quiz.id, page)
    if not rows and page > 1:
        page = 1
        rows, has_next = teacher_attempts_page(quiz.id, page)

    return _private_no_store(
        "teacher/quizzes/attempts.html",
        group=group,
        quiz=build_quiz_detail(quiz, tz_name),
        attempts=_build_attempt_summaries(rows, tz_name),
        tz_name=tz_name,
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=ATTEMPT_PAGE_SIZE,
    )


@teacher_bp.get(
    "/groups/<group_public_id>/quizzes/<quiz_public_id>"
    "/attempts/<attempt_public_id>"
)
@roles_required(UserRole.TEACHER.value)
def quiz_attempt_detail(group_public_id, quiz_public_id, attempt_public_id):
    """One attempt in full: the Student's saved selections **and** the
    authored answer key, side by side.

    All three public ids must name the same nested chain. A Teacher is
    authorized to see the key; a Student never is, which is why this page
    uses its own builder rather than a flag on the Student one.

    Four bounded statements build the whole page -- never one per question
    or per answer.
    """
    group = _teacher_group_or_404(group_public_id)
    quiz = _quiz_for_group_or_404(group, quiz_public_id)
    quiz_id = quiz.id
    tz_name = _tz_name()

    # Settle only THIS attempt, and only if it is overdue. Same reset
    # caveat as the list route, so the display copies are re-read after.
    preview_attempt = teacher_attempt(quiz_id, attempt_public_id)
    if preview_attempt is None:
        abort(404)
    settle_due_attempts(
        group_public_id, quiz_id, _write_moment(), attempt_ids=[preview_attempt.id]
    )

    group = _teacher_group_or_404(group_public_id)
    quiz = _quiz_for_group_or_404(group, quiz_public_id)
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
        "teacher/quizzes/attempt_detail.html",
        group=group,
        quiz=build_quiz_detail(quiz, tz_name),
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
