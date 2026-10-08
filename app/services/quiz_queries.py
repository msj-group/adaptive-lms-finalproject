"""Read-only query layer for Group-owned quiz drafts (Phase 4 / M04A).

Flask-independent -- plain functions over the ORM, no ``request`` /
``flash`` / route decorators, mirroring
``app/services/assignment_queries.py`` and
``app/services/submission_feedback_queries.py``. **Every function here is
read-only**: no locks, no writes. The locking write path lives in
``app/blueprints/teacher/quizzes.py``, which owns the single transaction
reset and the lock order.

**Authorization is never performed here.** Each function is handed an
internal ``group_id`` the calling route has already proven the acting
Teacher holds an **active** ``GroupTeacherAssignment`` for. These
functions only *find the quizzes for it*, and every nested lookup is
constrained by that ``group_id`` so a Quiz ``public_id`` belonging to
another Group can never resolve through another Group's URL.

**Student reads exist only from Phase 4 / M04D, and only for published
Quizzes.** M04A and M04B had none at all, because a Quiz was a draft by
construction. M04D adds publication, so an eligible Student can now list
and open a published Quiz -- through the **one** fully scoped query
``student_visible_quiz_query`` at the bottom of this module, whose
``WHERE`` clause is the authorization. A draft can never come out of it.

**The authored answer key never reaches a Student read.** No Student-facing
function here selects ``QuestionOption.is_correct``, and no dict they build
carries it, so it cannot leak through a page, a form, a URL or a token --
before, during or after the Quiz's window. The Teacher review builder is a
separate function rather than a flag on a shared one, precisely so there
is no argument anybody can pass the wrong way.

**Every read is bounded, and the list is bounded in columns too.** The
list fetches ``PAGE_SIZE + 1`` rows to derive a non-disclosing "there is
a next page" flag without a ``COUNT``, and selects explicit **columns**
rather than entities -- notably **not** ``instructions``, which is up to
10,000 characters of body text that a list preview has no use for.
Nothing here loads an unbounded quiz history, and nothing iterates the
``Group.quizzes`` relationship.

Ordering is always fully deterministic, tie-broken by the internal ``id``
**inside the SQL only** -- the id is never placed in a dict that reaches
a template.
"""

from collections import defaultdict

from sqlalchemy import and_, case, func, or_

from app.extensions import db
from app.models import (
    MAX_ACTIVE_OPTIONS,
    MAX_QUIZ_QUESTIONS,
    MIN_ACTIVE_OPTIONS,
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    Level,
    ListeningActivity,
    QuestionAnswerMode,
    QuestionOption,
    Quiz,
    QuizAnswer,
    QuizAnswerSelection,
    QuizAttempt,
    QuizQuestion,
    QuizStatus,
    User,
    UserRole,
    UserStatus,
)
from app.services.schedule_occurrences import to_app_local

_ACTIVE = AcademicStatus.ACTIVE.value

#: Fixed page size for the Teacher quiz list. Not configurable and never
#: client-supplied -- the explicit bound the Part requires instead of an
#: unbounded history.
PAGE_SIZE = 20

#: The upper bound on an accepted ``page`` argument, mirroring
#: ``assignment_queries``. See :func:`normalize_page`.
_MAX_PAGE = 10000

#: The two question-reorder directions, named once so the route, the
#: signed move token and the neighbour query cannot spell them
#: differently. Anything else is rejected as an unknown direction.
MOVE_UP = "up"
MOVE_DOWN = "down"
MOVE_DIRECTIONS = (MOVE_UP, MOVE_DOWN)


# ---------------------------------------------------------------------------
# Ordinary Quiz versus Listening activity (Phase 4 / M05)
# ---------------------------------------------------------------------------
#
# A Quiz is a **Listening activity** exactly when a
# ``listening_activities`` row points at it, and an **ordinary Quiz**
# exactly when none does. There is deliberately no discriminator column on
# ``quizzes``: a nullable flag and that row could disagree, and then two
# places would answer the same question differently.
#
# Both predicates are declared here, once, and every Quiz read in the
# project applies one of them -- the ordinary Teacher list, the ordinary
# Teacher lookup and the Student visibility query exclude Listening
# activities; ``app/services/listening_queries.py`` requires them. Neither
# surface can therefore reach the other's objects, and the rule is a
# correlated ``EXISTS`` in SQL rather than a Python filter over rows that
# were already loaded.


def has_listening_extension():
    """A correlated ``EXISTS`` that is true for a Quiz carrying a
    Listening extension. Use it -- or its negation -- in a ``WHERE``
    clause; never filter loaded rows in Python.

    ``correlate(Quiz)`` is explicit rather than left to autocorrelation:
    the Listening reads **join** ``listening_activities`` in their outer
    query as well, and SQLAlchemy's automatic correlation would then
    remove it from this subquery's own ``FROM`` and leave the statement
    with none. Naming the one table this predicate correlates on keeps it
    correct in both a query that joins the extension and one that does
    not.
    """
    return (
        db.session.query(ListeningActivity.id)
        .filter(ListeningActivity.quiz_id == Quiz.id)
        .correlate(Quiz)
        .exists()
    )


