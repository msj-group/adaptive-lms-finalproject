from flask import render_template, request
from sqlalchemy import or_

from app.blueprints.admin import admin_bp
from app.models import User, UserRole, UserStatus
from app.security.decorators import roles_required


@admin_bp.get("/students")
@roles_required(UserRole.ADMINISTRATOR.value)
def students_list():
    search = request.args.get("q", "").strip()
    raw_status = request.args.get("status", "").strip()
    status = raw_status if raw_status in {s.value for s in UserStatus} else ""

    query = User.query.filter(User.role == UserRole.STUDENT.value)
    if search:
        like = f"%{search}%"
        query = query.filter(or_(User.full_name.ilike(like), User.email.ilike(like)))
    if status:
        query = query.filter(User.status == status)

    students = query.order_by(User.created_at.desc(), User.id.desc()).all()
    return render_template(
        "admin/students/list.html",
        students=students,
        search=search,
        selected_status=status,
    )
