"""Teacher authoring and grading of one Group's gradebook
(Phase 4 / M08).

Group- and item-centered routes only, every object addressed by
``public_id`` and no internal numeric id anywhere in a URL, a form value,
a signed token or the rendered HTML::

    GET       /teacher/grades
    GET       /teacher/groups/<gp>/gradebook
    GET|POST  /teacher/groups/<gp>/gradebook/categories/new
    GET|POST  /teacher/groups/<gp>/gradebook/categories/<cp>/edit
    GET|POST  /teacher/groups/<gp>/gradebook/items/new
    GET       /teacher/groups/<gp>/gradebook/items/<ip>
    GET|POST  /teacher/groups/<gp>/gradebook/items/<ip>/edit
    GET|POST  /teacher/groups/<gp>/gradebook/items/<ip>/scores
    POST      /teacher/groups/<gp>/gradebook/items/<ip>/release

The only flat route is the Teacher's own overview of the Groups they are
assigned to; everything else is nested under a Group. There is
deliberately **no delete, archive, unrelease, reopen, restore, duplicate,
import, recalculate, bulk-generate or export action** for a category, an
item or a record -- none of that exists server-side either, and no
placeholder is left for one.

**The gradebook is the source of truth for Teacher-entered grades.** No
score column was added to ``submissions``, ``quiz_attempts``,
``speaking_submissions``, ``attendance_records``, ``submission_feedback``
or ``speaking_feedback``, and nothing here reads a result out of any of
them. A Quiz attempt already computes its own assessment result; a
Teacher who wants that number in the gradebook creates a ``quiz`` item,
looks at the attempt, and enters it **deliberately**. There is no import
button and no sync job anywhere in M08.

**The roster is captured once and then frozen.** Creating an item inserts
the item and one ungraded record per eligible active Student in the same
transaction. Afterwards the *set* of records never changes: a later
enrollment adds nothing, and a withdrawal, suspension, re-assignment or
Group archival removes nothing. A Group with no eligible active Student
cannot have an item created at all.

**Draft versus released.** A draft item -- its structure, its scores and
its comments -- is Teacher-only working material that no Student route,
query or page can reach. Release makes that item's scores and comments
visible to the Students on its captured roster, and freezes its
structure permanently. Scores stay **correctable** after release, because
an honest gradebook has to let a Teacher fix a number they got wrong; what
a correction may never do is blank a score that a Student has already
been shown.

**Two different protections, both preserved.** The lock chain
(``app/services/grade_transactions.py``) decides against the rows as they
are *now*; the signed exact-shape tokens decide against the state the
form was *opened* on. Neither replaces the other: the locks stop two
co-teachers interleaving a write, and the token turns the loser of that
race into an explicit "reload and review" rejection instead of a silent
overwrite.

**Authorization.** ``roles_required(TEACHER)`` handles anonymous (login
redirect) and non-Teacher (403). Object authorization is then server-side
and nested, reusing the exact helpers every other Teacher surface uses:
``_teacher_group_or_404`` proves an **active**
``GroupTeacherAssignment`` to the Group in the URL, and every nested
lookup is constrained by that Group in SQL -- through the *category* for
an item, because ``grade_items`` deliberately carries no ``group_id``. A
missing Group, a missing category or item, one belonging to another
Group, an unassigned Teacher and a removed assignment all return the same
non-disclosing **404** -- never a 403, and never a hint that the object
exists. Multiple active assigned Teachers are equal collaborators on the
same gradebook.

**Reading is historical; writing is not.** The overview, the gradebook,
the item detail and the score sheet stay readable for an actively
assigned Teacher even when the Group or an academic ancestor is archived,
so a term's grades can always be read back. Creating, editing, scoring
and releasing additionally require an operational chain, re-checked
against the **locked** rows.
"""

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.assignments import _private_no_store
from app.blueprints.teacher.grade_forms import (
    COMMENT_MAX,
    GradeCategoryForm,
    GradeItemForm,
    parse_score_submission,
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
    AcademicStatus,
    Assignment,
    BASIS_POINTS_TOTAL,
    Course,
    GRADE_CATEGORY_TITLE_MAX_LENGTH,
    GRADE_ITEM_TITLE_MAX_LENGTH,
    GradeCategory,
    Group,
    Quiz,
    SpeakingActivity,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.grade_calculations import (
    OVERALL_CATEGORY_NOT_STARTED,
    OVERALL_MISSING_SCORES,
    OVERALL_NO_CATEGORIES,
    OVERALL_WEIGHTS_INCOMPLETE,
    configuration_blockers,
    format_points,
    format_weight,
    remaining_weight,
    total_weight,
)
from app.services.grade_queries import (
    LINKED_SOURCE_COLUMNS,
    RELEASE_MISSING_SCORES,
    RELEASE_NO_ROSTER,
    RELEASE_SOURCE_DRAFT,
    RELEASE_SOURCE_MISSING,
    RELEASE_WEIGHTS_INCOMPLETE,
    SOURCE_KIND_LABELS,
    UNLINKED_SOURCE_KINDS,
    assignment_for_group,
    build_gradebook_view,
    build_record_view,
    build_student_totals,
    calculation_categories,
    captured_record_ids,
    captured_student_ids,
    category_for_group,
    category_rows,
    category_weight_rows,
    duplicate_category_title_exists,
    eligible_roster_rows,
    item_for_group,
    item_records,
    item_release_blockers,
    item_rows,
    quiz_for_group,
    record_counts_for_items,
    release_blockers,
    released_scores_for_group,
    source_choices,
    speaking_activity_for_group,
    teacher_group_cards,
    teacher_is_actively_assigned,
)
from app.services.grade_transactions import (
    category_has_released_item,
    create_item_with_roster,
    eligible_locked_student_ids,
    lock_gradebook_chain,
    source_belongs_to_group,
    source_is_published_row,
)
from app.services.schedule_occurrences import utc_reference_now

_ACTIVE = AcademicStatus.ACTIVE.value
_TEACHER = UserRole.TEACHER.value
_USER_ACTIVE = UserStatus.ACTIVE.value


def _write_moment():
    """The **authoritative** naive-UTC moment for one gradebook write,
    truncated to whole seconds.

    ``DATETIME`` on MySQL carries fractional precision 0 and *rounds* an
    excess fraction rather than truncating it, so a value carrying
    microseconds would be stored as a different instant from the one the
    request used. Read only **after** every lock that could have blocked,
    so a request that waited behind a competing co-teacher records the
    moment it actually wrote.

    Declared here rather than imported so this surface's clock can be
    injected on its own in tests, exactly as each blueprint already does.
    """
    return utc_reference_now().replace(microsecond=0)


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


# ======================================================================
# Teacher-facing sentences, declared once each
# ======================================================================
#
# Every rule a Teacher can hit is stated in exactly one place, so the
# friendly pre-lock check and the authoritative post-lock check can never
# explain the same rule differently.

_NOT_OPERATIONAL_MESSAGE = (
    "The gradebook can only be changed while the group and its academic term, course, and "
    "level are all active. Existing grades stay readable."
)
_OPERATIONAL_WORDING = (
    "The gradebook can only be changed",
    "Existing grades stay readable.",
)
_GROUP_CHANGED_MESSAGE = (
    "This group changed while you were working. Reload the page and try again."
)
_STALE_MESSAGE = (
    "This gradebook was changed by someone else since this page was opened. Your changes "
    "were not saved. Please reload, read the current values, and make your change against "
    "them."
)
_INTEGRITY_MESSAGE = (
    "That change could not be saved because the data changed at the same moment. Nothing "
    "was written. Please reload and try again."
)

# Every write below catches ``IntegrityError`` and nothing else, which is
# the established convention in this project -- and which is correct here
# for a reason worth stating.
#
# On MySQL a **UNIQUE** violation arrives as ``IntegrityError`` (errno
# 1062) and so does a refused ``ON DELETE`` (errno 1451), which is exactly
# the duplicate-title and lost-race case these handlers exist for. A
# **CHECK** violation, by contrast, arrives as ``OperationalError``
# (errno 3819) and is deliberately *not* caught: it is unreachable from
# these routes, because every value a CHECK bounds is already bounded
# twice before the write -- at the request boundary by
# ``app/blueprints/teacher/grade_forms.py`` and again by the model's own
# ``@validates`` guard. A CHECK that fired here would mean one of those
# two layers had a bug, and swallowing it into a friendly sentence would
# hide exactly the defect worth seeing. Verified against the authorized
# development MySQL database rather than assumed.
_DUPLICATE_CATEGORY_MESSAGE = (
    "This group already has a grade category with this name. Nothing was saved. Choose a "
    "different name."
)
_CATEGORY_FROZEN_MESSAGE = (
    "This category already has a released grade item, so its name and weight can no longer "
    "be changed. Students have already been shown results calculated with this weight, and "
    "changing it now would silently rewrite what those results meant. Create a new category "
    "instead."
)
_WEIGHT_OVER_MESSAGE = (
    "This group's grade categories may not add up to more than 100%. Nothing was saved."
)
_NO_CATEGORY_MESSAGE = (
    "Create at least one grade category before adding a grade item. Every grade item belongs "
    "to a weighted category."
)
_EMPTY_ROSTER_MESSAGE = (
    "This group has no active enrolled students, so there is nobody to grade. A grade item is "
    "never created empty."
)
_SOURCE_MISSING_MESSAGE = (
    "That item is not one of this group's. Please choose one from the list and try again."
)
_ITEM_RELEASED_MESSAGE = (
    "This grade item has been released, so its name, category, kind, linked item and maximum "
    "points can no longer be changed. Released grades are permanent — you can still correct a "
    "student's score or comment."
)
_ALREADY_RELEASED_MESSAGE = (
    "This grade item was already released. Nothing was changed."
)
_NO_CHANGES_MESSAGE = "Nothing was changed, so nothing was saved."
_SCORES_SAVED_MESSAGE = "Scores saved."
_SCORES_SAVED_RELEASED_MESSAGE = (
    "Scores corrected. The students on this item can see the updated results now."
)
_RELEASE_CONFIRM_MESSAGE = (
    "Please tick the confirmation box before releasing. Releasing cannot be undone."
)
_RELEASED_OK_MESSAGE = (
    "Grade item released. The students on its list can now see their own score and comment."
)
_ROSTER_BROKEN_MESSAGE = (
    "This grade item's student list could not be read completely, so nothing was changed. "
    "Nothing has been deleted. Please reload, and ask an administrator to review this item if "
    "the problem continues."
)
_CATEGORY_SAVED_MESSAGE = "Grade category saved."
_ITEM_SAVED_MESSAGE = "Grade item saved."

#: The Teacher's wording for each release blocker. Declared beside the
#: codes they name (``app/services/grade_queries.py``) so the readiness
#: panel and the rejected POST say the same thing.
_RELEASE_MESSAGES = {
    RELEASE_NO_ROSTER: (
        "This grade item has no students on it, so there is nothing to release."
    ),
    RELEASE_MISSING_SCORES: (
        "Every student on this item needs a score before it can be released. Fill in the "
        "missing ones first."
    ),
    RELEASE_WEIGHTS_INCOMPLETE: (
        "This group's grade categories must add up to exactly 100% before any grade item can "
        "be released. Adjust the category weights first."
    ),
    RELEASE_SOURCE_MISSING: (
        "The item this grade is linked to could not be read, or is no longer one of this "
        "group's. Nothing was released."
    ),
    RELEASE_SOURCE_DRAFT: (
        "The item this grade is linked to is still a draft. Publish it first — a student must "
        "never be shown a grade for work they have not been given."
    ),
}

#: The Teacher's wording for each "no overall grade yet" reason. The
#: Student surface declares its own, deliberately different, wording for
#: the same codes: a Teacher needs to know what to fix, a Student needs to
#: know their term is not finished being graded.
_OVERALL_MESSAGES = {
    OVERALL_NO_CATEGORIES: (
        "This gradebook has no categories yet, so no overall grade can be calculated."
    ),
    OVERALL_WEIGHTS_INCOMPLETE: (
        "The category weights do not add up to exactly 100%, so no overall grade can be "
        "calculated yet."
    ),
    OVERALL_CATEGORY_NOT_STARTED: (
        "At least one category has no released grade item yet, so no overall grade can be "
        "calculated yet."
    ),
    OVERALL_MISSING_SCORES: (
        "This student does not have a score on every released grade item, so no overall grade "
        "can be calculated for them."
    ),
}


# ======================================================================
# URLs
# ======================================================================


def _gradebook_url(group_public_id):
    return url_for("teacher.group_gradebook", group_public_id=group_public_id)


def _category_new_url(group_public_id):
    return url_for("teacher.grade_category_create", group_public_id=group_public_id)


def _category_edit_url(group_public_id, category_public_id):
    return url_for(
        "teacher.grade_category_edit",
        group_public_id=group_public_id,
        category_public_id=category_public_id,
    )


def _item_new_url(group_public_id):
    return url_for("teacher.grade_item_create", group_public_id=group_public_id)


def _item_url(group_public_id, item_public_id):
    return url_for(
        "teacher.grade_item_detail",
        group_public_id=group_public_id,
        item_public_id=item_public_id,
    )


def _item_edit_url(group_public_id, item_public_id):
    return url_for(
        "teacher.grade_item_edit",
        group_public_id=group_public_id,
        item_public_id=item_public_id,
    )


def _scores_url(group_public_id, item_public_id):
    return url_for(
        "teacher.grade_item_scores",
        group_public_id=group_public_id,
        item_public_id=item_public_id,
    )


def _release_url(group_public_id, item_public_id):
    return url_for(
        "teacher.grade_item_release",
        group_public_id=group_public_id,
        item_public_id=item_public_id,
    )


# ======================================================================
# Signed exact-shape state tokens (Phase 4 / M08)
# ======================================================================
#
# Five dedicated M08 salts and five exact purpose markers. A token minted
# under any other salt -- every M01 assignment snapshot, M02 submission
# context, M03 feedback state, M04 quiz token, M05 listening token, M06
# speaking token and M07 attendance token -- fails signature verification
# here even though all of them are signed with the same application
# SECRET_KEY, and a token minted under one of these salts but for another
# M08 purpose fails the purpose check. The reverse holds too.
#
# Every payload carries **public identifiers, versions and a purpose
# only**. A signed token is authenticated, not encrypted: anyone holding
# it can read its payload, so no Student name, no score, no comment, no
# weight, no roster size, no internal database id and no source title is
# ever placed in one. The score token does carry every captured record's
# public id and version -- that is exactly the state it must bind -- and
# nothing about who those records are for or what they say.
#
# The shape check below is exact and typed rather than merely "is a
# dict": the key set must match exactly, the purpose must be the expected
# one, identifiers must be strings, versions must be genuine positive
# ints (``bool`` excluded explicitly, since it is an ``int`` subclass and
# ``True`` must never pass as version 1), and both state lists must be
# lists of well-formed ``[public_id, version]`` pairs.

_SALTS = {
    "grade-category-create": "teacher.grade-category-create.phase4-m08.v1",
    "grade-category-edit": "teacher.grade-category-edit.phase4-m08.v1",
    "grade-item-write": "teacher.grade-item-write.phase4-m08.v1",
    "grade-scores": "teacher.grade-scores.phase4-m08.v1",
    "grade-release": "teacher.grade-release.phase4-m08.v1",
}

_FIELDS = {
    # Creating a category binds the Teacher, the Group and the Group's
    # WHOLE current category set with versions. The set is what matters:
    # a co-teacher who added a category, removed one or changed a weight
    # has changed how much room is left, and the form was written against
    # the old answer. Binding only a total would miss an A -> B -> A
    # round trip that leaves the sum identical.
    "grade-category-create": (
        "purpose",
        "teacher_public_id",
        "group_public_id",
        "categories",
    ),
    "grade-category-edit": (
        "purpose",
        "teacher_public_id",
        "group_public_id",
        "category_public_id",
        "categories",
    ),
    # Creating or editing an item binds the category set too -- it is the
    # list the form's category dropdown was built from -- plus, for an
    # edit, the item's own version. There is no roster in the payload:
    # the roster is whatever the locked rows say at the instant of
    # capture, which a token minted a moment earlier could not promise.
    # ``item_public_id`` / ``item_version`` are the empty string / 0 on a
    # create, so one shape serves both and a create token can never be
    # replayed as an edit (the purpose and the values both differ).
    "grade-item-write": (
        "purpose",
        "teacher_public_id",
        "group_public_id",
        "item_public_id",
        "item_version",
        "categories",
    ),
    # A score save binds the item's current version AND the exact
    # captured record public ids with their versions. Binding the item
    # version alone would miss a co-teacher who changed one score;
    # binding the records alone would miss a release. Together they are
    # the complete state the sheet was written against.
    "grade-scores": (
        "purpose",
        "teacher_public_id",
        "group_public_id",
        "item_public_id",
        "item_version",
        "records",
    ),
    # Release additionally binds the category set, because "the weights
    # total exactly 100%" is one of the rules it enforces and a
    # co-teacher can change that between the page load and the button.
    "grade-release": (
        "purpose",
        "teacher_public_id",
        "group_public_id",
        "item_public_id",
        "item_version",
        "records",
        "categories",
    ),
}

_STATE_FIELDS = ("records", "categories")
_VERSION_FIELDS = ("item_version",)


def _serializer(salt):
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=salt)


