"""Read-only query layer for Group-owned Assignments (Phase 4 / M01).

Flask-independent: plain functions over the ORM, no ``request`` /
``flash`` / route decorators, mirroring ``app/services/student_lessons.py``
and ``app/services/notification_queries.py``. **Every function here is
read-only** -- no locks, no writes.

Design rules (Part Phase 4 / M01):

- **Student authorization lives in the SQL ``WHERE`` clause**, keyed off
  the caller-supplied ``student_id``. An Assignment is never loaded
  broadly and filtered in Python afterwards, and there is no query here
  that can return a row the caller may not see. The full effective
  visibility formula is::

      User is that Student, with a valid role and an active account
      AND Enrollment(student, group).status == active
      AND AcademicTerm / Level / Course / Group .status == active
      AND Assignment.status == published
      AND Assignment.opens_at <= reference UTC moment

  The ``users`` join is not redundant with the session: a foreign key
  into ``users`` proves the row exists, never that it is still a Student
  or still active -- the project rule already applied to Enrollment and
  GroupTeacherAssignment.
- **One injected reference moment.** Every comparison takes
  ``reference_utc`` (naive UTC) from the caller, which obtains it once
  per request from
  :func:`app.services.schedule_occurrences.utc_reference_now`. No
  function here reads a wall clock, so a response can never straddle a
  deadline and contradict itself, and tests are deterministic.
- **Every read is bounded.** Lists fetch ``PAGE_SIZE + 1`` rows to derive
  a non-disclosing "there is a next page" flag without a COUNT; the
  dashboard takes a small fixed cap. Nothing loads an unbounded
  Assignment history.
- **Every row this module returns is converted to a plain presentation
  dict** before it reaches a template, so rendering an Assignment can
  never trigger a lazy load or an ORM-driven authorization decision. The
  Student pages are plain dicts throughout; the Teacher list route also
  hands its template the eagerly loaded ``Group`` object for the page
  header, which is stated where it happens rather than claimed away here.
- Ordering is always fully deterministic, tie-broken by the internal
  ``id`` **inside the SQL only** -- the id is never placed in a dict that
  reaches a template.
"""

from sqlalchemy import case

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Assignment,
    AssignmentStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    Level,
    User,
    UserRole,
    UserStatus,
)
from app.services.schedule_occurrences import to_app_local

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_PUBLISHED = AssignmentStatus.PUBLISHED.value

#: Fixed page size for both the Teacher history list and the Student
#: list. Not configurable and never client-supplied -- the explicit bound
#: the Part requires instead of an unbounded history.
PAGE_SIZE = 20

#: How many upcoming deadlines the Student dashboard section shows. Small
#: and fixed on purpose: the dashboard is a summary, and the full list
#: lives behind its own paginated route.
DASHBOARD_DEADLINE_CAP = 5

#: Derived, never-stored presentation states (Part "Assignment time
#: semantics"). ``SCHEDULED`` is Teacher-only -- a Student cannot see a
#: published Assignment before it opens at all.
STATE_SCHEDULED = "scheduled"
STATE_OPEN = "open"
STATE_PAST_DUE = "past_due"

STATE_LABELS = {
    STATE_SCHEDULED: "Scheduled",
    STATE_OPEN: "Open",
    STATE_PAST_DUE: "Past due",
}


def normalize_page(value):
    """Normalise a ``page`` query argument to a positive integer.

    A missing, non-numeric, zero, negative, or absurdly large value all
    become page 1 rather than reaching the database as an offset. Kept
    local to this module rather than shared with the notification inbox:
    each feature owns its own bounds, so tightening one can never
    silently change the other.
    """
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    if page < 1 or page > 10000:
        return 1
    return page


def derived_state(status, opens_at, due_at, reference_utc):
    """The derived presentation state of one Assignment, or ``None`` for
    a draft (which has no time-driven state -- it is simply not published
    to anybody yet).

    Never stored: computed from `reference_utc` so the passage of time
    can never leave a stale value in the database.
    """
    if status != _PUBLISHED:
        return None
    if reference_utc < opens_at:
        return STATE_SCHEDULED
    if reference_utc < due_at:
        return STATE_OPEN
    return STATE_PAST_DUE


