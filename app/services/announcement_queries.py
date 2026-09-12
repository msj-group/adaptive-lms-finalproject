"""Read-only query layer for Announcements (Phase 4 / M09).

Flask-independent: plain functions over the ORM, no ``request`` /
``flash`` / route decorators, mirroring every other ``app/services``
module. **Every function here is read-only** -- no locks, no writes.

The one rule this module exists to state exactly once
-----------------------------------------------------
*A published announcement is visible to somebody when they currently
stand in the place it was posted.*

That sentence is expressed as a SQL ``WHERE`` clause -- built by
:func:`student_visibility_clause` and :func:`teacher_visibility_clause`
-- and **never** as a loop, a template condition or a post-filter in
Python. The feed, the detail page, the dashboard preview, the search
integration and the notification recipient selection all reduce to the
same two clauses, so no surface can be more generous than another:

- **center** -- the reader is an active Student or Teacher account. No
  enrollment and no assignment is required: a Student between terms still
  belongs to the center.
- **course** -- the reader currently holds an ``active`` Enrollment
  (Student) or an ``active`` GroupTeacherAssignment (Teacher) in an
  **operational** Group of that Course.
- **group** -- the same, for that exact Group.

"Operational" means what it means everywhere else in this project: the
Group **and** its AcademicTerm, Course and Level are all ``active``. A
Group archived at the end of a term stops carrying notices, which is the
correct behaviour for a notice board and the deliberate opposite of the
Gradebook's and Attendance's historical reads.

Why ``EXISTS`` and not a join
-----------------------------
A Student can belong to three Groups of one Course. Joined into the feed,
one course-scoped announcement would come back three times, and
``DISTINCT`` over a row that also carries ``published_at`` and context
columns is both fragile and needlessly expensive. A correlated ``EXISTS``
answers "does this reader stand here?" once per announcement and can
short-circuit, so **one announcement is always exactly one row** -- no
duplicates to deduplicate, and nothing for a future column to break.

The context columns (the Course title, the Group name) come from plain
``LEFT JOIN``s on the announcement's own ``course_id`` / ``group_id``,
each of which matches at most one row, so they cannot multiply anything
either.

Everything else
---------------
- Every function returns plain values or presentation dicts -- no ORM
  entity reaches a template, so rendering can never lazy-load or make an
  authorization decision, and no internal numeric id is ever exposed.
- Every list is bounded: fixed page size, deterministic ordering
  (``published_at DESC, id DESC`` for reader feeds; ``created_at DESC,
  id DESC`` for management lists) and ``LIMIT PAGE_SIZE + 1`` for the
  has-next flag, with **no** ``COUNT`` -- an exact total would be an
  unbounded scan and a disclosure of how much exists beyond the page.
- Query cost never grows with the number of rows on the page: a feed is
  one query, a detail is one query. Nothing is asked per announcement.
"""

from sqlalchemy import and_, or_

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Announcement,
    AnnouncementScope,
    AnnouncementStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    User,
)
from app.services.announcement_search import match_clause
from app.services.schedule_occurrences import to_app_local

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_CENTER = AnnouncementScope.CENTER.value
_COURSE = AnnouncementScope.COURSE.value
_GROUP = AnnouncementScope.GROUP.value

_DRAFT = AnnouncementStatus.DRAFT.value
_PUBLISHED = AnnouncementStatus.PUBLISHED.value
_WITHDRAWN = AnnouncementStatus.WITHDRAWN.value

#: Fixed page size for every announcement list. Not configurable and not
#: client-supplied.
#:
#: Declared here rather than imported, for the reason ``quiz_queries``,
#: ``grade_queries`` and ``attendance_queries`` each state: **each feature
#: owns its own bounds**, so tightening or widening one list can never
#: silently change another.
PAGE_SIZE = 20

#: How many announcements a dashboard preview shows. A preview is a
#: pointer to the feed, not a second feed.
DASHBOARD_PREVIEW_CAP = 3

#: Reader-facing label per scope, declared once so a feed, a badge, a
#: search result and a notification message cannot word the same scope
#: three different ways.
SCOPE_LABELS = {
    _CENTER: "Center",
    _COURSE: "Course",
    _GROUP: "Group",
}

#: Author-facing label per status. Never shown to a Student: a Student
#: only ever sees published announcements, so a status badge on their
#: surfaces would be a badge that always says the same thing.
STATUS_LABELS = {
    _DRAFT: "Draft",
    _PUBLISHED: "Published",
    _WITHDRAWN: "Withdrawn",
}

