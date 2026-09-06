"""Student Assignment reading (Phase 4 / M01): a bounded personal list
and one authorized nested Assignment page.

Two GET-only routes -- ``/student/assignments`` and
``/student/groups/<group_public_id>/assignments/<assignment_public_id>``.
Neither writes anything.

**Authorization is SQL-scoped** to ``current_user.id`` in
``app/services/assignment_queries.py`` -- an Assignment is never loaded
broadly and then authorized. A Student receives a row only when the query
itself proves the whole effective-visibility formula: the account is that
Student with a valid role and active status, holds an **active**
``Enrollment`` for the Group, the AcademicTerm / Level / Course / Group
are all active, the Assignment is ``published``, and its ``opens_at`` has
been reached. Every failure -- a draft, a published Assignment that has
not opened yet, a withdrawn or missing Enrollment, an archived ancestor,
another Group's Assignment public id, a mismatched nested pair, or a
simply non-existent id -- returns the identical non-disclosing **404**.

Visibility deliberately does **not** depend on a Schedule existing, nor
on any current Teacher assignment: those answer different questions.
A **past-due** Assignment stays visible; M01 has no submission route, so
"Past due" is informational only.

``roles_required(STUDENT)`` gives the role guard (anonymous -> login, any
other role -> 403); a suspended Student cannot hold a session at all (the
Flask-Login ``user_loader`` rejects it), and the query re-proves role and
active status regardless.

Both responses carry ``Cache-Control: private, no-store`` and
``Vary: Cookie`` -- these pages are per-Student and time-gated, so a
shared or reused cache entry could show one Student another's list, or
show an Assignment after it stopped being visible.
"""

from flask import abort, current_app, request
from flask_login import current_user

from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.assignment_queries import (
    PAGE_SIZE,
    build_student_item,
    build_student_view,
    normalize_page,
    student_assignment_detail,
    student_assignments_page,
)
from app.services.schedule_occurrences import utc_reference_now


@student_bp.get("/assignments")
@roles_required(UserRole.STUDENT.value)
def assignments_list():
    """Every Assignment this Student can currently see, in fixed pages of
    :data:`PAGE_SIZE`: **open work by nearest deadline, then past-due
    work most recent first** (``student_list_order``).

    That bucketing is what stops a long history of overdue work from
    pushing an approaching deadline onto a later page. One reference
    moment is derived here and passed into the query and the presentation
    builder, so the visibility gate, the bucketing, the ordering, and the
    Open / Past due labels on this page all agree.
    """
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    reference_utc = utc_reference_now()
    page = normalize_page(request.args.get("page"))

    rows, has_next = student_assignments_page(current_user.id, reference_utc, page)
    if not rows and page > 1:
        # A page past the end (a stale bookmark, or an Assignment that
        # has since become invisible) shows page 1 rather than a
        # confusing empty page with a "Previous" button.
        page = 1
        rows, has_next = student_assignments_page(current_user.id, reference_utc, page)

    return private_no_store(
        "student/assignments/list.html",
        assignments=build_student_view(rows, tz_name, reference_utc),
        tz_name=tz_name,
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


@student_bp.get("/groups/<group_public_id>/assignments/<assignment_public_id>")
@roles_required(UserRole.STUDENT.value)
def assignment_detail(group_public_id, assignment_public_id):
    """One Assignment, or a non-disclosing 404.

    Both public ids are supplied to the same fully scoped query, so a
    valid Assignment id paired with the wrong Group id -- and every other
    unauthorized combination -- produces no row and the same 404.
    """
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    reference_utc = utc_reference_now()
    row = student_assignment_detail(
        current_user.id, group_public_id, assignment_public_id, reference_utc
    )
    if row is None:
        abort(404)
    return private_no_store(
        "student/assignments/detail.html",
        assignment=build_student_item(row, tz_name, reference_utc),
        tz_name=tz_name,
    )
