from datetime import datetime, timezone

from flask import current_app, make_response, render_template
from flask_login import current_user

from app.blueprints.student import student_bp
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.assignment_queries import (
    DASHBOARD_DEADLINE_CAP,
    build_student_view,
    student_upcoming_deadlines,
)
from app.services.dashboard_queries import student_dashboard
from app.services.schedule_occurrences import app_now, utc_reference_now


def private_no_store(template, **context):
    """Render `template` with the two headers every personal, time-gated
    Student page must carry.

    ``private, no-store`` because the content is per-Student and gated on
    a moment in time -- an Assignment can stop being visible, and a
    Student's own browser or an intermediary must not keep serving the
    old page. ``Vary: Cookie`` so a cache can never hand one session's
    page to another.

    Defined here rather than in ``student/assignments.py`` so both can
    share it without a circular import: this module imports nothing from
    ``student.assignments``, and ``__init__`` loads ``routes`` first.
    """
    response = make_response(render_template(template, **context))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


@student_bp.get("/dashboard")
@roles_required(UserRole.STUDENT.value)
def dashboard():
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    # ONE reference moment for the whole response (Phase 4 / M01): the
    # local wall clock the M09 schedule sections already use, and the
    # naive-UTC instant the Assignment deadline section needs, are both
    # derived from it. Reading the clock twice could straddle a deadline
    # and render a page that contradicts itself.
    utc_now = datetime.now(timezone.utc)
    now = app_now(tz_name, utc_now)
    reference_utc = utc_reference_now(utc_now)

    data = student_dashboard(current_user.id, now)
    # One additional bounded query, independent of how many Assignments
    # exist. It re-proves the full Student visibility formula in SQL, so
    # a deadline can never appear here for something the Student could
    # not open.
    upcoming_deadlines = build_student_view(
        student_upcoming_deadlines(current_user.id, reference_utc), tz_name, reference_utc
    )
    return private_no_store(
        "student/dashboard.html",
        tz_name=tz_name,
        now=now,
        upcoming_deadlines=upcoming_deadlines,
        deadline_cap=DASHBOARD_DEADLINE_CAP,
        **data,
    )