def group_has_assignment_history(group_id):
    """True if **any** Assignment row -- draft or published -- exists for
    this Group.

    An Assignment row freezes the Group's academic identity
    (``academic_term_id`` / ``course_id``) exactly like
    Enrollment / GroupTeacherAssignment / Schedule / Unit history does --
    see ``app.blueprints.admin.groups._group_identity_frozen``.

    Publication status is deliberately irrelevant: even a draft was
    *authored* against this Group's Course and AcademicTerm, so
    retargeting the Group afterwards would silently reinterpret what that
    work is for.
    """
    return db.session.query(Assignment.id).filter_by(group_id=group_id).first() is not None


# ---------------------------------------------------------------------------
# Teacher reads -- already authorized by the route's active assignment check
# ---------------------------------------------------------------------------


def teacher_assignments_page(group_id, page):
    """One bounded page of a Group's Assignments -- draft **and**
    published together, newest deadline first.

    Returns ``(rows, has_next)``. Ordering is ``due_at DESC, id DESC``,
    fully deterministic. Its column order mirrors
    ``ix_assignments_group_due_id``, whose ordering columns follow the
    ``group_id`` equality directly -- but no MySQL plan has been measured,
    so that is a reasoned design, not a proven index-ordered read.
    Fetches ``PAGE_SIZE + 1`` rows and drops the extra, so "is there a
    next page" costs no second query and discloses no total count.

    Authorization is **not** performed here: the Teacher routes have
    already proven an ACTIVE ``GroupTeacherAssignment`` to this exact
    Group before calling, and pass its internal id.
    """
    rows = (
        Assignment.query.filter(Assignment.group_id == group_id)
        .order_by(Assignment.due_at.desc(), Assignment.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    has_next = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_next


def build_teacher_view(rows, tz_name, reference_utc):
    """Plain presentation dicts for the Teacher **Assignment rows** --
    localized times, the derived state, and public ids only; no
    Assignment ORM row and no internal id is carried in them.

    Scoped claim on purpose: the Teacher list route additionally passes
    the eagerly loaded ``Group`` ORM object to its template for the page
    header, so that template does receive one ORM object. Only the
    Student pages receive plain dicts throughout.
    """
    view = []
    for row in rows:
        state = derived_state(row.status, row.opens_at, row.due_at, reference_utc)
        view.append(
            {
                "public_id": row.public_id,
                "title": row.title,
                "status": row.status,
                "is_published": row.status == _PUBLISHED,
                "opens_local": to_app_local(tz_name, row.opens_at),
                "due_local": to_app_local(tz_name, row.due_at),
                "published_local": (
                    to_app_local(tz_name, row.published_at)
                    if row.published_at is not None
                    else None
                ),
                "state": state,
                "state_label": STATE_LABELS.get(state),
            }
        )
    return view


# ---------------------------------------------------------------------------
# Student reads -- the effective visibility formula, entirely in SQL
# ---------------------------------------------------------------------------


def _visible_assignment_query(student_id, reference_utc):
    """The one shared, fully scoped base query behind every Student read.

    Defining it once is deliberate: the list, the detail page, and the
    dashboard section must agree exactly on what "visible" means, so a
    future change cannot tighten one path and leave another open.
    """
    return (
        db.session.query(Assignment, Group, Course, Level, AcademicTerm)
        .join(Group, Assignment.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(Enrollment, Enrollment.group_id == Group.id)
        .join(User, Enrollment.student_id == User.id)
        .filter(
            User.id == student_id,
            User.role == UserRole.STUDENT.value,
            User.status == UserStatus.ACTIVE.value,
            Enrollment.status == _ENROLLMENT_ACTIVE,
            AcademicTerm.status == _ACTIVE,
            Level.status == _ACTIVE,
            Course.status == _ACTIVE,
            Group.status == _ACTIVE,
            Assignment.status == _PUBLISHED,
            Assignment.opens_at <= reference_utc,
        )
    )


def student_list_order(reference_utc):
    """The Student list's ORDER BY terms: **current work first, history
    after it**, expressed entirely in SQL against the one injected
    `reference_utc`.

    A single ``due_at ASC`` sequence was wrong. Past-due Assignments stay
    visible on purpose, so the oldest deadline in a Student's whole
    history sorted *first* -- and with one full page of old work, an open
    Assignment whose deadline is approaching was pushed onto a later
    page. That contradicts what the page says it shows.

    The four terms are:

    1. bucket -- open (``due_at > reference``) before past due;
    2. inside the **open** bucket, ``due_at ASC`` (nearest deadline
       first); the term is NULL for every past-due row, so it cannot
       reorder them;
    3. inside the **past-due** bucket, ``due_at DESC`` (most recent
       history first); likewise NULL for every open row;
    4. ``Assignment.id ASC`` -- the deterministic final tie-break, so
       equal deadlines page stably. The id orders the SQL only; it is
       never placed in a dict that reaches a template.

    Two opposite directions cannot be expressed as one sort key, hence
    the two complementary ``CASE`` terms. Within either bucket exactly
    one of them is non-NULL for *every* row of that bucket, so NULL
    ordering never mixes the buckets.

    The boundary matches :func:`derived_state` exactly: ``now == due_at``
    is **past due**.
    """
    is_past_due = Assignment.due_at <= reference_utc
    return (
        case((is_past_due, 1), else_=0).asc(),
        case((Assignment.due_at > reference_utc, Assignment.due_at)).asc(),
        case((is_past_due, Assignment.due_at)).desc(),
        Assignment.id.asc(),
    )


def student_assignments_page(student_id, reference_utc, page):
    """One bounded page of the Assignments this Student may currently
    see: open work by nearest deadline, then past-due work most recent
    first (see :func:`student_list_order`).

    Returns ``(rows, has_next)`` where each row is the
    ``(Assignment, Group, Course, Level, AcademicTerm)`` tuple.
    Authorization, bucketing, ordering, offset and limit all happen in
    the one SQL statement -- nothing is fetched broadly and reordered in
    Python. Fetches ``PAGE_SIZE + 1`` rows and drops the extra, so "is
    there a next page" costs no second query and discloses no total
    count.

    A past-due Assignment stays in the list on purpose: M01 has no
    submission route, so "Past due" is informational and withdrawing the
    row would hide the record of what was set.
    """
    rows = (
        _visible_assignment_query(student_id, reference_utc)
        .order_by(*student_list_order(reference_utc))
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    has_next = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_next


def student_upcoming_deadlines(student_id, reference_utc, cap=DASHBOARD_DEADLINE_CAP):
    """The Student dashboard's bounded "upcoming deadlines" section: the
    next `cap` visible Assignments whose deadline has **not** passed.

    Deliberately **stricter** than the list: the list keeps past-due rows
    as history, this section excludes them outright with
    ``due_at > reference``, so every row here is genuinely upcoming.
    Because that filter leaves only the open bucket, a plain
    ``due_at ASC, id ASC`` is the whole ordering -- the list's
    bucketing (:func:`student_list_order`) would be a no-op here.

    A dashboard entry can never be something the Student could not open:
    the same shared visibility query backs it. One query, independent of
    how many Assignments exist -- there is no per-Group or per-Assignment
    follow-up read.
    """
    return (
        _visible_assignment_query(student_id, reference_utc)
        .filter(Assignment.due_at > reference_utc)
        .order_by(Assignment.due_at.asc(), Assignment.id.asc())
        .limit(cap)
        .all()
    )


def student_assignment_detail(
    student_id, group_public_id, assignment_public_id, reference_utc
):
    """One SQL query: a visible Assignment plus its authorized hierarchy
    context, or ``None``.

    Adds only the two nested public-id predicates to the shared
    visibility query, so a draft, a not-yet-open Assignment, an
    Assignment whose ``public_id`` belongs to another Group, a withdrawn
    or missing Enrollment, an archived ancestor, and a simply
    non-existent id all produce no row -- and the route turns every one of
    them into the identical non-disclosing 404.
    """
    return (
        _visible_assignment_query(student_id, reference_utc)
        .filter(
            Assignment.public_id == assignment_public_id,
            Group.public_id == group_public_id,
        )
        .first()
    )


def build_student_item(row, tz_name, reference_utc):
    """One plain presentation dict for a Student-visible Assignment.

    `row` is the ``(Assignment, Group, Course, Level, AcademicTerm)``
    tuple returned by the queries above. Only display strings, localized
    times, and public ids -- no ORM row, no Teacher identity, no internal
    id.
    """
    assignment, group, course, level, term = row
    state = derived_state(
        assignment.status, assignment.opens_at, assignment.due_at, reference_utc
    )
    return {
        "public_id": assignment.public_id,
        "group_public_id": group.public_id,
        "title": assignment.title,
        "instructions": assignment.instructions,
        "group_name": group.name,
        "course_title": course.title,
        "level_name": level.name,
        "term_name": term.name,
        "opens_local": to_app_local(tz_name, assignment.opens_at),
        "due_local": to_app_local(tz_name, assignment.due_at),
        "state": state,
        "state_label": STATE_LABELS.get(state),
    }


def build_student_view(rows, tz_name, reference_utc):
    """:func:`build_student_item` over a list of rows."""
    return [build_student_item(row, tz_name, reference_utc) for row in rows]
