from flask import current_app, render_template
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
    return render_template(
        "teacher/dashboard.html",
        tz_name=tz_name,
        now=now,
        announcements=announcements,
        announcement_cap=DASHBOARD_PREVIEW_CAP,
        **data,
    )
