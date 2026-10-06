from datetime import datetime, timezone

from flask import current_app, make_response, redirect, render_template
from flask_login import current_user

from app.blueprints.student import student_bp
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.assignment_queries import (
    DASHBOARD_DEADLINE_CAP,
    build_student_view,
    student_upcoming_deadlines,
)
from app.services.announcement_queries import (
    DASHBOARD_PREVIEW_CAP,
    build_reader_view,
    student_dashboard_preview,
)
from app.services.dashboard_queries import student_dashboard
from app.services.lesson_progress_queries import (
    DASHBOARD_GROUP_LIMIT,
    RECENTLY_OPENED_LIMIT,
    build_recent_view,
    continue_learning,
    recently_opened,
    student_group_progress,
)
from app.services.message_queries import (
    DASHBOARD_RECENT_CAP,
    build_inbox_view,
    recent_conversations,
)
from app.services.schedule_occurrences import app_now, utc_reference_now


def private_redirect(target):
    """Keep personalized compatibility redirects out of browser/shared caches."""
    response = redirect(target, code=303)
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


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
    from flask import url_for
    from app.services.activity_queries import activities_page, activity_view
    activity_rows, _ = activities_page(current_user.id, reference_utc, upcoming=True, cap=DASHBOARD_DEADLINE_CAP)
    upcoming_deadlines = activity_view(activity_rows, tz_name, url_for)
    # Phase 4 / M09: one more bounded query, independent of how many
    # announcements exist. It applies the SAME visibility clause the
    # announcement feed applies, so nothing can be previewed here that the
    # feed would hide -- and a Student with no enrollment at all still
    # sees the center's announcements, which is why this section is
    # rendered outside the "no active enrollments" branch.
    announcements = build_reader_view(student_dashboard_preview(current_user.id), tz_name)
    # Phase 4 / M11: one more bounded query. Thread membership alone
    # decides what appears, so a conversation whose academic relationship
    # has ended stays listed -- which is also why it renders outside the
    # "no active enrollments" branch.
    conversations = build_inbox_view(
        recent_conversations(current_user.id), current_user.id, tz_name
    )
    # Phase 4 / M13: three more bounded queries, independent of how many
    # Groups, Lessons or progress rows exist, and run only for a Student
    # with an active enrollment -- the only case the sections render. Each
    # one re-proves the whole Student visibility formula in SQL, so no
    # Group, Lesson or destination can appear here that this Student could
    # not open right now.
    progress_groups, progress_truncated = [], False
    continue_lesson, recent_lessons = None, []
    if data["cards"]:
        progress_groups, progress_truncated = student_group_progress(current_user.id)
        continue_lesson = continue_learning(current_user.id)
        recent_lessons = build_recent_view(recently_opened(current_user.id), tz_name)
    return private_no_store(
        "student/dashboard.html",
        tz_name=tz_name,
        now=now,
        upcoming_deadlines=upcoming_deadlines,
        deadline_cap=DASHBOARD_DEADLINE_CAP,
        announcements=announcements,
        announcement_cap=DASHBOARD_PREVIEW_CAP,
        conversations=conversations,
        conversation_cap=DASHBOARD_RECENT_CAP,
        progress_groups=progress_groups,
        progress_truncated=progress_truncated,
        progress_group_cap=DASHBOARD_GROUP_LIMIT,
        continue_lesson=continue_lesson,
        recent_lessons=recent_lessons,
        recent_cap=RECENTLY_OPENED_LIMIT,
        **data,
    )
