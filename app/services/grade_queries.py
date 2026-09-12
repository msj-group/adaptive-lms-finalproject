"""Read-only query layer for the Group gradebook (Phase 4 / M08).

Flask-independent: plain functions over the ORM, no ``request`` /
``flash`` / ``redirect`` and no route decorators, mirroring
``app/services/attendance_queries.py`` and
``app/services/assignment_queries.py``. **Every function here is
read-only** -- no locks, no writes, no commits.

Design rules (Part Phase 4 / M08):

- **Authorization lives in the SQL ``WHERE`` clause.** A category, an
  item or a record is never loaded broadly and filtered in Python
  afterwards. The Teacher reads are keyed off a Group the caller has
  already proved an active ``GroupTeacherAssignment`` to, and every
  nested lookup is additionally constrained by that Group *through the
  category join* -- so another Group's category, item or record public id
  resolves to nothing here rather than to a row the caller then has to
  remember to check. The Student read is keyed off ``student_id`` **and**
  ``released_at IS NOT NULL``, so no query in this module can return a
  draft or somebody else's grade.
- **Every read is bounded.** Lists fetch :data:`PAGE_SIZE` ``+ 1`` rows
  to derive a non-disclosing "there is a next page" flag **without a
  COUNT**, and every aggregate is a ``GROUP BY`` whose result set is
  bounded by one page of parents.
- **No N+1.** Per-item record counts for a whole gradebook are one query
  keyed by that gradebook's item ids; per-Group report figures for a
  whole page of Groups are three queries keyed by the page's Group ids.
  Nothing asks per row.
- **No arithmetic.** Not one percentage, weighted total or score sum is
  computed here or in SQL. This module fetches exact values; the single
  service in ``app/services/grade_calculations.py`` does every
  calculation, in Python ``Decimal``. The only aggregates below are
  integer ``COUNT``s and one integer ``SUM`` of basis points -- exact on
  both backends, and neither is a grade.
- **Every row this module returns is converted to a plain presentation
  dict** before it reaches a template, so rendering a gradebook can never
  trigger a lazy load or an ORM-driven authorization decision. Internal
  numeric ids are used *inside* the SQL only -- for joins, ordering
  tie-breaks and keyed aggregates -- and never placed in a dict that
  reaches a template.
- Ordering is always fully deterministic, tie-broken by an internal
  ``id``.

**These helpers take no locks.** A read reflects whatever snapshot the
caller's transaction already holds. An authoritative mutation caller MUST
hold the M08 lock chain (see ``app/services/grade_transactions.py``) and
re-run the relevant helper against the locked, current rows before
deciding. A pre-lock call is only acceptable as a friendly preview whose
result is re-verified after the lock.
"""

from sqlalchemy import case, distinct, func
from sqlalchemy.orm import aliased

from app.models import (
    AcademicStatus,
    AcademicTerm,
    BASIS_POINTS_TOTAL,
    Assignment,
    AssignmentStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    GradeCategory,
    GradeItem,
    GradeRecord,
    GradeSourceKind,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    Quiz,
    QuizStatus,
    SpeakingActivity,
    User,
    UserRole,
    UserStatus,
)
from app.extensions import db
from app.services.attendance_queries import eligible_roster_rows  # noqa: F401
from app.services.grade_calculations import (
    format_percentage,
    format_points,
    format_weight,
    summarize,
)

_ACTIVE = AcademicStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_PUBLISHED_ASSIGNMENT = AssignmentStatus.PUBLISHED.value
_PUBLISHED_QUIZ = QuizStatus.PUBLISHED.value
_STUDENT = UserRole.STUDENT.value
_USER_ACTIVE = UserStatus.ACTIVE.value

#: Fixed page size for the Administrator report list and the Student's
#: group list alike. Not configurable and never client-supplied.
#:
#: The Teacher gradebook page is deliberately **not** paginated: a
#: gradebook is one Group's complete configuration, and a half-shown one
#: would be worse than useless -- a Teacher cannot check that weights add
#: up to 100% on page 1 of 2. It is bounded instead by what a Group can
#: hold, which is a small operational number rather than a history that
#: grows with time.
PAGE_SIZE = 20

#: ``eligible_roster_rows`` is imported from the Attendance query layer
#: rather than re-implemented, deliberately. "Who is an eligible active
#: member of this Group right now?" -- an ``active`` Enrollment, in
#: exactly this Group, whose User has role ``student`` and an ``active``
#: account -- must have exactly **one** answer in this project. M07
#: already stated it for the attendance roster; a second copy here would
#: be a second definition that could drift, and a Group whose attendance
#: roster and gradebook roster disagreed would be impossible to explain.

#: Teacher-facing labels for the five source kinds, declared once so a
#: template, a form and a report cannot disagree about the wording. Not
#: alphabetical: it reads in the order a Teacher thinks about their work.
SOURCE_KIND_ORDER = (
    GradeSourceKind.ASSIGNMENT.value,
    GradeSourceKind.QUIZ.value,
    GradeSourceKind.SPEAKING.value,
    GradeSourceKind.ACTIVITY.value,
    GradeSourceKind.MANUAL.value,
)

SOURCE_KIND_LABELS = {
    GradeSourceKind.ASSIGNMENT.value: "Assignment",
    GradeSourceKind.QUIZ.value: "Quiz or listening activity",
    GradeSourceKind.SPEAKING.value: "Speaking activity",
    GradeSourceKind.ACTIVITY.value: "Class activity (no linked item)",
    GradeSourceKind.MANUAL.value: "Manual grade (no linked item)",
}

SOURCE_KIND_VALUES = frozenset(SOURCE_KIND_ORDER)

#: The three source kinds that require a link, mapped to the GradeItem
#: column that must hold it. Declared once and consumed by the form, the
#: write path and the release check, so "which column does this kind
#: use?" has one answer.
LINKED_SOURCE_COLUMNS = {
    GradeSourceKind.ASSIGNMENT.value: "assignment_id",
    GradeSourceKind.QUIZ.value: "quiz_id",
    GradeSourceKind.SPEAKING.value: "speaking_activity_id",
}

#: The two source kinds that must carry no link at all.
UNLINKED_SOURCE_KINDS = frozenset(
    {GradeSourceKind.ACTIVITY.value, GradeSourceKind.MANUAL.value}
)