#: The three statuses an Administrator may filter by, and the three
#: scopes. Anything else normalises to "no filter" rather than erroring
#: or revealing that a value was rejected.
STATUS_FILTERS = (_DRAFT, _PUBLISHED, _WITHDRAWN)
SCOPE_FILTERS = (_CENTER, _COURSE, _GROUP)


def normalize_page(value):
    """Normalise a ``page`` query argument to a positive integer.

    A missing, non-numeric, zero, negative or absurdly large value all
    become page 1 rather than reaching the database as an offset.
    """
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    if page < 1 or page > 10000:
        return 1
    return page


def normalize_scope_filter(value):
    """One of :data:`SCOPE_FILTERS`, or ``None`` for "any scope"."""
    value = (value or "").strip()
    return value if value in SCOPE_FILTERS else None


def normalize_status_filter(value):
    """One of :data:`STATUS_FILTERS`, or ``None`` for "any status"."""
    value = (value or "").strip()
    return value if value in STATUS_FILTERS else None


# ---------------------------------------------------------------------------
# Visibility -- the one rule, stated once, as SQL
# ---------------------------------------------------------------------------


def _operational_group_chain(query):
    """Add the "this Group is operational right now" conditions.

    The Group and all three academic ancestors must be ``active``. A
    legacy inconsistent ancestor is treated honestly as non-operational
    rather than repaired.
    """
    return query.filter(
        Group.status == _ACTIVE,
        Course.status == _ACTIVE,
        Level.status == _ACTIVE,
        AcademicTerm.status == _ACTIVE,
    )


def _student_membership_exists(student_id, group_condition):
    """``EXISTS`` -- this Student holds an active Enrollment in an
    operational Group satisfying `group_condition`.

    `group_condition` is correlated against the outer ``announcements``
    row (``Group.course_id == Announcement.course_id`` for a course-scoped
    notice, ``Group.id == Announcement.group_id`` for a group-scoped one),
    which is what makes one subquery serve both scopes without either
    being able to widen the other.
    """
    return (
        _operational_group_chain(
            db.session.query(Enrollment.id)
            .select_from(Enrollment)
            .join(Group, Enrollment.group_id == Group.id)
            .join(Course, Group.course_id == Course.id)
            .join(Level, Course.level_id == Level.id)
            .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        )
        .filter(
            Enrollment.student_id == student_id,
            Enrollment.status == _ENROLLMENT_ACTIVE,
            group_condition,
        )
        .exists()
    )


def _teacher_membership_exists(teacher_id, group_condition):
    """``EXISTS`` -- this Teacher holds an active GroupTeacherAssignment
    to an operational Group satisfying `group_condition`."""
    return (
        _operational_group_chain(
            db.session.query(GroupTeacherAssignment.id)
            .select_from(GroupTeacherAssignment)
            .join(Group, GroupTeacherAssignment.group_id == Group.id)
            .join(Course, Group.course_id == Course.id)
            .join(Level, Course.level_id == Level.id)
            .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        )
        .filter(
            GroupTeacherAssignment.teacher_id == teacher_id,
            GroupTeacherAssignment.status == _ASSIGNMENT_ACTIVE,
            group_condition,
        )
        .exists()
    )


def student_visibility_clause(student_id):
    """The complete "this Student may read this announcement" condition.

    Deliberately does **not** include ``status == 'published'``: callers
    add that themselves, so the one place a draft or a withdrawn
    announcement could slip into a reader surface is a filter somebody has
    to write explicitly rather than one buried inside a helper. Every
    caller in this project does add it, and the tests prove each one.
    """
    return or_(
        Announcement.scope == _CENTER,
        and_(
            Announcement.scope == _COURSE,
            _student_membership_exists(
                student_id, Group.course_id == Announcement.course_id
            ),
        ),
        and_(
            Announcement.scope == _GROUP,
            _student_membership_exists(student_id, Group.id == Announcement.group_id),
        ),
    )


def teacher_visibility_clause(teacher_id):
    """The complete "this Teacher may read this announcement" condition.
    Same shape, same caveat about ``status`` as
    :func:`student_visibility_clause`."""
    return or_(
        Announcement.scope == _CENTER,
        and_(
            Announcement.scope == _COURSE,
            _teacher_membership_exists(
                teacher_id, Group.course_id == Announcement.course_id
            ),
        ),
        and_(
            Announcement.scope == _GROUP,
            _teacher_membership_exists(teacher_id, Group.id == Announcement.group_id),
        ),
    )