def quiz_is_listening(quiz_id):
    """True when this Quiz is a Listening activity.

    Bounded by construction: it asks for one id and stops. Used by the
    write paths, which have already locked the Quiz row and need the
    classification as a scalar rather than as a query fragment.
    """
    return (
        db.session.query(ListeningActivity.id).filter_by(quiz_id=quiz_id).first()
        is not None
    )


def normalize_page(value):
    """Normalise a ``page`` query argument to a positive integer.

    A missing, non-numeric, zero, negative, or absurdly large value all
    become page 1 rather than reaching the database as an offset.

    Deliberately declared here rather than imported from
    ``assignment_queries``, for the reason that module already states
    about the notification inbox: **each feature owns its own bounds**, so
    tightening or widening one list can never silently change another.
    The rule and the limits are identical today on purpose -- a Teacher
    should not meet two different pagination behaviours in one section --
    and any future divergence has to be a deliberate edit here.
    """
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    if page < 1 or page > _MAX_PAGE:
        return 1
    return page


# ---------------------------------------------------------------------------
# Group identity history
# ---------------------------------------------------------------------------


def group_has_quiz_history(group_id):
    """True if **any** Quiz row exists for this Group.

    A Quiz row freezes the Group's academic identity
    (``academic_term_id`` / ``course_id``) exactly like
    Enrollment / GroupTeacherAssignment / Schedule / Unit / Assignment
    history does -- see
    ``app.blueprints.admin.groups._group_identity_frozen``.

    **An empty draft counts.** Its title and instructions were already
    authored against *this* Group's Course in *this* AcademicTerm, so
    retargeting the Group afterwards would silently reinterpret what that
    work is for -- the same reasoning that makes a draft Unit and a draft
    Assignment freeze identity. Whether the draft has questions is
    irrelevant, and deliberately so: M04A had no questions at all, and a
    rule that waited for them would have left every M04A draft
    unprotected. Phase 4 / M04B adds questions and options **inside** the
    Quiz, so they need no identity-freeze integration of their own --
    they cannot exist without a Quiz row that already froze the Group.

    Bounded by construction: it asks for one ``id`` and stops, so the cost
    does not grow with the Group's quiz history.

    This is an **identity** guard only. It adds no archive blocker, no
    cascade, and no new membership or capacity rule.
    """
    return db.session.query(Quiz.id).filter_by(group_id=group_id).first() is not None


# ---------------------------------------------------------------------------
# Duplicate-title check -- one definition, used before AND after the locks
# ---------------------------------------------------------------------------


def duplicate_title_exists(group_id, title, exclude_quiz_id=None):
    """True when `group_id` already carries a Quiz called `title`.

    `title` must be the **normalized** (trimmed) value the write path
    would actually persist, so the friendly pre-lock guard and the
    authoritative post-lock recheck can never disagree about what "the
    same title" means. `exclude_quiz_id` is the row being edited, which
    must not count as its own duplicate.

    Defined once and called from both
    ``app.blueprints.teacher.quiz_forms.QuizForm.validate_title`` (the
    friendly guard, which lets the Teacher fix the field in place) and the
    post-lock recheck in ``app/blueprints/teacher/quizzes.py`` (the
    authoritative one, taken against locked rows). Neither is the final
    defense: ``uq_quizzes_group_title`` is, and the route catches the
    resulting ``IntegrityError``.

    Comparison is left to the database, so the effective case- and
    accent-sensitivity is the column's collation
    (``utf8mb4_0900_ai_ci`` on MySQL, binary on the SQLite test backend).
    That difference is inherited from the project's existing title
    checks rather than introduced here, and **it has not been measured
    against real MySQL in this Part**.
    """
    query = Quiz.query.filter(Quiz.group_id == group_id, Quiz.title == title)
    if exclude_quiz_id is not None:
        query = query.filter(Quiz.id != exclude_quiz_id)
    return db.session.query(query.exists()).scalar()


# ---------------------------------------------------------------------------
# Teacher reads -- already authorized by the route's active assignment check
# ---------------------------------------------------------------------------