def normalize_page(value):
    """Normalise a ``page`` query argument to a positive integer.

    A missing, non-numeric, zero, negative or absurdly large value all
    become page 1 rather than reaching the database as an offset.

    Deliberately declared here rather than imported, for the reason
    ``assignment_queries`` and ``quiz_queries`` both state: **each
    feature owns its own bounds**, so tightening or widening one list can
    never silently change another. The rule is identical to theirs today
    on purpose; any future divergence has to be a deliberate edit here.
    """
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    if page < 1 or page > 10000:
        return 1
    return page


# ---------------------------------------------------------------------------
# Group identity history
# ---------------------------------------------------------------------------


def group_has_gradebook_history(group_id):
    """True if **any** GradeCategory row exists for this Group.

    A gradebook row freezes the Group's academic identity
    (``academic_term_id`` / ``course_id``) exactly like
    Enrollment / GroupTeacherAssignment / Schedule / Unit / Assignment /
    Quiz history does -- see
    ``app.blueprints.admin.groups._group_identity_frozen``.

    **An empty category counts**, on exactly the reasoning that makes an
    empty Quiz draft count: its title and its weight were already decided
    for *this* Group's Course in *this* AcademicTerm, and retargeting the
    Group afterwards would silently reinterpret what that plan is for.

    **Checking categories alone is sufficient, and deliberately so.** A
    :class:`~app.models.grade_item.GradeItem` cannot exist without a
    ``category_id`` (NOT NULL, foreign key), and a
    :class:`~app.models.grade_record.GradeRecord` cannot exist without an
    item -- so "this Group owns GradeItem or GradeRecord history" already
    implies "this Group owns a GradeCategory". One indexed lookup answers
    all three, and a second query joining through the category would cost
    more to prove something the schema already guarantees.

    Bounded by construction: it asks for one ``id`` and stops, so the
    cost does not grow with how large the gradebook is.

    This is an **identity** guard only. It adds no archive blocker, no
    cascade, and no new membership or capacity rule: a Group with a
    gradebook can still be archived, and archiving it never touches a
    grade.
    """
    return (
        db.session.query(GradeCategory.id).filter_by(group_id=group_id).first()
        is not None
    )


# ---------------------------------------------------------------------------
# Nested lookups -- SQL-scoped, never "trust the public id"
# ---------------------------------------------------------------------------


def teacher_is_actively_assigned(teacher_id, group_id):
    """A scalar ``EXISTS``: does this Teacher hold an **active**
    assignment to this exact Group right now?"""
    clause = GroupTeacherAssignment.query.filter_by(
        group_id=group_id, teacher_id=teacher_id, status=_ASSIGNMENT_ACTIVE
    ).exists()
    return bool(db.session.query(clause).scalar())


def category_for_group(group_id, category_public_id):
    """One GradeCategory by its own ``public_id``, constrained to
    `group_id`, or ``None``.

    A category public id valid only for another Group resolves to nothing
    here -- the nested-IDOR protection every other surface in this
    project uses, applied in SQL rather than by a check the caller could
    forget.
    """
    if not category_public_id:
        return None
    return GradeCategory.query.filter_by(
        public_id=category_public_id, group_id=group_id
    ).first()


def item_for_group(group_id, item_public_id):
    """One GradeItem by its own ``public_id``, constrained to `group_id`
    **through its category**, or ``None``.

    The join is what makes the constraint real: ``grade_items`` carries
    no ``group_id`` of its own (deliberately -- see the model), so the
    only honest way to ask "is this item this Group's?" is to ask its
    category.
    """
    if not item_public_id:
        return None
    return (
        GradeItem.query.join(GradeCategory, GradeItem.category_id == GradeCategory.id)
        .filter(GradeItem.public_id == item_public_id, GradeCategory.group_id == group_id)
        .first()
    )


def assignment_for_group(group_id, assignment_public_id):
    """One **ordinary** Assignment of this Group by ``public_id``, or
    ``None``.

    Speaking activities are excluded here on purpose: a Speaking activity
    *is* an Assignment carrying a ``speaking_activities`` extension, and
    it has its own source kind. Letting it also resolve as an
    ``assignment`` source would give one object two names in the
    gradebook.
    """
    if not assignment_public_id:
        return None
    return (
        Assignment.query.outerjoin(
            SpeakingActivity, SpeakingActivity.assignment_id == Assignment.id
        )
        .filter(
            Assignment.public_id == assignment_public_id,
            Assignment.group_id == group_id,
            SpeakingActivity.id.is_(None),
        )
        .first()
    )


def quiz_for_group(group_id, quiz_public_id):
    """One Quiz of this Group by ``public_id``, or ``None``.

    Listening activities are deliberately **included**: a Listening
    activity is a Quiz carrying a ``listening_activities`` extension, and
    M08 grades it as a ``quiz``.
    """
    if not quiz_public_id:
        return None
    return Quiz.query.filter_by(public_id=quiz_public_id, group_id=group_id).first()


def speaking_activity_for_group(group_id, speaking_public_id):
    """One SpeakingActivity of this Group by ``public_id``, or ``None``.

    Ownership runs ``speaking_activities -> assignments -> groups``, so
    the Group constraint is applied to the parent Assignment in SQL.
    """
    if not speaking_public_id:
        return None
    return (
        SpeakingActivity.query.join(
            Assignment, SpeakingActivity.assignment_id == Assignment.id
        )
        .filter(
            SpeakingActivity.public_id == speaking_public_id,
            Assignment.group_id == group_id,
        )
        .first()
    )


def group_by_public_id(group_public_id):
    """One Group by ``public_id`` with the ancestors the Administrator
    pages display, or ``None``. Column-projected: no ORM entity, and no
    lazy load behind it."""
    if not group_public_id:
        return None
    return (
        db.session.query(
            Group.id,
            Group.public_id,
            Group.name,
            Group.status,
            Course.title.label("course_title"),
            Level.name.label("level_name"),
            AcademicTerm.name.label("term_name"),
        )
        .select_from(Group)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .filter(Group.public_id == group_public_id)
        .first()
    )


# ---------------------------------------------------------------------------
# The gradebook itself -- three bounded reads, no matter how big it is
# ---------------------------------------------------------------------------


