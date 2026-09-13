"""Teacher read-only lesson progress for one assigned Group
(Phase 4 / M13).

One Teacher-only route, addressed by the Group's ``public_id``::

    GET  /teacher/groups/<gp>/progress

There is deliberately **no** Teacher route that marks, clears or edits a
Student's progress: completion is the Student's own statement about their
own work, and no such endpoint exists server-side.

**Authorization is the current assignment.** ``roles_required(TEACHER)``
gives the project's standard behaviour -- anonymous users follow the login
redirect, every other role receives 403 -- and the Group is then found
through
:func:`~app.services.lesson_progress_queries.teacher_progress_group`, which
proves in SQL an active Teacher account and an **active**
``GroupTeacherAssignment`` to exactly that Group. An unassigned Teacher, a
removed assignment and a malformed or unknown identifier are the same
non-disclosing 404. Like the Units, Lessons, Attendance and Gradebook pages
-- and unlike Discussions -- the page stays readable while the Group or an
ancestor is archived, so a finished term's progress can be read back; the
page says plainly that such a Group is not operational.

**Scope.** Only Students with an active Enrollment in this Group and an
active Student account are listed, 20 per page by name, and only their rows
for this Group are counted -- against the Lessons Students could open here:
published Lessons in active Units. No other Group is named, and no internal
id or email address reaches the page.

**Bounded.** Four queries whatever the roster or history size: the Group
proof, one roster page (``TEACHER_PAGE_SIZE + 1`` rows, no COUNT), one
Lesson COUNT and one grouped aggregate over that page's Students.

The response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``.
"""

from flask import abort, current_app, request
from flask_login import current_user

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.assignments import _private_no_store
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.lesson_progress_queries import (
    TEACHER_PAGE_SIZE,
    build_roster_view,
    group_lesson_total,
    normalize_page,
    public_group,
    roster_page,
    roster_progress,
    teacher_progress_group,
)

_TEACHER = UserRole.TEACHER.value


@teacher_bp.get("/groups/<group_public_id>/progress")
@roles_required(_TEACHER)
def group_progress(group_public_id):
    """One page of the Group's Students with their lesson progress."""
    group = teacher_progress_group(current_user.id, group_public_id)
    if group is None:
        abort(404)
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    page = normalize_page(request.args.get("page"))
    rows, has_next = roster_page(group["id"], page)
    if not rows and page > 1:
        page = 1
        rows, has_next = roster_page(group["id"], page)
    lesson_total = group_lesson_total(group["id"])
    progress = roster_progress(group["id"], [row[0] for row in rows])
    return _private_no_store(
        "teacher/progress/group.html",
        group=public_group(group),
        students=build_roster_view(rows, progress, tz_name),
        lesson_total=lesson_total,
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=TEACHER_PAGE_SIZE,
        tz_name=tz_name,
    )
