from flask import render_template

from app.blueprints.admin import admin_bp
from app.models import AcademicTerm, Course, Level, UserRole
from app.security.decorators import roles_required


@admin_bp.get("/academics")
@roles_required(UserRole.ADMINISTRATOR.value)
def academics():
    counts = {
        "academic_terms": AcademicTerm.query.count(),
        "levels": Level.query.count(),
        "courses": Course.query.count(),
    }
    return render_template("admin/academics.html", counts=counts)