def category_rows(group_id):
    """Every GradeCategory of one Group, ordered by ``(title, id)``.

    Bounded by one Group's configuration. Ordering is by title so the
    page reads the way a Teacher wrote it, with the internal ``id`` as a
    deterministic tie-break that never reaches a template.
    """
    return (
        db.session.query(
            GradeCategory.id,
            GradeCategory.public_id,
            GradeCategory.title,
            GradeCategory.weight_basis_points,
            GradeCategory.version,
        )
        .filter(GradeCategory.group_id == group_id)
        .order_by(GradeCategory.title.asc(), GradeCategory.id.asc())
        .all()
    )


def category_weight_rows(group_id, exclude_category_id=None):
    """``[(category_id, weight_basis_points)]`` for one Group, ascending
    by ``category_id`` -- the exact input the weight-total rule needs, and
    the exact order the category rows must be **locked** in.

    `exclude_category_id` drops the category being edited, which must not
    count against its own new weight. Non-locking preview; an
    authoritative caller re-reads this from the locked rows.
    """
    query = db.session.query(GradeCategory.id, GradeCategory.weight_basis_points).filter(
        GradeCategory.group_id == group_id
    )
    if exclude_category_id is not None:
        query = query.filter(GradeCategory.id != exclude_category_id)
    return [
        (row.id, row.weight_basis_points)
        for row in query.order_by(GradeCategory.id.asc())
    ]


def duplicate_category_title_exists(group_id, title, exclude_category_id=None):
    """True when `group_id` already carries a category called `title`.

    `title` must be the **normalized** (trimmed) value the write path
    would actually persist, so the friendly pre-lock guard and the
    authoritative post-lock recheck can never disagree about what "the
    same title" means. `exclude_category_id` is the row being edited,
    which must not count as its own duplicate.

    Neither caller is the final defense:
    ``uq_grade_categories_group_title`` is, and the route catches the
    resulting ``IntegrityError``. Comparison is left to the database, so
    the effective case- and accent-sensitivity is the column's collation
    (``utf8mb4_0900_ai_ci`` on MySQL, binary on the SQLite test backend)
    -- inherited from this project's existing title checks rather than
    introduced here, and **not measured against real MySQL in this
    Part**.
    """
    query = GradeCategory.query.filter(
        GradeCategory.group_id == group_id, GradeCategory.title == title
    )
    if exclude_category_id is not None:
        query = query.filter(GradeCategory.id != exclude_category_id)
    return bool(db.session.query(query.exists()).scalar())


#: The Assignment alias used to reach a Speaking activity's own title and
#: publication status, which live on its parent Assignment rather than on
#: the extension row. Declared at module level so every query that needs
#: it uses the same alias and the same join shape.
_SpeakingParent = aliased(Assignment)


def item_rows(group_id):
    """Every GradeItem of one Group's categories, ordered by
    ``(category_id, id)``, with the linked source's title and publication
    status resolved in the **same** query.

    Four ``LEFT JOIN``s rather than four extra lookups per row: an item
    links to at most one source, so at most one side of each join
    contributes, and a gradebook of any size still costs one query. This
    is the N+1 the Part asks to be avoided, avoided in SQL rather than by
    a cache.

    The source's publication status is fetched here because the Teacher
    page has to *show* why an item cannot be released yet, and the
    release check has to *decide* it -- and both must read the same fact.
    """
    return (
        db.session.query(
            GradeItem.id,
            GradeItem.public_id,
            GradeItem.category_id,
            GradeItem.title,
            GradeItem.source_kind,
            GradeItem.max_points,
            GradeItem.released_at,
            GradeItem.version,
            Assignment.title.label("assignment_title"),
            Assignment.status.label("assignment_status"),
            Quiz.title.label("quiz_title"),
            Quiz.status.label("quiz_status"),
            _SpeakingParent.title.label("speaking_title"),
            _SpeakingParent.status.label("speaking_status"),
        )
        .select_from(GradeItem)
        .join(GradeCategory, GradeItem.category_id == GradeCategory.id)
        .outerjoin(Assignment, GradeItem.assignment_id == Assignment.id)
        .outerjoin(Quiz, GradeItem.quiz_id == Quiz.id)
        .outerjoin(SpeakingActivity, GradeItem.speaking_activity_id == SpeakingActivity.id)
        .outerjoin(_SpeakingParent, SpeakingActivity.assignment_id == _SpeakingParent.id)
        .filter(GradeCategory.group_id == group_id)
        .order_by(GradeItem.category_id.asc(), GradeItem.id.asc())
        .all()
    )


def record_counts_for_items(item_ids):
    """``{item_id: (captured, scored)}`` for the given items, in **one**
    ``GROUP BY`` query.

    ``captured`` is the size of the item's frozen roster; ``scored`` is
    how many of those records carry a real score. Asking per item would
    be an N+1 on a page that already knows every id it cares about. The
    result set is bounded by ``len(item_ids)``, itself one gradebook. An
    empty input issues no query at all.

    Both figures are integer ``COUNT``s -- the backend never touches the
    ``score`` column's value, only its nullness, so nothing here is
    arithmetic on a grade.
    """
    ids = [item_id for item_id in item_ids if item_id is not None]
    if not ids:
        return {}
    rows = (
        db.session.query(
            GradeRecord.grade_item_id,
            func.count(GradeRecord.id),
            func.count(GradeRecord.score),
        )
        .filter(GradeRecord.grade_item_id.in_(ids))
        .group_by(GradeRecord.grade_item_id)
        .all()
    )
    return {item_id: (captured, scored) for item_id, captured, scored in rows}


def source_title(row):
    """The linked source's title for one item row, or ``None``.

    One place decides it, so the gradebook page, the item detail, the
    report and the Student page can never name the same link
    differently. ``activity`` and ``manual`` have no link and correctly
    return ``None`` rather than an invented label.
    """
    return {
        GradeSourceKind.ASSIGNMENT.value: row.assignment_title,
        GradeSourceKind.QUIZ.value: row.quiz_title,
        GradeSourceKind.SPEAKING.value: row.speaking_title,
    }.get(row.source_kind)


