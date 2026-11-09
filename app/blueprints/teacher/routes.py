from flask import current_app, url_for
from flask_login import current_user

from app.blueprints.teacher import teacher_bp
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.announcement_queries import (
    DASHBOARD_PREVIEW_CAP,
    build_reader_view,
    teacher_dashboard_preview,
)
from app.services.dashboard_queries import teacher_dashboard
from app.services.activity_queries import TYPES as ACTIVITY_TYPES
from app.services.schedule_occurrences import app_now
from app.services.communication_presentation import live_requested
from app.services.query_presentation import render_query_page


@teacher_bp.get("/dashboard")
@roles_required(UserRole.TEACHER.value)
def dashboard():
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    if live_requested():
        announcements = build_reader_view(teacher_dashboard_preview(current_user.id), tz_name)
        return render_query_page(
            "teacher/dashboard.html", announcements=announcements, announcement_cap=DASHBOARD_PREVIEW_CAP,
        )
    now = app_now(tz_name)
    data = teacher_dashboard(current_user.id, now)
    # Phase 4 / M09: one more bounded query, independent of how many
    # announcements exist, applying the SAME visibility clause the Teacher
    # announcement feed applies.
    announcements = build_reader_view(teacher_dashboard_preview(current_user.id), tz_name)
    from app.services.teacher_review_queries import review_page, review_view
    review_rows, review_has_more = review_page(current_user.id, tz_name, cap=3)
    pending_reviews = review_view(review_rows, tz_name, url_for)
    # The recent conversations are private correspondence, so this page
    # now carries the same private-page headers as every messaging page.
    response = render_query_page(
        "teacher/dashboard.html",
        tz_name=tz_name,
        now=now,
        announcements=announcements,
        announcement_cap=DASHBOARD_PREVIEW_CAP,
        pending_reviews=pending_reviews,
        review_has_more=review_has_more,
        activity_types=ACTIVITY_TYPES,
        **data,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response
