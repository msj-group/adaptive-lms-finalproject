from flask import current_app, make_response, render_template, url_for
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
from app.services.message_queries import (
    DASHBOARD_RECENT_CAP,
    build_inbox_view,
    recent_conversations,
)
from app.services.schedule_occurrences import app_now


@teacher_bp.get("/dashboard")
@roles_required(UserRole.TEACHER.value)
def dashboard():
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    now = app_now(tz_name)
    data = teacher_dashboard(current_user.id, now)
    # Phase 4 / M09: one more bounded query, independent of how many
    # announcements exist, applying the SAME visibility clause the Teacher
    # announcement feed applies.
    announcements = build_reader_view(teacher_dashboard_preview(current_user.id), tz_name)
    # Phase 4 / M11: one more bounded query, keyed on thread membership
    # alone, so a conversation with a former student stays listed.
    conversations = build_inbox_view(
        recent_conversations(current_user.id), current_user.id, tz_name
    )
    from app.services.teacher_review_queries import review_page, review_view
    review_rows, review_has_more = review_page(current_user.id, tz_name, cap=5)
    pending_reviews = review_view(review_rows, tz_name, url_for)
    # The recent conversations are private correspondence, so this page
    # now carries the same private-page headers as every messaging page.
    response = make_response(
        render_template(
            "teacher/dashboard.html",
            tz_name=tz_name,
            now=now,
            announcements=announcements,
            announcement_cap=DASHBOARD_PREVIEW_CAP,
            conversations=conversations,
            conversation_cap=DASHBOARD_RECENT_CAP,
            pending_reviews=pending_reviews,
            review_has_more=review_has_more,
            **data,
        )
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response
