"""The role-specific calendar read model (Phase 4 / M10).

Flask-independent: plain functions over the ORM and over ``datetime``
values, no ``request`` / ``flash`` / ``url_for`` / route decorators,
mirroring every other ``app/services`` module. **Every function here is
read-only** -- no locks, no writes, and nothing a calendar renders can
alter the object it came from.

The one rule this module exists to state exactly once
-----------------------------------------------------
*A calendar is a view of things that already exist.*

M10 introduces exactly one stored object, the center-wide
:class:`~app.models.calendar_event.CalendarEvent`. Everything else on
every calendar in this project is **derived at read time from its own
source row**:

- a **class** entry is an occurrence of an ``active``
  :class:`~app.models.schedule.Schedule`, expanded by the canonical
  arithmetic in ``app/services/schedule_occurrences.py`` --
  :func:`~app.services.schedule_occurrences.iter_occurrences_between`,
  the same function M07's attendance and M09's dashboards already use.
  No occurrence is stored, no recurrence is re-implemented here, and no
  weekday arithmetic happens in a template or in JavaScript;
- an **assignment opens / assignment due** entry is
  ``assignments.opens_at`` / ``assignments.due_at``;
- a **quiz opens / quiz deadline** entry is ``quizzes.opens_at`` /
  ``quizzes.closes_at``;
- a **listening opens / listening deadline** entry is the *same two
  columns* of the Quiz that backs a
  :class:`~app.models.listening_activity.ListeningActivity`;
- a **center event** entry is one ``calendar_events`` row.

Nothing is copied into a new table, nothing is cached, and there is no
"calendar row" table, materialized occurrence table or denormalized date
column anywhere in M10. A deadline moved by a Teacher moves on every
calendar on the next request, because there is only one copy of it.

Visibility is the ``WHERE`` clause, not a filter afterwards
-----------------------------------------------------------
Each source is fetched by a query whose scope **is** the authorization,
keyed off the caller-supplied ``student_id`` / ``teacher_id``:

- a **Student** reaches a class, an Assignment, a Quiz or a Listening
  activity only through an ``active`` Enrollment of their own -- held by
  an account that still has the Student role and an ``active`` status --
  in an **operational** Group, and only when the source itself is
  published and has already opened (the existing M01 / M04D / M05 rule:
  a Student cannot see a published Assignment or Quiz before its opening
  moment at all);
- a **Teacher** reaches them only through an ``active``
  GroupTeacherAssignment of their own, held by an account that still has
  the Teacher role and an ``active`` status, in an **operational** Group,
  and only when the source is published. A Teacher *may* see a published
  source whose opening moment is still ahead -- that is the existing
  M01 "Scheduled" state, and it is theirs to see;
- an **Administrator** reaches every operational Group's sources across
  the center, and is the only role whose calendar returns a ``cancelled``
  center event.

"Operational" means what it means everywhere else in this project: the
Group **and** its AcademicTerm, Course and Level are all ``active``. A
withdrawn Enrollment, a removed assignment, a suspended account, an
archived link anywhere in the chain, a draft source and an unpublished
source each remove the entry on the very next read -- not by being
hidden in a template, but by never being selected.

Speaking activities are deliberately **not** a calendar source. An
Assignment carrying a ``speaking_activities`` row is a Speaking activity
rather than an ordinary Assignment (the M06 rule), every ordinary
Assignment read in this project excludes them with
:func:`~app.services.assignment_queries.has_speaking_extension`, and
M10's approved source list names no speaking source. Including them
would also mean emitting a link to an Assignment surface that correctly
404s for them. The same reasoning, applied the other way, is why an
ordinary Quiz row and its Listening counterpart can never be the same
entry: the ordinary Quiz fetch excludes the extension and the Listening
fetch requires it, so one underlying activity produces exactly one pair
of entries.

Bounds, ordering and cost
-------------------------
- The visible range is a **validated, bounded** span of local civil
  dates, at most :data:`MAX_RANGE_DAYS` days inclusive, within
  :data:`NAVIGATION_WINDOW_DAYS` either side of today. A malformed,
  reversed, oversized or absurd range normalises rather than reaching the
  database, and there is no unbounded historical or future scan.
- Each source type costs **one** bounded query, narrowed by the range in
  SQL. It retains at most :data:`SOURCE_ROW_CAP` source rows and reads
  one extra sentinel row so the page can report when that source had to
  be cut. Nothing is asked per row, per Group or per day, so a
  calendar's cost does not grow with how many entries it shows.
- The merged result is capped at :data:`MAX_CALENDAR_ROWS` entries and
  reports whether it was cut, rather than silently showing part of a
  range as if it were all of it.
- There is **no** ``COUNT`` anywhere: range navigation is decided by
  arithmetic on dates, never by asking the database how much exists.
- Ordering is fully deterministic -- see :func:`sort_key`.
- Every row that leaves this module is a plain presentation dict of
  display strings, local civil date/time values and **public
  identifiers**. No ORM entity, no internal numeric id, no storage key,
  no token, no score, no attempt, no submission, no enrollment detail,
  no roster, no author identity and no raw database value crosses this
  boundary, so rendering a calendar can never lazy-load anything or make
  an authorization decision.

Why the UTC-stored sources are filtered in two steps
----------------------------------------------------
``assignments`` and ``quizzes`` store **naive UTC** instants, while a
calendar range is a span of **local civil dates** in ``APP_TIMEZONE``.
Turning a local date into a UTC instant is exactly what
:func:`~app.services.schedule_occurrences.from_app_local` does -- and it
deliberately *refuses* a local wall-clock value that a real IANA zone
maps to no instant or to two, which midnight can be on a DST transition
day. A range boundary must never be able to raise.

So the SQL filter uses a **deliberately widened** UTC window --
``±15 hours`` around the range, more than the ±14:00 maximum real UTC
offset -- which is guaranteed to be a superset of the instants whose
local date falls inside the range. The exact decision is then made per
row by :func:`~app.services.schedule_occurrences.to_app_local`, which is
total: it always answers, for every instant, in the one direction this
project treats as authoritative. The visibility logic stays entirely in
SQL; only the "which local day is this instant on" classification
happens in Python, over at most :data:`SOURCE_ROW_CAP` already-authorized
rows.
"""