def source_is_published(row):
    """``True`` / ``False`` for a linked source's publication state, or
    ``None`` when this item has no link.

    ``None`` is not "no": ``activity`` and ``manual`` items have nothing
    to publish and are releasable on their own, so the caller must
    distinguish "nothing to check" from "checked and it is a draft".
    """
    status = {
        GradeSourceKind.ASSIGNMENT.value: (row.assignment_status, _PUBLISHED_ASSIGNMENT),
        GradeSourceKind.QUIZ.value: (row.quiz_status, _PUBLISHED_QUIZ),
        # A Speaking activity's publication lives on its parent
        # Assignment -- the extension row has no status of its own.
        GradeSourceKind.SPEAKING.value: (row.speaking_status, _PUBLISHED_ASSIGNMENT),
    }.get(row.source_kind)
    if status is None:
        return None
    actual, published = status
    return actual == published


def build_item_view(row, counts):
    """One item as a plain presentation dict.

    ``max_points`` is carried **both** as the exact ``Decimal`` (for the
    calculation service) and as a display string (for the template), so
    no template ever formats a number and no caller ever calculates with
    a formatted one.
    """
    captured, scored = counts.get(row.id, (0, 0))
    return {
        "public_id": row.public_id,
        "title": row.title,
        "source_kind": row.source_kind,
        "source_label": SOURCE_KIND_LABELS.get(row.source_kind, row.source_kind),
        "source_title": source_title(row),
        "source_published": source_is_published(row),
        "max_points": row.max_points,
        "max_points_display": format_points(row.max_points),
        "is_released": row.released_at is not None,
        "released_at": row.released_at,
        "version": row.version,
        "captured_count": captured,
        "scored_count": scored,
        "scores_complete": captured > 0 and captured == scored,
    }


def build_gradebook_view(categories, items, counts):
    """The Teacher's whole gradebook as plain presentation dicts:
    categories in display order, each carrying its own items.

    Takes the three already-fetched row sets rather than querying, so the
    caller can see at a glance that rendering a gradebook of any size
    costs exactly three queries.

    ``frozen`` is the rule that makes a category's title and weight
    immutable -- it holds a released item -- computed here, once, from
    the same rows the page renders, so the badge a Teacher sees and the
    rule the write path enforces cannot drift.
    """
    by_category = {}
    for row in items:
        by_category.setdefault(row.category_id, []).append(build_item_view(row, counts))
    view = []
    for category in categories:
        own = by_category.get(category.id, [])
        released = [item for item in own if item["is_released"]]
        view.append(
            {
                "public_id": category.public_id,
                "title": category.title,
                "weight_basis_points": category.weight_basis_points,
                "weight_display": format_weight(category.weight_basis_points),
                "version": category.version,
                "grade_items": own,
                "item_count": len(own),
                "released_item_count": len(released),
                "draft_item_count": len(own) - len(released),
                "frozen": bool(released),
            }
        )
    return view


def calculation_categories(view):
    """The gradebook view reduced to exactly what
    :func:`app.services.grade_calculations.summarize` needs.

    A deliberate narrowing: the calculation service is handed public
    ids, weights and released ``(item_public_id, max_points)`` pairs and
    **nothing else** -- no titles a Student should not see, no draft
    item, no internal id, no roster. It cannot leak what it was never
    given, and it cannot accidentally count a draft because no draft is
    in its input.
    """
    return [
        {
            "public_id": category["public_id"],
            "title": category["title"],
            "weight_basis_points": category["weight_basis_points"],
            "released_item_count": category["released_item_count"],
            "released_items": [
                (item["public_id"], item["max_points"])
                for item in category["grade_items"]
                if item["is_released"]
            ],
        }
        for category in view
    ]


# ---------------------------------------------------------------------------
# Roster and score entry
# ---------------------------------------------------------------------------


def item_records(item_id):
    """Every captured record of one item, with its Student's name and the
    Teacher who last graded it, ordered deterministically by
    ``(full name, record id)``.

    Bounded by one item's frozen roster. The Student name and the grader
    name are joined once here rather than lazy-loaded per row -- two
    ``JOIN``s, not ``2n`` queries.

    **The Student's current role, account status and Enrollment are
    deliberately not filtered.** They were all required at capture time,
    against the locked rows; afterwards the record is history, and a
    withdrawal, a suspension or a re-assignment must never make a
    captured roster look incomplete -- which is also what keeps the
    release completeness check honest.
    """
    grader = aliased(User)
    return (
        db.session.query(
            GradeRecord.id,
            GradeRecord.public_id,
            GradeRecord.score,
            GradeRecord.comment,
            GradeRecord.version,
            GradeRecord.graded_at,
            User.full_name.label("student_name"),
            grader.full_name.label("grader_name"),
        )
        .select_from(GradeRecord)
        .join(User, GradeRecord.student_id == User.id)
        .outerjoin(grader, GradeRecord.graded_by_id == grader.id)
        .filter(GradeRecord.grade_item_id == item_id)
        .order_by(User.full_name.asc(), GradeRecord.id.asc())
        .all()
    )


def build_record_view(rows):
    """Plain presentation dicts for one item's roster.

    Used only by the **Teacher** surfaces. The Student page deliberately
    does not go through here at all: it has its own query and its own
    builder, which never fetch another Student's row, a version or a
    grader name in the first place. Keeping the two apart is what makes
    "a Student cannot be shown the roster" a property of the query rather
    than of a flag somebody could pass wrongly.
    """
    return [
        {
            "public_id": row.public_id,
            "student_name": row.student_name,
            "score": row.score,
            "score_display": format_points(row.score),
            "comment": row.comment,
            "version": row.version,
            "graded_at": row.graded_at,
            "grader_name": row.grader_name,
            "is_graded": row.score is not None,
        }
        for row in rows
    ]


def captured_record_ids(item_id):
    """Every captured record's internal id for one item, **ascending** --
    the exact order the score-save and release paths must lock them in.
    Non-locking preview; the caller re-locks each row."""
    return [
        row.id
        for row in db.session.query(GradeRecord.id)
        .filter(GradeRecord.grade_item_id == item_id)
        .order_by(GradeRecord.id.asc())
        .all()
    ]


def captured_student_ids(item_id):
    """Every captured Student's internal User id for one item,
    **ascending** -- the order the User rows must be locked in, before
    the records. Non-locking preview; the caller re-locks each row."""
    return [
        row.student_id
        for row in db.session.query(GradeRecord.student_id)
        .filter(GradeRecord.grade_item_id == item_id)
        .order_by(GradeRecord.student_id.asc())
        .all()
    ]


