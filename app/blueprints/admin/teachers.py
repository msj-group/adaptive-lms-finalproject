from flask import render_template, request
from sqlalchemy import or_

from app.blueprints.admin import admin_bp
from app.models import User, UserRole, UserStatus
from app.security.decorators import roles_required


def _escape_like(value):
    """Escape LIKE/ILIKE metacharacters so a search term containing a
    literal '%' or '_' is matched as those literal characters instead of
    being interpreted as a SQL wildcard.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@admin_bp.get("/teachers")
@roles_required(UserRole.ADMINISTRATOR.value)
def teachers_list():
    search = request.args.get("q", "").strip()
    raw_status = request.args.get("status", "").strip()
    status = raw_status if raw_status in {s.value for s in UserStatus} else ""

    query = User.query.filter(User.role == UserRole.TEACHER.value)
    if search:
        # Prefix match only: the beginning of the full name, the beginning
        # of any individual word within it, or the beginning of the email.
        escaped = _escape_like(search)
        name_prefix = f"{escaped}%"
        name_word_prefix = f"% {escaped}%"
        email_prefix = f"{escaped}%"
        query = query.filter(
            or_(
                User.full_name.ilike(name_prefix, escape="\\"),
                User.full_name.ilike(name_word_prefix, escape="\\"),
                User.email.ilike(email_prefix, escape="\\"),
            )
        )
    if status:
        query = query.filter(User.status == status)

    teachers = query.order_by(User.created_at.desc(), User.id.desc()).all()
    is_live_search_request = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    template = "admin/teachers/_results.html" if is_live_search_request else "admin/teachers/list.html"
    return render_template(
        template,
        teachers=teachers,
        search=search,
        selected_status=status,
    )