from calendar import monthrange
from collections import namedtuple
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, or_

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Assignment,
    AssignmentStatus,
    CalendarEvent,
    CalendarEventStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    ListeningActivity,
    Quiz,
    QuizStatus,
    Schedule,
    User,
    UserRole,
    UserStatus,
)
from app.services.assignment_queries import has_speaking_extension
from app.services.quiz_queries import has_listening_extension
from app.services.schedule_occurrences import (
    iter_occurrences_between,
    slot_spec,
    to_app_local,
)

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_ASSIGNMENT_PUBLISHED = AssignmentStatus.PUBLISHED.value
_QUIZ_PUBLISHED = QuizStatus.PUBLISHED.value
_EVENT_SCHEDULED = CalendarEventStatus.SCHEDULED.value
_EVENT_CANCELLED = CalendarEventStatus.CANCELLED.value


# ---------------------------------------------------------------------------
# Bounds -- declared here, and owned by this feature alone
# ---------------------------------------------------------------------------

#: The largest number of local civil days one calendar view may span,
#: inclusive of both ends. Two months of a term is as much as anybody
#: reads at once, and it is what keeps every source query's result set
#: small whatever the center's history contains. Not configurable and
#: never client-supplied: an oversized request is narrowed to this, not
#: honoured.
MAX_RANGE_DAYS = 62

#: How far either side of *today* a calendar range may reach. Five years
#: is far beyond any real academic planning horizon and well inside
#: ``date``'s own limits, so navigation can neither overflow a date nor
#: turn into an open-ended scan of the center's whole history.
NAVIGATION_WINDOW_DAYS = 5 * 366

#: How many **source rows** one calendar read retains per source type.
#: Every source query reads at most one additional sentinel row with a
#: deterministic ``ORDER BY``. That sentinel is never rendered; it only
#: proves that the source was cut so the page can say so honestly.
SOURCE_ROW_CAP = 400

#: How many **calendar entries** one range renders after expansion and
#: merging. Reported honestly rather than applied silently: see
#: :func:`build_calendar`, which returns whether the cap was reached so
#: the page can say that the range holds more than it is showing.
MAX_CALENDAR_ROWS = 600


# ---------------------------------------------------------------------------
# Sources -- the closed set of things a calendar entry can be
# ---------------------------------------------------------------------------

SOURCE_CLASS = "class"
SOURCE_ASSIGNMENT_OPENS = "assignment_opens"
SOURCE_ASSIGNMENT_DUE = "assignment_due"
SOURCE_QUIZ_OPENS = "quiz_opens"
SOURCE_QUIZ_DEADLINE = "quiz_deadline"
SOURCE_LISTENING_OPENS = "listening_opens"
SOURCE_LISTENING_DEADLINE = "listening_deadline"
SOURCE_CENTER_EVENT = "center_event"

#: The one label per source, declared once so a badge, an empty state and
#: a test cannot word the same source three different ways. An entry
#: always says **what it is** -- "Assignment due" is never shortened to
#: the assignment's title alone, because a calendar whose entries do not
#: say what they are is a list of dates.
SOURCE_LABELS = {
    SOURCE_CLASS: "Class",
    SOURCE_ASSIGNMENT_OPENS: "Assignment opens",
    SOURCE_ASSIGNMENT_DUE: "Assignment due",
    SOURCE_QUIZ_OPENS: "Quiz opens",
    SOURCE_QUIZ_DEADLINE: "Quiz deadline",
    SOURCE_LISTENING_OPENS: "Listening opens",
    SOURCE_LISTENING_DEADLINE: "Listening deadline",
    SOURCE_CENTER_EVENT: "Center event",
}