# ---------------------------------------------------------------------------
# Reader feeds
# ---------------------------------------------------------------------------


def _reader_select():
    """The column projection every reader surface uses.

    ``Course`` and ``Group`` are joined **twice over**, deliberately:
    ``Course`` directly for a course-scoped row, and ``Group`` -> its own
    ``Course`` for a group-scoped one, because a group-scoped announcement
    stores no ``course_id`` of its own. Both are ``LEFT JOIN``s on a
    nullable foreign key, so each matches at most one row and neither can
    multiply the result.
    """
    group_course = db.aliased(Course)
    return (
        db.session.query(
            Announcement.public_id.label("public_id"),
            Announcement.scope.label("scope"),
            Announcement.title.label("title"),
            Announcement.body.label("body"),
            Announcement.published_at.label("published_at"),
            Course.title.label("course_title"),
            Group.name.label("group_name"),
            group_course.title.label("group_course_title"),
        )
        .select_from(Announcement)
        .outerjoin(Course, Announcement.course_id == Course.id)
        .outerjoin(Group, Announcement.group_id == Group.id)
        .outerjoin(group_course, Group.course_id == group_course.id)
    )


def _local(tz_name, moment):
    """`moment` (stored naive UTC) as a naive local wall clock, or
    ``None``. A NULL publication or withdrawal timestamp is a legitimate
    state, so it passes through rather than becoming an epoch."""
    return None if moment is None else to_app_local(tz_name, moment)


def _context_label(row):
    """The one short phrase naming *where* an announcement was posted.

    Center notices have no place beyond the center; a course notice names
    its Course; a group notice names its Group **and** the Course reached
    through that Group -- never a stored duplicate of it.
    """
    if row.scope == _COURSE:
        return row.course_title or ""
    if row.scope == _GROUP:
        if row.group_course_title:
            return f"{row.group_name} · {row.group_course_title}"
        return row.group_name or ""
    return ""


def build_reader_view(rows, tz_name="UTC"):
    """Plain presentation dicts for a reader surface. No ORM row, no
    internal id, no author, no status and no version crosses this
    boundary -- every one of those is either author-only information or a
    thing a reader has no use for.

    ``published_at`` is stored naive-UTC and is rendered here in
    ``APP_TIMEZONE`` through the M09 :func:`to_app_local` utility, so no
    template ever does timezone arithmetic.
    """
    return [
        {
            "public_id": row.public_id,
            "scope": row.scope,
            "scope_label": SCOPE_LABELS.get(row.scope, "Announcement"),
            "title": row.title,
            "body": row.body,
            "published_local": _local(tz_name, row.published_at),
            "context": _context_label(row),
        }
        for row in rows
    ]


def _visible_feed_query(visibility):
    return _visible_published(_reader_select(), visibility)


def _visible_published(query, visibility):
    return query.filter(Announcement.status == _PUBLISHED, visibility)


def _page(query, page, page_size=PAGE_SIZE):
    """``(rows, has_next)`` -- ``LIMIT page_size + 1`` and drop the extra,
    so "is there a next page" costs no second query and discloses no
    total."""
    rows = (
        query.offset((page - 1) * page_size).limit(page_size + 1).all()
    )
    return rows[:page_size], len(rows) > page_size


def _newest_first(query):
    return query.order_by(
        Announcement.published_at.desc(), Announcement.id.desc()
    )


def student_feed_page(student_id, page):
    """One bounded page of the announcements this Student may read right
    now, newest publication first.

    Center, course and group scopes in **one** query and therefore in one
    correctly interleaved chronological order -- not three lists stitched
    together afterwards, which could not be paged coherently. A Student
    enrolled in several Groups of the same Course sees that Course's
    announcement exactly once; see the module docstring.
    """
    query = _newest_first(_visible_feed_query(student_visibility_clause(student_id)))
    return _page(query, page)