def teacher_quizzes_page(group_id, page):
    """One bounded page of a Group's quiz drafts, newest first.

    Returns ``(rows, has_next)``. Ordering is ``created_at DESC, id DESC``
    -- fully deterministic, and mirroring
    ``ix_quizzes_group_created_id``, whose ordering columns follow the
    ``group_id`` equality directly. No MySQL plan has been measured, so
    that is a reasoned design, not a proven index-ordered read.

    Fetches ``PAGE_SIZE + 1`` rows and drops the extra, so "is there a
    next page" costs no second query and discloses no total count.

    **``instructions`` is deliberately not selected.** A list row shows a
    title and two timestamps; loading up to 10,000 characters of body text
    per row to render none of it would make the page's cost grow with how
    much Teachers have written. The detail route fetches the body, once,
    for the one Quiz that is actually being read.

    Authorization is **not** performed here: the Teacher route has already
    proven an ACTIVE ``GroupTeacherAssignment`` to this exact Group before
    calling, and passes its internal id.
    """
    rows = (
        db.session.query(Quiz.public_id, Quiz.title, Quiz.status, Quiz.opens_at,
                         Quiz.closes_at, Quiz.created_at, Quiz.updated_at)
        .filter(Quiz.group_id == group_id, ~has_listening_extension())
        .order_by(Quiz.created_at.desc(), Quiz.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    has_next = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_next


def build_teacher_list_view(rows, tz_name):
    """Plain presentation dicts for the Teacher quiz list -- localized
    times and public ids only.

    No Quiz ORM row and no internal id is carried in them, so rendering
    the list can never trigger a lazy load or an ORM-driven authorization
    decision. ``instructions`` is absent because the query never fetched
    it.
    """
    return [
        {
            "public_id": row.public_id,
            "title": row.title,
            "status": row.status,
            "opens_local": to_app_local(tz_name, row.opens_at) if row.opens_at else None,
            "closes_local": to_app_local(tz_name, row.closes_at) if row.closes_at else None,
            "created_local": to_app_local(tz_name, row.created_at),
            "updated_local": to_app_local(tz_name, row.updated_at),
        }
        for row in rows
    ]


def teacher_quiz(group_id, quiz_public_id):
    """One Quiz by its own ``public_id``, **constrained to `group_id`**,
    or ``None``.

    The Group constraint is the whole point: a Quiz ``public_id`` that is
    valid only under another Group produces no row here, and the route
    turns that into the same non-disclosing 404 a genuinely missing Quiz
    produces. Returns the ORM row -- the detail page needs
    ``instructions``, and the edit path needs ``version`` -- so callers
    convert it with :func:`build_quiz_detail` before rendering.
    """
    return (
        Quiz.query.filter(
            Quiz.public_id == quiz_public_id,
            Quiz.group_id == group_id,
            ~has_listening_extension(),
        ).first()
    )


def build_quiz_detail(row, tz_name):
    """One plain presentation dict for a Quiz draft.

    ``version`` is deliberately **excluded**: it is a concurrency signal,
    not a revision number, and showing it would invite Teachers to read it
    as one. The signed edit token carries it instead, where it is
    authenticated and bound to the acting Teacher.
    """
    return {
        "public_id": row.public_id,
        "title": row.title,
        "instructions": row.instructions,
        "created_local": to_app_local(tz_name, row.created_at),
        "updated_local": to_app_local(tz_name, row.updated_at),
    }


# ===========================================================================
# Phase 4 / M04B -- ordered multiple-choice questions and their options
# ===========================================================================
#
# Same contract as everything above: read-only, Flask-independent, and
# **never** an authorization decision. Each function is handed an internal
# id the calling route has already proven -- a Quiz the acting Teacher's
# Group owns, or a Question that Quiz owns -- and every nested lookup is
# constrained by it, so a Question public_id valid only under another Quiz
# (or an Option public_id valid only under another Question) can never
# resolve through the wrong URL.
#
# There is still deliberately **no Student-facing read here.** A question
# lives inside a draft Quiz that no Student route, query, search
# projection, notification or dashboard section can reach, and none is
# stubbed. The answer key (`is_correct`) is Teacher-authored draft data
# with no scoring meaning and no Student-visible path of any kind.

#: Fixed page size for the Teacher question list on the Quiz detail page.
#: Not configurable and never client-supplied.
QUESTION_PAGE_SIZE = 20

#: How much of a question prompt the list shows. The list SELECTs exactly
#: this many characters plus one, in SQL, so a 5,000-character prompt is
#: never loaded to render a one-line preview -- the same "bounded in
#: columns, not only in rows" rule `teacher_quizzes_page` applies to
#: `instructions`. The extra character is what tells the caller the prompt
#: was longer without a second read or a COUNT.
PROMPT_PREVIEW_LENGTH = 160

#: Human labels for the two approved answer modes. Declared here, once, so
#: the list page and the editor can never describe the same mode
#: differently.
ANSWER_MODE_LABELS = {
    QuestionAnswerMode.SINGLE.value: "Single answer",
    QuestionAnswerMode.MULTIPLE.value: "Multiple answers",
}


def normalize_question_page(value):
    """Normalise a question-list ``page`` argument.

    Same rule and same bounds as :func:`normalize_page`, declared
    separately for the same reason that one is: each list owns its own
    bounds, so tightening one can never silently change another.
    """
    return normalize_page(value)


# ---------------------------------------------------------------------------
# Question reads
# ---------------------------------------------------------------------------


def teacher_questions_page(quiz_id, page):
    """One bounded page of a Quiz's questions in authored order.

    Returns ``(rows, has_next)``. Ordering is ``display_order ASC, id ASC``
    -- fully deterministic even when two rows share an order value, and
    mirroring ``ix_quiz_questions_quiz_order_id``, whose ordering columns
    follow the ``quiz_id`` equality directly. No MySQL plan has been
    measured, so that is a reasoned design, not a proven index-ordered
    read.

    Fetches ``QUESTION_PAGE_SIZE + 1`` rows and drops the extra, so "is
    there a next page" costs no second query and discloses no total count.

    **The full prompt is never loaded.** The statement selects a bounded
    ``SUBSTR`` prefix of ``PROMPT_PREVIEW_LENGTH + 1`` characters, so the
    page's cost does not grow with how much a Teacher wrote into each
    question. The editor fetches the whole prompt, once, for the one
    question actually being edited.

    Each row carries the internal ``id`` so the caller can resolve the
    per-question option counts in **one** further bounded query, and
    ``version`` so the route can mint each row's Move Up / Move Down
    token. Both are consumed outside the presentation layer: the id is
    never placed in a dict or a page, and the version reaches the page
    only inside a signed token (see :func:`build_question_summaries`).

    Authorization is **not** performed here: the route has already proven
    an ACTIVE ``GroupTeacherAssignment`` to the Quiz's Group and the
    Quiz's ownership by that Group, and passes the verified internal id.
    """
    rows = (
        db.session.query(
            QuizQuestion.id,
            QuizQuestion.public_id,
            QuizQuestion.answer_mode,
            QuizQuestion.display_order,
            QuizQuestion.version,
            func.substr(QuizQuestion.prompt, 1, PROMPT_PREVIEW_LENGTH + 1).label(
                "prompt_preview"
            ),
        )
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.display_order.asc(), QuizQuestion.id.asc())
        .offset((page - 1) * QUESTION_PAGE_SIZE)
        .limit(QUESTION_PAGE_SIZE + 1)
        .all()
    )
    has_next = len(rows) > QUESTION_PAGE_SIZE
    return rows[:QUESTION_PAGE_SIZE], has_next


def active_option_counts(question_ids):
    """``{question_id: (active_count, correct_count)}`` for at most one
    page of questions.

    **One** grouped statement for the whole page rather than a lookup per
    row, which would be an N+1 -- the same arrangement M02 uses for the
    Assignment freeze badge and M03 for the feedback indicator. The
    ``IN`` list is at most ``QUESTION_PAGE_SIZE`` ids, so the read stays
    bounded no matter how many questions or options a Quiz holds, and the
    relationship (``QuizQuestion.options``) is never iterated.

    Retired options are excluded: a retired row is history, and counting
    it would misreport how many answers a question currently offers.

    A question with no rows at all is simply absent from the mapping;
    callers default it to ``(0, 0)``.
    """
    ids = [qid for qid in question_ids if qid is not None]
    if not ids:
        return {}
    rows = (
        db.session.query(
            QuestionOption.question_id,
            func.count(QuestionOption.id),
            func.sum(case((QuestionOption.is_correct.is_(True), 1), else_=0)),
        )
        .filter(
            QuestionOption.question_id.in_(ids),
            QuestionOption.is_active.is_(True),
        )
        .group_by(QuestionOption.question_id)
        .all()
    )
    return {
        question_id: (int(active or 0), int(correct or 0))
        for question_id, active, correct in rows
    }


def build_question_summaries(rows, counts, first_number):
    """Plain presentation dicts for the Teacher question list.

    `first_number` is the 1-based position of the page's first row in the
    **complete** Quiz order, so the numbers a Teacher reads continue
    across pages instead of restarting at 1 on page 2.

    No ORM row and no internal id is carried in the result, so rendering
    the list can never trigger a lazy load or an ORM-driven authorization
    decision. ``prompt_preview`` is the bounded prefix the query already
    fetched; ``prompt_truncated`` says whether more text exists, which the
    template renders as an ellipsis rather than by measuring anything
    itself.

    ``display_order`` is deliberately absent: it is a server-owned storage
    detail that may legitimately contain gaps, and showing it would invite
    a Teacher to read it as the question number. The position is what they
    see.
    """
    summaries = []
    for offset, row in enumerate(rows):
        preview = row.prompt_preview or ""
        truncated = len(preview) > PROMPT_PREVIEW_LENGTH
        active_count, correct_count = counts.get(row.id, (0, 0))
        summaries.append(
            {
                "public_id": row.public_id,
                "number": first_number + offset,
                "prompt_preview": preview[:PROMPT_PREVIEW_LENGTH],
                "prompt_truncated": truncated,
                "answer_mode": row.answer_mode,
                "answer_mode_label": ANSWER_MODE_LABELS.get(
                    row.answer_mode, row.answer_mode
                ),
                "active_option_count": active_count,
                "correct_option_count": correct_count,
            }
        )
    return summaries


def teacher_question(quiz_id, question_public_id):
    """One Question by its own ``public_id``, **constrained to `quiz_id`**,
    or ``None``.

    The Quiz constraint is the whole point: a Question ``public_id`` that
    is valid only under another Quiz -- in this Group or any other --
    produces no row here, and the route turns that into the same
    non-disclosing 404 a genuinely missing Question produces. Returns the
    ORM row because the editor needs the full ``prompt`` and the write
    path needs ``version`` and ``display_order``.
    """
    return QuizQuestion.query.filter_by(
        public_id=question_public_id, quiz_id=quiz_id
    ).first()


def next_question_display_order(quiz_id):
    """The ``display_order`` a newly created Question should take:
    strictly after the Quiz's current highest, or ``0`` for the first.

    Gaps are acceptable, so this never renumbers existing rows -- the same
    append rule M10 uses for Units. It must be read **under the held Quiz
    lock**, which is what makes two concurrent creates append rather than
    collide.
    """
    highest = (
        db.session.query(func.max(QuizQuestion.display_order))
        .filter(QuizQuestion.quiz_id == quiz_id)
        .scalar()
    )
    return 0 if highest is None else highest + 1


def neighbour_question(quiz_id, display_order, question_id, direction):
    """The Question immediately before (``"up"``) or after (``"down"``)
    the given one in the complete Quiz order, or ``None`` at a boundary.

    **Bounded to one row**, and expressed as a keyset comparison on
    exactly the ordering the list uses -- ``(display_order, id)`` -- so
    it is correct across ``display_order`` gaps, correct when two rows
    share an order value, and correct when the moved Question sits on a
    different page from its neighbour. Nothing loads the Quiz's whole
    question set, which is what makes this usable on a long draft; the
    shared-order comparison is spelled out rather than written as a row
    constructor so it renders identically on MySQL and the SQLite test
    backend.

    Must be read under the held Quiz lock; the caller then locks the two
    Question rows in ascending **internal id** -- never visual order --
    so two co-teachers moving adjacent questions cannot deadlock.
    """
    query = QuizQuestion.query.filter(QuizQuestion.quiz_id == quiz_id)
    if direction == MOVE_UP:
        return (
            query.filter(
                or_(
                    QuizQuestion.display_order < display_order,
                    and_(
                        QuizQuestion.display_order == display_order,
                        QuizQuestion.id < question_id,
                    ),
                )
            )
            .order_by(QuizQuestion.display_order.desc(), QuizQuestion.id.desc())
            .limit(1)
            .first()
        )
    return (
        query.filter(
            or_(
                QuizQuestion.display_order > display_order,
                and_(
                    QuizQuestion.display_order == display_order,
                    QuizQuestion.id > question_id,
                ),
            )
        )
        .order_by(QuizQuestion.display_order.asc(), QuizQuestion.id.asc())
        .limit(1)
        .first()
    )


# ---------------------------------------------------------------------------
# Option reads
# ---------------------------------------------------------------------------


def active_options_ordered(question_id):
    """A Question's **active** options in authored order, hard-limited to
    ``MAX_ACTIVE_OPTIONS + 1`` rows.

    Returns at most 9 rows for an 8-option maximum. The ninth is not a
    row the caller should render: it is the *detection* of a structurally
    invalid question, obtained without loading an unlimited option set --
    see :func:`active_option_set_is_invalid`. A question found in that
    state is refused safely by the write path and never silently truncated
    or "repaired".

    Ordering is ``display_order ASC, id ASC``, matching
    ``ix_question_options_question_active_order_id`` (two equality columns,
    then the ordering columns), and deterministic even if two rows share
    an order value. Retired options are excluded everywhere: they are
    history, never a current answer.
    """
    return (
        QuestionOption.query.filter(
            QuestionOption.question_id == question_id,
            QuestionOption.is_active.is_(True),
        )
        .order_by(QuestionOption.display_order.asc(), QuestionOption.id.asc())
        .limit(MAX_ACTIVE_OPTIONS + 1)
        .all()
    )


def active_option_ids(question_id):
    """The internal ids of a Question's **active** options, in authored
    order, hard-limited to ``MAX_ACTIVE_OPTIONS + 1``.

    The write path's read: it exists so the route can lock those rows
    ``FOR UPDATE`` in ascending internal id and then decide on the
    *locked* entities, rather than on entities an earlier read may have
    cached. Projecting ids only keeps that step cheap, and the same
    ``+ 1`` bound still detects a structurally invalid question without an
    unbounded read.
    """
    rows = (
        db.session.query(QuestionOption.id)
        .filter(
            QuestionOption.question_id == question_id,
            QuestionOption.is_active.is_(True),
        )
        .order_by(QuestionOption.display_order.asc(), QuestionOption.id.asc())
        .limit(MAX_ACTIVE_OPTIONS + 1)
        .all()
    )
    return [row.id for row in rows]


def active_option_set_is_invalid(options):
    """True when the rows :func:`active_options_ordered` returned describe
    a question that cannot be edited safely.

    Two cases, both structural rather than user error: more active options
    than the approved maximum (the extra row the bounded query fetched
    proves it without an unbounded read), or fewer than the approved
    minimum. Either means the stored data does not satisfy the rule the
    editor is about to validate against, so the write path refuses with a
    generic message instead of guessing which rows to drop.
    """
    return len(options) > MAX_ACTIVE_OPTIONS or len(options) < MIN_ACTIVE_OPTIONS


def build_option_rows(options):
    """Plain presentation dicts for one Question's active options.

    Public identifiers only -- an option is addressed in a form by its
    ``public_id``, never by its internal id, and never by a positional
    index that a reorder could silently repoint at a different row.
    """
    return [
        {
            "public_id": option.public_id,
            "text": option.option_text,
            "is_correct": bool(option.is_correct),
        }
        for option in options
    ]


def build_question_editor(question, options):
    """One plain presentation dict for the question editor page.

    ``version`` is deliberately excluded, exactly as it is from
    :func:`build_quiz_detail`: it is a concurrency signal, not a revision
    number. The signed edit token carries it instead, where it is
    authenticated and bound to the acting Teacher, alongside the ordered
    option identifiers the form was actually rendered against.
    """
    return {
        "public_id": question.public_id,
        "prompt": question.prompt,
        "answer_mode": question.answer_mode,
        "answer_mode_label": ANSWER_MODE_LABELS.get(
            question.answer_mode, question.answer_mode
        ),
        "options": build_option_rows(options),
    }


# ===========================================================================
# Phase 4 / M04D -- Student visibility, navigation, and Teacher attempt review
# ===========================================================================
#
# The M04A/M04B statement that this module contains no Student-facing read
# is **superseded here**: M04D publishes Quizzes, so eligible Students do
# get reads -- but only through the one fully scoped query below, and
# never one that could return a draft.
#
# The answer key is deliberately absent from every Student-facing function
# and from every dict they build. A Student page cannot leak
# `is_correct` because no Student read ever selects it.


def student_visible_quiz_query(student_id, reference_utc, listening=False):
    """The one shared, fully scoped base query behind every Student read.

    **Authorization lives in the SQL ``WHERE`` clause**, exactly as it
    does for M01 Assignments: a Quiz is never loaded broadly and filtered
    in Python afterwards. Defining it once is deliberate -- the list, the
    detail page and every attempt route must agree exactly on what
    "visible" means, so a future change cannot tighten one path and leave
    another open. The full formula is::

        User is that Student, with the Student role and an active account
        AND Enrollment(student, group).status == active
        AND AcademicTerm / Level / Course / Group .status == active
        AND Quiz.status == published
        AND Quiz.opens_at <= reference moment

    The ``users`` join is not redundant with the session: a foreign key
    into ``users`` proves the row exists, never that it is still a Student
    or still active.

    A **closed** Quiz stays visible on purpose. What ``closes_at``
    withdraws is the ability to *start* an attempt, never the ability to
    reach a receipt -- the same rule M01 applies to a past-due Assignment.
    """
    return (
        db.session.query(Quiz, Group, Course, Level, AcademicTerm)
        .join(Group, Quiz.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(Enrollment, Enrollment.group_id == Group.id)
        .join(User, Enrollment.student_id == User.id)
        .filter(
            User.id == student_id,
            User.role == UserRole.STUDENT.value,
            User.status == UserStatus.ACTIVE.value,
            Enrollment.status == EnrollmentStatus.ACTIVE.value,
            AcademicTerm.status == _ACTIVE,
            Level.status == _ACTIVE,
            Course.status == _ACTIVE,
            Group.status == _ACTIVE,
            Quiz.status == QuizStatus.PUBLISHED.value,
            Quiz.opens_at <= reference_utc,
            # Phase 4 / M05. The ONE formula, with the one scope that
            # separates the two surfaces: `listening=False` is the
            # ordinary Quiz surface and `listening=True` the Listening
            # one. Parameterizing the shared query rather than writing a
            # second one is deliberate -- two definitions of "visible"
            # could be tightened separately and drift apart, which is
            # exactly what this function exists to prevent.
            has_listening_extension() if listening else ~has_listening_extension(),
        )
    )


def student_quizzes_page(student_id, reference_utc, page):
    """One bounded page of the Quizzes this Student may currently see.

    Ordering is ``closes_at DESC, id DESC`` -- the work whose window is
    still open or most recently closed first, which is what a Student
    looking for "what do I have to do" needs. Fully deterministic.

    Fetches ``PAGE_SIZE + 1`` rows and drops the extra, so "is there a
    next page" costs no second query and discloses no total count.
    """
    rows = (
        student_visible_quiz_query(student_id, reference_utc)
        .order_by(Quiz.closes_at.desc(), Quiz.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    has_next = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_next


def student_quiz(student_id, group_public_id, quiz_public_id, reference_utc):
    """One visible Quiz plus its authorized hierarchy context, or ``None``.

    Adds only the two nested public-id predicates to the shared visibility
    query, so a draft, a not-yet-open Quiz, a Quiz whose ``public_id``
    belongs to another Group, a withdrawn or missing Enrollment, an
    archived ancestor and a simply non-existent id all produce no row --
    and the route turns every one of them into the identical
    non-disclosing 404.
    """
    return (
        student_visible_quiz_query(student_id, reference_utc)
        .filter(
            Quiz.public_id == quiz_public_id,
            Group.public_id == group_public_id,
        )
        .first()
    )


def attempt_counts_by_quiz(student_id, quiz_ids):
    """``{quiz_id: attempt_count}`` for at most one page of Quizzes.

    **One** grouped statement for the whole page rather than a lookup per
    row, so the Student list's cost does not grow with how many Quizzes it
    shows. Resolves through ``uq_quiz_attempts_quiz_student_number``'s
    leftmost columns.
    """
    ids = [quiz_id for quiz_id in quiz_ids if quiz_id is not None]
    if not ids:
        return {}
    rows = (
        db.session.query(QuizAttempt.quiz_id, func.count(QuizAttempt.id))
        .filter(QuizAttempt.quiz_id.in_(ids), QuizAttempt.student_id == student_id, active_episode_record(QuizAttempt))
        .group_by(QuizAttempt.quiz_id)
        .all()
    )
    return {quiz_id: int(count) for quiz_id, count in rows}


def build_student_quiz_item(row, tz_name, reference_utc, attempts_used=0):
    """One plain presentation dict for a Student-visible Quiz.

    Only display strings, localized times, public ids and the Student's
    own attempt count. **No answer key, no question content, no
    ``is_correct`` and no internal id** -- none of that is even selected
    by the query behind it.
    """
    from app.services.quiz_attempts import STATE_LABELS, availability_state

    quiz, group, course, level, term = row
    state = availability_state(quiz, reference_utc)
    return {
        "public_id": quiz.public_id,
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


def build_student_quiz_view(rows, tz_name, reference_utc, counts):
    """:func:`build_student_quiz_item` over one page of rows."""
    return [
        build_student_quiz_item(row, tz_name, reference_utc, counts.get(row[0].id, 0))
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Taking an attempt -- bounded navigation and one question at a time
# ---------------------------------------------------------------------------


def question_navigation(quiz_id, question_public_id):
    """``(position, total, previous_public_id, next_public_id)`` for one
    question, or ``None`` when it does not belong to this Quiz.

    **One bounded statement** answers all four: at most
    ``MAX_QUIZ_QUESTIONS + 1`` ``(id, public_id)`` pairs in authored
    order. Position and neighbours are then list arithmetic, so Previous
    and Next are correct across ``display_order`` gaps and across the
    Teacher list's page boundaries alike -- they follow the complete
    authored order, never the rendered page.

    Nothing here loads a prompt or an option, and the internal ids never
    leave this function.
    """
    rows = (
        db.session.query(QuizQuestion.public_id)
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.display_order.asc(), QuizQuestion.id.asc())
        .limit(MAX_QUIZ_QUESTIONS + 1)
        .all()
    )
    public_ids = [row.public_id for row in rows]
    try:
        index = public_ids.index(question_public_id)
    except ValueError:
        return None
    total = len(public_ids)
    return (
        index + 1,
        total,
        public_ids[index - 1] if index > 0 else None,
        public_ids[index + 1] if index + 1 < total else None,
    )


def first_question_public_id(quiz_id):
    """The Quiz's first question in authored order, or ``None``."""
    row = (
        db.session.query(QuizQuestion.public_id)
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.display_order.asc(), QuizQuestion.id.asc())
        .limit(1)
        .first()
    )
    return row.public_id if row is not None else None


def student_question_index(quiz_id):
    """Bounded authored order for the own-attempt question shortcuts.

    The caller authorizes the Quiz/attempt. Only identifiers are selected;
    internal ids stay in the route's progress lookup, never the rendered DTO.
    No other prompt, option text or answer key is loaded for navigation.
    """
    return (
        db.session.query(QuizQuestion.id, QuizQuestion.public_id)
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.display_order.asc(), QuizQuestion.id.asc())
        .limit(MAX_QUIZ_QUESTIONS + 1)
        .all()
    )


def attempt_selected_option_ids(attempt_id, question_id):
    """The option ids this attempt currently has saved for one question.

    Bounded by the option maximum, and restricted to **active** options so
    a retired row could never be re-rendered as a live selection.
    """
    rows = (
        db.session.query(QuizAnswerSelection.option_id)
        .join(QuizAnswer, QuizAnswer.id == QuizAnswerSelection.answer_id)
        .join(QuestionOption, QuestionOption.id == QuizAnswerSelection.option_id)
        .filter(
            QuizAnswer.attempt_id == attempt_id,
            QuizAnswer.question_id == question_id,
            QuestionOption.is_active.is_(True),
        )
        .limit(MAX_ACTIVE_OPTIONS + 1)
        .all()
    )
    return {row.option_id for row in rows}


def build_student_option_rows(options, selected_option_ids):
    """The option rows a Student sees while answering.

    **``is_correct`` is deliberately not read and not carried.** The
    answer key never reaches a Student page, a form value, a URL or a
    token -- not while the Quiz is open, and not after it closes.
    """
    return [
        {
            "public_id": option.public_id,
            "text": option.option_text,
            "selected": option.id in selected_option_ids,
        }
        for option in options
    ]


def answered_question_ids(attempt_id, question_ids):
    """Which of these questions the attempt has a saved answer for.

    One bounded statement over at most one Quiz's worth of ids, used for
    the progress indicator and the unanswered-question confirmation. An
    answer whose selections were cleared is unanswered; retain its history
    row, but require at least one selection. Listening's nonempty saves keep
    their existing meaning.
    """
    if not question_ids:
        return set()
    rows = (
        db.session.query(QuizAnswer.question_id)
        .filter(
            QuizAnswer.attempt_id == attempt_id,
            QuizAnswer.question_id.in_(question_ids),
            db.session.query(QuizAnswerSelection.id)
            .filter(QuizAnswerSelection.answer_id == QuizAnswer.id)
            .exists(),
        )
        .all()
    )
    return {row.question_id for row in rows}


# ---------------------------------------------------------------------------
# Teacher attempt review
# ---------------------------------------------------------------------------


ATTEMPT_PAGE_SIZE = 20


def teacher_attempts_page(quiz_id, page):
    """One bounded page of a Quiz's attempts, newest first.

    Returns ``(rows, has_next)`` where each row carries the attempt
    columns **and** the Student's display name from the same joined
    statement -- so the page costs a fixed number of queries no matter how
    many attempts it shows, and never a name lookup per row.

    Ordering is ``started_at DESC, id DESC``, mirroring
    ``ix_quiz_attempts_quiz_started_id``. ``LIMIT ATTEMPT_PAGE_SIZE + 1``
    supplies the has-next flag with no ``COUNT`` over a table that grows
    with every attempt ever taken.

    The ``users`` join also re-proves the row belongs to a Student: a
    foreign key never proves a role.
    """
    rows = (
        db.session.query(
            QuizAttempt.public_id,
            QuizAttempt.attempt_number,
            QuizAttempt.status,
            QuizAttempt.started_at,
            QuizAttempt.deadline_at,
            QuizAttempt.submitted_at,
            QuizAttempt.correct_count,
            QuizAttempt.total_questions,
            User.full_name.label("student_name"),
        )
        .join(User, QuizAttempt.student_id == User.id)
        .filter(
            QuizAttempt.quiz_id == quiz_id,
            User.role == UserRole.STUDENT.value,
        )
        .order_by(QuizAttempt.started_at.desc(), QuizAttempt.id.desc())
        .offset((page - 1) * ATTEMPT_PAGE_SIZE)
        .limit(ATTEMPT_PAGE_SIZE + 1)
        .all()
    )
    has_next = len(rows) > ATTEMPT_PAGE_SIZE
    return rows[:ATTEMPT_PAGE_SIZE], has_next


def teacher_attempt(quiz_id, attempt_public_id):
    """One attempt by its own ``public_id``, **constrained to `quiz_id`**,
    or ``None``.

    The Quiz constraint is the whole point: an attempt ``public_id`` valid
    only under another Quiz produces no row, and the route turns that into
    the same non-disclosing 404 a missing attempt produces.
    """
    return QuizAttempt.query.filter_by(
        public_id=attempt_public_id, quiz_id=quiz_id
    ).first()


def attempt_review_rows(quiz_id, attempt_id):
    """Everything the Teacher attempt-detail page renders, in **four**
    bounded statements regardless of Quiz size.

    Returns a list of per-question dicts carrying the prompt, the answer
    mode, every active option with both what the Student selected **and**
    what the Teacher authored as correct, and the derived per-question
    result. A Teacher is authorized to see the answer key; a Student
    never is, which is why this builder is separate from
    :func:`build_student_option_rows` rather than a flag on it.

    Never one query per question or per answer.
    """
    from app.models import QuestionAnswerMode
    from app.services.quiz_attempts import (
        correct_option_ids_by_question,
        selected_option_ids_by_question,
    )

    questions = (
        db.session.query(
            QuizQuestion.id,
            QuizQuestion.public_id,
            QuizQuestion.prompt,
            QuizQuestion.answer_mode,
        )
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.display_order.asc(), QuizQuestion.id.asc())
        .limit(MAX_QUIZ_QUESTIONS + 1)
        .all()
    )
    question_ids = [question.id for question in questions]
    if not question_ids:
        return []

    options_by_question = defaultdict(list)
    option_rows = (
        db.session.query(
            QuestionOption.id,
            QuestionOption.question_id,
            QuestionOption.public_id,
            QuestionOption.option_text,
            QuestionOption.is_correct,
        )
        .filter(
            QuestionOption.question_id.in_(question_ids),
            QuestionOption.is_active.is_(True),
        )
        .order_by(QuestionOption.display_order.asc(), QuestionOption.id.asc())
        .all()
    )
    for option in option_rows:
        options_by_question[option.question_id].append(option)

    answer_key = correct_option_ids_by_question(question_ids)
    selected = selected_option_ids_by_question(attempt_id)

    review = []
    for position, question in enumerate(questions, start=1):
        picked = selected.get(question.id, set())
        expected = answer_key.get(question.id, set())
        review.append(
            {
                "number": position,
                "public_id": question.public_id,
                "prompt": question.prompt,
                "answer_mode": question.answer_mode,
                "answer_mode_label": ANSWER_MODE_LABELS.get(
                    question.answer_mode, question.answer_mode
                ),
                "answered": bool(picked),
                "is_correct": bool(expected) and picked == expected,
                "options": [
                    {
                        "public_id": option.public_id,
                        "text": option.option_text,
                        "selected": option.id in picked,
                        "is_correct": bool(option.is_correct),
                    }
                    for option in options_by_question.get(question.id, [])
                ],
            }
        )
    return review


def student_result_rows(quiz_id, attempt_id):
    """The per-question result a **Student** may see for their own
    finalized attempt.

    Deliberately a different builder from :func:`attempt_review_rows`:
    it reports whether each question was right or wrong and nothing else.
    No option text, no ``is_correct`` flag, no selected/unselected marking
    and no authored key -- so the answer key cannot leak through the
    results page even after the Quiz closes. Making that a separate
    function rather than a boolean argument is the point: there is no
    flag anyone can pass the wrong way.

    Three bounded statements regardless of Quiz size.
    """
    from app.services.quiz_attempts import (
        correct_option_ids_by_question,
        selected_option_ids_by_question,
    )

    questions = (
        db.session.query(QuizQuestion.id)
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.display_order.asc(), QuizQuestion.id.asc())
        .limit(MAX_QUIZ_QUESTIONS + 1)
        .all()
    )
    question_ids = [question.id for question in questions]
    if not question_ids:
        return []

    answer_key = correct_option_ids_by_question(question_ids)
    selected = selected_option_ids_by_question(attempt_id)

    rows = []
    for position, question_id in enumerate(question_ids, start=1):
        picked = selected.get(question_id, set())
        expected = answer_key.get(question_id, set())
        rows.append(
            {
                "number": position,
                "answered": bool(picked),
                "is_correct": bool(expected) and picked == expected,
            }
        )
    return rows

from app.services.episode_queries import active_episode_record