def _positive_int(value):
    """True for a genuine non-negative ``int``.

    ``bool`` is excluded explicitly: it is a subclass of ``int`` in
    Python, and ``True`` must never be accepted as version 1. Zero is
    allowed only because a create token carries ``item_version`` 0 to
    mean "there is no item yet"; a real version is always at least 1 and
    is compared against the locked row, so a forged 0 matches nothing.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _state(pairs):
    """The canonical bound state of a set of rows:
    ``[[public_id, version], ...]`` sorted by ``public_id``.

    Sorted by identifier rather than by the page's display order on
    purpose. The score sheet lists Students by name and the gradebook
    lists categories by title, so an administrator renaming an account or
    a co-teacher renaming a category between the GET and the POST would
    otherwise reorder the list and make an untouched form look stale.
    Sorting by ``public_id`` binds the *set and its versions*, which is
    what actually matters, and nothing about presentation.
    """
    return sorted([public_id, version] for public_id, version in pairs)


def _make_token(purpose, **payload):
    """Sign one exact-shape M08 token.

    Only ever called with freshly read persisted state, never with
    attempted form values: a fresh token may only pair with fresh state,
    which is precisely the bypass the stale rejection exists to close.
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
        if field in _VERSION_FIELDS:
            if not _positive_int(value):
                return None
        elif field in _STATE_FIELDS:
            if not isinstance(value, list):
                return None
            for entry in value:
                if not isinstance(entry, list) or len(entry) != 2:
                    return None
                if not isinstance(entry[0], str) or not _positive_int(entry[1]):
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
    for field, value in expected.items():
        if field in _STATE_FIELDS:
            if sorted(list(item) for item in payload[field]) != sorted(
                list(item) for item in value
            ):
                return True
        elif payload[field] != value:
            return True
    return False


