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

**Totals are counts, never a score.** The summary shows how many times
each of the four statuses appears in this Student's own finalized
history, from one bounded aggregate scoped to that Student. There is no
percentage, no rate, no "attendance score" and no center-wide comparison:
any of those would read as a grade, Grades are an undecided module, and a
percentage over a partially recorded term would be actively misleading.
Nothing here touches the dashboard or any progress figure.
"""

from flask import current_app, request
from flask_login import current_user

from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.attendance_queries import (
    PAGE_SIZE,
    build_student_attendance_view,
    normalize_page,
    student_attendance_page,
    student_status_totals,
)


@student_bp.get("/attendance")
@roles_required(UserRole.STUDENT.value)
def attendance_list():
    """One bounded page of this Student's own finalized attendance,
    newest class date first.

    Fixed page size, deterministic SQL ordering
    (``session_date DESC, record id DESC``) and ``LIMIT PAGE_SIZE + 1``
    for the has-next flag with **no** ``COUNT`` -- an exact total would be
    an unbounded scan, and the per-status totals below are a separate,
    deliberately bounded aggregate rather than a by-product of paging.

    The response carries ``Cache-Control: private, no-store`` and
    ``Vary: Cookie``: this is one person's attendance, and a shared or
    reused cache entry must never be able to hand it to anybody else.
    """
    page = normalize_page(request.args.get("page"))
    rows, has_next = student_attendance_page(current_user.id, page)
    if not rows and page > 1:
        # A page past the end (a stale bookmark) shows page 1 rather than
        # a confusing empty page with a "Previous" button.
        page = 1
        rows, has_next = student_attendance_page(current_user.id, page)

    totals, total_count = student_status_totals(current_user.id)
    return private_no_store(
        "student/attendance/list.html",
        records=build_student_attendance_view(rows),
        totals=totals,
        total_count=total_count,
        # The stored class date and wall-clock window are LOCAL civil
        # values copied from the Schedule, so the page names the center
        # timezone rather than converting anything.
        tz_name=current_app.config.get("APP_TIMEZONE", "UTC"),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )
