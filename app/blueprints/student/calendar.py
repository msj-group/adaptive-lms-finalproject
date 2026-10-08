"""A Student's own calendar (Phase 4 / M10).

One route, and it is a ``GET``::

    GET  /student/calendar

There is deliberately **no** POST, PUT, PATCH or DELETE anywhere on the
Student calendar surface -- no event creation, no RSVP, no
acknowledgement, no "mark as done", no dismiss and no comment. A Student
never writes a calendar entry or anything attached to one, so no write
path exists for one to reach: a POST to this URL returns 405, because no
such endpoint was ever registered. In particular there is **no** Student
route that can touch a ``calendar_events`` row.

**What a Student sees is what is currently theirs.** The whole
visibility formula lives in the SQL ``WHERE`` clauses of
``app/services/calendar_queries.py`` and is applied once, keyed off
``current_user.id``:

- **center events** -- every ``scheduled`` one, because a Student belongs
  to the center whether or not they are currently enrolled in anything.
  A **cancelled** event is never returned, and cannot be: the filter is
  in the query, not in this template;
- **classes** -- occurrences of ``active`` Schedules of Groups where this
  Student holds an ``active`` Enrollment and the whole academic chain
  (Group, AcademicTerm, Course, Level) is ``active``, expanded by the
  canonical arithmetic in ``app/services/schedule_occurrences.py``;
- **assignment opening and due moments** -- of published, already-open
  ordinary Assignments in exactly those Groups;
- **quiz and listening opening and deadline moments** -- of published,
  already-open Quizzes and Listening activities in exactly those Groups.

**What is deliberately excluded**, and excluded by never being fetched
rather than by being hidden in a template: every draft, every
unpublished source, every Assignment or Quiz that has not opened yet
(the existing M01 / M04D rule -- a Student cannot see one before its
opening moment at all), every Group they do not currently belong to,
every cancelled center event, and every internal numeric id. Nothing
about anybody's attempts, submissions, grades, attendance or classmates
appears here either: none of it is a calendar entry, and none of it is
selected by the queries behind this page.

**Visibility is current, not historical.** A withdrawn Enrollment, a
suspended account or an archived link anywhere in the chain removes
those entries from this calendar on the very next request -- the same
rule the M09 announcement feed applies, and deliberately the opposite of
the Gradebook's and Attendance's historical reads. A *past* class or a
*past* deadline inside the range still shows: the range is what decides
what is on the page, and looking back at last week is the ordinary use
of a calendar.

**Every link is server-generated and re-authorized.** An entry's
destination is built by ``url_for`` from the public identifiers the query
returned -- never from anything in the query string -- and the
destination route applies its own full visibility check again, so
following a link from a page rendered a minute ago can only ever land on
the ordinary non-disclosing 404 rather than on something that stopped
being this Student's. Center events carry **no** link at all: there is no
Student center-event page, and none is invented.

The response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``: which classes and deadlines a person has is itself
information about where they belong, and a shared or reused cache entry
must never be able to hand it to somebody else.
"""

from datetime import datetime, timezone

from flask import current_app, request, url_for
from flask_login import current_user

from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
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

#: Which Student surface each group-scoped source leads to. Declared as
#: a table rather than as a chain of ``if``s so the complete set of
#: destinations this page can emit is readable in one place -- and so a
#: source with no entry here provably gets **no** link.
#:
#: All four are existing, authorized Student routes that re-apply their
#: own visibility formula on arrival. ``center_event`` is deliberately
#: absent: there is no Student center-event page.
_DESTINATIONS = {
    SOURCE_CLASS: "student.group_units",
    SOURCE_ASSIGNMENT_OPENS: "student.assignment_detail",
    SOURCE_ASSIGNMENT_DUE: "student.assignment_detail",
    SOURCE_QUIZ_OPENS: "student.quiz_detail",
    SOURCE_QUIZ_DEADLINE: "student.quiz_detail",
    SOURCE_LISTENING_OPENS: "student.listening_detail",
    SOURCE_LISTENING_DEADLINE: "student.listening_detail",
}

#: The object-id keyword each destination takes. A ``class`` entry names
#: only its Group, so it has none.
_OBJECT_ARGUMENT = {
    SOURCE_ASSIGNMENT_OPENS: "assignment_public_id",
    SOURCE_ASSIGNMENT_DUE: "assignment_public_id",
    SOURCE_QUIZ_OPENS: "quiz_public_id",
    SOURCE_QUIZ_DEADLINE: "quiz_public_id",
    SOURCE_LISTENING_OPENS: "listening_public_id",
    SOURCE_LISTENING_DEADLINE: "listening_public_id",
}


def _attach_urls(rows):
    """Fill in each entry's **server-generated** Student destination.

    Built with ``url_for`` from the public identifiers the query layer
    returned, so no path is ever assembled from user input and no
    internal id can appear in one. An entry whose source has no Student
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


@student_bp.get("/calendar")
@roles_required(UserRole.STUDENT.value)
def calendar():
    """One bounded date range of this Student's own calendar.

    The default view is the current calendar month in ``APP_TIMEZONE``;
    ``from`` / ``to`` select another range and are validated into at most
    ``calendar_queries.MAX_RANGE_DAYS`` days inside the navigable window.
    A malformed, reversed, oversized or absurd pair shows the default
    month or the nearest legal range rather than reaching SQL, and the
    Previous / Next / This month links are built from the **normalised**
    range, so this page never emits a URL it would have to normalise
    again.

    Five bounded queries, whatever the range holds and however many
    Groups this Student is in: the Schedules, the Assignments, the
    ordinary Quizzes, the Listening activities and the center events.
    Nothing is asked per entry, per Group or per day, and nothing is
    counted -- range navigation is arithmetic on dates.
    """
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    # ONE reference moment for the whole response: the local wall clock
    # the range and the class occurrences use, and the naive-UTC instant
    # the Assignment / Quiz visibility and moments are compared against,
    # are both derived from it. Reading the clock twice could straddle a
    # deadline and render a page that contradicts itself.
    utc_now = datetime.now(timezone.utc)
    today = app_now(tz_name, utc_now).date()
    reference_utc = utc_reference_now(utc_now)

    normalized = normalize_range(
        request.args.get("from"), request.args.get("to"), today
    )
    a_range = normalized.range
    rows, truncated = build_calendar(
        a_range, tz_name, reference_utc, student_id=current_user.id
    )
    _attach_urls(rows)

    return private_no_store(
        "student/calendar/index.html",
        active_nav="calendar",
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
            "student.calendar", **range_args(previous_range(a_range, today))
        ),
        next_url=url_for("student.calendar", **range_args(next_range(a_range, today))),
        today_url=url_for("student.calendar"),
        tz_name=tz_name,
    )