# ======================================================================
# Fresh authorization -- the only evidence a post-rollback path may use
# ======================================================================


def _fresh_gradebook_authorization(actor_id, group_public_id, item_public_id=None):
    """Prove from **current database state** that `actor_id` may read this
    Group -- and, when asked, this item -- *right now*, and return
    ``(group, item)``.

    ``roles_required`` runs once, before the view, so a path that has
    rolled back and released its locks no longer holds current evidence,
    and the same concurrent change that forced the rollback may have
    ended this Teacher's access. ``_teacher_group_or_404`` alone is not
    enough either -- it proves an active ``GroupTeacherAssignment`` but
    never re-reads the actor's own ``role`` and ``status``. The actor is
    identified by a **scalar id captured before the reset**, never by
    ``current_user``.

    What is proved, in order, all as current reads: the acting User row
    exists, its role is ``teacher``, its status is ``active``, an active
    ``GroupTeacherAssignment`` links it to the exact Group named in the
    URL, and the item belongs to that exact Group through its category.

    What is deliberately **not** proved, because reading is historical:
    the AcademicTerm / Level / Course / Group need not be active, and the
    item need not be a draft.
    """
    if actor_id is None:  # pragma: no cover -- an authenticated view always has one
        abort(404)

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

    if not teacher_is_actively_assigned(actor_id, group.id):
        abort(404)

    if item_public_id is None:
        return group, None
    item = item_for_group(group.id, item_public_id)
    if item is None:
        abort(404)
    return group, item


def _category_or_404(group, category_public_id):
    """One category of this Group, or the established non-disclosing 404.
    Another Group's category public id and a nonexistent one fail
    identically here."""
    category = category_for_group(group.id, category_public_id)
    if category is None:
        abort(404)
    return category


def _item_or_404(group, item_public_id):
    """One item of this Group, or the established non-disclosing 404. The
    Group constraint is applied through the item's **category**, in SQL --
    ``grade_items`` carries no ``group_id`` of its own."""
    item = item_for_group(group.id, item_public_id)
    if item is None:
        abort(404)
    return item


def _hierarchy_context(group):
    return group.academic_term_id, group.course.level_id, group.course_id


def _operational_block(locks, group_id_expected, term_id, level_id, course_id):
    """``None`` if the **locked** Group and its locked AcademicTerm /
    Level / Course all exist and are active (so this write may proceed),
    else a Teacher-facing message.

    Same shape and same reasoning as M01's, M05's, M06's and M07's
    equivalents; the wording differs because what is blocked differs, and
    it comes from a constant declared once.
    """
    group = locks.group
    term = locks.hierarchy.term(term_id)
    level = locks.hierarchy.level(level_id)
    course = locks.hierarchy.course(course_id)
    if (
        group is None
        or course is None
        or group.id != group_id_expected
        or group.course_id != course.id
        or group.academic_term_id != term_id
        or course.level_id != level_id
    ):
        return _GROUP_CHANGED_MESSAGE
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
        verb = "is" if len(labels) == 1 else "are"
        lead, tail = _OPERATIONAL_WORDING
        return (
            f"{lead} while the group and its academic term, course, and level are all "
            f"active. The {_join_labels(labels)} {verb} archived. {tail}"
        )
    return None


def _roster_broken(locks, item_id, expected_record_ids):
    """True when the **locked** record rows are no longer the complete,
    intact roster this item captured.

    Every one of the ids the preview read must have locked to a real row
    that still belongs to this exact item, and the set must be non-empty.
    This is what "one GradeRecord for every captured roster student"
    means in practice, and it is checked on an ordinary score save too: a
    partially readable roster must never be half-written.
    """
    if not expected_record_ids:
        return True
    if set(locks.records) != set(expected_record_ids):
        return True
    for record in locks.records.values():
        if record is None or record.grade_item_id != item_id:
            return True
    return False


def _source_columns(source_kind, source_id):
    """The ``{column: value}`` mapping one validated source kind writes.

    Built from :data:`LINKED_SOURCE_COLUMNS` rather than by three
    ``if``s, so an unlinked kind cannot set a link even by mistake: it
    simply produces an empty mapping, and the two columns a linked kind
    does not use are never mentioned at all.
    """
    column = LINKED_SOURCE_COLUMNS.get(source_kind)
    return {} if column is None else {column: source_id}


def _resolve_source(group_id, source_kind, source_public_id):
    """The same-Group source row one submitted public id names, or
    ``None``.

    Every lookup is **Group-scoped in SQL**, so another Group's public id
    resolves to nothing here rather than to a row somebody then has to
    remember to check. An unlinked kind resolves to ``None`` and that is
    correct -- the caller distinguishes the two cases by the kind, never
    by this value alone.
    """
    if source_kind in UNLINKED_SOURCE_KINDS:
        return None
    resolver = {
        "assignment": assignment_for_group,
        "quiz": quiz_for_group,
        "speaking": speaking_activity_for_group,
    }.get(source_kind)
    if resolver is None:  # pragma: no cover -- the form validated the kind
        return None
    return resolver(group_id, source_public_id)


