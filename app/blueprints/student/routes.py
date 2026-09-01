from flask import current_app, render_template
from flask_login import current_user

from app.blueprints.student import student_bp
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.dashboard_queries import student_dashboard
from app.services.schedule_occurrences import app_now


@student_bp.get("/dashboard")
@roles_required(UserRole.STUDENT.value)
def dashboard():
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    now = app_now(tz_name)
    data = student_dashboard(current_user.id, now)
    return render_template(
        "student/dashboard.html",
        tz_name=tz_name,
        now=now,
        **data,
    )