#: Administrator-facing label for a center event's own lifecycle. Never
#: shown to a Student or a Teacher: their calendars only ever contain
#: scheduled events, so a status badge there would always say the same
#: thing.
EVENT_STATUS_LABELS = {
    _EVENT_SCHEDULED: "Scheduled",
    _EVENT_CANCELLED: "Cancelled",
}

#: More than the ±14:00 maximum real UTC offset, so the widened UTC
#: window below is guaranteed to contain every instant whose local civil
#: date falls inside the requested range. See the module docstring.
_UTC_WINDOW_PAD = timedelta(hours=15)


# ---------------------------------------------------------------------------
# The date range -- parsing, normalising and navigating
# ---------------------------------------------------------------------------

#: One inclusive span of **local civil dates** in ``APP_TIMEZONE``.
CalendarRange = namedtuple("CalendarRange", "start end")

#: :func:`normalize_range`'s answer. ``normalized`` is ``True`` when the
#: requested range was not usable exactly as asked for -- malformed,
#: reversed, out of the navigable window, or wider than
#: :data:`MAX_RANGE_DAYS` -- so a page can say so instead of silently
#: showing something other than what the URL asked for.
NormalizedRange = namedtuple("NormalizedRange", "range normalized")


def month_range(any_date):
    """The whole calendar month containing `any_date`, as a
    :class:`CalendarRange`.

    The default view of every calendar in M10: "this month, in the
    center's timezone". A calendar month is at most 31 days and therefore
    always inside :data:`MAX_RANGE_DAYS`.
    """
    last = monthrange(any_date.year, any_date.month)[1]
    return CalendarRange(
        any_date.replace(day=1), any_date.replace(day=last)
    )


def span_days(a_range):
    """How many local civil days `a_range` covers, inclusive."""
    return (a_range.end - a_range.start).days + 1


def is_whole_month(a_range):
    """``True`` when `a_range` is exactly one whole calendar month.

    Used only by :func:`previous_range` / :func:`next_range`, so that the
    default month view navigates by *months* -- which is what somebody
    looking at May expects of "Previous" -- while a hand-made range
    navigates by its own length.
    """
    if a_range.start.day != 1:
        return False
    last = monthrange(a_range.start.year, a_range.start.month)[1]
    return a_range.end == a_range.start.replace(day=last)


def _clamp(a_date, today):
    lo = today - timedelta(days=NAVIGATION_WINDOW_DAYS)
    hi = today + timedelta(days=NAVIGATION_WINDOW_DAYS)
    if a_date < lo:
        return lo
    if a_date > hi:
        return hi
    return a_date


def previous_range(a_range, today):
    """The range immediately before `a_range`: the previous whole month
    for a whole-month view, otherwise the same number of days ending the
    day before it starts. Clamped into the navigable window, so
    "Previous" can never walk off the end of the calendar.
    """
    lo = today - timedelta(days=NAVIGATION_WINDOW_DAYS)
    hi = today + timedelta(days=NAVIGATION_WINDOW_DAYS)
    if a_range.start <= lo:
        return a_range
    if is_whole_month(a_range):
        target = month_range(a_range.start - timedelta(days=1))
        return CalendarRange(max(target.start, lo), min(target.end, hi))
    days = span_days(a_range)
    end = a_range.start - timedelta(days=1)
    start = max(end - timedelta(days=days - 1), lo)
    return CalendarRange(start, end)


def next_range(a_range, today):
    """The range immediately after `a_range`. Mirror of
    :func:`previous_range`."""
    lo = today - timedelta(days=NAVIGATION_WINDOW_DAYS)
    hi = today + timedelta(days=NAVIGATION_WINDOW_DAYS)
    if a_range.end >= hi:
        return a_range
    if is_whole_month(a_range):
        target = month_range(a_range.end + timedelta(days=1))
        return CalendarRange(max(target.start, lo), min(target.end, hi))
    days = span_days(a_range)
    start = a_range.end + timedelta(days=1)
    end = min(start + timedelta(days=days - 1), hi)
    return CalendarRange(start, end)