# ======================================================================
# Overview -- the Teacher's assigned Groups
# ======================================================================


@teacher_bp.get("/grades")
@roles_required(UserRole.TEACHER.value)
def gradebook_overview():
    """Every Group this Teacher is actively assigned to, as the way in to
    each Group's gradebook.

    Bounded by how many Groups one Teacher is assigned to -- an
    operational number, not a history that grows with time. Archived
    Groups are deliberately listed: reading a gradebook is historical,
    and this is the page that reaches it.
    """
    return _private_no_store(
        "teacher/grades/overview.html",
        cards=teacher_group_cards(current_user.id),
    )


# ======================================================================
# The gradebook itself
# ======================================================================


def _gradebook_context(group):
    """Everything the gradebook page renders, in **five** bounded reads
    no matter how large the gradebook is.

    One query for the categories, one for their items (with each linked
    source's title and publication status resolved by ``LEFT JOIN``), one
    aggregate for every item's captured / scored counts, and one for
    every score on every released item -- which the per-Student totals
    are then grouped out of in Python. Nothing is asked per category, per
    item, per record or per Student.
    """
    categories = category_rows(group.id)
    items = item_rows(group.id)
    counts = record_counts_for_items([row.id for row in items])
    view = build_gradebook_view(categories, items, counts)
    weights = [category["weight_basis_points"] for category in view]
    weight_total = total_weight(weights)
    for category in view:
        for item in category["grade_items"]:
            item["release_blockers"] = [
                _RELEASE_MESSAGES[code]
                for code in item_release_blockers(item, weight_total)
            ]
    return {
        "categories": view,
        "weight_total": weight_total,
        "weight_total_display": format_weight(weight_total),
        "weight_remaining_display": format_weight(remaining_weight(weights)),
        "weights_complete": weight_total == BASIS_POINTS_TOTAL,
        "config_messages": [
            _OVERALL_MESSAGES[code]
            for code in configuration_blockers(calculation_categories(view))
        ],
        "student_totals": build_student_totals(
            view, released_scores_for_group(group.id)
        ),
    }


@teacher_bp.get("/groups/<group_public_id>/gradebook")
@roles_required(UserRole.TEACHER.value)
def group_gradebook(group_public_id):
    """One Group's whole gradebook: its weighted categories, the items in
    each, how many scores are in, and every Student's calculated totals.

    Deliberately **not paginated**. A gradebook is one Group's complete
    configuration, and a Teacher cannot check that the weights add up to
    100% on page 1 of 2; it is bounded instead by what one Group holds.

    Stays available under an archived Group or ancestor: an eligible
    assigned Teacher can always read back what was graded.
    """
    group = _teacher_group_or_404(group_public_id)
    operational = _group_is_operational(group)
    return _private_no_store(
        "teacher/grades/gradebook.html",
        group=group,
        operational=operational,
        archived_labels=[] if operational else _archived_chain_labels(group),
        tz_name=_tz_name(),
        **_gradebook_context(group),
    )


# ======================================================================
# Categories
# ======================================================================


def _category_token_state(group_id):
    """The bound category state from a **plain** read, for minting a
    token onto a freshly rendered page.

    The authoritative comparison always uses
    :meth:`GradebookLocks.category_state` instead; this is only ever used
    to describe the state a page was rendered against.
    """
    return _state(
        (row.public_id, row.version) for row in category_rows(group_id)
    )


def _render_category_form(
    group, form, category=None, message=None, category_level="danger"
):
    """Render the category create / edit page against **current
    persisted state**, with a freshly minted token."""
    if message is not None:
        flash(message, category_level)
    weights = [
        weight
        for _, weight in category_weight_rows(
            group.id, exclude_category_id=None if category is None else category.id
        )
    ]
    purpose = "grade-category-create" if category is None else "grade-category-edit"
    payload = {
        "teacher_public_id": current_user.public_id,
        "group_public_id": group.public_id,
        "categories": _category_token_state(group.id),
    }
    if category is not None:
        payload["category_public_id"] = category.public_id
    return _private_no_store(
        "teacher/grades/category_form.html",
        group=group,
        form=form,
        category=category,
        title_max=GRADE_CATEGORY_TITLE_MAX_LENGTH,
        remaining_display=format_weight(remaining_weight(weights)),
        cancel_url=_gradebook_url(group.public_id),
        grade_state=_make_token(purpose, **payload),
    )


def _reject_category(group_public_id, message, url, level="danger"):
    """Release any lock, re-prove authorization from current state, then
    flash and redirect to a plain GET (PRG).

    The message is flashed **only after** authorization has passed, so a
    request whose access ended in the same window that caused the failure
    404s silently instead of leaving a message behind for whatever page
    the actor reaches next.
    """
    actor_id = current_user.id
    db.session.rollback()
    _fresh_gradebook_authorization(actor_id, group_public_id)
    flash(message, level)
    return redirect(url)


@teacher_bp.route(
    "/groups/<group_public_id>/gradebook/categories/new", methods=["GET", "POST"]
)
@roles_required(UserRole.TEACHER.value)
def grade_category_create(group_public_id):
    """Add one weighted category to this Group's gradebook.

    The weight is typed as a percentage and stored as an integer count of
    basis points, so "do these add up to 100%?" is an exact integer
    question. A Group may sit below 100% for as long as the Teacher
    needs; it may never exceed it, and that rule is re-proved against
    **every one of the Group's locked categories** -- discovered inside
    the locked transaction, not from the preview -- so two co-teachers
    each adding 60% cannot both succeed.
    """
    group = _teacher_group_or_404(group_public_id)
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_gradebook_url(group_public_id))

    other_weights = [weight for _, weight in category_weight_rows(group.id)]
    form = GradeCategoryForm(group_id=group.id, other_weights=other_weights)
    if request.method == "GET" or not form.validate_on_submit():
        return _render_category_form(group, form)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("grade_state")
    title = form.title.data.strip()
    weight = form.weight_basis_points
    term_id, level_id, course_id = _hierarchy_context(group)
    group_id = group.id

    locks = lock_gradebook_chain(
        group_public_id, term_id, level_id, course_id, actor_id
    )
    if _authz_broken(locks.group, locks.teacher, locks.teacher_assignment):
        db.session.rollback()
        abort(404)

    block = _operational_block(locks, group_id, term_id, level_id, course_id)
    if block is not None:
        return _reject_category(group_public_id, block, _gradebook_url(group_public_id))

    if _token_is_stale(
        token,
        "grade-category-create",
        teacher_public_id=actor_public_id,
        group_public_id=group_public_id,
        categories=locks.category_state(),
    ):
        return _reject_category(
            group_public_id, _STALE_MESSAGE, _category_new_url(group_public_id)
        )

    if total_weight(locks.weights()) + weight > BASIS_POINTS_TOTAL:
        return _reject_category(
            group_public_id, _WEIGHT_OVER_MESSAGE, _category_new_url(group_public_id)
        )
    if duplicate_category_title_exists(group_id, title):
        return _reject_category(
            group_public_id,
            _DUPLICATE_CATEGORY_MESSAGE,
            _category_new_url(group_public_id),
        )

    moment = _write_moment()
    db.session.add(
        GradeCategory(
            group_id=group_id,
            title=title,
            weight_basis_points=weight,
            version=1,
            created_at=moment,
            updated_at=moment,
        )
    )
    try:
        db.session.commit()
    except IntegrityError:
        return _reject_category(
            group_public_id,
            _DUPLICATE_CATEGORY_MESSAGE,
            _category_new_url(group_public_id),
        )

    flash(_CATEGORY_SAVED_MESSAGE, "success")
    return redirect(_gradebook_url(group_public_id))