# ---------------------------------------------------------------------------
# Source choices for the item form -- same Group only, resolved in SQL
# ---------------------------------------------------------------------------


def assignment_choice_rows(group_id):
    """This Group's **ordinary** Assignments (draft and published), newest
    deadline first, as ``(public_id, title, is_published)``.

    Drafts are offered deliberately: a Teacher plans a gradebook before
    everything in it is published, and linking a draft is legitimate.
    What a draft link cannot do is be **released** -- the release check
    refuses it, and the page marks it -- so nothing a Student can see
    ever points at unpublished work.
    """
    rows = (
        db.session.query(Assignment.public_id, Assignment.title, Assignment.status)
        .outerjoin(SpeakingActivity, SpeakingActivity.assignment_id == Assignment.id)
        .filter(Assignment.group_id == group_id, SpeakingActivity.id.is_(None))
        .order_by(Assignment.due_at.desc(), Assignment.id.desc())
        .all()
    )
    return [
        (row.public_id, row.title, row.status == _PUBLISHED_ASSIGNMENT) for row in rows
    ]


def quiz_choice_rows(group_id):
    """This Group's Quizzes -- Listening activities included, since a
    Listening activity is a Quiz -- newest first, as
    ``(public_id, title, is_published)``."""
    rows = (
        db.session.query(Quiz.public_id, Quiz.title, Quiz.status)
        .filter(Quiz.group_id == group_id)
        .order_by(Quiz.created_at.desc(), Quiz.id.desc())
        .all()
    )
    return [(row.public_id, row.title, row.status == _PUBLISHED_QUIZ) for row in rows]


def speaking_choice_rows(group_id):
    """This Group's Speaking activities, newest deadline first, as
    ``(public_id, title, is_published)``.

    The title and the publication status both come from the parent
    Assignment -- the extension row has neither of its own.
    """
    rows = (
        db.session.query(
            SpeakingActivity.public_id, Assignment.title, Assignment.status
        )
        .select_from(SpeakingActivity)
        .join(Assignment, SpeakingActivity.assignment_id == Assignment.id)
        .filter(Assignment.group_id == group_id)
        .order_by(Assignment.due_at.desc(), Assignment.id.desc())
        .all()
    )
    return [
        (row.public_id, row.title, row.status == _PUBLISHED_ASSIGNMENT) for row in rows
    ]


def source_choices(group_id):
    """``{source_kind: [(public_id, label, is_published)]}`` for the three
    linked kinds.

    Every list is keyed by `group_id` in SQL, so another Group's
    Assignment, Quiz or Speaking activity is not merely hidden from the
    dropdown -- it is not in the result at all, and submitting its public
    id resolves to ``None`` in the write path for exactly the same
    reason.
    """
    return {
        GradeSourceKind.ASSIGNMENT.value: assignment_choice_rows(group_id),
        GradeSourceKind.QUIZ.value: quiz_choice_rows(group_id),
        GradeSourceKind.SPEAKING.value: speaking_choice_rows(group_id),
    }


# ---------------------------------------------------------------------------
# Per-Student totals -- one query per Group, whatever the roster size
# ---------------------------------------------------------------------------


def released_scores_for_group(group_id):
    """Every score on every **released** item of one Group, as
    ``(student_id, student_name, item_public_id, score)``, ordered by
    ``(full name, student id, item id)``.

    One query. The Teacher's per-Student totals and the Administrator's
    per-Student report are then produced by grouping this in Python and
    handing each Student's scores to the calculation service -- never by
    re-querying per Student, and never by summing in SQL.

    Draft items are excluded **in the ``WHERE`` clause**, so a draft
    score cannot reach a total by accident: it is not in the result set.
    """
    return (
        db.session.query(
            GradeRecord.student_id,
            User.full_name.label("student_name"),
            GradeItem.public_id.label("item_public_id"),
            GradeRecord.score,
        )
        .select_from(GradeRecord)
        .join(GradeItem, GradeRecord.grade_item_id == GradeItem.id)
        .join(GradeCategory, GradeItem.category_id == GradeCategory.id)
        .join(User, GradeRecord.student_id == User.id)
        .filter(
            GradeCategory.group_id == group_id,
            GradeItem.released_at.isnot(None),
        )
        .order_by(User.full_name.asc(), GradeRecord.student_id.asc(), GradeItem.id.asc())
        .all()
    )


def build_student_totals(view, score_rows):
    """One row per Student who appears on any released item of this
    Group, each carrying their category results and their weighted
    overall grade (or the reason there is not one).

    `view` is :func:`build_gradebook_view`'s output and `score_rows` is
    :func:`released_scores_for_group`'s -- both already fetched, so this
    function issues no query and the whole section costs nothing beyond
    the reads the page already made.

    Every number here comes from
    :func:`app.services.grade_calculations.summarize`. Nothing is added
    up in this module.
    """
    categories = calculation_categories(view)
    scores_by_student, names = {}, {}
    for row in score_rows:
        names[row.student_id] = row.student_name
        if row.score is not None:
            scores_by_student.setdefault(row.student_id, {})[row.item_public_id] = row.score
        else:
            scores_by_student.setdefault(row.student_id, {})
    totals = []
    for student_id, name in sorted(names.items(), key=lambda pair: (pair[1], pair[0])):
        summary = summarize(categories, scores_by_student.get(student_id, {}))
        totals.append(
            {
                "student_name": name,
                "categories": [
                    {
                        "title": result.title,
                        "percentage_display": format_percentage(result.percentage),
                        "complete": result.complete,
                        "scored_item_count": result.scored_item_count,
                        "released_item_count": result.released_item_count,
                    }
                    for result in summary.categories
                ],
                "overall_display": format_percentage(summary.overall),
                "unavailable_reason": summary.unavailable_reason,
            }
        )
    return totals


# ---------------------------------------------------------------------------
# Teacher overview
# ---------------------------------------------------------------------------


