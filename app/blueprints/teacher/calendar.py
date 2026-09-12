"""A Teacher's own calendar (Phase 4 / M10).

One route, and it is a ``GET``::

    GET  /teacher/calendar

There is deliberately **no** POST, PUT, PATCH or DELETE anywhere on the
Teacher calendar surface, and in particular **no Teacher route that can
create, edit or cancel a ``calendar_events`` row**. Center events are the
center's, managed by Administrators; a Teacher reads them and nothing
more. No such endpoint is registered, so a POST to this URL returns 405
rather than being refused by a check somebody could later relax.

**What a Teacher sees is what is currently theirs to teach.** The whole
visibility formula lives in the SQL ``WHERE`` clauses of
``app/services/calendar_queries.py`` and is applied once, keyed off
``current_user.id``:

- **center events** -- every ``scheduled`` one, because a Teacher belongs
  to the center whether or not they are currently assigned to anything.
  A **cancelled** event is never returned, and cannot be: the filter is
  in the query, not in this template;
- **classes** -- occurrences of ``active`` Schedules of Groups where this
  Teacher holds an ``active`` GroupTeacherAssignment and the whole
  academic chain (Group, AcademicTerm, Course, Level) is ``active``,
  expanded by the canonical arithmetic in
  ``app/services/schedule_occurrences.py``;
- **assignment opening and due moments**, and **quiz and listening
  opening and deadline moments** -- of published ordinary Assignments,
  published ordinary Quizzes and published Listening activities in
  exactly those Groups.

A Teacher deliberately *does* see a published source whose opening
moment is still ahead, which a Student does not: that is the existing
M01 "Scheduled" state, and knowing when their own Assignment opens is
part of teaching it. What a Teacher still does not see is a **draft** --
nothing unpublished is a calendar entry for anybody.

**What is deliberately excluded**, and excluded by never being fetched:
every Group this Teacher is not actively assigned to, every unassigned
Course, every draft, every cancelled center event, every internal
numeric id, and every administrative control. There is no event-creation
button, no cancel button and no management link on this page, because
there is no Teacher endpoint behind one. Nothing about any individual
Student -- their attempts, submissions, grades, attendance or enrollment
-- appears here either: none of it is a calendar entry, and none of it is
selected by the queries behind this page.

**Visibility is current, not historical.** A removed assignment, a
suspended account or an archived link anywhere in the chain removes
those entries on the very next request. A *past* class or deadline
inside the range still shows: the range is what decides what is on the
page.

**Every link is server-generated and re-authorized.** An entry's
destination is built by ``url_for`` from the public identifiers the query
returned -- never from anything in the query string -- and points only at
existing, authorized Group-scoped Teacher routes, each of which re-proves
the active assignment on arrival. Center events carry **no** link:
there is no Teacher center-event page, and none is invented.

The response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``: which classes and deadlines a person has is itself
information about where they belong.
"""

from datetime import datetime, timezone

from flask import current_app, request, url_for
from flask_login import current_user

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.assignments import _private_no_store
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.calendar_queries import (
    MAX_CALENDAR_ROWS,
    MAX_RANGE_DAYS,
    SOURCE_ASSIGNMENT_DUE,
    SOURCE_ASSIGNMENT_OPENS,
    SOURCE_CLASS,
    SOURCE_LISTENING_DEADLINE,
    SOURCE_LISTENING_OPENS,
    SOURCE_QUIZ_DEADLINE,
    SOURCE_QUIZ_OPENS,
    build_calendar,
    group_by_day,
    next_range,
    normalize_range,
    previous_range,
    range_args,
    span_days,
)
from app.services.schedule_occurrences import app_now, utc_reference_now

_RANGE_NORMALIZED_MESSAGE = (
    f"That date range could not be used as asked for, so your calendar is showing the "
    f"nearest range it can. A calendar view covers at most {MAX_RANGE_DAYS} days."
)