def _parse_date(raw):
    """One ``YYYY-MM-DD`` query argument as a ``date``, or ``None``.

    A missing, empty, malformed or impossible value (``2026-02-30``) all
    become ``None`` -- exactly as ``admin/attendance._date_arg`` already
    treats its filter -- rather than reaching the database or raising.
    ``date.fromisoformat`` is deliberately strict: it accepts no
    separators, no locales and no partial dates, so nothing else has to
    be validated away.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def normalize_range(raw_from, raw_to, today):
    """The bounded :class:`CalendarRange` a request may actually read,
    plus whether it had to be normalised.

    The rules, applied in this order and each of them a *narrowing*:

    1. either date missing or unparseable -> the whole current month in
       ``APP_TIMEZONE``. A malformed URL shows the default view rather
       than an error page, and never reaches SQL;
    2. reversed (``from`` after ``to``) -> swapped, because somebody who
       typed the two the other way round asked for that span;
    3. outside the navigable window -> clamped into it. Clamping is
       monotone, so a swapped-then-clamped range is still ordered;
    4. wider than :data:`MAX_RANGE_DAYS` -> the end is pulled back to
       ``start + MAX_RANGE_DAYS - 1``. The *start* is kept, so "from this
       date" still means what it says.

    ``normalized`` is ``True`` whenever the answer differs from what was
    asked for -- including the "one date supplied, the other missing"
    case, which is a half-written range rather than a request for the
    default month.
    """
    start = _parse_date(raw_from)
    end = _parse_date(raw_to)
    if start is None or end is None:
        asked_for_something = bool((raw_from or "").strip() or (raw_to or "").strip())
        return NormalizedRange(month_range(today), asked_for_something)

    normalized = False
    if start > end:
        start, end = end, start
        normalized = True

    clamped_start, clamped_end = _clamp(start, today), _clamp(end, today)
    if (clamped_start, clamped_end) != (start, end):
        normalized = True
    start, end = clamped_start, clamped_end

    if (end - start).days + 1 > MAX_RANGE_DAYS:
        end = start + timedelta(days=MAX_RANGE_DAYS - 1)
        normalized = True

    return NormalizedRange(CalendarRange(start, end), normalized)


def range_args(a_range):
    """The two canonical query-string values for `a_range`.

    Every navigation link in M10 is built from a **normalised** range, so
    the URLs a page emits are always ones this module would accept back
    unchanged -- no half-written range, no reversed pair and no oversized
    span is ever produced by the application itself.
    """
    return {"from": a_range.start.isoformat(), "to": a_range.end.isoformat()}


def _utc_window(a_range):
    """The widened naive-UTC window that is guaranteed to contain every
    instant whose local civil date lies inside `a_range`. See the module
    docstring for why it is widened rather than converted exactly.
    """
    return (
        datetime.combine(a_range.start, time.min) - _UTC_WINDOW_PAD,
        datetime.combine(a_range.end, time.min) + timedelta(days=1) + _UTC_WINDOW_PAD,
    )


# ---------------------------------------------------------------------------
# Scope -- the role's authorization, as SQL
# ---------------------------------------------------------------------------


def _operational_chain(query):
    """Join ``Group -> Course -> Level`` and ``Group -> AcademicTerm``
    and require every link to be ``active``.

    A legacy inconsistent ancestor (an active Group under an archived
    Course, only reachable by direct database manipulation) is treated
    honestly as non-operational rather than repaired -- the same choice
    ``dashboard_queries`` and ``announcement_queries`` make.
    """
    return (
        query.join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .filter(
            Group.status == _ACTIVE,
            Course.status == _ACTIVE,
            Level.status == _ACTIVE,
            AcademicTerm.status == _ACTIVE,
        )
    )


def _membership(query, student_id, teacher_id):
    """Narrow `query` to the Groups the acting role actually belongs to.

    Exactly one of `student_id` / `teacher_id` is given for a Student or
    a Teacher; both are ``None`` for an Administrator, whose calendar is
    center-wide by role and therefore adds no membership join at all.

    The ``users`` join is not redundant with the session: a foreign key
    into ``users`` proves the row exists, never that it is still a
    Student's or a Teacher's, or that the account is still ``active`` --
    the project rule already applied to every Enrollment and
    GroupTeacherAssignment read.

    ``uq_enrollments_student_group`` and
    ``uq_group_teacher_assignments_group_teacher`` mean one person holds
    at most one membership row per Group, so these joins can never
    multiply a source row into duplicates.
    """
    if student_id is not None:
        return (
            query.join(Enrollment, Enrollment.group_id == Group.id)
            .join(User, Enrollment.student_id == User.id)
            .filter(
                User.id == student_id,
                User.role == UserRole.STUDENT.value,
                User.status == _USER_ACTIVE,
                Enrollment.status == _ENROLLMENT_ACTIVE,
            )
        )
    if teacher_id is not None:
        return (
            query.join(
                GroupTeacherAssignment, GroupTeacherAssignment.group_id == Group.id
            )
            .join(User, GroupTeacherAssignment.teacher_id == User.id)
            .filter(
                User.id == teacher_id,
                User.role == UserRole.TEACHER.value,
                User.status == _USER_ACTIVE,
                GroupTeacherAssignment.status == _ASSIGNMENT_ACTIVE,
            )
        )
    return query


#: The display context every group-scoped entry carries, and nothing
#: more. The Level's and AcademicTerm's **names** are deliberately not
#: selected even though both tables are already joined for their status
#: filters: a calendar entry says which Group and which Course it belongs
#: to, and a projection that fetches values no row uses is a projection
#: somebody will later be tempted to expose.
_GROUP_CONTEXT_COLUMNS = (
    Group.public_id.label("group_public_id"),
    Group.name.label("group_name"),
    Course.title.label("course_title"),
)


def _group_context(row):
    """The one short phrase naming *where* a group-scoped entry belongs.

    The Group's name and its Course's title, reached through the Group
    rather than stored twice, so the two can never disagree after an
    Administrator retargets a Group. An entry whose own title does not
    say which subject it is -- an Assignment, a Quiz, a Listening
    activity -- needs both.
    """
    return f"{row.group_name} · {row.course_title}"


# ---------------------------------------------------------------------------
# Source fetches -- one bounded query each
# ---------------------------------------------------------------------------


class _BoundedSourceRows(list):
    """A list of retained source rows plus whether one more row existed.

    Keeping the list interface preserves the small, useful source-query
    API while carrying the one fact :func:`build_calendar` needs to avoid
    silent truncation. The sentinel row itself is never expanded or
    rendered.
    """

    __slots__ = ("truncated",)

    def __init__(self, rows, truncated):
        super().__init__(rows)
        self.truncated = truncated


def _bounded_source_rows(query):
    """Execute one ordered source query with a single overflow sentinel."""
    rows = query.limit(SOURCE_ROW_CAP + 1).all()
    return _BoundedSourceRows(
        rows[:SOURCE_ROW_CAP], truncated=len(rows) > SOURCE_ROW_CAP
    )


def _require_reference(student_id, reference_utc):
    """A Student read gates on "has this opened yet?", so it cannot
    proceed without the request's reference moment.

    Raised loudly rather than left to SQL: ``opens_at <= NULL`` is never
    true, so a missing moment would silently return an **empty**
    calendar, and an empty calendar looks exactly like a Student with
    nothing on. Fail closed, but say so.
    """
    if student_id is not None and reference_utc is None:
        raise ValueError(
            "a Student calendar read requires the request's naive-UTC reference moment"
        )


def schedule_source_rows(a_range, student_id=None, teacher_id=None):
    """Every ``active`` Schedule the role may see whose effective period
    overlaps `a_range`, column-projected with its display context.

    The overlap test is in SQL (``effective_start_date <= range.end AND
    effective_end_date >= range.start``), so a Schedule that produces no
    occurrence in this range is never fetched and never expanded.
    """
    query = _membership(
        _operational_chain(
            db.session.query(
                Schedule.public_id.label("public_id"),
                Schedule.day_of_week,
                Schedule.start_time,
                Schedule.end_time,
                Schedule.effective_start_date,
                Schedule.effective_end_date,
                Schedule.location,
                *_GROUP_CONTEXT_COLUMNS,
            )
            .select_from(Schedule)
            .join(Group, Schedule.group_id == Group.id)
        ),
        student_id,
        teacher_id,
    )
    return _bounded_source_rows(
        query.filter(
            Schedule.status == _ACTIVE,
            Schedule.effective_start_date <= a_range.end,
            Schedule.effective_end_date >= a_range.start,
        )
        .order_by(Schedule.id.asc())
    )


def assignment_source_rows(a_range, student_id=None, teacher_id=None, reference_utc=None):
    """Every **published** ordinary Assignment the role may see with an
    opening or a due instant inside the widened UTC window of `a_range`.

    Speaking activities are excluded (``~has_speaking_extension()``) --
    see the module docstring. For a Student the existing M01 visibility
    rule additionally applies: a published Assignment is not visible at
    all before ``opens_at``, so `reference_utc` is required and is
    compared in SQL.
    """
    _require_reference(student_id, reference_utc)
    window_start, window_end = _utc_window(a_range)
    query = _membership(
        _operational_chain(
            db.session.query(
                Assignment.public_id.label("public_id"),
                Assignment.title,
                Assignment.opens_at,
                Assignment.due_at,
                *_GROUP_CONTEXT_COLUMNS,
            )
            .select_from(Assignment)
            .join(Group, Assignment.group_id == Group.id)
        ),
        student_id,
        teacher_id,
    ).filter(
        Assignment.status == _ASSIGNMENT_PUBLISHED,
        ~has_speaking_extension(),
        or_(
            and_(Assignment.opens_at >= window_start, Assignment.opens_at < window_end),
            and_(Assignment.due_at >= window_start, Assignment.due_at < window_end),
        ),
    )
    if student_id is not None:
        query = query.filter(Assignment.opens_at <= reference_utc)
    return _bounded_source_rows(query.order_by(Assignment.id.asc()))


def _quiz_source_query(a_range, listening, student_id, teacher_id, reference_utc):
    """The shared fetch behind both Quiz sources.

    ``listening`` is the one scope that separates them: ``False`` selects
    ordinary Quizzes and ``True`` selects the Quizzes that carry a
    Listening extension. Parameterising the one query rather than writing
    two is deliberate -- two definitions could be tightened separately
    and drift apart, and a Quiz would then be able to appear on a
    calendar twice or not at all. This is exactly how
    ``quiz_queries.student_visible_quiz_query`` already separates the two
    Student surfaces.
    """
    _require_reference(student_id, reference_utc)
    window_start, window_end = _utc_window(a_range)
    columns = [
        Quiz.public_id.label("quiz_public_id"),
        Quiz.title,
        Quiz.opens_at,
        Quiz.closes_at,
        *_GROUP_CONTEXT_COLUMNS,
    ]
    if listening:
        columns.append(ListeningActivity.public_id.label("listening_public_id"))
    query = (
        db.session.query(*columns)
        .select_from(Quiz)
        .join(Group, Quiz.group_id == Group.id)
    )
    if listening:
        query = query.join(ListeningActivity, ListeningActivity.quiz_id == Quiz.id)
    query = _membership(_operational_chain(query), student_id, teacher_id).filter(
        Quiz.status == _QUIZ_PUBLISHED,
        has_listening_extension() if listening else ~has_listening_extension(),
        or_(
            and_(Quiz.opens_at >= window_start, Quiz.opens_at < window_end),
            and_(Quiz.closes_at >= window_start, Quiz.closes_at < window_end),
        ),
    )
    if student_id is not None:
        query = query.filter(Quiz.opens_at <= reference_utc)
    return _bounded_source_rows(query.order_by(Quiz.id.asc()))


def quiz_source_rows(a_range, student_id=None, teacher_id=None, reference_utc=None):
    """Every published **ordinary** Quiz the role may see with an opening
    or closing instant inside the widened UTC window of `a_range`."""
    return _quiz_source_query(a_range, False, student_id, teacher_id, reference_utc)


def listening_source_rows(a_range, student_id=None, teacher_id=None, reference_utc=None):
    """Every published **Listening activity** the role may see with an
    opening or closing instant inside the widened UTC window of
    `a_range`.

    A Listening activity has no lifecycle, deadline or availability
    window of its own: it *is* a Quiz (the M05 rule), so these are the
    backing Quiz's own ``opens_at`` / ``closes_at``. The row carries the
    **activity's** public id, because that is what the Listening routes
    address, and the entry is labelled as Listening rather than as a
    Quiz.
    """
    return _quiz_source_query(a_range, True, student_id, teacher_id, reference_utc)


def center_event_source_rows(a_range, include_cancelled=False):
    """Every center event whose local civil date lies inside `a_range`.

    ``event_date`` is already a local civil ``DATE``, so this is a plain
    inclusive comparison -- no conversion, and nothing for a timezone to
    get wrong.

    ``include_cancelled`` is ``False`` for every Student and Teacher
    read: a cancelled event must never come back for them, and it cannot,
    because ``status = 'scheduled'`` is in the ``WHERE`` clause rather
    than in a template condition. Only the Administrator surfaces pass
    ``True``.
    """
    query = db.session.query(
        CalendarEvent.public_id.label("public_id"),
        CalendarEvent.title,
        CalendarEvent.details,
        CalendarEvent.event_date,
        CalendarEvent.start_time,
        CalendarEvent.end_time,
        CalendarEvent.location,
        CalendarEvent.status,
    ).filter(
        CalendarEvent.event_date >= a_range.start,
        CalendarEvent.event_date <= a_range.end,
    )
    if not include_cancelled:
        query = query.filter(CalendarEvent.status == _EVENT_SCHEDULED)
    return _bounded_source_rows(
        query.order_by(CalendarEvent.event_date.asc(), CalendarEvent.id.asc())
    )


# ---------------------------------------------------------------------------
# The one canonical calendar row
# ---------------------------------------------------------------------------


def calendar_row(
    source,
    event_date,
    title,
    start_time=None,
    end_time=None,
    context="",
    location=None,
    details=None,
    group_public_id=None,
    source_public_id=None,
    status=None,
):
    """One calendar entry, as the single presentation structure every
    source is reduced to.

    Only display strings, local civil date/time values and **public
    identifiers**. Deliberately absent: every internal numeric id, every
    storage key, every signed token, every score, attempt, submission,
    grade, attendance mark, roster, recipient and author identity, and
    every raw ORM row -- none of them is selected by the queries above,
    so none of them can leak from here.

    ``url`` starts as ``None`` and is filled in by the blueprint that
    renders the calendar, never here: this module is Flask-independent,
    and the *destination* of an entry is role-specific by nature. See
    each blueprint's own ``_attach_urls``.
    """
    return {
        "source": source,
        "source_label": SOURCE_LABELS[source],
        "event_date": event_date,
        "start_time": start_time,
        "end_time": end_time,
        "all_day": start_time is None and end_time is None,
        "title": title,
        "context": context,
        "location": location,
        "details": details,
        "group_public_id": group_public_id,
        "source_public_id": source_public_id,
        "cancelled": status == _EVENT_CANCELLED,
        "status_label": None if status is None else EVENT_STATUS_LABELS.get(status),
        "url": None,
    }


def sort_key(row):
    """The deterministic order of one calendar entry.

    Ascending by:

    1. the local civil date;
    2. **timed entries before all-day entries** on the same date -- an
       all-day center event is a property of the whole day, so it reads
       correctly after the day's timetable rather than interleaved into
       it at an invented midnight;
    3. the local start time, and then the local end time -- of two
       entries starting at the same moment the one that finishes first
       comes first, which is how a timetable reads. An entry that is a
       single instant (an assignment due moment, a quiz deadline) carries
       a start and no end, so it sorts before a class that starts at the
       same minute and runs on;
    4. the source kind, so two entries at the very same moment are
       grouped by what they are rather than arbitrarily;
    5. the entry's own **public id** -- the stable final tie-break. It is
       already in the row (it is what the links are built from), so no
       internal id is needed to make the order total.
    """
    return (
        row["event_date"],
        1 if row["all_day"] else 0,
        row["start_time"] or time.min,
        row["end_time"] or time.min,
        row["source"],
        row["source_public_id"] or "",
    )


def _in_range(a_range, local_moment):
    return a_range.start <= local_moment.date() <= a_range.end


def _class_rows(a_range, rows):
    """Expand each fetched Schedule into its occurrences inside
    `a_range`, using the canonical
    :func:`~app.services.schedule_occurrences.iter_occurrences_between`.

    Bounded by construction: each slot yields at most
    ``(range length / 7) + 1`` occurrences -- nine, at
    :data:`MAX_RANGE_DAYS` -- and the number of slots is already capped
    by :data:`SOURCE_ROW_CAP`. Nothing here re-implements recurrence, and
    no occurrence is written anywhere.
    """
    out = []
    for row in rows:
        for occurrence in iter_occurrences_between(
            slot_spec(row, ref=row), a_range.start, a_range.end
        ):
            out.append(
                calendar_row(
                    SOURCE_CLASS,
                    occurrence.start.date(),
                    # A class *is* its Course meeting, so the Course title
                    # is the entry's own name and the context only has to
                    # say which Group's class it is. Repeating the Course
                    # in both would be the same word twice on one card --
                    # and a Student in two Groups of one Course needs
                    # exactly the Group to tell the two entries apart.
                    row.course_title,
                    start_time=occurrence.start.time(),
                    end_time=occurrence.end.time(),
                    context=row.group_name,
                    location=row.location,
                    group_public_id=row.group_public_id,
                    source_public_id=row.public_id,
                )
            )
    return out


def _instant_rows(tz_name, a_range, rows, moments):
    """Turn UTC instants on already-authorized source rows into calendar
    entries on their **local** civil day.

    `moments` names, per row, which attribute is which source kind and
    which public id to carry. A source whose only relevant instant falls
    outside the range contributes nothing; a source with both instants in
    range contributes **both**, each with its own distinct label, which
    is what makes "opens" and "due" two entries rather than one ambiguous
    one.
    """
    out = []
    for row in rows:
        context = _group_context(row)
        for source, attribute, public_id_attribute in moments:
            moment = getattr(row, attribute)
            if moment is None:
                continue
            local = to_app_local(tz_name, moment)
            if not _in_range(a_range, local):
                continue
            out.append(
                calendar_row(
                    source,
                    local.date(),
                    row.title,
                    start_time=local.time(),
                    context=context,
                    group_public_id=row.group_public_id,
                    source_public_id=getattr(row, public_id_attribute),
                )
            )
    return out


def _center_rows(rows):
    return [
        calendar_row(
            SOURCE_CENTER_EVENT,
            row.event_date,
            row.title,
            start_time=row.start_time,
            end_time=row.end_time,
            location=row.location,
            details=row.details,
            source_public_id=row.public_id,
            status=row.status,
        )
        for row in rows
    ]


_ASSIGNMENT_MOMENTS = (
    (SOURCE_ASSIGNMENT_OPENS, "opens_at", "public_id"),
    (SOURCE_ASSIGNMENT_DUE, "due_at", "public_id"),
)
_QUIZ_MOMENTS = (
    (SOURCE_QUIZ_OPENS, "opens_at", "quiz_public_id"),
    (SOURCE_QUIZ_DEADLINE, "closes_at", "quiz_public_id"),
)
_LISTENING_MOMENTS = (
    (SOURCE_LISTENING_OPENS, "opens_at", "listening_public_id"),
    (SOURCE_LISTENING_DEADLINE, "closes_at", "listening_public_id"),
)


def build_calendar(
    a_range,
    tz_name,
    reference_utc,
    student_id=None,
    teacher_id=None,
    include_cancelled_events=False,
):
    """``(rows, truncated)`` -- every calendar entry the acting role may
    see inside `a_range`, in :func:`sort_key` order.

    Exactly five bounded queries, whatever the range holds and whatever
    the center's history contains: the Schedules, the Assignments, the
    ordinary Quizzes, the Listening activities and the center events.
    Nothing is asked per entry, per Group or per day, so a calendar
    showing one entry and a calendar showing hundreds cost the same
    number of statements.

    ``truncated`` is ``True`` when the merged range exceeds
    :data:`MAX_CALENDAR_ROWS` entries **or** any source query reaches its
    own row cap. The latter is carried by one overflow sentinel per
    query; without it, a range containing 401 center events would return
    400 rows and incorrectly claim that nothing had been omitted.
    """
    schedule_rows = schedule_source_rows(a_range, student_id, teacher_id)
    assignment_rows = assignment_source_rows(
        a_range, student_id, teacher_id, reference_utc
    )
    quiz_rows = quiz_source_rows(a_range, student_id, teacher_id, reference_utc)
    listening_rows = listening_source_rows(
        a_range, student_id, teacher_id, reference_utc
    )
    event_rows = center_event_source_rows(
        a_range, include_cancelled=include_cancelled_events
    )
    source_truncated = any(
        getattr(source_rows, "truncated", False)
        for source_rows in (
            schedule_rows,
            assignment_rows,
            quiz_rows,
            listening_rows,
            event_rows,
        )
    )

    rows = []
    rows.extend(_class_rows(a_range, schedule_rows))
    rows.extend(
        _instant_rows(
            tz_name,
            a_range,
            assignment_rows,
            _ASSIGNMENT_MOMENTS,
        )
    )
    rows.extend(
        _instant_rows(
            tz_name,
            a_range,
            quiz_rows,
            _QUIZ_MOMENTS,
        )
    )
    rows.extend(
        _instant_rows(
            tz_name,
            a_range,
            listening_rows,
            _LISTENING_MOMENTS,
        )
    )
    rows.extend(_center_rows(event_rows))
    rows.sort(key=sort_key)
    return (
        rows[:MAX_CALENDAR_ROWS],
        source_truncated or len(rows) > MAX_CALENDAR_ROWS,
    )


def group_by_day(rows):
    """The entries grouped into one bucket per local civil day that
    actually has any, ascending.

    Days with nothing on them are deliberately **not** emitted: a
    two-month range would otherwise be mostly empty cells, and the page's
    own empty state already says when a whole range is empty. `rows` must
    already be in :func:`sort_key` order, which
    :func:`build_calendar` guarantees.
    """
    days = []
    for row in rows:
        if not days or days[-1]["date"] != row["event_date"]:
            days.append({"date": row["event_date"], "rows": []})
        days[-1]["rows"].append(row)
    return days


# ---------------------------------------------------------------------------
# Administrator center-event reads
# ---------------------------------------------------------------------------


def _local(tz_name, moment):
    """`moment` (stored naive UTC) as a naive local wall clock, or
    ``None``. A NULL cancellation timestamp is a legitimate state, so it
    passes through rather than becoming an epoch."""
    return None if moment is None else to_app_local(tz_name, moment)


def _event_select():
    """The projection the Administrator event surfaces use.

    The internal ``id`` is selected here -- and **only** here -- because
    the write paths need it to name the row they are about to lock, after
    a non-locking public-id lookup has decided *which* row that is. It is
    never placed in a presentation dict, a form value, a signed token or
    the rendered HTML; :func:`build_event_view` is what templates get.
    """
    return db.session.query(
        CalendarEvent.id.label("id"),
        CalendarEvent.public_id.label("public_id"),
        CalendarEvent.title,
        CalendarEvent.details,
        CalendarEvent.event_date,
        CalendarEvent.start_time,
        CalendarEvent.end_time,
        CalendarEvent.location,
        CalendarEvent.status,
        CalendarEvent.cancelled_at,
        CalendarEvent.version,
        CalendarEvent.created_at,
        CalendarEvent.updated_at,
        User.full_name.label("created_by_name"),
    ).join(User, CalendarEvent.created_by_id == User.id)


def admin_event(public_id):
    """One center event by ``public_id`` for the Administrator surfaces,
    or ``None``.

    ``None`` for a public id that does not exist and for one that belongs
    to a different kind of object alike -- the caller turns both into the
    same non-disclosing 404.
    """
    if not public_id:
        return None
    return _event_select().filter(CalendarEvent.public_id == public_id).first()


def build_event_view(row, tz_name="UTC"):
    """One plain presentation dict for a center event on an
    Administrator surface.

    The event's own date and times are **local civil values already** and
    are passed through untouched -- converting them would be exactly the
    "compare a local value to UTC" mistake this milestone is required not
    to make. The three audit timestamps are stored naive UTC and *are*
    converted here through the project's :func:`to_app_local`, so no
    template ever does timezone arithmetic.

    Still no internal numeric id: the extra columns over a reader's view
    are the lifecycle, the version and the creator's display name, all of
    which an Administrator legitimately needs.
    """
    return {
        "public_id": row.public_id,
        "title": row.title,
        "details": row.details,
        "event_date": row.event_date,
        "start_time": row.start_time,
        "end_time": row.end_time,
        "all_day": row.start_time is None and row.end_time is None,
        "location": row.location,
        "status": row.status,
        "status_label": EVENT_STATUS_LABELS.get(row.status, row.status),
        "cancelled": row.status == _EVENT_CANCELLED,
        "version": row.version,
        "cancelled_local": _local(tz_name, row.cancelled_at),
        "created_local": _local(tz_name, row.created_at),
        "updated_local": _local(tz_name, row.updated_at),
        "created_by_name": row.created_by_name,
    }