def teacher_group_cards(teacher_id):
    """Every Group this Teacher is **actively assigned** to, as plain
    dicts, ascending by ``(group name, group id)``.

    Bounded by how many Groups one Teacher is assigned to, which is a
    small operational number rather than a history that grows over time.
    Archived Groups and archived ancestors are deliberately
    **included**: reading a gradebook is historical, and this overview is
    how a Teacher reaches it.
    """
    rows = (
        db.session.query(
            Group.public_id,
            Group.name,
            Group.status,
            Course.title,
            Level.name,
            AcademicTerm.name,
            AcademicTerm.status,
            Level.status,
            Course.status,
        )
        .select_from(GroupTeacherAssignment)
        .join(Group, GroupTeacherAssignment.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .filter(
            GroupTeacherAssignment.teacher_id == teacher_id,
            GroupTeacherAssignment.status == _ASSIGNMENT_ACTIVE,
        )
        .order_by(Group.name.asc(), Group.id.asc())
        .all()
    )
    cards = []
    for (
        group_public_id,
        group_name,
        group_status,
        course_title,
        level_name,
        term_name,
        term_status,
        level_status,
        course_status,
    ) in rows:
        archived = [
            label
            for label, status in (
                ("academic term", term_status),
                ("level", level_status),
                ("course", course_status),
                ("group", group_status),
            )
            if status != _ACTIVE
        ]
        cards.append(
            {
                "group_public_id": group_public_id,
                "group_name": group_name,
                "course_title": course_title,
                "level_name": level_name,
                "term_name": term_name,
                "operational": not archived,
                "archived_labels": archived,
            }
        )
    return cards


# ---------------------------------------------------------------------------
# Student reads -- the Student's OWN released records, and nothing else
# ---------------------------------------------------------------------------


def _student_released_base(student_id):
    """The one visibility formula every Student gradebook read applies::

        the acting User is that Student, with role 'student' and an
        active account
        AND GradeRecord.student_id == that Student
        AND the record's GradeItem is RELEASED

    Deliberately **not** included: the Enrollment, the Group and the
    academic ancestors need not still be active, and the Enrollment need
    not still exist as ``active`` at all -- a Student who has withdrawn
    keeps reading their own released grades, which is the whole point of
    capturing the roster. Equally deliberately, a **draft** item is
    invisible: it is a Teacher's work in progress, and a Student shown a
    draft row would be shown a grade nobody has finished deciding.

    The ``users`` join re-proves the acting account's role and status
    rather than trusting the foreign key, exactly as M07 does: a foreign
    key proves a row exists, never that it is still a Student's.
    """
    return (
        db.session.query(
            GradeRecord.id.label("record_id"),
            GradeRecord.score,
            GradeRecord.comment,
            GradeItem.public_id.label("item_public_id"),
            GradeItem.title.label("item_title"),
            GradeItem.source_kind,
            GradeItem.max_points,
            GradeItem.released_at,
            GradeCategory.public_id.label("category_public_id"),
            GradeCategory.title.label("category_title"),
            GradeCategory.weight_basis_points,
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
        )
        .select_from(GradeRecord)
        .join(GradeItem, GradeRecord.grade_item_id == GradeItem.id)
        .join(GradeCategory, GradeItem.category_id == GradeCategory.id)
        .join(Group, GradeCategory.group_id == Group.id)
        .join(User, GradeRecord.student_id == User.id)
        .filter(
            GradeRecord.student_id == student_id,
            User.id == student_id,
            User.role == _STUDENT,
            User.status == _USER_ACTIVE,
            GradeItem.released_at.isnot(None),
        )
    )


def student_group_rows(student_id, page):
    """``(rows, has_next)`` -- one bounded page of the Groups whose
    gradebook this Student has something released in, newest Group first.

    A Group appears exactly when the Student holds at least one record on
    a released item of it, which is the same condition the detail page
    applies -- so the list can never offer a link to a page that would
    then be empty or 404.

    ``LIMIT PAGE_SIZE + 1`` for the has-next flag and **no** ``COUNT``.
    """
    offset, limit = (page - 1) * PAGE_SIZE, PAGE_SIZE + 1
    rows = (
        db.session.query(
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            Course.title.label("course_title"),
            AcademicTerm.name.label("term_name"),
            func.count(GradeRecord.id).label("released_count"),
            func.max(Group.id).label("group_id"),
        )
        .select_from(GradeRecord)
        .join(GradeItem, GradeRecord.grade_item_id == GradeItem.id)
        .join(GradeCategory, GradeItem.category_id == GradeCategory.id)
        .join(Group, GradeCategory.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(User, GradeRecord.student_id == User.id)
        .filter(
            GradeRecord.student_id == student_id,
            User.role == _STUDENT,
            User.status == _USER_ACTIVE,
            GradeItem.released_at.isnot(None),
        )
        .group_by(
            Group.public_id, Group.name, Course.title, AcademicTerm.name
        )
        .order_by(func.max(Group.id).desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


def student_released_rows(student_id, group_public_id):
    """Every released record this Student holds in one Group, ordered by
    ``(category title, item title, record id)``.

    One query, and it is the **only** query the Student detail page makes
    for grades. The Group is named by its public id and applied in the
    same ``WHERE`` clause as the Student condition, so a Group public id
    the Student has nothing released in returns an empty set rather than
    a page that leaks the Group's existence.
    """
    return (
        _student_released_base(student_id)
        .filter(Group.public_id == group_public_id)
        .order_by(
            GradeCategory.title.asc(),
            GradeItem.title.asc(),
            GradeRecord.id.asc(),
        )
        .all()
    )


def student_category_rows(group_id):
    """Every category of one Group with its **released** item count and
    the released items' ``(public_id, max_points)``, for the Student's
    weighted-overall calculation.

    This is the Group-level half of the rule -- "the weights total 100%
    and every category has a released item" is a statement about the
    Group, not about the Student, so it has to be read from the Group's
    own configuration rather than from what this Student happens to hold.

    Two bounded queries (categories, then their released items), and
    **nothing a Student may not see** is fetched: no draft item, no other
    Student, no roster, no score, no comment, no internal id in the
    output.
    """
    categories = (
        db.session.query(
            GradeCategory.id,
            GradeCategory.public_id,
            GradeCategory.title,
            GradeCategory.weight_basis_points,
        )
        .filter(GradeCategory.group_id == group_id)
        .order_by(GradeCategory.title.asc(), GradeCategory.id.asc())
        .all()
    )
    released = (
        db.session.query(
            GradeItem.category_id,
            GradeItem.public_id,
            GradeItem.max_points,
        )
        .join(GradeCategory, GradeItem.category_id == GradeCategory.id)
        .filter(
            GradeCategory.group_id == group_id, GradeItem.released_at.isnot(None)
        )
        .order_by(GradeItem.category_id.asc(), GradeItem.id.asc())
        .all()
    )
    by_category = {}
    for row in released:
        by_category.setdefault(row.category_id, []).append(
            (row.public_id, row.max_points)
        )
    return [
        {
            "public_id": category.public_id,
            "title": category.title,
            "weight_basis_points": category.weight_basis_points,
            "released_items": by_category.get(category.id, []),
            "released_item_count": len(by_category.get(category.id, [])),
        }
        for category in categories
    ]


def build_student_grade_view(rows):
    """Plain presentation dicts for a Student's own released grades.

    **No other Student, no roster, no draft, no teacher-only metadata and
    no internal id** is placed in these dicts -- not hidden by the
    template, simply never fetched into the row set and never put here.
    The comment *is* included: it is the Teacher's message to this
    Student about this released grade, and they are its intended reader.
    """
    return [
        {
            "item_public_id": row.item_public_id,
            "item_title": row.item_title,
            "category_title": row.category_title,
            "source_label": SOURCE_KIND_LABELS.get(row.source_kind, row.source_kind),
            "max_points_display": format_points(row.max_points),
            "score_display": format_points(row.score),
            "percentage_display": _item_percentage_display(row.score, row.max_points),
            "comment": row.comment,
            "released_at": row.released_at,
            "is_graded": row.score is not None,
        }
        for row in rows
    ]


def _item_percentage_display(score, max_points):
    """One item's own percentage, as a display string, or ``None``.

    Routed through the calculation service like every other number in
    this milestone -- a single item is just a category of one, and
    computing ``score / max_points`` inline here would be the second
    implementation of the rule this whole design exists to prevent.
    """
    from app.services.grade_calculations import category_percentage

    if score is None:
        return None
    return format_percentage(category_percentage(score, max_points))


# ---------------------------------------------------------------------------
# Administrator report -- read only, center-wide, four queries per page
# ---------------------------------------------------------------------------


def admin_groups_page(page, group_id=None, term_id=None, configured=None):
    """``(rows, has_next)`` -- one bounded page of Groups that own a
    gradebook, newest Group first.

    Every filter is optional and already **validated by the caller** into
    a known shape: an internal Group id resolved from a submitted
    *public* id, an internal AcademicTerm id from a submitted one, or
    ``True`` / ``False`` / ``None`` for fully configured / not / either.
    Nothing raw from the query string reaches SQL, and an unrecognised
    filter value is dropped by the caller rather than guessed at here.

    Only Groups that actually own a category are listed: a report about
    Groups with no gradebook would be a list of every Group in the
    center, which is the Groups page, not this one.

    Deterministic ordering (``Group.id DESC``), ``LIMIT PAGE_SIZE + 1``
    for the has-next flag and **no** ``COUNT``.

    The ``configured`` filter is applied with a ``HAVING`` on the exact
    integer weight sum -- 10,000 basis points, an exact integer
    comparison, never a float tolerance.
    """
    offset, limit = (page - 1) * PAGE_SIZE, PAGE_SIZE + 1
    query = (
        db.session.query(
            Group.id,
            Group.public_id,
            Group.name,
            Group.status,
            Course.title.label("course_title"),
            Level.name.label("level_name"),
            AcademicTerm.name.label("term_name"),
            func.count(GradeCategory.id).label("category_count"),
            func.sum(GradeCategory.weight_basis_points).label("weight_total"),
        )
        .select_from(GradeCategory)
        .join(Group, GradeCategory.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
    )
    if group_id is not None:
        query = query.filter(Group.id == group_id)
    if term_id is not None:
        query = query.filter(Group.academic_term_id == term_id)
    query = query.group_by(
        Group.id,
        Group.public_id,
        Group.name,
        Group.status,
        Course.title,
        Level.name,
        AcademicTerm.name,
    )
    if configured is True:
        query = query.having(
            func.sum(GradeCategory.weight_basis_points) == BASIS_POINTS_TOTAL
        )
    elif configured is False:
        query = query.having(
            func.sum(GradeCategory.weight_basis_points) != BASIS_POINTS_TOTAL
        )
    rows = query.order_by(Group.id.desc()).offset(offset).limit(limit).all()
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


def item_counts_for_groups(group_ids):
    """``{group_id: (items, released_items, categories_with_a_release)}``
    in **one** ``GROUP BY`` query.

    The third figure is what decides "every category has at least one
    released item" without fetching a row per category: a
    ``COUNT(DISTINCT CASE WHEN released THEN category_id END)``, which
    counts only the categories that have one because ``COUNT`` ignores
    the ``NULL``s the ``CASE`` produces for the rest. The result set is
    bounded by one page of Groups.
    """
    ids = [group_id for group_id in group_ids if group_id is not None]
    if not ids:
        return {}
    released = GradeItem.released_at.isnot(None)
    rows = (
        db.session.query(
            GradeCategory.group_id,
            func.count(GradeItem.id),
            func.sum(case((released, 1), else_=0)),
            func.count(distinct(case((released, GradeItem.category_id)))),
        )
        .select_from(GradeItem)
        .join(GradeCategory, GradeItem.category_id == GradeCategory.id)
        .filter(GradeCategory.group_id.in_(ids))
        .group_by(GradeCategory.group_id)
        .all()
    )
    return {
        group_id: (int(items or 0), int(released_items or 0), int(started or 0))
        for group_id, items, released_items, started in rows
    }


def active_student_counts_for_groups(group_ids):
    """``{group_id: active eligible Student count}`` in **one**
    ``GROUP BY`` query.

    The same four eligibility conditions
    :func:`eligible_roster_rows` applies, stated as a count rather than a
    row set. Bounded by one page of Groups.
    """
    ids = [group_id for group_id in group_ids if group_id is not None]
    if not ids:
        return {}
    rows = (
        db.session.query(Enrollment.group_id, func.count(Enrollment.id))
        .join(User, Enrollment.student_id == User.id)
        .filter(
            Enrollment.group_id.in_(ids),
            Enrollment.status == _ENROLLMENT_ACTIVE,
            User.role == _STUDENT,
            User.status == _USER_ACTIVE,
        )
        .group_by(Enrollment.group_id)
        .all()
    )
    return {group_id: int(total) for group_id, total in rows}


def build_admin_report_view(rows, item_counts, student_counts):
    """Plain presentation dicts for one page of the Administrator report.

    ``overall_available`` is the Group-level half of the weighted-grade
    rule -- weights exactly 100% **and** every category holding a
    released item -- decided here from the already-fetched integer
    aggregates, and stated identically to
    :func:`app.services.grade_calculations.configuration_blockers`. It
    says whether a weighted overall grade is *possible* in this Group,
    never what any individual Student's grade is: no score, no
    percentage and no comment appears anywhere on this surface.
    """
    view = []
    for row in rows:
        items, released_items, started_categories = item_counts.get(row.id, (0, 0, 0))
        weight_total = int(row.weight_total or 0)
        view.append(
            {
                "group_public_id": row.public_id,
                "group_name": row.name,
                "group_status": row.status,
                "course_title": row.course_title,
                "level_name": row.level_name,
                "term_name": row.term_name,
                "category_count": int(row.category_count or 0),
                "weight_total_basis_points": weight_total,
                "weight_total_display": format_weight(weight_total),
                "weights_complete": weight_total == BASIS_POINTS_TOTAL,
                "item_count": items,
                "released_item_count": released_items,
                "draft_item_count": items - released_items,
                "student_count": student_counts.get(row.id, 0),
                "overall_available": (
                    weight_total == BASIS_POINTS_TOTAL
                    and int(row.category_count or 0) > 0
                    and started_categories == int(row.category_count or 0)
                ),
            }
        )
    return view


def term_choice_rows():
    """Every AcademicTerm as ``(public-facing id, name)`` for the report's
    Term filter, newest start date first.

    Bounded by how many terms the center has run. The *value* is the
    Term's internal id, which is the established internal
    select/filter convention on the Administrator surfaces (see
    ``app/blueprints/admin/groups.py``); it is a filter value, never an
    object identity URL.
    """
    return (
        db.session.query(AcademicTerm.id, AcademicTerm.name)
        .order_by(AcademicTerm.start_date.desc(), AcademicTerm.id.desc())
        .all()
    )


# ---------------------------------------------------------------------------
# Release rules -- pure, and the ONLY place they are stated
# ---------------------------------------------------------------------------
#
# Returned as short codes rather than sentences, for the reason M07 gives
# about its occurrence rules: the Teacher-facing wording belongs to the
# Blueprint, which declares each sentence exactly once so the friendly
# readiness panel and the authoritative post-lock check can never explain
# the same rule differently.
#
# Pure over already-fetched facts, exactly like
# ``speaking_queries.speaking_publication_blockers``: no query, no lock,
# no clock. The readiness panel calls it against a plain read and the
# release route calls it against the LOCKED rows, and because it is the
# same function neither can drift from the other.

#: The item captured no roster at all -- impossible through the create
#: route, which refuses an empty Group, but checked anyway rather than
#: assumed.
RELEASE_NO_ROSTER = "no_roster"
#: At least one captured record still has no score.
RELEASE_MISSING_SCORES = "missing_scores"
#: The Group's category weights do not total exactly 100.00%.
RELEASE_WEIGHTS_INCOMPLETE = "weights_incomplete"
#: The linked Assignment / Quiz / Speaking activity is still a draft.
RELEASE_SOURCE_DRAFT = "source_draft"
#: The linked source could not be read at all, or does not belong to this
#: Group.
RELEASE_SOURCE_MISSING = "source_missing"

#: The order the reasons are reported in: the roster first (nothing else
#: matters without one), then the scores, then the configuration, then
#: the link. All of them are returned, never just the first -- a Teacher
#: fixing one blocker deserves to know about the other three before they
#: press the button again.
RELEASE_BLOCKER_ORDER = (
    RELEASE_NO_ROSTER,
    RELEASE_MISSING_SCORES,
    RELEASE_WEIGHTS_INCOMPLETE,
    RELEASE_SOURCE_MISSING,
    RELEASE_SOURCE_DRAFT,
)


def release_blockers(captured, scored, weight_total, source_published, source_present):
    """Every reason one GradeItem may not be released, as codes. An empty
    list means it is ready.

    The five conditions, and nothing else:

    1. it captured a roster, and
    2. every captured record carries a real score -- so a released grade
       is never a partly-filled sheet somebody could read as a zero;
    3. the Group's categories total **exactly** 10,000 basis points, so
       the weighted grade this release feeds into is meaningful rather
       than a fraction of an unfinished plan;
    4. the linked source still exists and belongs to this Group, and
    5. it is published -- a Student must never be shown a grade for work
       that has not been given to them.

    The sixth release rule -- that the acting Teacher still holds an
    active assignment to the Group -- is deliberately **not** here: it is
    an authorization question, answered by the locked
    ``GroupTeacherAssignment`` before this function is ever called, and
    folding it in would turn a 404 into a readable sentence about a Group
    the caller may no longer touch. The seventh -- that the submitted
    stale token is current -- is likewise the route's, because it is a
    question about the request rather than about the item.

    `source_published` is ``None`` for an ``activity`` or ``manual``
    item, which has nothing to publish; that is why it is a three-valued
    argument and not a boolean.
    """
    blockers = []
    if captured <= 0:
        blockers.append(RELEASE_NO_ROSTER)
    elif scored < captured:
        blockers.append(RELEASE_MISSING_SCORES)
    if weight_total != BASIS_POINTS_TOTAL:
        blockers.append(RELEASE_WEIGHTS_INCOMPLETE)
    if not source_present:
        blockers.append(RELEASE_SOURCE_MISSING)
    elif source_published is False:
        blockers.append(RELEASE_SOURCE_DRAFT)
    return [code for code in RELEASE_BLOCKER_ORDER if code in blockers]


def item_release_blockers(item_view, weight_total):
    """:func:`release_blockers` for one already-built item presentation
    dict -- the shape the readiness panel has in hand.

    A thin adapter rather than a second rule: it unpacks the dict and
    calls the one function above, so the panel a Teacher reads is
    literally the same decision the release route makes.
    """
    linked = item_view["source_kind"] in LINKED_SOURCE_COLUMNS
    return release_blockers(
        item_view["captured_count"],
        item_view["scored_count"],
        weight_total,
        item_view["source_published"],
        source_present=(not linked) or item_view["source_title"] is not None,
    )
