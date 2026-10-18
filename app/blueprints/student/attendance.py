"""A Student's own finalized attendance history (Phase 4 / M07).

Exactly one route, and it is a ``GET``::

    GET  /student/attendance

There is deliberately **no** POST, PUT, PATCH or DELETE anywhere on the
Student attendance surface -- no self-check-in, no correction request, no
dispute, no acknowledgement and no excuse upload. A Student never writes
an attendance record, so no write path exists for one to reach.

**What a Student sees is their own finalized records, and nothing else.**
The visibility formula lives in the SQL ``WHERE`` clause of
``app/services/attendance_queries.py`` and is applied once, keyed off
``current_user.id``:

- the record's ``student_id`` is this Student;
- the acting account still has the ``student`` role and an ``active``
  status -- a foreign key proves a row exists, never that it is still a
  Student's, so the join re-proves it rather than trusting the session;
- the record's session is **finalized**.

**What is deliberately excluded**, and excluded by never being fetched
rather than by being hidden in a template: any other Student's record,
any Teacher-only ``note``, any **draft** session, the Group roster, the
Teacher's identity, and every internal numeric id. A draft is a Teacher's
work in progress -- a roster that starts out entirely ``absent`` -- and
showing it would tell a Student they were marked absent for a class
nobody has finished recording.

**History survives.** The Enrollment, the Group, the Schedule and the
academic ancestors are deliberately *not* required to still be active: a
Student who has withdrawn keeps reading exactly the attendance that was
recorded for them, which is the whole reason the roster is captured at
creation instead of re-derived later.

The owner-approved summary shows counts and each status's percentage of
matching finalized records across all pages. Course, Group, mark and inclusive
class-date filters apply equally to the list and summary. These percentages
describe recorded marks only; no grading or progress rule changes.
"""

from datetime import date

from flask import abort, current_app, request
from flask_login import current_user

from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.attendance_queries import (
    PAGE_SIZE,
    STATUS_LABELS,
    STATUS_VALUES,
    build_student_attendance_view,
    normalize_page,
    student_attendance_page,
    student_attendance_filter_options,
    student_status_totals,
)


def _date_filter(name):
    value = request.args.get(name, "")
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        abort(400)
    if parsed.isoformat() != value:
        abort(400)
    return parsed


@student_bp.get("/attendance")
@roles_required(UserRole.STUDENT.value)
def attendance_list():
    """One bounded page of this Student's own finalized attendance,
    newest class date first.

    Fixed page size, deterministic SQL ordering
    (``session_date DESC, record id DESC``) and ``LIMIT PAGE_SIZE + 1``
    for the has-next flag. Per-status totals are a separate filtered aggregate
    returning at most four rows, rather than a by-product of paging.

    The response carries ``Cache-Control: private, no-store`` and
    ``Vary: Cookie``: this is one person's attendance, and a shared or
    reused cache entry must never be able to hand it to anybody else.
    """
    courses, groups = student_attendance_filter_options(current_user.id)
    course = request.args.get("course", "all") or "all"
    group = request.args.get("group", "all") or "all"
    status = request.args.get("status", "all") or "all"
    if course != "all" and not any(item["public_id"] == course for item in courses):
        abort(404)
    if group != "all":
        selected_group = next((item for item in groups if item["public_id"] == group), None)
        if selected_group is None:
            abort(404)
        # Native GET fallback after changing Course with an old owned Group.
        if course != "all" and selected_group["course_public_id"] != course:
            group = "all"
    if status != "all" and status not in STATUS_VALUES:
        abort(400)
    date_from, date_to = _date_filter("date_from"), _date_filter("date_to")
    if date_from and date_to and date_from > date_to:
        abort(400)
    filters = dict(course=course, group=group, status=status, date_from=date_from, date_to=date_to)
    page = normalize_page(request.args.get("page"))
    rows, has_next = student_attendance_page(current_user.id, page, **filters)
    if not rows and page > 1:
        # A page past the end (a stale bookmark) shows page 1 rather than
        # a confusing empty page with a "Previous" button.
        page = 1
        rows, has_next = student_attendance_page(current_user.id, page, **filters)

    totals, total_count = student_status_totals(current_user.id, **filters)
    for item in totals:
        item["percentage"] = 100 * item["count"] / total_count if total_count else 0
    return private_no_store(
        "student/attendance/list.html",
        records=build_student_attendance_view(rows),
        totals=totals,
        total_count=total_count,
        courses=courses,
        groups=groups,
        status_labels=STATUS_LABELS,
        filters=filters,
        filters_active=any(value != "all" for value in (course, group, status)) or bool(date_from or date_to),
        pagination_filters=dict(course=course, group=group, status=status,
                                date_from=date_from.isoformat() if date_from else "",
                                date_to=date_to.isoformat() if date_to else ""),
        # The stored class date and wall-clock window are LOCAL civil
        # values copied from the Schedule, so the page names the center
        # timezone rather than converting anything.
        tz_name=current_app.config.get("APP_TIMEZONE", "UTC"),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )
