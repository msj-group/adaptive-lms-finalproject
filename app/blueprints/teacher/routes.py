from flask import current_app, render_template
from flask_login import current_user

from app.blueprints.teacher import teacher_bp
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.dashboard_queries import teacher_dashboard
from app.services.schedule_occurrences import app_now


@teacher_bp.get("/dashboard")
@roles_required(UserRole.TEACHER.value)
def dashboard():
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    now = app_now(tz_name)
    data = teacher_dashboard(current_user.id, now)
    return render_template(
        "teacher/dashboard.html",
        tz_name=tz_name,
        now=now,
        **data,
    )