def teacher_feed_page(teacher_id, page, tokens=()):
    """One bounded page of the announcements this Teacher may read right
    now, newest publication first.

    `tokens` optionally narrows the feed to announcements whose title or
    body contains every token -- the Teacher-side equivalent of the
    Student's global search, applied through exactly the same normalised,
    escaped ``LIKE`` conventions (see
    :func:`app.services.announcement_search.match_clause`) and **on top
    of** the visibility clause, never instead of it.
    """
    query = _visible_feed_query(teacher_visibility_clause(teacher_id))
    if tokens:
        query = query.filter(match_clause(tokens))
    return _page(_newest_first(query), page)


def student_visible_announcement(student_id, public_id):
    """One published announcement this Student may read right now, or
    ``None``.

    ``None`` for a public id that does not exist, for one that is a draft,
    for one that has been withdrawn, and for one whose scope this Student
    does not currently stand in -- all four indistinguishable from the
    outside, because the caller turns every one of them into the same
    non-disclosing 404.
    """
    if not public_id:
        return None
    return _visible_published(
        _reader_select().filter(Announcement.public_id == public_id),
        student_visibility_clause(student_id),
    ).first()


def teacher_visible_announcement(teacher_id, public_id):
    """One published announcement this Teacher may read right now, or
    ``None``. Same four indistinguishable failures as the Student form."""
    if not public_id:
        return None
    return _visible_published(
        _reader_select().filter(Announcement.public_id == public_id),
        teacher_visibility_clause(teacher_id),
    ).first()


def student_dashboard_preview(student_id, cap=DASHBOARD_PREVIEW_CAP):
    """The newest `cap` announcements this Student may read, for the
    dashboard. One bounded query; the same visibility clause as the feed,
    so the preview can never show something the feed would hide."""
    return (
        _newest_first(_visible_feed_query(student_visibility_clause(student_id)))
        .limit(cap)
        .all()
    )


def teacher_dashboard_preview(teacher_id, cap=DASHBOARD_PREVIEW_CAP):
    """The newest `cap` announcements this Teacher may read, for the
    dashboard."""
    return (
        _newest_first(_visible_feed_query(teacher_visibility_clause(teacher_id)))
        .limit(cap)
        .all()
    )


# ---------------------------------------------------------------------------
# Management reads -- authors and administrators only
# ---------------------------------------------------------------------------


def _management_select():
    """The projection the Teacher and Administrator management surfaces
    use: everything the reader projection has, plus the lifecycle columns
    and the author's display name.

    ``author`` is an ordinary ``JOIN`` on a non-nullable foreign key, so
    it matches exactly one row. The author's **name** is the only thing
    taken from it -- never their email, their role, their status or their
    internal id.
    """
    author = db.aliased(User)
    group_course = db.aliased(Course)
    return (
        db.session.query(
            Announcement.id.label("id"),
            Announcement.public_id.label("public_id"),
            Announcement.scope.label("scope"),
            Announcement.status.label("status"),
            Announcement.title.label("title"),
            Announcement.body.label("body"),
            Announcement.version.label("version"),
            Announcement.published_at.label("published_at"),
            Announcement.withdrawn_at.label("withdrawn_at"),
            Announcement.created_at.label("created_at"),
            Announcement.updated_at.label("updated_at"),
            author.full_name.label("author_name"),
            Course.public_id.label("course_public_id"),
            Course.title.label("course_title"),
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            group_course.title.label("group_course_title"),
        )
        .select_from(Announcement)
        .join(author, Announcement.author_id == author.id)
        .outerjoin(Course, Announcement.course_id == Course.id)
        .outerjoin(Group, Announcement.group_id == Group.id)
        .outerjoin(group_course, Group.course_id == group_course.id)
    )


def build_management_view(rows, tz_name="UTC"):
    """Plain presentation dicts for a management surface. Still no ORM
    row and still no internal numeric id -- the extra columns over
    :func:`build_reader_view` are the lifecycle, the version and the
    author's display name, all of which an author or an Administrator
    legitimately needs.

    Every timestamp is converted to ``APP_TIMEZONE`` here, for the same
    reason as in :func:`build_reader_view`.
    """
    return [
        {
            "public_id": row.public_id,
            "scope": row.scope,
            "scope_label": SCOPE_LABELS.get(row.scope, "Announcement"),
            "status": row.status,
            "status_label": STATUS_LABELS.get(row.status, row.status),
            "title": row.title,
            "body": row.body,
            "version": row.version,
            "published_local": _local(tz_name, row.published_at),
            "withdrawn_local": _local(tz_name, row.withdrawn_at),
            "created_local": _local(tz_name, row.created_at),
            "updated_local": _local(tz_name, row.updated_at),
            "author_name": row.author_name,
            "context": _context_label(row),
            "group_public_id": row.group_public_id,
            "course_public_id": row.course_public_id,
        }
        for row in rows
    ]