@teacher_bp.route(
    "/groups/<group_public_id>/gradebook/categories/<category_public_id>/edit",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def grade_category_edit(group_public_id, category_public_id):
    """Rename one category or change its weight -- **while it still
    holds no released item**.

    The moment any item in it is released, its name and weight are frozen
    permanently: Students have already been shown results computed with
    that weight under that name, and changing either afterwards would
    retroactively rewrite what those results meant. The route says so in
    a clear sentence rather than silently succeeding or silently doing
    nothing.

    A save whose normalized title **and** weight equal the stored ones is
    a no-op: no version moves, no timestamp moves, and the transaction is
    rolled back.
    """
    group = _teacher_group_or_404(group_public_id)
    category = _category_or_404(group, category_public_id)

    if category_has_released_item(category.id):
        flash(_CATEGORY_FROZEN_MESSAGE, "warning")
        return redirect(_gradebook_url(group_public_id))
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_gradebook_url(group_public_id))

    other_weights = [
        weight
        for _, weight in category_weight_rows(group.id, exclude_category_id=category.id)
    ]
    form = GradeCategoryForm(
        formdata=request.form if request.method == "POST" else None,
        group_id=group.id,
        category_id=category.id,
        other_weights=other_weights,
        data={
            "title": category.title,
            "weight": format_weight(category.weight_basis_points),
        },
    )
    if request.method == "GET" or not form.validate_on_submit():
        return _render_category_form(group, form, category=category)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("grade_state")
    title = form.title.data.strip()
    weight = form.weight_basis_points
    term_id, level_id, course_id = _hierarchy_context(group)
    group_id, category_id = group.id, category.id
    edit_url = _category_edit_url(group_public_id, category_public_id)

    locks = lock_gradebook_chain(
        group_public_id, term_id, level_id, course_id, actor_id
    )
    if _authz_broken(locks.group, locks.teacher, locks.teacher_assignment):
        db.session.rollback()
        abort(404)
    locked = locks.category(category_id)
    if (
        locked is None
        or locked.group_id != group_id
        or locked.public_id != category_public_id
    ):
        db.session.rollback()
        abort(404)

    block = _operational_block(locks, group_id, term_id, level_id, course_id)
    if block is not None:
        return _reject_category(group_public_id, block, _gradebook_url(group_public_id))

    if _token_is_stale(
        token,
        "grade-category-edit",
        teacher_public_id=actor_public_id,
        group_public_id=group_public_id,
        category_public_id=category_public_id,
        categories=locks.category_state(),
    ):
        return _reject_category(group_public_id, _STALE_MESSAGE, edit_url)

    # Re-proved against the locked rows: a co-teacher may have released
    # an item in this category between the GET and now, which freezes it.
    if category_has_released_item(category_id):
        return _reject_category(
            group_public_id,
            _CATEGORY_FROZEN_MESSAGE,
            _gradebook_url(group_public_id),
            "warning",
        )
    if total_weight(locks.weights(exclude_category_id=category_id)) + weight > (
        BASIS_POINTS_TOTAL
    ):
        return _reject_category(group_public_id, _WEIGHT_OVER_MESSAGE, edit_url)
    if duplicate_category_title_exists(group_id, title, exclude_category_id=category_id):
        return _reject_category(group_public_id, _DUPLICATE_CATEGORY_MESSAGE, edit_url)

    if locked.title == title and locked.weight_basis_points == weight:
        db.session.rollback()
        flash(_NO_CHANGES_MESSAGE, "info")
        return redirect(_gradebook_url(group_public_id))

    moment = _write_moment()
    locked.title = title
    locked.weight_basis_points = weight
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject_category(group_public_id, _DUPLICATE_CATEGORY_MESSAGE, edit_url)

    flash(_CATEGORY_SAVED_MESSAGE, "success")
    return redirect(_gradebook_url(group_public_id))


# ======================================================================
# Grade items -- create
# ======================================================================


def _category_choices(group_id):
    """``(public_id, label)`` pairs for the item form's category select.

    The *value* is the category's ``public_id``, never an internal id,
    and the label carries the weight so a Teacher can see where the item
    is landing. Every choice is one of this Group's own categories, so
    ``SelectField`` pre-validation already refuses another Group's id --
    and the write path refuses it again by resolving it with a
    Group-scoped query.
    """
    return [
        (row.public_id, f"{row.title} ({format_weight(row.weight_basis_points)}%)")
        for row in category_rows(group_id)
    ]


def _existing_source_public_id(item):
    """The ``public_id`` of the source a stored item is linked to, or
    ``None``.

    Used only to pre-select the right option when the edit form is
    rendered. It is never trusted as authorization: the submitted value
    is resolved again through a Group-scoped query before anything is
    written, so an item whose source somehow no longer belongs to this
    Group cannot be resubmitted past that check.
    """
    row = None
    if item.source_kind == "assignment" and item.assignment_id is not None:
        row = db.session.get(Assignment, item.assignment_id)
    elif item.source_kind == "quiz" and item.quiz_id is not None:
        row = db.session.get(Quiz, item.quiz_id)
    elif item.source_kind == "speaking" and item.speaking_activity_id is not None:
        row = db.session.get(SpeakingActivity, item.speaking_activity_id)
    return None if row is None else row.public_id


def _render_item_form(group, form, item=None, message=None, level="danger"):
    """Render the item create / edit page against **current persisted
    state**, with a freshly minted token."""
    if message is not None:
        flash(message, level)
    return _private_no_store(
        "teacher/grades/item_form.html",
        group=group,
        form=form,
        item=item,
        title_max=GRADE_ITEM_TITLE_MAX_LENGTH,
        source_labels=SOURCE_KIND_LABELS,
        linked_kinds=sorted(LINKED_SOURCE_COLUMNS),
        cancel_url=(
            _gradebook_url(group.public_id)
            if item is None
            else _item_url(group.public_id, item.public_id)
        ),
        grade_state=_make_token(
            "grade-item-write",
            teacher_public_id=current_user.public_id,
            group_public_id=group.public_id,
            item_public_id="" if item is None else item.public_id,
            item_version=0 if item is None else item.version,
            categories=_category_token_state(group.id),
        ),
    )


def _item_form(group, formdata=None, data=None):
    """One :class:`GradeItemForm` wired to this Group's own choices.

    Both source lists and the category list are built from Group-scoped
    queries, so the form literally cannot offer another Group's object --
    the isolation is a property of the query, not of a later check.
    """
    return GradeItemForm(
        formdata=formdata,
        category_choices=_category_choices(group.id),
        source_choices=source_choices(group.id),
        data=data,
    )


def _reject_item(group_public_id, message, url, level="danger", item_public_id=None):
    """Release any lock, re-prove authorization (and the nested item when
    there is one) from current state, then flash and redirect to a plain
    GET, so no submitted value survives a rejection."""
    actor_id = current_user.id
    db.session.rollback()
    _fresh_gradebook_authorization(actor_id, group_public_id, item_public_id)
    flash(message, level)
    return redirect(url)