#: Which Teacher surface each group-scoped source leads to. Declared as a
#: table rather than as a chain of ``if``s so the complete set of
#: destinations this page can emit is readable in one place -- and so a
#: source with no entry here provably gets **no** link.
#:
#: Every one is an existing, authorized, Group-nested Teacher **read**
#: route that re-proves the active assignment on arrival. A class leads
#: to that Group's attendance page, which is the Teacher surface that
#: owns a class meeting; an Assignment entry leads to the Group's
#: Assignment list, because this project has no per-Assignment Teacher
#: read page (only an edit form and a submissions list, neither of which
#: is where a calendar should drop somebody). ``center_event`` is
#: deliberately absent: a Teacher has no center-event surface at all.
_DESTINATIONS = {
    SOURCE_CLASS: "teacher.group_attendance",
    SOURCE_ASSIGNMENT_OPENS: "teacher.group_assignments",
    SOURCE_ASSIGNMENT_DUE: "teacher.group_assignments",
    SOURCE_QUIZ_OPENS: "teacher.quiz_detail",
    SOURCE_QUIZ_DEADLINE: "teacher.quiz_detail",
    SOURCE_LISTENING_OPENS: "teacher.listening_detail",
    SOURCE_LISTENING_DEADLINE: "teacher.listening_detail",
}

#: The object-id keyword each destination takes. ``class`` and the two
#: Assignment sources name only their Group, so they have none.
_OBJECT_ARGUMENT = {
    SOURCE_QUIZ_OPENS: "quiz_public_id",
    SOURCE_QUIZ_DEADLINE: "quiz_public_id",
    SOURCE_LISTENING_OPENS: "listening_public_id",
    SOURCE_LISTENING_DEADLINE: "listening_public_id",
}


def _attach_urls(rows):
    """Fill in each entry's **server-generated** Teacher destination.

    Built with ``url_for`` from the public identifiers the query layer
    returned, so no path is ever assembled from user input and no
    internal id can appear in one. An entry whose source has no Teacher
    destination -- a center event -- keeps ``url = None`` and renders as
    plain text.
    """
    for row in rows:
        endpoint = _DESTINATIONS.get(row["source"])
        if endpoint is None or not row["group_public_id"]:
            continue
        kwargs = {"group_public_id": row["group_public_id"]}
        argument = _OBJECT_ARGUMENT.get(row["source"])
        if argument is not None:
            if not row["source_public_id"]:
                continue
            kwargs[argument] = row["source_public_id"]
        row["url"] = url_for(endpoint, **kwargs)


@teacher_bp.get("/calendar")
@roles_required(UserRole.TEACHER.value)
def calendar():
    """One bounded date range of this Teacher's own calendar.

    The default view is the current calendar month in ``APP_TIMEZONE``;
    ``from`` / ``to`` select another range and are validated into at most
    ``calendar_queries.MAX_RANGE_DAYS`` days inside the navigable window.
    A malformed, reversed, oversized or absurd pair shows the default
    month or the nearest legal range rather than reaching SQL, and the
    Previous / Next / This month links are built from the **normalised**
    range.

    Five bounded queries, whatever the range holds and however many
    Groups this Teacher is assigned to. Nothing is asked per entry, per
    Group or per day, and nothing is counted.
    """
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    # ONE reference moment for the whole response -- see the Student
    # calendar's identical note.
    utc_now = datetime.now(timezone.utc)
    today = app_now(tz_name, utc_now).date()
    reference_utc = utc_reference_now(utc_now)

    normalized = normalize_range(
        request.args.get("from"), request.args.get("to"), today
    )
    a_range = normalized.range
    rows, truncated = build_calendar(
        a_range, tz_name, reference_utc, teacher_id=current_user.id
    )
    _attach_urls(rows)

    return _private_no_store(
        "teacher/calendar/index.html",
        days=group_by_day(rows),
        entry_count=len(rows),
        truncated=truncated,
        max_rows=MAX_CALENDAR_ROWS,
        range_start=a_range.start,
        range_end=a_range.end,
        range_days=span_days(a_range),
        range_normalized=normalized.normalized,
        range_message=_RANGE_NORMALIZED_MESSAGE,
        max_range_days=MAX_RANGE_DAYS,
        previous_url=url_for(
            "teacher.calendar", **range_args(previous_range(a_range, today))
        ),
        next_url=url_for("teacher.calendar", **range_args(next_range(a_range, today))),
        today_url=url_for("teacher.calendar"),
        tz_name=tz_name,
    )