def _management_order(query):
    return query.order_by(Announcement.created_at.desc(), Announcement.id.desc())


def group_announcements_page(group_id, page):
    """One bounded page of **every** announcement owned by one Group --
    drafts, published and withdrawn alike -- newest first.

    This is the Teacher's management list, and it is reached only after
    the caller has proved an active assignment to that exact Group. The
    Group constraint is applied in SQL rather than by a check somebody
    could forget.
    """
    query = _management_order(
        _management_select().filter(Announcement.group_id == group_id)
    )
    return _page(query, page)


def group_announcement(group_id, public_id):
    """One announcement by its own ``public_id``, constrained to
    `group_id`, or ``None``.

    An announcement public id valid only for another Group -- or for a
    Course, or for the center -- resolves to nothing here. That is the
    nested-IDOR protection every other surface in this project uses,
    applied in SQL.
    """
    if not public_id:
        return None
    return (
        _management_select()
        .filter(Announcement.public_id == public_id, Announcement.group_id == group_id)
        .first()
    )


def admin_announcements_page(page, scope=None, status=None):
    """One bounded page of the center's announcements, newest first, with
    the two optional filters an Administrator may apply.

    Both filters are already normalised to a known value or ``None`` by
    :func:`normalize_scope_filter` / :func:`normalize_status_filter`, so
    no raw query-string value ever reaches SQL.
    """
    query = _management_select()
    if scope is not None:
        query = query.filter(Announcement.scope == scope)
    if status is not None:
        query = query.filter(Announcement.status == status)
    return _page(_management_order(query), page)


def admin_announcement(public_id):
    """One announcement by ``public_id`` for the Administrator surfaces,
    or ``None``. Unscoped by design: an Administrator manages all three
    scopes."""
    if not public_id:
        return None
    return _management_select().filter(Announcement.public_id == public_id).first()


# ---------------------------------------------------------------------------
# Small shared predicates
# ---------------------------------------------------------------------------


def teacher_is_actively_assigned(teacher_id, group_id):
    """A scalar ``EXISTS``: does this Teacher hold an **active**
    assignment to this exact Group right now?

    Re-declared here rather than imported from ``grade_queries`` for the
    reason that module states about its own borrowed helpers: a milestone
    owns the predicates its authorization depends on, so a future change
    to the gradebook's notion of "assigned" can never silently change who
    may publish an announcement. The rule is identical today on purpose.
    """
    clause = GroupTeacherAssignment.query.filter_by(
        group_id=group_id, teacher_id=teacher_id, status=_ASSIGNMENT_ACTIVE
    ).exists()
    return bool(db.session.query(clause).scalar())