@teacher_bp.route(
    "/groups/<group_public_id>/gradebook/items/new", methods=["GET", "POST"]
)
@roles_required(UserRole.TEACHER.value)
def grade_item_create(group_public_id):
    """Create one draft grade item **and** capture its roster, atomically.

    The full M08 lock order is taken by ``lock_gradebook_chain``; every
    authoritative condition is then re-checked against those locked rows
    before a single row is written: the acting Teacher's role, account
    status and active assignment; the Group and its academic ancestors
    being operational; the chosen category still existing and belonging
    to this exact Group; the source-kind rule; the linked source existing
    and belonging to this same Group; and a non-empty eligible roster.

    **The item and every one of its records are inserted in one
    transaction.** A failure anywhere leaves nothing behind -- never an
    item with a partial roster, and never an item with no roster at all.

    Nothing is imported from the linked source. The records are created
    **ungraded**, not zeroed: a zero would be a claim that somebody
    scored nothing, and nobody has said that yet.
    """
    group = _teacher_group_or_404(group_public_id)
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_gradebook_url(group_public_id))
    if not category_rows(group.id):
        flash(_NO_CATEGORY_MESSAGE, "warning")
        return redirect(_category_new_url(group_public_id))

    form = _item_form(
        group, formdata=request.form if request.method == "POST" else None
    )
    if request.method == "GET" or not form.validate_on_submit():
        return _render_item_form(group, form)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("grade_state")
    title = form.title.data.strip()
    source_kind = form.source_kind.data
    max_points = form.max_points_value
    term_id, level_id, course_id = _hierarchy_context(group)
    group_id = group.id

    # Pre-lock preview: it only discovers which rows to lock.
    preview_category = category_for_group(group_id, form.category.data)
    if preview_category is None:
        return _render_item_form(group, form, message=_GROUP_CHANGED_MESSAGE)
    preview_source = _resolve_source(group_id, source_kind, form.source_public_id)
    if source_kind not in UNLINKED_SOURCE_KINDS and preview_source is None:
        return _render_item_form(group, form, message=_SOURCE_MISSING_MESSAGE)
    category_id = preview_category.id
    source_id = None if preview_source is None else preview_source.id
    roster_preview = eligible_roster_rows(group_id)
    if not roster_preview:
        return _render_item_form(group, form, message=_EMPTY_ROSTER_MESSAGE, level="warning")

    locks = lock_gradebook_chain(
        group_public_id,
        term_id,
        level_id,
        course_id,
        actor_id,
        source_kind=source_kind,
        source_id=source_id,
        student_ids=[user_id for user_id, _ in roster_preview],
        enrollment_ids=[enrollment_id for _, enrollment_id in roster_preview],
    )

    if _authz_broken(locks.group, locks.teacher, locks.teacher_assignment):
        db.session.rollback()
        abort(404)

    block = _operational_block(locks, group_id, term_id, level_id, course_id)
    if block is not None:
        return _reject_item(group_public_id, block, _gradebook_url(group_public_id))

    if _token_is_stale(
        token,
        "grade-item-write",
        teacher_public_id=actor_public_id,
        group_public_id=group_public_id,
        item_public_id="",
        item_version=0,
        categories=locks.category_state(),
    ):
        return _reject_item(
            group_public_id, _STALE_MESSAGE, _item_new_url(group_public_id)
        )

    locked_category = locks.category(category_id)
    if locked_category is None or locked_category.group_id != group_id:
        return _reject_item(
            group_public_id, _GROUP_CHANGED_MESSAGE, _item_new_url(group_public_id)
        )
    if not source_belongs_to_group(source_kind, locks.source, group_id):
        return _reject_item(
            group_public_id, _SOURCE_MISSING_MESSAGE, _item_new_url(group_public_id)
        )

    eligible_ids = eligible_locked_student_ids(locks, group_id)
    if not eligible_ids:
        return _reject_item(
            group_public_id,
            _EMPTY_ROSTER_MESSAGE,
            _item_new_url(group_public_id),
            "warning",
        )

    moment = _write_moment()
    try:
        created = create_item_with_roster(
            category_id,
            title,
            source_kind,
            max_points,
            _source_columns(source_kind, source_id),
            eligible_ids,
            moment,
        )
        item_public_id = created.public_id
        db.session.commit()
    except IntegrityError:
        return _reject_item(
            group_public_id, _INTEGRITY_MESSAGE, _item_new_url(group_public_id)
        )

    plural = "" if len(eligible_ids) == 1 else "s"
    flash(
        f"Grade item created for {len(eligible_ids)} student{plural}. Enter the scores, "
        "then release it when you are ready — students see nothing until you do.",
        "success",
    )
    return redirect(_scores_url(group_public_id, item_public_id))


# ======================================================================
# Grade item detail -- read only, draft or released
# ======================================================================


def _item_presentation(group, item_public_id):
    """One item's presentation dict plus its category's and the Group's
    weight total, or the established non-disclosing 404.

    Built from the same reads the gradebook page makes, narrowed to one
    item, so the detail page and the gradebook can never describe the
    same item differently.
    """
    item = _item_or_404(group, item_public_id)
    rows = [row for row in item_rows(group.id) if row.public_id == item_public_id]
    if not rows:  # pragma: no cover -- the lookup above already proved it exists
        abort(404)
    counts = record_counts_for_items([rows[0].id])
    view = build_gradebook_view(
        [row for row in category_rows(group.id) if row.id == rows[0].category_id],
        rows,
        counts,
    )
    weight_total = total_weight([weight for _, weight in category_weight_rows(group.id)])
    return item, view[0], view[0]["grade_items"][0], weight_total


@teacher_bp.get("/groups/<group_public_id>/gradebook/items/<item_public_id>")
@roles_required(UserRole.TEACHER.value)
def grade_item_detail(group_public_id, item_public_id):
    """One grade item, read only, with its readiness panel.

    **This route writes nothing.** It renders the release form only when
    the item is still a draft, the chain is operational and every release
    rule already passes; the panel lists **every** remaining blocker
    rather than the first, so a Teacher fixing one is not sent back for
    the next.

    Stays readable for a released item and under an archived Group --
    reading is historical.
    """
    group = _teacher_group_or_404(group_public_id)
    item, category_view, item_view, weight_total = _item_presentation(
        group, item_public_id
    )
    operational = _group_is_operational(group)
    blockers = item_release_blockers(item_view, weight_total)
    records = item_records(item.id)
    releasable = operational and not item_view["is_released"] and not blockers
    return _private_no_store(
        "teacher/grades/item_detail.html",
        group=group,
        item=item_view,
        category=category_view,
        operational=operational,
        archived_labels=[] if operational else _archived_chain_labels(group),
        tz_name=_tz_name(),
        blocker_messages=[_RELEASE_MESSAGES[code] for code in blockers],
        releasable=releasable,
        records=build_record_view(records),
        gradebook_url=_gradebook_url(group_public_id),
        scores_url=_scores_url(group_public_id, item_public_id),
        edit_url=(
            _item_edit_url(group_public_id, item_public_id)
            if operational and not item_view["is_released"]
            else None
        ),
        release_url=_release_url(group_public_id, item_public_id),
        release_token=(
            _make_token(
                "grade-release",
                teacher_public_id=current_user.public_id,
                group_public_id=group_public_id,
                item_public_id=item_public_id,
                item_version=item.version,
                records=_state((row.public_id, row.version) for row in records),
                categories=_category_token_state(group.id),
            )
            if releasable
            else None
        ),
    )


# ======================================================================
# Grade item edit -- draft only
# ======================================================================


