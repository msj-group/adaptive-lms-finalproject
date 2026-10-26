from datetime import timedelta

from flask import current_app, render_template

from app.blueprints.admin import admin_bp
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.dashboard_queries import admin_dashboard
from app.services.schedule_occurrences import app_now


@admin_bp.get("/dashboard")
@roles_required(UserRole.ADMINISTRATOR.value)
def dashboard():
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    now = app_now(tz_name)
    data = admin_dashboard(now)
    monday = now.date() - timedelta(days=now.weekday())
    week_days = [dict(label=label, date=monday + timedelta(days=index),
                      today=index == now.weekday())
                 for index, label in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))]
    return render_template(
        "admin/dashboard.html",
        tz_name=tz_name,
        now=now,
        week_days=week_days,
        **data,
    )