def group_by_public_id(group_public_id):
    """One Group by ``public_id`` with the ancestors the announcement
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
            Group.course_id,
            Course.public_id.label("course_public_id"),
            Course.title.label("course_title"),
            Course.level_id,
            Course.status.label("course_status"),
            Level.name.label("level_name"),
            Level.status.label("level_status"),
            AcademicTerm.id.label("academic_term_id"),
            AcademicTerm.name.label("term_name"),
            AcademicTerm.status.label("term_status"),
        )
        .select_from(Group)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .filter(Group.public_id == group_public_id)
        .first()
    )


def course_by_public_id(course_public_id):
    """One Course by ``public_id`` with its Level, or ``None``. Used by
    the Administrator's course-scoped authoring form, which addresses a
    Course by its public id like every other object in this project."""
    if not course_public_id:
        return None
    return (
        db.session.query(
            Course.id,
            Course.public_id,
            Course.title,
            Course.status,
            Course.level_id,
            Level.name.label("level_name"),
            Level.status.label("level_status"),
        )
        .select_from(Course)
        .join(Level, Course.level_id == Level.id)
        .filter(Course.public_id == course_public_id)
        .first()
    )


def group_is_operational_row(row):
    """``True`` when a :func:`group_by_public_id` row's Group and all
    three academic ancestors are ``active``."""
    return (
        row is not None
        and row.status == _ACTIVE
        and row.course_status == _ACTIVE
        and row.level_status == _ACTIVE
        and row.term_status == _ACTIVE
    )


def archived_chain_labels(row):
    """Which links of a :func:`group_by_public_id` row's chain are
    archived, in outside-in order, for a Teacher-facing sentence."""
    return [
        label
        for label, status in (
            ("academic term", row.term_status),
            ("level", row.level_status),
            ("course", row.course_status),
            ("group", row.status),
        )
        if status != _ACTIVE
    ]


def teacher_group_cards(teacher_id):
    """Every Group this Teacher is **actively assigned** to, as plain
    dicts ordered by ``(group name, group id)``, each carrying how many
    announcements of each lifecycle state it holds.

    Bounded by how many Groups one Teacher is assigned to -- a small
    operational number -- and **two** queries in total however many
    Groups or announcements exist: one for the assignments, one grouped
    aggregate for the counts. Nothing is asked per Group.

    Archived Groups are deliberately listed. A Teacher must be able to
    reach an archived Group's announcements to read back what was posted,
    and to withdraw something that should no longer be standing.
    """
    rows = (
        db.session.query(
            Group.id,
            Group.public_id,
            Group.name,
            Group.status,
            Course.title.label("course_title"),
            Course.status.label("course_status"),
            Level.name.label("level_name"),
            Level.status.label("level_status"),
            AcademicTerm.name.label("term_name"),
            AcademicTerm.status.label("term_status"),
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
    counts = announcement_counts_for_groups([row.id for row in rows])
    cards = []
    for row in rows:
        operational = group_is_operational_row(row)
        per_group = counts.get(row.id, {})
        cards.append(
            {
                "group_public_id": row.public_id,
                "group_name": row.name,
                "course_title": row.course_title,
                "level_name": row.level_name,
                "term_name": row.term_name,
                "operational": operational,
                "archived_labels": [] if operational else archived_chain_labels(row),
                "draft_count": per_group.get(_DRAFT, 0),
                "published_count": per_group.get(_PUBLISHED, 0),
                "withdrawn_count": per_group.get(_WITHDRAWN, 0),
            }
        )
    return cards


def announcement_counts_for_groups(group_ids):
    """``{group_id: {status: count}}`` in **one** grouped aggregate, for
    the at-most-a-page of Groups the caller is rendering. Never one query
    per Group."""
    if not group_ids:
        return {}
    rows = (
        db.session.query(
            Announcement.group_id,
            Announcement.status,
            db.func.count(Announcement.id),
        )
        .filter(Announcement.group_id.in_(group_ids))
        .group_by(Announcement.group_id, Announcement.status)
        .all()
    )
    out = {}
    for group_id, status, count in rows:
        out.setdefault(group_id, {})[status] = count
    return out


def course_choice_rows():
    """Every **operational** Course -- active, under an active Level --
    as ``(public_id, label)`` pairs ordered the way the Courses page
    orders them.

    Only operational targets are offered, deliberately: an announcement
    may only be published to a place that is currently standing, so
    offering an archived Course would be offering a draft that could
    never be published. The value is the Course's ``public_id``, never an
    internal id.

    Bounded by the center's *current configuration* rather than by its
    history, which is what keeps this a select rather than a search.
    """
    rows = (
        db.session.query(
            Course.public_id, Course.title, Level.name.label("level_name")
        )
        .select_from(Course)
        .join(Level, Course.level_id == Level.id)
        .filter(Course.status == _ACTIVE, Level.status == _ACTIVE)
        .order_by(Level.display_order, Course.display_order, Course.id)
        .all()
    )
    return [(row.public_id, f"{row.title} — {row.level_name}") for row in rows]


def group_choice_rows():
    """Every **operational** Group -- active, under an active Course,
    Level and AcademicTerm -- as ``(public_id, label)`` pairs ordered by
    name.

    Same reasoning and same bound as :func:`course_choice_rows`.
    """
    rows = (
        db.session.query(
            Group.public_id,
            Group.name,
            Course.title.label("course_title"),
            AcademicTerm.name.label("term_name"),
        )
        .select_from(Group)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .filter(
            Group.status == _ACTIVE,
            Course.status == _ACTIVE,
            Level.status == _ACTIVE,
            AcademicTerm.status == _ACTIVE,
        )
        .order_by(Group.name, Group.id)
        .all()
    )
    return [
        (row.public_id, f"{row.name} — {row.course_title} — {row.term_name}")
        for row in rows
    ]