@teacher_bp.route(
    "/groups/<group_public_id>/gradebook/items/<item_public_id>/edit",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def grade_item_edit(group_public_id, item_public_id):
    """Change a **draft** item's name, category, kind, linked item or
    maximum points.

    Once released, every one of those is frozen permanently: the item can
    never be moved to another category, to another Group, to another
    source, or onto a different roster. What stays possible after release
    is correcting a *score*, which is a different route and a different
    kind of change.

    **The roster is never touched here**, in either direction. Changing a
    draft item's category or source does not re-capture, top up or prune
    its records: the roster is a statement about who was being graded
    when the item was created, and editing the item's description does
    not change who that was.

    A save whose every field equals the stored one is a no-op: no
    version moves, no timestamp moves, and the transaction is rolled
    back.
    """
    group = _teacher_group_or_404(group_public_id)
    item = _item_or_404(group, item_public_id)

    if item.is_released():
        flash(_ITEM_RELEASED_MESSAGE, "warning")
        return redirect(_item_url(group_public_id, item_public_id))
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_item_url(group_public_id, item_public_id))

    current_category = db.session.get(GradeCategory, item.category_id)
    prefill = {f"{kind}_source": "" for kind in LINKED_SOURCE_COLUMNS}
    if item.source_kind in LINKED_SOURCE_COLUMNS:
        prefill[f"{item.source_kind}_source"] = _existing_source_public_id(item) or ""

    form = _item_form(
        group,
        formdata=request.form if request.method == "POST" else None,
        data={
            "category": None if current_category is None else current_category.public_id,
            "title": item.title,
            "source_kind": item.source_kind,
            "max_points": str(item.max_points),
            **prefill,
        },
    )
    if request.method == "GET" or not form.validate_on_submit():
        return _render_item_form(group, form, item=item)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("grade_state")
    title = form.title.data.strip()
    source_kind = form.source_kind.data
    max_points = form.max_points_value
    term_id, level_id, course_id = _hierarchy_context(group)
    group_id, item_id = group.id, item.id
    edit_url = _item_edit_url(group_public_id, item_public_id)
    detail_url = _item_url(group_public_id, item_public_id)

    preview_category = category_for_group(group_id, form.category.data)
    if preview_category is None:
        return _render_item_form(group, form, item=item, message=_GROUP_CHANGED_MESSAGE)
    preview_source = _resolve_source(group_id, source_kind, form.source_public_id)
    if source_kind not in UNLINKED_SOURCE_KINDS and preview_source is None:
        return _render_item_form(group, form, item=item, message=_SOURCE_MISSING_MESSAGE)
    category_id = preview_category.id
    source_id = None if preview_source is None else preview_source.id

    locks = lock_gradebook_chain(
        group_public_id,
        term_id,
        level_id,
        course_id,
        actor_id,
        item_ids=[item_id],
        source_kind=source_kind,
        source_id=source_id,
    )

    if _authz_broken(locks.group, locks.teacher, locks.teacher_assignment):
        db.session.rollback()
        abort(404)
    locked_item = locks.item(item_id)
    if locked_item is None or locked_item.public_id != item_public_id:
        db.session.rollback()
        abort(404)

    block = _operational_block(locks, group_id, term_id, level_id, course_id)
    if block is not None:
        return _reject_item(
            group_public_id, block, detail_url, item_public_id=item_public_id
        )
    if locked_item.is_released():
        return _reject_item(
            group_public_id,
            _ITEM_RELEASED_MESSAGE,
            detail_url,
            "warning",
            item_public_id=item_public_id,
        )

    if _token_is_stale(
        token,
        "grade-item-write",
        teacher_public_id=actor_public_id,
        group_public_id=group_public_id,
        item_public_id=item_public_id,
        item_version=locked_item.version,
        categories=locks.category_state(),
    ):
        return _reject_item(
            group_public_id, _STALE_MESSAGE, edit_url, item_public_id=item_public_id
        )

    locked_category = locks.category(category_id)
    if locked_category is None or locked_category.group_id != group_id:
        return _reject_item(
            group_public_id,
            _GROUP_CHANGED_MESSAGE,
            edit_url,
            item_public_id=item_public_id,
        )
    if not source_belongs_to_group(source_kind, locks.source, group_id):
        return _reject_item(
            group_public_id,
            _SOURCE_MISSING_MESSAGE,
            edit_url,
            item_public_id=item_public_id,
        )

    columns = _source_columns(source_kind, source_id)
    unchanged = (
        locked_item.title == title
        and locked_item.category_id == category_id
        and locked_item.source_kind == source_kind
        and locked_item.max_points == max_points
        and locked_item.assignment_id == columns.get("assignment_id")
        and locked_item.quiz_id == columns.get("quiz_id")
        and locked_item.speaking_activity_id == columns.get("speaking_activity_id")
    )
    if unchanged:
        db.session.rollback()
        flash(_NO_CHANGES_MESSAGE, "info")
        return redirect(detail_url)

    moment = _write_moment()
    locked_item.title = title
    locked_item.category_id = category_id
    locked_item.source_kind = source_kind
    locked_item.max_points = max_points
    # Every link column is assigned, never only the one in use: an item
    # changing from `quiz` to `manual` must have its old quiz_id cleared,
    # and leaving it would violate ck_grade_items_source_link.
    locked_item.assignment_id = columns.get("assignment_id")
    locked_item.quiz_id = columns.get("quiz_id")
    locked_item.speaking_activity_id = columns.get("speaking_activity_id")
    locked_item.version = locked_item.version + 1
    locked_item.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject_item(
            group_public_id, _INTEGRITY_MESSAGE, edit_url, item_public_id=item_public_id
        )

    flash(_ITEM_SAVED_MESSAGE, "success")
    return redirect(detail_url)


# ======================================================================
# Score entry -- one transaction, all or nothing
# ======================================================================


def _render_scores_page(group, item, submitted=None, messages=(), level="danger"):
    """Render the score sheet against **current persisted state**, with a
    freshly minted scores token.

    `submitted` (a ``{public_id: (raw_score, raw_comment)}`` mapping) is
    echoed back on an ordinary validation failure so a Teacher does not
    lose the rest of their sheet over one mistyped number. It is applied
    on top of the persisted rows purely for display; nothing about it is
    trusted, and the token is minted from the persisted versions only.
    """
    for message in messages:
        flash(message, level)
    rows = item_records(item.id)
    records = build_record_view(rows)
    if submitted:
        for record in records:
            if record["public_id"] in submitted:
                raw_score, raw_comment = submitted[record["public_id"]]
                record["score_display"] = raw_score
                record["comment"] = raw_comment
    return _private_no_store(
        "teacher/grades/scores.html",
        group=group,
        item=item,
        max_points_display=format_points(item.max_points),
        records=records,
        comment_max=COMMENT_MAX,
        is_released=item.is_released(),
        tz_name=_tz_name(),
        detail_url=_item_url(group.public_id, item.public_id),
        gradebook_url=_gradebook_url(group.public_id),
        grade_state=_make_token(
            "grade-scores",
            teacher_public_id=current_user.public_id,
            group_public_id=group.public_id,
            item_public_id=item.public_id,
            item_version=item.version,
            records=_state((row.public_id, row.version) for row in rows),
        ),
    )


@teacher_bp.route(
    "/groups/<group_public_id>/gradebook/items/<item_public_id>/scores",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def grade_item_scores(group_public_id, item_public_id):
    """Enter or correct every captured Student's score and comment.

    **The set of records is read from the database and the submitted
    field set is matched against it**, never the other way round: a
    request naming an extra record, omitting one, or renaming one adds,
    removes, replaces and reorders nothing.

    **All or nothing.** Ordinary validation runs before any lock and
    before any write, and a single bad value rejects the whole
    submission: the Teacher gets their sheet back to correct, and the
    database is untouched. Once validation passes, every change is
    applied inside one transaction and committed once, so a failure at
    any point leaves no partial grade edit behind.

    **A no-op save is a no-op.** If every score and every normalised
    comment is already what is stored, no version moves, no timestamp
    moves, ``graded_by`` is not rewritten, and the transaction is rolled
    back -- re-saving unchanged numbers is not grading.

    **A meaningful save increments each changed record's ``version``
    exactly once and the item's ``version`` exactly once**, whatever the
    number of records changed, and stamps one authoritative post-lock
    whole-second moment plus the acting Teacher on each changed record.

    **This route works on a released item too**, because correcting a
    grade a Teacher got wrong is exactly what an honest gradebook has to
    allow, and the Student sees the correction immediately. What it
    refuses on a released item is *blanking* a score: release guaranteed
    every captured record carried one, a Student has already been shown
    it, and withdrawing it silently would leave no trace that it existed.
    """
    group = _teacher_group_or_404(group_public_id)
    item = _item_or_404(group, item_public_id)

    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_item_url(group_public_id, item_public_id))

    if request.method == "GET":
        return _render_scores_page(group, item)

    # --- ordinary validation, before any lock and before any write -----
    preview_records = build_record_view(item_records(item.id))
    submission = parse_score_submission(
        request.form,
        preview_records,
        item.max_points,
        allow_blank_scores=not item.is_released(),
    )
    if not submission.ok:
        return _render_scores_page(group, item, submission.raw, submission.errors)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("grade_state")
    term_id, level_id, course_id = _hierarchy_context(group)
    group_id, item_id = group.id, item.id
    scores_url = _scores_url(group_public_id, item_public_id)
    student_ids = captured_student_ids(item_id)
    record_ids = captured_record_ids(item_id)

    locks = lock_gradebook_chain(
        group_public_id,
        term_id,
        level_id,
        course_id,
        actor_id,
        item_ids=[item_id],
        student_ids=student_ids,
        record_ids=record_ids,
    )

    if _authz_broken(locks.group, locks.teacher, locks.teacher_assignment):
        db.session.rollback()
        abort(404)
    locked_item = locks.item(item_id)
    if locked_item is None or locked_item.public_id != item_public_id:
        db.session.rollback()
        abort(404)
    locked_category = locks.category(locked_item.category_id)
    if locked_category is None or locked_category.group_id != group_id:
        db.session.rollback()
        abort(404)

    block = _operational_block(locks, group_id, term_id, level_id, course_id)
    if block is not None:
        return _reject_item(
            group_public_id, block, scores_url, item_public_id=item_public_id
        )
    if _roster_broken(locks, item_id, record_ids):
        return _reject_item(
            group_public_id,
            _ROSTER_BROKEN_MESSAGE,
            scores_url,
            item_public_id=item_public_id,
        )

    locked_records = list(locks.records.values())
    if _token_is_stale(
        token,
        "grade-scores",
        teacher_public_id=actor_public_id,
        group_public_id=group_public_id,
        item_public_id=item_public_id,
        item_version=locked_item.version,
        records=_state((row.public_id, row.version) for row in locked_records),
    ):
        return _reject_item(
            group_public_id, _STALE_MESSAGE, scores_url, item_public_id=item_public_id
        )

    # The submitted values were matched against a pre-lock read of the
    # roster; the locked rows are the authority, so the two must describe
    # the same set before anything is written.
    if {row.public_id for row in locked_records} != set(submission.values):
        return _reject_item(
            group_public_id, _STALE_MESSAGE, scores_url, item_public_id=item_public_id
        )

    # Re-proved against the LOCKED item, because both facts live on a
    # different row than the scores do and either can have changed since
    # the sheet was rendered: a draft item's maximum may have been edited
    # down, and the item may have been released by a co-teacher (which
    # turns "blank" from legitimate into a withdrawal of a published
    # result).
    released_now = locked_item.is_released()
    for record in locked_records:
        score, _ = submission.values[record.public_id]
        if score is not None and score > locked_item.max_points:
            return _reject_item(
                group_public_id,
                _STALE_MESSAGE,
                scores_url,
                item_public_id=item_public_id,
            )
        if released_now and score is None:
            return _reject_item(
                group_public_id,
                _STALE_MESSAGE,
                scores_url,
                item_public_id=item_public_id,
            )

    moment = _write_moment()
    changed = 0
    for record in locked_records:
        score, comment = submission.values[record.public_id]
        if record.score == score and record.comment == comment:
            continue
        record.score = score
        record.comment = comment
        record.graded_by_id = actor_id
        record.graded_at = moment
        record.version = record.version + 1
        record.updated_at = moment
        changed += 1

    if not changed:
        db.session.rollback()
        flash(_NO_CHANGES_MESSAGE, "info")
        return redirect(scores_url)

    locked_item.version = locked_item.version + 1
    locked_item.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject_item(
            group_public_id, _INTEGRITY_MESSAGE, scores_url, item_public_id=item_public_id
        )

    flash(
        _SCORES_SAVED_RELEASED_MESSAGE if released_now else _SCORES_SAVED_MESSAGE,
        "success",
    )
    return redirect(scores_url)


