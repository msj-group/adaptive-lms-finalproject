from flask import render_template

from app.blueprints.admin import admin_bp
from app.models import AcademicStatus, AcademicTerm, Course, Level, UserRole
from app.security.decorators import roles_required


@admin_bp.get("/dashboard")
@roles_required(UserRole.ADMINISTRATOR.value)
def dashboard():
    stats = {
        "academic_terms_total": AcademicTerm.query.count(),
        "academic_terms_active": AcademicTerm.query.filter_by(status=AcademicStatus.ACTIVE.value).count(),
        "levels_total": Level.query.count(),
        "levels_active": Level.query.filter_by(status=AcademicStatus.ACTIVE.value).count(),
        "courses_total": Course.query.count(),
        "courses_active": Course.query.filter_by(status=AcademicStatus.ACTIVE.value).count(),
    }
    return render_template("admin/dashboard.html", stats=stats)