# ======================================================================
# Release -- one permanent publication, and a safe replay
# ======================================================================


@teacher_bp.post(
    "/groups/<group_public_id>/gradebook/items/<item_public_id>/release"
)
@roles_required(UserRole.TEACHER.value)
def grade_item_release(group_public_id, item_public_id):
    """Release one grade item to the Students on its captured roster.

    POST-only and CSRF-protected, behind an explicit confirmation the
    Teacher must tick. Every release rule is enforced **against the
    locked rows**, never against the panel the page rendered:

    * the acting Teacher still holds an active assignment to this Group
      (the locked ``GroupTeacherAssignment``; a failure is a 404, not a
      sentence);
    * the Group and its ancestors are operational;
    * the captured roster is intact and complete;
    * every captured record carries a real score;
    * the Group's category weights total exactly 10,000 basis points;
    * any linked source still exists, belongs to this Group, and is
      published;
    * the signed release token still describes the item's version, every
      record's version, and the Group's whole category set -- so a
      co-teacher's score change, a weight change or a release landing in
      between produces a "reload and review" rejection rather than a
      release of numbers nobody read.

    Sets one authoritative whole-second UTC ``released_at``, increments
    ``version`` exactly once, and writes nothing else -- no score, no
    comment, no record. **A replay is safe**: a second release of an
    already-released item changes no timestamp, no version and no score,
    and simply returns its detail page.
    """
    group = _teacher_group_or_404(group_public_id)
    item = _item_or_404(group, item_public_id)
    detail_url = _item_url(group_public_id, item_public_id)

    if item.is_released():
        # The replay, answered before any lock is taken: there is nothing
        # to decide and nothing to write.
        flash(_ALREADY_RELEASED_MESSAGE, "warning")
        return redirect(detail_url)
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(detail_url)
    if request.form.get("confirm_release") != "yes":
        flash(_RELEASE_CONFIRM_MESSAGE, "danger")
        return redirect(detail_url)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("grade_state")
    term_id, level_id, course_id = _hierarchy_context(group)
    group_id, item_id = group.id, item.id
    source_kind, source_id = item.source_kind, _stored_source_id(item)
    student_ids = captured_student_ids(item_id)
    record_ids = captured_record_ids(item_id)

    locks = lock_gradebook_chain(
        group_public_id,
        term_id,
        level_id,
        course_id,
        actor_id,
        item_ids=[item_id],
        source_kind=source_kind,
        source_id=source_id,
        student_ids=student_ids,
        record_ids=record_ids,
    )

    if _authz_broken(locks.group, locks.teacher, locks.teacher_assignment):
        db.session.rollback()
        abort(404)
    locked_item = locks.item(item_id)
    if locked_item is None or locked_item.public_id != item_public_id:
        db.session.rollback()
        abort(404)
    locked_category = locks.category(locked_item.category_id)
    if locked_category is None or locked_category.group_id != group_id:
        db.session.rollback()
        abort(404)

    if locked_item.is_released():
        return _reject_item(
            group_public_id,
            _ALREADY_RELEASED_MESSAGE,
            detail_url,
            "warning",
            item_public_id=item_public_id,
        )

    block = _operational_block(locks, group_id, term_id, level_id, course_id)
    if block is not None:
        return _reject_item(
            group_public_id, block, detail_url, item_public_id=item_public_id
        )
    if _roster_broken(locks, item_id, record_ids):
        return _reject_item(
            group_public_id,
            _ROSTER_BROKEN_MESSAGE,
            detail_url,
            item_public_id=item_public_id,
        )

    locked_records = list(locks.records.values())
    if _token_is_stale(
        token,
        "grade-release",
        teacher_public_id=actor_public_id,
        group_public_id=group_public_id,
        item_public_id=item_public_id,
        item_version=locked_item.version,
        records=_state((row.public_id, row.version) for row in locked_records),
        categories=locks.category_state(),
    ):
        return _reject_item(
            group_public_id, _STALE_MESSAGE, detail_url, item_public_id=item_public_id
        )

    # The release rules themselves, decided by the one shared function
    # against the LOCKED rows -- so the panel the Teacher read and this
    # decision cannot disagree about anything except the passage of time.
    captured = len(locked_records)
    scored = sum(1 for row in locked_records if row.score is not None)
    blockers = release_blockers(
        captured,
        scored,
        total_weight(locks.weights()),
        source_is_published_row(source_kind, locks.source),
        source_present=source_belongs_to_group(source_kind, locks.source, group_id),
    )
    if blockers:
        return _reject_item(
            group_public_id,
            _RELEASE_MESSAGES[blockers[0]],
            detail_url,
            item_public_id=item_public_id,
        )

    moment = _write_moment()
    locked_item.released_at = moment
    locked_item.version = locked_item.version + 1
    locked_item.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject_item(
            group_public_id, _INTEGRITY_MESSAGE, detail_url, item_public_id=item_public_id
        )

    flash(_RELEASED_OK_MESSAGE, "success")
    return redirect(detail_url)


def _stored_source_id(item):
    """The internal id of whatever this item is linked to, or ``None``.

    Read from the stored row rather than from a request, because release
    re-checks the link the item *actually has* -- not one a submitted
    field claims it has.
    """
    return {
        "assignment": item.assignment_id,
        "quiz": item.quiz_id,
        "speaking": item.speaking_activity_id,
    }.get(item.source_kind)
